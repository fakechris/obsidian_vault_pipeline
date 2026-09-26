//! Ask-agent session transcript store — the AUDIT AUTHORITY for agent turns
//! (candidate `ask_agent-v1`, guardrail `transcript_authority`).
//!
//! One JSONL file per session under `<sessions_dir>/<session_id>.jsonl`,
//! schema `ovp.ask_transcript/v1`. Design contracts (A0 §5.2):
//!
//! - **Turn atomicity** (`turn_atomicity_recovery`): a turn's events are
//!   buffered in memory and committed as ONE append finalized by a
//!   `turn_finished` event. On open, trailing events after the last
//!   `turn_finished` are COMPACTED away — crash recovery always lands on the
//!   last complete turn.
//! - **Idempotency** (`idempotency`): `turn_started` records the caller's
//!   `idempotency_key`; a completed turn with the same key replays its
//!   outcome instead of running again.
//! - **Session serialization** (`session_serialization`): an OS advisory lock
//!   (`File::try_lock`) on `<session_id>.lock` serializes same-session turns.
//!   The kernel releases it when the holder dies, so there is no stale-lock
//!   reclaim step. The PID-file reclaim it replaced could let two turns hold
//!   one session (`docs/tla/SessionLock.tla`).
//! - **Projection** (`transcript_authority`): the model context is REBUILT
//!   from stored `message` events under a hard char cap that trims whole
//!   turns oldest-first — a tool_use/tool_result pair can never be split
//!   because a turn is the trim unit.

use std::collections::BTreeSet;
use std::fs;
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use ovp_llm::ModelMessage;
use serde::{Deserialize, Serialize};

pub const TRANSCRIPT_SCHEMA: &str = "ovp.ask_transcript/v1";

/// The on-disk line shape: EVERY event row carries the schema version and its
/// session id, so audit rows stay versioned and correlatable even when
/// separated from their filename (turn ids restart per session).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct TranscriptLine {
    schema: String,
    session_id: String,
    #[serde(flatten)]
    event: TranscriptEvent,
}

/// One transcript event (the line wrapper above adds schema + session id).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum TranscriptEvent {
    TurnStarted {
        turn_id: String,
        question: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        idempotency_key: Option<String>,
    },
    /// A conversation message appended during the turn, in order. The
    /// projection for later turns is rebuilt from exactly these events.
    Message {
        turn_id: String,
        message: ModelMessage,
    },
    /// One model call's cost (`token_accounting` — A0 §5.2 requires
    /// {in, out, src, scope} so multi-client / nested-ask usage stays
    /// attributable).
    ModelCalled {
        turn_id: String,
        round: usize,
        input_tokens: u32,
        output_tokens: u32,
        /// Which model incurred the usage.
        #[serde(default)]
        src: String,
        /// Budget scope — "turn" today; nested asks stamp their own.
        #[serde(default)]
        scope: String,
    },
    /// One tool execution's audit line. `content` is the FULL RAW result —
    /// the audit transcript is complete by contract; the adjacent
    /// `ToolResults` message carries the CAPPED text the model actually saw
    /// (that distinction is the audit-vs-projection split, A0 §3.5).
    ToolCalled {
        turn_id: String,
        tool_call_id: String,
        tool: String,
        is_error: bool,
        /// Raw (pre-cap) result size in bytes.
        result_bytes: usize,
        /// Full raw result content (audit-only; never projected).
        content: String,
        /// Whether the model-facing copy was truncated to the result cap.
        truncated: bool,
        /// The result arrived after the turn deadline: audit-only data the
        /// model/caller never saw — replays must show the marker, not this.
        #[serde(default)]
        late: bool,
    },
    /// A model call failed (audit-only; excluded from projection).
    ModelFailed {
        turn_id: String,
        round: usize,
        detail: String,
        /// [`ovp_llm::failure_class`] slug (auth, rate_limited, …). Default
        /// covers transcripts written before the field existed.
        #[serde(default)]
        class: String,
    },
    /// A model reply was rejected by the runtime without entering the
    /// conversation (duplicate tool_use ids, over-cap round) — audit-only.
    ReplyDiscarded {
        turn_id: String,
        round: usize,
        reason: String,
    },
    TurnFinished {
        turn_id: String,
        stopped_reason: String,
        answer: String,
        rounds: usize,
        input_tokens_total: u32,
        output_tokens_total: u32,
    },
}

