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
//! - **Session serialization** (`session_serialization`): a pid lock file
//!   serializes same-session turns; stale (dead-pid) locks are reclaimed.
//! - **Projection** (`transcript_authority`): the model context is REBUILT
//!   from stored `message` events under a hard char cap that trims whole
//!   turns oldest-first — a tool_use/tool_result pair can never be split
//!   because a turn is the trim unit.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use ovp_llm::ModelMessage;
use serde::{Deserialize, Serialize};

pub const TRANSCRIPT_SCHEMA: &str = "ovp.ask_transcript/v1";

/// One transcript line. `schema` is stamped on every event so a reader never
/// needs file-level framing to know what it is looking at.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum TranscriptEvent {
    TurnStarted {
        schema: String,
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
    },
    /// A model call failed (audit-only; excluded from projection).
    ModelFailed {
        turn_id: String,
        round: usize,
        detail: String,
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

/// Session lock guard — pid file, removed on drop. Same primitive the daily
/// heartbeat / RunLock use to reclaim stale locks (probe with `kill -0`;
/// duplicated here because ovp-memory must not depend on ovp-daily).
pub struct SessionLock {
    path: PathBuf,
}

impl Drop for SessionLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(unix)]
    {
        std::process::Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            // Conservative: if the probe can't run, assume alive so a real
            // concurrent turn is never falsely stolen.
            .unwrap_or(true)
    }
    #[cfg(not(unix))]
    {
        true
    }
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
        let mut store = Self { path, lock_path, events: Vec::new() };
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
                match serde_json::from_str::<TranscriptEvent>(line) {
                    Ok(ev) => parsed.push(ev),
                    // A torn/corrupt line means everything from here on is
                    // suspect; keep only what precedes it.
                    Err(_) => {
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

    /// Atomic whole-file rewrite (tmp + rename) — used only by compaction.
    fn rewrite(&self) -> Result<(), StoreError> {
        let mut body = String::new();
        for ev in &self.events {
            body.push_str(
                &serde_json::to_string(ev)
                    .map_err(|e| StoreError::Io(format!("serialize event: {e}")))?,
            );
            body.push('\n');
        }
        let tmp = self.path.with_extension("jsonl.tmp");
        fs::write(&tmp, body).map_err(|e| StoreError::Io(format!("write tmp: {e}")))?;
        fs::rename(&tmp, &self.path)
            .map_err(|e| StoreError::Io(format!("publish {}: {e}", self.path.display())))
    }

    /// Serialize this session: create the pid lock, reclaiming a stale one.
    pub fn lock(&self) -> Result<SessionLock, StoreError> {
        for attempt in 0..2 {
            match fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&self.lock_path)
            {
                Ok(mut f) => {
                    // The pid stamp must land: an unstamped lock reads as
                    // invalid to other contenders (conservatively busy), and
                    // holding one would wedge the session; on failure, release
                    // and surface the IO error.
                    if let Err(e) = write!(f, "{}", std::process::id()).and_then(|_| f.sync_data())
                    {
                        drop(f);
                        let _ = fs::remove_file(&self.lock_path);
                        return Err(StoreError::Io(format!("stamp lock: {e}")));
                    }
                    return Ok(SessionLock { path: self.lock_path.clone() });
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    // Missing/empty/invalid owner data is CONSERVATIVELY BUSY:
                    // a racing creator may sit between create_new and the pid
                    // write, and stealing its lock would let two turns run
                    // concurrently. (The crashed-unstamped case is a
                    // microsecond window; doctor can clean a wedged lock.)
                    let Some(holder) = fs::read_to_string(&self.lock_path)
                        .ok()
                        .and_then(|s| s.trim().parse::<u32>().ok())
                        .filter(|pid| *pid != 0)
                    else {
                        return Err(StoreError::SessionBusy { holder_pid: 0 });
                    };
                    // A live holder is BUSY — including our own pid: two
                    // stores in one process (desktop in-process server
                    // threads) must serialize too, and a same-pid leak is
                    // practically impossible (panic unwinds run Drop; an
                    // abort gets a fresh pid).
                    if pid_alive(holder) {
                        return Err(StoreError::SessionBusy { holder_pid: holder });
                    }
                    // Stale (dead pid / unreadable). Reclaim must be ATOMIC:
                    // a naive remove-then-create lets contender B delete the
                    // lock contender A just created after the same removal.
                    // rename() arbitrates — exactly one mover wins; the loser
                    // gets NotFound and treats the session as busy (someone
                    // else is mid-reclaim).
                    if attempt == 0 {
                        let grave = self
                            .lock_path
                            .with_extension(format!("stale-{}", std::process::id()));
                        match fs::rename(&self.lock_path, &grave) {
                            Ok(()) => {
                                let _ = fs::remove_file(&grave);
                                continue; // we won the reclaim — retry create_new
                            }
                            Err(_) => {
                                return Err(StoreError::SessionBusy { holder_pid: holder });
                            }
                        }
                    }
                    return Err(StoreError::SessionBusy { holder_pid: holder });
                }
                Err(e) => return Err(StoreError::Io(format!("lock: {e}"))),
            }
        }
        unreachable!("loop returns on every path");
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
            body.push_str(
                &serde_json::to_string(ev)
                    .map_err(|e| StoreError::Io(format!("serialize event: {e}")))?,
            );
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
    pub fn tool_calls_for_turn(&self, id: &str) -> Vec<(String, String, bool, String)> {
        self.events
            .iter()
            .filter_map(|e| match e {
                TranscriptEvent::ToolCalled { turn_id, tool_call_id, tool, is_error, content, .. }
                    if turn_id == id =>
                {
                    Some((
                        tool.clone(),
                        tool_call_id.clone(),
                        *is_error,
                        content.chars().take(120).collect(),
                    ))
                }
                _ => None,
            })
            .collect()
    }

    /// All complete-turn events (read-only view; tests + future exporters).
    pub fn events(&self) -> &[TranscriptEvent] {
        &self.events
    }
}