impl TranscriptEvent {
    fn turn_id(&self) -> &str {
        match self {
            TranscriptEvent::TurnStarted { turn_id, .. }
            | TranscriptEvent::Message { turn_id, .. }
            | TranscriptEvent::ModelCalled { turn_id, .. }
            | TranscriptEvent::ToolCalled { turn_id, .. }
            | TranscriptEvent::ModelFailed { turn_id, .. }
            | TranscriptEvent::ReplyDiscarded { turn_id, .. }
            | TranscriptEvent::TurnFinished { turn_id, .. } => turn_id,
        }
    }
}

/// One tool call in a turn's trail: `(call_id, tool_name, is_error, rendered
/// summary, raw input, text output, progress hits)`.
pub type ToolTrailEntry = (
    String,
    String,
    bool,
    String,
    serde_json::Value,
    Option<String>,
    Vec<crate::agent::ProgressHit>,
);

/// A previously COMPLETED turn's outcome, replayable for idempotent retries.
#[derive(Debug, Clone, PartialEq)]
pub struct CompletedTurn {
    pub turn_id: String,
    pub stopped_reason: String,
    pub answer: String,
    pub rounds: usize,
    pub input_tokens_total: u32,
    pub output_tokens_total: u32,
}

/// Session lock guard: an OS advisory lock held through an open handle on
/// `<session_id>.lock`. Dropping it closes the handle, which releases the lock.
/// The file is NEVER deleted. Deleting by path is what let a stale-lock
/// reclaim remove a live holder's fresh lock (`docs/tla/SessionLock.tla`,
/// `SessionLock.cfg`), and every contender must lock the same inode.
pub struct SessionLock {
    key: PathBuf,
    file: Option<fs::File>,
}

impl Drop for SessionLock {
    fn drop(&mut self) {
        // Release the OS lock BEFORE leaving the in-process set, so a thread
        // that wins the set never finds the OS lock still held by us.
        drop(self.file.take());
        held_in_process().remove(&self.key);
    }
}

/// Session locks this process holds. flock-style locks already conflict
/// between two handles in one process, but where the OS emulates them with
/// per-process fcntl locks (e.g. some network filesystems) they would not.
/// The desktop's in-process server threads must still serialize.
fn held_in_process() -> std::sync::MutexGuard<'static, BTreeSet<PathBuf>> {
    static HELD: Mutex<BTreeSet<PathBuf>> = Mutex::new(BTreeSet::new());
    HELD.lock().unwrap_or_else(|e| e.into_inner())
}

/// Best-effort holder PID for the busy message. The holder writes it after
/// acquiring the lock. Returns 0 when unknown: not written yet, or unreadable
/// (on Windows the holder's byte-range lock blocks other readers).
fn read_holder_pid(path: &Path) -> u32 {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok())
        .unwrap_or(0)
}

/// Errors the store distinguishes because callers behave differently on them.
#[derive(Debug)]
pub enum StoreError {
    /// Another live process (or turn) holds this session — retry later.
    SessionBusy { holder_pid: u32 },
    Io(String),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StoreError::SessionBusy { holder_pid } => {
                write!(f, "session busy (held by pid {holder_pid})")
            }
            StoreError::Io(detail) => write!(f, "transcript store: {detail}"),
        }
    }
}

impl std::error::Error for StoreError {}

/// Store for ONE session's transcript. Opening compacts a crash-torn tail.
pub struct SessionStore {
    path: PathBuf,
    lock_path: PathBuf,
    session_id: String,
    /// Complete-turn events only (compaction dropped any torn tail).
    events: Vec<TranscriptEvent>,
}

/// Session ids come from clients; confine them to one path segment.
pub fn valid_session_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

impl SessionStore {
    /// Open (creating the dir if needed) and take a READ-ONLY view: events
    /// after the last `turn_finished` are ignored IN MEMORY, but the file is
    /// never rewritten here — a concurrent turn may be mid-append, and a
    /// pre-lock rewrite would race it (rename to the compacted copy while the
    /// active writer finishes against the old inode, losing its turn).
    /// Physical compaction happens only under the session lock, in
    /// [`Self::reload_under_lock`].
    pub fn open(sessions_dir: &Path, session_id: &str) -> Result<Self, StoreError> {
        if !valid_session_id(session_id) {
            return Err(StoreError::Io(format!(
                "invalid session id `{session_id}` (want [A-Za-z0-9_-]{{1,64}})"
            )));
        }
        fs::create_dir_all(sessions_dir)
            .map_err(|e| StoreError::Io(format!("create {}: {e}", sessions_dir.display())))?;
        let path = sessions_dir.join(format!("{session_id}.jsonl"));
        let lock_path = sessions_dir.join(format!("{session_id}.lock"));
        let mut store =
            Self { path, lock_path, session_id: session_id.to_string(), events: Vec::new() };
        store.read_complete_events()?;
        Ok(store)
    }

    /// (Re)read the file into `self.events`, truncating to the last complete
    /// turn IN MEMORY. Returns whether the file had a torn/extra tail.
    fn read_complete_events(&mut self) -> Result<bool, StoreError> {
        let mut events: Vec<TranscriptEvent> = Vec::new();
        let mut torn_tail = false;
        if self.path.is_file() {
            let body = fs::read_to_string(&self.path)
                .map_err(|e| StoreError::Io(format!("read {}: {e}", self.path.display())))?;
            let mut parsed: Vec<TranscriptEvent> = Vec::new();
            for line in body.lines().filter(|l| !l.trim().is_empty()) {
                match serde_json::from_str::<TranscriptLine>(line) {
                    // A schema we don't understand OR a row from another
                    // session (renamed/copied file) is treated like a torn
                    // line: conservative — foreign events must never join the
                    // projection, replay, or turn numbering, and compaction
                    // must not relabel them.
                    Ok(l) if l.schema == TRANSCRIPT_SCHEMA && l.session_id == self.session_id => {
                        parsed.push(l.event)
                    }
                    // A torn/corrupt/foreign line means everything from here
                    // on is suspect; keep only what precedes it.
                    _ => {
                        torn_tail = true;
                        break;
                    }
                }
            }
            let last_complete = parsed
                .iter()
                .rposition(|e| matches!(e, TranscriptEvent::TurnFinished { .. }))
                .map(|i| i + 1)
                .unwrap_or(0);
            torn_tail = torn_tail || last_complete != parsed.len();
            parsed.truncate(last_complete);
            events = parsed;
        }
        self.events = events;
        Ok(torn_tail)
    }

    /// Read-only refresh (no rewrite): update the in-memory complete-turn
    /// view. Used before pre-lock idempotency lookups on long-lived stores —
    /// a key committed by ANOTHER process must replay even while a different
    /// turn holds the lock.
    pub fn refresh_readonly(&mut self) -> Result<(), StoreError> {
        self.read_complete_events().map(|_| ())
    }

    /// Under the session lock: re-read the file (the pre-lock view may be
    /// stale — another turn can have committed between `open` and `lock`) and
    /// physically COMPACT a crash-torn tail. Safe here and only here: the
    /// lock guarantees no concurrent appender.
    pub fn reload_under_lock(&mut self, _lock: &SessionLock) -> Result<(), StoreError> {
        let torn = self.read_complete_events()?;
        if torn {
            self.rewrite()?;
        }
        Ok(())
    }

    /// One on-disk line for `ev`: schema + session id + the event fields.
    fn line_json(&self, ev: &TranscriptEvent) -> Result<String, StoreError> {
        serde_json::to_string(&TranscriptLine {
            schema: TRANSCRIPT_SCHEMA.to_string(),
            session_id: self.session_id.clone(),
            event: ev.clone(),
        })
        .map_err(|e| StoreError::Io(format!("serialize event: {e}")))
    }

    /// Atomic whole-file rewrite (tmp + rename) — used only by compaction.
    fn rewrite(&self) -> Result<(), StoreError> {
        let mut body = String::new();
        for ev in &self.events {
            body.push_str(&self.line_json(ev)?);
            body.push('\n');
        }
        let tmp = self.path.with_extension("jsonl.tmp");
        fs::write(&tmp, body).map_err(|e| StoreError::Io(format!("write tmp: {e}")))?;
        fs::rename(&tmp, &self.path)
            .map_err(|e| StoreError::Io(format!("publish {}: {e}", self.path.display())))
    }

    /// Serialize this session: take the OS lock on `<session_id>.lock`.
    /// A leftover file from a crashed or older run is simply locked again.
    pub fn lock(&self) -> Result<SessionLock, StoreError> {
        // The PID stamp truncates the file, so never follow a symlink planted
        // at the lock path: that would truncate the link's target. On unix
        // this is atomic (O_NOFOLLOW makes the open itself fail on a link).
        // Elsewhere, fall back to a check before the open.
        #[cfg(not(unix))]
        if fs::symlink_metadata(&self.lock_path).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(StoreError::Io(format!(
                "lock {}: is a symlink, refusing to use it",
                self.lock_path.display()
            )));
        }
        let mut opts = fs::OpenOptions::new();
        opts.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.custom_flags(libc::O_NOFOLLOW);
        }
        let mut file = opts.open(&self.lock_path).map_err(|e| {
            StoreError::Io(format!("lock {}: {e} (a symlink is refused)", self.lock_path.display()))
        })?;
        if !file.metadata().is_ok_and(|m| m.is_file()) {
            return Err(StoreError::Io(format!(
                "lock {}: not a regular file",
                self.lock_path.display()
            )));
        }
        let key = fs::canonicalize(&self.lock_path).unwrap_or_else(|_| self.lock_path.clone());
        // Reserve the key in the in-process set, then release that mutex
        // before the (possibly sleeping) retries, so a contended session
        // never stalls lock attempts on unrelated sessions. The reservation
        // is removed on every failure path.
        if !held_in_process().insert(key.clone()) {
            return Err(StoreError::SessionBusy { holder_pid: std::process::id() });
        }
        let release_reservation = |key: &PathBuf| {
            held_in_process().remove(key);
        };
        // Retry WouldBlock briefly. A flock lives on the open file
        // DESCRIPTION: when any thread of this process spawns a child, the
        // child holds a copy of every fd from fork until exec closes the
        // CLOEXEC ones. A lock we just released stays held through that copy
        // for those microseconds, which gives a spurious "busy". The desktop
        // spawns sidecars while server threads take session locks. A real
        // holder (another turn) keeps the lock for seconds, so ~50ms of
        // retries only absorbs the spawn window. It never admits a second
        // holder.
        let mut attempt = 0;
        loop {
            match file.try_lock() {
                Ok(()) => break,
                Err(fs::TryLockError::WouldBlock) if attempt < 10 => {
                    attempt += 1;
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
                Err(fs::TryLockError::WouldBlock) => {
                    release_reservation(&key);
                    return Err(StoreError::SessionBusy {
                        holder_pid: read_holder_pid(&self.lock_path),
                    });
                }
                Err(fs::TryLockError::Error(e)) => {
                    release_reservation(&key);
                    return Err(StoreError::Io(format!("lock {}: {e}", self.lock_path.display())));
                }
            }
        }
        // Display only: correctness rests on the OS lock, not on this PID.
        let _ = file
            .set_len(0)
            .and_then(|_| file.seek(SeekFrom::Start(0)))
            .and_then(|_| write!(file, "{}", std::process::id()));
        Ok(SessionLock { key, file: Some(file) })
    }

    /// Next turn id: `t<N>` over COMPLETE turns (a torn turn was compacted
    /// away, so its id is safely reused).
    pub fn next_turn_id(&self) -> String {
        let n = self
            .events
            .iter()
            .filter(|e| matches!(e, TranscriptEvent::TurnFinished { .. }))
            .count();
        format!("t{}", n + 1)
    }

    /// The completed turn matching `idempotency_key`, if any — the idempotent
    /// replay source.
    pub fn completed_turn_for_key(&self, key: &str) -> Option<CompletedTurn> {
        let turn_id = self.events.iter().find_map(|e| match e {
            TranscriptEvent::TurnStarted { turn_id, idempotency_key: Some(k), .. } if k == key => {
                Some(turn_id.clone())
            }
            _ => None,
        })?;
        self.completed_turn(&turn_id)
    }

    fn completed_turn(&self, id: &str) -> Option<CompletedTurn> {
        self.events.iter().find_map(|e| match e {
            TranscriptEvent::TurnFinished {
                turn_id,
                stopped_reason,
                answer,
                rounds,
                input_tokens_total,
                output_tokens_total,
            } if turn_id == id => Some(CompletedTurn {
                turn_id: turn_id.clone(),
                stopped_reason: stopped_reason.clone(),
                answer: answer.clone(),
                rounds: *rounds,
                input_tokens_total: *input_tokens_total,
                output_tokens_total: *output_tokens_total,
            }),
            _ => None,
        })
    }

    /// Model-context projection: all `Message` events of complete turns, then
    /// trimmed to `max_chars` (serialized length) by dropping WHOLE turns
    /// oldest-first. A turn is the trim unit, so tool_use/tool_result pairs
    /// stay intact by construction and results are never orphaned.
    pub fn projection(&self, max_chars: usize) -> Vec<ModelMessage> {
        // Group message events by turn, in order.
        let mut turns: Vec<(String, Vec<ModelMessage>)> = Vec::new();
        for ev in &self.events {
            if let TranscriptEvent::Message { turn_id, message } = ev {
                match turns.last_mut() {
                    Some((id, msgs)) if id == turn_id => msgs.push(message.clone()),
                    _ => turns.push((turn_id.clone(), vec![message.clone()])),
                }
            }
        }
        // CHARS, not bytes: the budget is documented as characters, and CJK/
        // emoji content would otherwise be charged 3-4x and dropped early.
        let turn_len = |msgs: &[ModelMessage]| -> usize {
            msgs.iter()
                .map(|m| serde_json::to_string(m).map(|s| s.chars().count()).unwrap_or(0))
                .sum()
        };
        // Keep newest-first until the cap, then restore order.
        let mut kept: Vec<usize> = Vec::new();
        let mut used = 0usize;
        for (i, (_, msgs)) in turns.iter().enumerate().rev() {
            let len = turn_len(msgs);
            if used + len > max_chars {
                break;
            }
            used += len;
            kept.push(i);
        }
        kept.reverse();
        kept.into_iter()
            .flat_map(|i| turns[i].1.clone())
            .collect()
    }

    /// Commit a finished turn: ONE append of all its lines, fsynced. The last
    /// line must be `TurnFinished` — enforced here so a partial commit can
    /// never look complete to compaction.
    pub fn commit_turn(&mut self, turn_events: Vec<TranscriptEvent>) -> Result<(), StoreError> {
        let Some(TranscriptEvent::TurnFinished { .. }) = turn_events.last() else {
            return Err(StoreError::Io(
                "commit_turn: last event must be TurnFinished (atomicity contract)".into(),
            ));
        };
        let first_turn = turn_events
            .first()
            .map(|e| e.turn_id().to_string())
            .unwrap_or_default();
        if turn_events.iter().any(|e| e.turn_id() != first_turn) {
            return Err(StoreError::Io(
                "commit_turn: events span multiple turn ids".into(),
            ));
        }
        let mut body = String::new();
        for ev in &turn_events {
            body.push_str(&self.line_json(ev)?);
            body.push('\n');
        }
        let mut f = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| StoreError::Io(format!("open {}: {e}", self.path.display())))?;
        f.write_all(body.as_bytes())
            .map_err(|e| StoreError::Io(format!("append: {e}")))?;
        f.sync_data()
            .map_err(|e| StoreError::Io(format!("fsync: {e}")))?;
        self.events.extend(turn_events);
        Ok(())
    }

    /// The audit ToolCalled rows of one turn — replayed outcomes rebuild
    /// their tool_trace from these (a retry must not lose the provenance
    /// trail the original response carried).
    /// [`Self::tool_calls_for_turn`] plus the narration fields a replayed
    /// trail needs for 复盘: the model's call ARGUMENTS (from the recorded
    /// assistant blocks) and a result-stats note (from the full audit
    /// content; late results narrate nothing, matching the original reply).
    /// Every COMPLETED turn in transcript order — (turn_id, question,
    /// answer, stopped_reason). The saved-chat surface uses this to marry
    /// the product History with the audit trail's per-turn detail.
    pub fn completed_turns(&self) -> Vec<(String, String, String, String)> {
        // One chronological pass: a finish pairs only with a start already
        // SEEN — an orphaned finish never borrows a later turn's question.
        let mut questions: std::collections::HashMap<&str, &str> =
            std::collections::HashMap::new();
        let mut out = Vec::new();
        for e in &self.events {
            match e {
                TranscriptEvent::TurnStarted { turn_id, question, .. } => {
                    questions.insert(turn_id.as_str(), question.as_str());
                }
                TranscriptEvent::TurnFinished { turn_id, answer, stopped_reason, .. } => {
                    out.push((
                        turn_id.clone(),
                        questions.get(turn_id.as_str()).copied().unwrap_or("").to_string(),
                        answer.clone(),
                        stopped_reason.clone(),
                    ));
                }
                _ => {}
            }
        }
        out
    }

    pub fn tool_trail_for_turn(&self, id: &str) -> Vec<ToolTrailEntry> {
        let mut args_by_call: std::collections::HashMap<&str, &serde_json::Value> =
            std::collections::HashMap::new();
        for e in &self.events {
            if let TranscriptEvent::Message {
                turn_id,
                message: ovp_llm::ModelMessage::AssistantBlocks { blocks },
            } = e
                && turn_id == id
            {
                for b in blocks {
                    if let ovp_llm::AssistantBlock::ToolUse { id: call_id, input, .. } = b {
                        args_by_call.insert(call_id.as_str(), input);
                    }
                }
            }
        }
        self.events
            .iter()
            .filter_map(|e| match e {
                TranscriptEvent::ToolCalled {
                    turn_id, tool_call_id, tool, is_error, content, late, ..
                } if turn_id == id => Some((
                    tool.clone(),
                    tool_call_id.clone(),
                    *is_error,
                    if *late {
                        "late: discarded (audit only)".to_string()
                    } else {
                        content.chars().take(120).collect()
                    },
                    args_by_call
                        .get(tool_call_id.as_str())
                        .cloned()
                        .cloned()
                        .unwrap_or(serde_json::Value::Null),
                    if *late { None } else { crate::agent::tool_result_note(content) },
                    // Rebuild process-viz hits from the durable audit body
                    // so History/session replay keeps the same graph the
                    // live turn painted.
                    if *late || *is_error {
                        Vec::new()
                    } else {
                        crate::agent::tool_result_hits(content)
                    },
                )),
                _ => None,
            })
            .collect()
    }

    pub fn tool_calls_for_turn(&self, id: &str) -> Vec<(String, String, bool, String)> {
        self.events
            .iter()
            .filter_map(|e| match e {
                TranscriptEvent::ToolCalled {
                    turn_id, tool_call_id, tool, is_error, content, late, ..
                } if turn_id == id => Some((
                    tool.clone(),
                    tool_call_id.clone(),
                    *is_error,
                    // Late data is audit-only: the replayed trail shows the
                    // same marker the ORIGINAL response carried, never the
                    // withheld content.
                    if *late {
                        "late: discarded (audit only)".to_string()
                    } else {
                        content.chars().take(120).collect()
                    },
                )),
                _ => None,
            })
            .collect()
    }

    /// All complete-turn events (read-only view; tests + future exporters).
    pub fn events(&self) -> &[TranscriptEvent] {
        &self.events
    }
}

#[cfg(test)]
mod lock_tests {
    use super::*;

    const CHILD_DIR: &str = "OVP_TEST_SESSION_LOCK_DIR";

    /// Not a test on its own: the body of the child process that
    /// `session_lock_excludes_another_process_until_it_dies` spawns. It takes
    /// the lock, signals `ready`, and holds the lock until it is killed.
    #[test]
    fn child_holds_session_lock() {
        let Ok(dir) = std::env::var(CHILD_DIR) else { return };
        let dir = PathBuf::from(dir);
        let st = SessionStore::open(&dir, "s1").unwrap();
        let _held = st.lock().expect("child takes the free lock");
        fs::write(dir.join("ready"), "").unwrap();
        std::thread::sleep(std::time::Duration::from_secs(60));
    }

    fn wait_for(path: &Path) {
        for _ in 0..600 {
            if path.exists() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        panic!("timed out waiting for {}", path.display());
    }

    #[test]
    fn session_lock_excludes_another_process_until_it_dies() {
        let dir = tempfile::tempdir().unwrap();
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "agent_transcript::lock_tests::child_holds_session_lock"])
            .env(CHILD_DIR, dir.path())
            .stdout(std::process::Stdio::null())
            .spawn()
            .unwrap();
        wait_for(&dir.path().join("ready"));

        let st = SessionStore::open(dir.path(), "s1").unwrap();
        match st.lock() {
            Err(StoreError::SessionBusy { holder_pid }) => {
                // Windows: the holder's byte-range lock blocks reading the pid.
                #[cfg(unix)]
                assert_eq!(holder_pid, child.id());
                let _ = holder_pid;
            }
            Err(other) => panic!("expected SessionBusy, got {other}"),
            Ok(_) => panic!("a live holder in another process must exclude us"),
        }

        // SIGKILL / TerminateProcess: no Drop runs, the lock FILE stays. The
        // kernel releases the lock, so the next turn gets in with no reclaim.
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(dir.path().join("s1.lock").exists());
        drop(st.lock().expect("lock is free once the holder is dead"));
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_lock_path_is_refused_and_target_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("precious.md");
        fs::write(&target, "keep me").unwrap();
        std::os::unix::fs::symlink(&target, dir.path().join("s1.lock")).unwrap();
        let st = SessionStore::open(dir.path(), "s1").unwrap();
        assert!(matches!(st.lock(), Err(StoreError::Io(_))));
        assert_eq!(fs::read_to_string(&target).unwrap(), "keep me");
    }

    #[test]
    fn same_process_second_store_is_busy_and_released_on_drop() {
        let dir = tempfile::tempdir().unwrap();
        let a = SessionStore::open(dir.path(), "s1").unwrap();
        let b = SessionStore::open(dir.path(), "s1").unwrap();
        let held = a.lock().unwrap();
        assert!(matches!(b.lock(), Err(StoreError::SessionBusy { .. })));
        drop(held);
        drop(b.lock().expect("free after drop"));
    }
}
