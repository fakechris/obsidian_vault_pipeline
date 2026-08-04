//! Per-article source-work queue (translate / summarize).
//!
//! **Unit of concurrency = one source article.**
//! - Across articles: serial (one article running at a time).
//! - Within an article: translate + summarize may run in parallel.
//!
//! Durable file: `<vault>/.ovp/source-work-queue.json` so the portal can
//! survive a server restart and show order / cancel / reorder.

use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

const QUEUE_SCHEMA: &str = "ovp.source-work-queue/v1";
const QUEUE_REL: &str = ".ovp/source-work-queue.json";
/// Cross-process exclusive lock for queue file mutations (enqueue/cancel/…).
const QUEUE_WRITE_LOCK: &str = "source-work-queue.write.lock";
/// Cross-process election: only one process runs the background worker.
pub const WORKER_LOCK: &str = "source-work-worker.lock";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskKind {
    Translate,
    Summarize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus {
    Queued,
    Running,
    Done,
    Failed,
    Skipped,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ItemStatus {
    Queued,
    Running,
    Done,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskState {
    pub wanted: bool,
    #[serde(default)]
    pub force: bool,
    pub status: TaskStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// Transient failures so far (fail-back lifecycle). Serde-additive:
    /// pre-failback queue files deserialize as 0 = today's behavior.
    #[serde(default)]
    pub attempts: u32,
    /// Earliest epoch-second this task may be claimed again (retry backoff /
    /// budget defer). None = claimable as soon as queued.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub not_before: Option<u64>,
}

impl TaskState {
    fn idle(wanted: bool, force: bool) -> Self {
        Self {
            wanted,
            force,
            status: if wanted {
                TaskStatus::Queued
            } else {
                TaskStatus::Skipped
            },
            error: None,
            attempts: 0,
            not_before: None,
        }
    }
}

/// Higher = claimed sooner. Pre-priority queue files deserialize as 0 (backfill).
/// Interactive UI jobs use [`PRIORITY_INTERACTIVE`]; bulk backfill/daily use
/// [`PRIORITY_BACKFILL`].
pub const PRIORITY_BACKFILL: i32 = 0;
pub const PRIORITY_NORMAL: i32 = 50;
pub const PRIORITY_INTERACTIVE: i32 = 100;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueueItem {
    pub id: String,
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    pub translate: TaskState,
    pub summarize: TaskState,
    pub status: ItemStatus,
    pub created_at: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub finished_at: Option<u64>,
    /// Client should surface a desktop/browser notification when terminal.
    #[serde(default = "default_true")]
    pub notify: bool,
    /// Set once when the item becomes terminal so clients can fire notify once.
    #[serde(default)]
    pub notify_sent: bool,
    /// Scheduling priority: higher runs first among `queued` items.
    /// Default 0 = backfill; UI interactive = 100. Serde-additive.
    #[serde(default)]
    pub priority: i32,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueueFile {
    pub schema: String,
    pub items: Vec<QueueItem>,
}

impl Default for QueueFile {
    fn default() -> Self {
        Self {
            schema: QUEUE_SCHEMA.into(),
            items: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnqueueRequest {
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// Wanted tasks; empty = both when neither flag set is invalid.
    #[serde(default)]
    pub translate: bool,
    #[serde(default)]
    pub summarize: bool,
    #[serde(default)]
    pub force: bool,
    #[serde(default = "default_true")]
    pub notify: bool,
    /// Higher = claimed sooner. Omitted/0 = backfill. UI should send 100.
    #[serde(default)]
    pub priority: i32,
}

/// How long a `running` item may sit with no finish before restart recovery
/// treats it as abandoned (seconds). LLM translate of a long article can take
/// several minutes; 12m is a generous upper bound that still unblocks a stuck
/// gate within a refresh cycle users notice.
const STALE_RUNNING_SECS: u64 = 12 * 60;

/// Total tries (initial attempt + automatic retries) before a transient
/// task failure goes terminal `Failed`. Retry schedule: 1m, 2m after the
/// first two transient failures; the third is terminal.
const MAX_TASK_ATTEMPTS: u32 = 3;
/// Backoff base in seconds: `not_before = now + BASE * 2^attempts_so_far`.
const RETRY_BACKOFF_BASE_SECS: u64 = 60;

/// Process-local queue + wake for the background worker.
pub struct SourceWorkQueue {
    path: PathBuf,
    /// Vault root — for artifact-aware recovery.
    vault_root: PathBuf,
    state: Mutex<QueueFile>,
    /// mtime of the queue file last loaded/persisted by THIS process.
    disk_mtime: Mutex<Option<SystemTime>>,
    wake: Condvar,
}

impl SourceWorkQueue {
    pub fn open(vault_root: &Path) -> Self {
        let path = vault_root.join(QUEUE_REL);
        let mut file = load_file(&path).unwrap_or_default();
        // Restart recovery: anything left `running` was mid-flight when the
        // process died. Promote to Done when artifacts already exist (no need
        // to re-burn LLM); otherwise re-queue so `claim_next` is not blocked
        // forever (one-running-at-a-time gate).
        let recovered = recover_interrupted(vault_root, &mut file);
        if recovered > 0 {
            let _ = persist(&path, &file);
            eprintln!(
                "source-work-queue: recovered {recovered} interrupted item(s) after restart"
            );
        }
        let mtime = std::fs::metadata(&path).and_then(|m| m.modified()).ok();
        Self {
            path,
            vault_root: vault_root.to_path_buf(),
            state: Mutex::new(file),
            disk_mtime: Mutex::new(mtime),
            wake: Condvar::new(),
        }
    }

    /// Re-read the durable file when another process (or a prior crash recovery)
    /// wrote a newer version. Skipped while THIS process owns a `running` item
    /// so we don't clobber in-flight task state.
    fn maybe_reload_from_disk(&self, g: &mut QueueFile) {
        let disk_m = std::fs::metadata(&self.path).and_then(|m| m.modified()).ok();
        let last = self
            .disk_mtime
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone();
        let newer = match (disk_m, last) {
            (Some(d), Some(l)) => d > l,
            (Some(_), None) => true,
            _ => false,
        };
        if !newer {
            return;
        }
        if g.items.iter().any(|i| i.status == ItemStatus::Running) {
            // We own a run — do not replace in-memory state mid-flight.
            return;
        }
        if let Some(mut file) = load_file(&self.path) {
            let n = recover_interrupted(&self.vault_root, &mut file);
            if n > 0 {
                let _ = self.persist_tracked(&file);
            }
            *g = file;
            if let Some(m) = disk_m {
                *self
                    .disk_mtime
                    .lock()
                    .unwrap_or_else(|p| p.into_inner()) = Some(m);
            }
            if n > 0 {
                eprintln!(
                    "source-work-queue: reloaded disk + recovered {n} interrupted item(s)"
                );
            }
        }
    }

    /// Unstick items that have been `running` longer than [`STALE_RUNNING_SECS`]
    /// (abandoned after crash/kill while this process still thought they ran).
    fn recover_stale_running(&self, g: &mut QueueFile) -> usize {
        let now = now_secs();
        let mut stale_ids = Vec::new();
        for item in g.items.iter() {
            if item.status != ItemStatus::Running {
                continue;
            }
            let started = item.started_at.unwrap_or(item.created_at);
            if now.saturating_sub(started) >= STALE_RUNNING_SECS {
                stale_ids.push(item.id.clone());
            }
        }
        if stale_ids.is_empty() {
            return 0;
        }
        // Re-run full interrupted recovery on the whole file so artifacts can
        // promote to Done.
        let n = recover_interrupted(&self.vault_root, g);
        // Force any remaining long-running items (no artifacts) back to queued
        // even if started_at was just cleared. Preserve `attempts`/`not_before`
        // on the requeued tasks: resetting them here would hand every stuck
        // task a free retry budget (and pull its backoff earlier) every 12
        // minutes — an infinite stale-recovery retry loop.
        for item in g.items.iter_mut() {
            if stale_ids.contains(&item.id) && item.status == ItemStatus::Running {
                item.status = ItemStatus::Queued;
                item.started_at = None;
                if item.translate.status == TaskStatus::Running {
                    item.translate.status = TaskStatus::Queued;
                }
                if item.summarize.status == TaskStatus::Running {
                    item.summarize.status = TaskStatus::Queued;
                }
            }
        }
        if n > 0 || !stale_ids.is_empty() {
            let _ = self.persist_tracked(g);
            eprintln!(
                "source-work-queue: unstuck {} stale running item(s)",
                stale_ids.len()
            );
        }
        stale_ids.len()
    }

    /// After a worker finishes an item (or panics), force any still-Running
    /// tasks on `id` into Failed so the serial gate cannot stick.
    pub fn fail_still_running(&self, id: &str, reason: &str) {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let Some(item) = g.items.iter_mut().find(|i| i.id == id) else {
            return;
        };
        let mut touched = false;
        if item.translate.wanted && item.translate.status == TaskStatus::Running {
            item.translate.status = TaskStatus::Failed;
            item.translate.error = Some(reason.into());
            touched = true;
        }
        if item.summarize.wanted && item.summarize.status == TaskStatus::Running {
            item.summarize.status = TaskStatus::Failed;
            item.summarize.error = Some(reason.into());
            touched = true;
        }
        if item.status == ItemStatus::Running && item_tasks_terminal(item) {
            recompute_item_status(item);
            touched = true;
        } else if item.status == ItemStatus::Running && !item_tasks_terminal(item) {
            // Still somehow non-terminal — park as failed item.
            item.status = ItemStatus::Failed;
            item.finished_at = Some(now_secs());
            touched = true;
        }
        if touched {
            let _ = self.persist_tracked(&g);
        }
    }

    pub fn snapshot(&self) -> QueueFile {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.maybe_reload_from_disk(&mut g);
        self.recover_stale_running(&mut g);
        g.clone()
    }

    pub fn wake_worker(&self) {
        self.wake.notify_one();
    }

    /// Who currently holds [`WORKER_LOCK`] for this vault (if any).
    pub fn worker_owner_pid(&self) -> Option<u32> {
        read_lock_pid(&self.vault_root.join(".ovp").join(WORKER_LOCK))
    }

    /// True when `pid` is the live owner of the worker lock (or no lock and
    /// we are about to take it — caller decides).
    pub fn worker_owner_is_this_process(&self) -> bool {
        match self.worker_owner_pid() {
            Some(p) => p == std::process::id(),
            None => false,
        }
    }

    /// Brief exclusive lock around a disk-coordinated mutation. Retries a few
    /// times if another portal is mid-write.
    fn with_write_lock<R>(&self, f: impl FnOnce() -> Result<R, String>) -> Result<R, String> {
        const ATTEMPTS: usize = 40;
        for attempt in 0..ATTEMPTS {
            match ovp_intake::RunLock::acquire_named(&self.vault_root, QUEUE_WRITE_LOCK) {
                Ok(_lock) => return f(),
                Err(_) if attempt + 1 < ATTEMPTS => {
                    std::thread::sleep(std::time::Duration::from_millis(25));
                }
                Err(e) => {
                    return Err(format!(
                        "source-work queue busy (another portal is writing): {e}"
                    ));
                }
            }
        }
        Err("source-work queue write lock timed out".into())
    }

    /// Force-replace in-memory state from the durable file (call under write lock).
    ///
    /// Does **not** run [`recover_interrupted`]: that would re-queue a
    /// just-claimed `Running` item mid-flight (breaking the one-at-a-time
    /// gate). Restart recovery belongs in [`Self::open`] and
    /// [`Self::recover_stale_running`] only.
    fn reload_from_disk(&self, g: &mut QueueFile) {
        if let Some(file) = load_file(&self.path) {
            *g = file;
            if let Ok(m) = std::fs::metadata(&self.path).and_then(|m| m.modified()) {
                *self
                    .disk_mtime
                    .lock()
                    .unwrap_or_else(|p| p.into_inner()) = Some(m);
            }
        }
    }

    /// Block until something may need work, or timeout.
    pub fn wait_for_work(&self, timeout: std::time::Duration) {
        let guard = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let _ = self.wake.wait_timeout(guard, timeout);
    }

    pub fn enqueue(&self, req: EnqueueRequest) -> Result<QueueItem, String> {
        if !req.translate && !req.summarize {
            return Err("at least one of translate/summarize required".into());
        }
        let sha = req.sha256.trim().to_string();
        if sha.is_empty() || sha.len() > 128 {
            return Err("invalid sha256".into());
        }
        let req = req;
        self.with_write_lock(|| {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.reload_from_disk(&mut g);
        // Merge into existing *queued* item for same sha.
        if let Some(item) = g
            .items
            .iter_mut()
            .find(|i| i.sha256 == sha && i.status == ItemStatus::Queued)
        {
            if req.translate {
                item.translate.wanted = true;
                item.translate.force = item.translate.force || req.force;
                if matches!(
                    item.translate.status,
                    TaskStatus::Skipped | TaskStatus::Cancelled | TaskStatus::Failed
                ) {
                    // Re-arm also resets the fail-back lifecycle: a manual
                    // backfill re-run must not inherit attempts=3 (retry dead
                    // for the item) or a stale not_before blocking the claim.
                    item.translate.status = TaskStatus::Queued;
                    item.translate.error = None;
                    item.translate.attempts = 0;
                    item.translate.not_before = None;
                }
            }
            if req.summarize {
                item.summarize.wanted = true;
                item.summarize.force = item.summarize.force || req.force;
                if matches!(
                    item.summarize.status,
                    TaskStatus::Skipped | TaskStatus::Cancelled | TaskStatus::Failed
                ) {
                    item.summarize.status = TaskStatus::Queued;
                    item.summarize.error = None;
                    item.summarize.attempts = 0;
                    item.summarize.not_before = None;
                }
            }
            if let Some(t) = req.title.filter(|s| !s.trim().is_empty()) {
                item.title = Some(t);
            }
            item.notify = item.notify || req.notify;
            // Interactive bump wins over a prior backfill enqueue for same sha.
            item.priority = item.priority.max(req.priority);
            let out = item.clone();
            // Keep queued list ordered: higher priority first, then FIFO.
            resort_queued(&mut g.items);
            self.persist_tracked(&g)?;
            drop(g);
            self.wake.notify_one();
            return Ok(out);
        }

        let now = now_secs();
        let id = format!("swq-{now}-{}", &sha[..sha.len().min(8)]);
        let item = QueueItem {
            id,
            sha256: sha.to_string(),
            title: req.title.filter(|s| !s.trim().is_empty()),
            translate: TaskState::idle(req.translate, req.force),
            summarize: TaskState::idle(req.summarize, req.force),
            status: ItemStatus::Queued,
            created_at: now,
            started_at: None,
            finished_at: None,
            notify: req.notify,
            notify_sent: false,
            priority: req.priority,
        };
        g.items.push(item.clone());
        resort_queued(&mut g.items);
        // Cap history: keep last 40 terminal + all active.
        prune_history(&mut g.items, 40);
        self.persist_tracked(&g)?;
        drop(g);
        self.wake.notify_one();
        Ok(item)
        })
    }

    /// Reorder: `ids` is the desired order of *queued* items (running is fixed first).
    pub fn reorder(&self, ids: &[String]) -> Result<QueueFile, String> {
        let ids = ids.to_vec();
        self.with_write_lock(|| {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.reload_from_disk(&mut g);
        let mut running: Vec<QueueItem> = Vec::new();
        let mut queued: Vec<QueueItem> = Vec::new();
        let mut rest: Vec<QueueItem> = Vec::new();
        for it in g.items.drain(..) {
            match it.status {
                ItemStatus::Running => running.push(it),
                ItemStatus::Queued => queued.push(it),
                _ => rest.push(it),
            }
        }
        let mut by_id: std::collections::HashMap<String, QueueItem> =
            queued.into_iter().map(|i| (i.id.clone(), i)).collect();
        let mut new_queued = Vec::new();
        for id in &ids {
            if let Some(it) = by_id.remove(id) {
                new_queued.push(it);
            }
        }
        // Append any queued not mentioned (keep relative tail order).
        let leftovers: Vec<QueueItem> = by_id.into_values().collect();
        // stable: items not in ids keep previous relative order via created_at
        let mut leftovers = leftovers;
        leftovers.sort_by_key(|i| i.created_at);
        new_queued.extend(leftovers);
        g.items = running;
        g.items.extend(new_queued);
        g.items.extend(rest);
        self.persist_tracked(&g)?;
        Ok(g.clone())
        })
    }

    pub fn cancel(&self, id: &str) -> Result<QueueItem, String> {
        let id = id.to_string();
        self.with_write_lock(|| {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.reload_from_disk(&mut g);
        let item = g
            .items
            .iter_mut()
            .find(|i| i.id == id)
            .ok_or_else(|| format!("queue item not found: {id}"))?;
        match item.status {
            ItemStatus::Queued => {
                item.status = ItemStatus::Cancelled;
                item.finished_at = Some(now_secs());
                if item.translate.wanted && item.translate.status == TaskStatus::Queued {
                    item.translate.status = TaskStatus::Cancelled;
                }
                if item.summarize.wanted && item.summarize.status == TaskStatus::Queued {
                    item.summarize.status = TaskStatus::Cancelled;
                }
            }
            ItemStatus::Running => {
                // Soft-cancel: mark for skip after current article finishes
                // tasks that haven't started — worker checks cancelled flag.
                item.status = ItemStatus::Cancelled;
                item.finished_at = Some(now_secs());
            }
            _ => return Err("item already terminal".into()),
        }
        let out = item.clone();
        self.persist_tracked(&g)?;
        Ok(out)
        })
    }

    pub fn remove(&self, id: &str) -> Result<(), String> {
        let id = id.to_string();
        self.with_write_lock(|| {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.reload_from_disk(&mut g);
        let before = g.items.len();
        g.items.retain(|i| {
            // Never drop a running item mid-flight from the list; cancel first.
            !(i.id == id && i.status != ItemStatus::Running)
        });
        if g.items.len() == before {
            // still there = was running or missing
            if g.items.iter().any(|i| i.id == id && i.status == ItemStatus::Running) {
                return Err("cannot remove a running item — cancel first".into());
            }
            return Err(format!("queue item not found: {id}"));
        }
        self.persist_tracked(&g)?;
        Ok(())
        })
    }

    /// Claim the next queued article for the worker (marks Running).
    ///
    /// Selection: highest [`QueueItem::priority`] first; within the same
    /// priority, older `created_at` (FIFO). UI interactive jobs (priority 100)
    /// therefore jump ahead of bulk backfill (priority 0) without reordering
    /// the whole list by hand.
    pub fn claim_next(&self) -> Option<QueueItem> {
        self.with_write_lock(|| {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.reload_from_disk(&mut g);
        self.recover_stale_running(&mut g);
        if g.items.iter().any(|i| i.status == ItemStatus::Running) {
            return Ok(None); // one article at a time
        }
        let Some(idx) = pick_next_queued_index(&g.items, now_secs()) else {
            return Ok(None);
        };
        let item = &mut g.items[idx];
        item.status = ItemStatus::Running;
        item.started_at = Some(now_secs());
        if item.translate.wanted && item.translate.status == TaskStatus::Queued {
            item.translate.status = TaskStatus::Running;
        }
        if item.summarize.wanted && item.summarize.status == TaskStatus::Queued {
            item.summarize.status = TaskStatus::Running;
        }
        let out = item.clone();
        self.persist_tracked(&g)?;
        Ok(Some(out))
        })
        .ok()
        .flatten()
    }

    pub fn finish_task(
        &self,
        id: &str,
        kind: TaskKind,
        result: Result<(), String>,
    ) -> Result<QueueItem, String> {
        let id = id.to_string();
        self.with_write_lock(|| {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        self.reload_from_disk(&mut g);
        let item = g
            .items
            .iter_mut()
            .find(|i| i.id == id)
            .ok_or_else(|| format!("queue item not found: {id}"))?;
        let cancelled = item.status == ItemStatus::Cancelled;
        let task = match kind {
            TaskKind::Translate => &mut item.translate,
            TaskKind::Summarize => &mut item.summarize,
        };
        let mut retried = false;
        match &result {
            Ok(()) => {
                task.status = TaskStatus::Done;
                task.error = None;
            }
            Err(e) if cancelled => {
                // A cancelled item takes TERMINAL task results only:
                // re-queueing a task under a Cancelled item strands it
                // forever (claim_next only selects Queued items) and blocks
                // the cancellation notification (CodeRabbit on PR #411).
                task.status = TaskStatus::Failed;
                task.error = Some(e.clone());
            }
            Err(e) => match classify_task_error(e) {
                FailureClass::BudgetDefer => {
                    // Reserved mechanism (nothing produces this class today):
                    // defer to next local midnight; does not burn retry attempts.
                    task.status = TaskStatus::Queued;
                    task.error = Some(e.clone());
                    task.not_before = Some(next_local_midnight(now_secs()));
                    retried = true;
                }
                FailureClass::Transient
                    if task.attempts.saturating_add(1) < MAX_TASK_ATTEMPTS =>
                {
                    // Backoff 1m/2m; the (MAX)th transient failure is terminal.
                    task.not_before =
                        Some(now_secs() + RETRY_BACKOFF_BASE_SECS * (1u64 << task.attempts));
                    task.attempts = task.attempts.saturating_add(1);
                    task.status = TaskStatus::Queued;
                    // Keep the error text for visibility while backing off.
                    task.error = Some(e.clone());
                    retried = true;
                }
                _ => {
                    task.status = TaskStatus::Failed;
                    task.error = Some(e.clone());
                }
            },
        }
        // If cancelled mid-run, keep cancelled item status but record task results.
        if retried {
            // NEVER route the retry through `recompute_item_status`: with the
            // sibling task still Running it would leave the item `Running`,
            // and claim_next's one-item-running gate would jam the WHOLE
            // queue behind this item's backoff until `recover_stale_running`
            // (12 min). Re-queue the item explicitly instead.
            if item.status != ItemStatus::Cancelled {
                item.status = ItemStatus::Queued;
                item.started_at = None;
            }
        } else if item.status != ItemStatus::Cancelled {
            if item_tasks_terminal(item) {
                recompute_item_status(item);
            }
            // Non-terminal: leave the item status alone. It is Running in the
            // normal mid-flight case; but it may already be Queued because a
            // sibling task failed back for retry while this one was still
            // running — flipping it back to Running here would jam
            // claim_next's one-item-running gate behind the sibling's
            // backoff window (the same stall the retry branch avoids).
        } else if item_tasks_terminal(item) {
            item.finished_at = item.finished_at.or_else(|| Some(now_secs()));
        }
        let out = item.clone();
        self.persist_tracked(&g)?;
        Ok(out)
        })
    }

    /// Items that need a client notification (terminal + notify + !notify_sent).
    pub fn take_notify_batch(&self) -> Vec<QueueItem> {
        self.with_write_lock(|| {
            let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
            self.reload_from_disk(&mut g);
            let mut out = Vec::new();
            for item in g.items.iter_mut() {
                if item.notify
                    && !item.notify_sent
                    && matches!(
                        item.status,
                        ItemStatus::Done | ItemStatus::Failed | ItemStatus::Cancelled
                    )
                    && item_tasks_terminal(item)
                {
                    item.notify_sent = true;
                    out.push(item.clone());
                }
            }
            if !out.is_empty() {
                self.persist_tracked(&g)?;
            }
            Ok(out)
        })
        .unwrap_or_default()
    }

    pub fn mark_task_skipped_if_not_wanted(&self, id: &str) {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(item) = g.items.iter_mut().find(|i| i.id == id) {
            if !item.translate.wanted {
                item.translate.status = TaskStatus::Skipped;
            }
            if !item.summarize.wanted {
                item.summarize.status = TaskStatus::Skipped;
            }
            // A re-queued (retry-backoff) item must not be flipped back to
            // Running here — that would jam claim_next's one-item-running
            // gate behind the backoff window. Every other state keeps
            // today's recompute semantics.
            if item.status == ItemStatus::Queued && !item_tasks_terminal(item) {
                // leave as Queued
            } else {
                recompute_item_status(item);
            }
            let _ = self.persist_tracked(&g);
        }
    }
}

fn item_tasks_terminal(item: &QueueItem) -> bool {
    let t_ok = !item.translate.wanted
        || matches!(
            item.translate.status,
            TaskStatus::Done
                | TaskStatus::Failed
                | TaskStatus::Skipped
                | TaskStatus::Cancelled
        );
    let s_ok = !item.summarize.wanted
        || matches!(
            item.summarize.status,
            TaskStatus::Done
                | TaskStatus::Failed
                | TaskStatus::Skipped
                | TaskStatus::Cancelled
        );
    t_ok && s_ok
}

fn recompute_item_status(item: &mut QueueItem) {
    if !item_tasks_terminal(item) {
        item.status = ItemStatus::Running;
        return;
    }
    let failed = (item.translate.wanted && item.translate.status == TaskStatus::Failed)
        || (item.summarize.wanted && item.summarize.status == TaskStatus::Failed);
    item.status = if failed {
        ItemStatus::Failed
    } else {
        ItemStatus::Done
    };
    item.finished_at = Some(now_secs());
}

/// Failure classification for the fail-back lifecycle.
///
/// Mirrors the rules of `ovp_llm::client::is_transient`, but on the
/// *stringified* error: by the time the source-work worker calls
/// `finish_task`, the typed `CallError` has been flattened through its
/// `Display` impl (`transport: …`, `provider error <code>: …`, `decode: …`,
/// `budget exhausted: …`, …) inside `source_work`'s `llm_text`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FailureClass {
    /// Transport / 429 / 5xx / rate_limit / overloaded / unavailable:
    /// worth an automatic retry with backoff.
    Transient,
    /// Daily token budget exhausted: defer until next local midnight.
    /// RESERVED — nothing produces this class today (meter-don't-enforce).
    /// Deliberately distinct from `CallError::BudgetExhausted`
    /// ("budget exhausted: …", a reasoning-token overflow), which stays
    /// `Permanent`.
    BudgetDefer,
    /// 4xx, decode, protocol, cache miss, local/worker errors: terminal
    /// `Failed`, exactly as today.
    Permanent,
}

/// Prefix a future daily-budget producer will use; nothing emits it today.
const BUDGET_DEFER_PREFIX: &str = "daily budget exhausted:";

fn classify_task_error(msg: &str) -> FailureClass {
    if msg.starts_with(BUDGET_DEFER_PREFIX) {
        return FailureClass::BudgetDefer;
    }
    if msg.starts_with("transport:") {
        return FailureClass::Transient;
    }
    if let Some(rest) = msg.strip_prefix("provider error ") {
        let code = rest.split(':').next().unwrap_or("").trim();
        if let Ok(n) = code.parse::<u16>() {
            if n == 429 || (500..=599).contains(&n) {
                return FailureClass::Transient;
            }
        } else {
            let c = code.to_ascii_lowercase();
            if c.contains("rate_limit") || c.contains("overloaded") || c.contains("unavailable") {
                return FailureClass::Transient;
            }
        }
    }
    FailureClass::Permanent
}

/// Next local midnight as epoch seconds (budget-defer target). Falls back to
/// `now + 24h` when the local timeline has no representable midnight.
fn next_local_midnight(now: u64) -> u64 {
    use chrono::{Local, TimeZone};
    let dt = Local
        .timestamp_opt(now.min(i64::MAX as u64) as i64, 0)
        .latest()
        .unwrap_or_else(Local::now);
    let tomorrow = dt.date_naive() + chrono::Duration::days(1);
    let Some(naive) = tomorrow.and_hms_opt(0, 0, 0) else {
        return now + 24 * 3600;
    };
    let ts = match Local.from_local_datetime(&naive) {
        chrono::LocalResult::Single(t) => t.timestamp(),
        chrono::LocalResult::Ambiguous(a, _) => a.timestamp(),
        chrono::LocalResult::None => return now + 24 * 3600,
    };
    ts.max(0) as u64
}

/// Index of the next queued item: highest priority, then oldest created_at.
///
/// Item-level claim gate: an item is claimable only when EVERY wanted+Queued
/// task is past its `not_before` — `claim_next` flips both tasks to Running
/// at once, so one backing-off task holds its own item back (but never
/// other items).
fn pick_next_queued_index(items: &[QueueItem], now: u64) -> Option<usize> {
    items
        .iter()
        .enumerate()
        .filter(|(_, i)| i.status == ItemStatus::Queued && item_claimable(i, now))
        .min_by(|(_, a), (_, b)| {
            // Prefer higher priority (so reverse cmp), then older created_at.
            b.priority
                .cmp(&a.priority)
                .then_with(|| a.created_at.cmp(&b.created_at))
        })
        .map(|(idx, _)| idx)
}

fn item_claimable(item: &QueueItem, now: u64) -> bool {
    fn task_ready(t: &TaskState, now: u64) -> bool {
        // A wanted task still RUNNING gates the claim: finish_task can
        // re-queue the item for a sibling's retry while this task's thread
        // is still in flight, and claim_next's one-item gate keys on ITEM
        // status (Queued in that case), not task status — without this a
        // second thread would start the same task and the late finisher
        // would overwrite the newer state (CodeRabbit on PR #411).
        if t.wanted && t.status == TaskStatus::Running {
            return false;
        }
        // Only wanted+Queued tasks gate; not_before in the past is ready.
        !(t.wanted && t.status == TaskStatus::Queued) || t.not_before.is_none_or(|nb| nb <= now)
    }
    task_ready(&item.translate, now) && task_ready(&item.summarize, now)
}

/// Stable visual order for the portal: running first, then queued by
/// priority desc / created_at asc, then terminal history as-is.
fn resort_queued(items: &mut Vec<QueueItem>) {
    let mut running = Vec::new();
    let mut queued = Vec::new();
    let mut rest = Vec::new();
    for it in items.drain(..) {
        match it.status {
            ItemStatus::Running => running.push(it),
            ItemStatus::Queued => queued.push(it),
            _ => rest.push(it),
        }
    }
    queued.sort_by(|a, b| {
        b.priority
            .cmp(&a.priority)
            .then_with(|| a.created_at.cmp(&b.created_at))
    });
    items.extend(running);
    items.extend(queued);
    items.extend(rest);
}

fn prune_history(items: &mut Vec<QueueItem>, keep_terminal: usize) {
    let terminal: Vec<usize> = items
        .iter()
        .enumerate()
        .filter(|(_, i)| {
            matches!(
                i.status,
                ItemStatus::Done | ItemStatus::Failed | ItemStatus::Cancelled
            )
        })
        .map(|(i, _)| i)
        .collect();
    if terminal.len() <= keep_terminal {
        return;
    }
    let drop_n = terminal.len() - keep_terminal;
    let drop_ids: std::collections::HashSet<String> = terminal
        .into_iter()
        .take(drop_n)
        .filter_map(|i| items.get(i).map(|it| it.id.clone()))
        .collect();
    items.retain(|i| !drop_ids.contains(&i.id));
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn read_lock_pid(path: &Path) -> Option<u32> {
    let raw = std::fs::read_to_string(path).ok()?;
    let pid = raw.trim().parse::<u32>().ok().filter(|p| *p > 0)?;
    // Only report if the process is still alive (unix kill -0).
    #[cfg(unix)]
    {
        let alive = std::process::Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !alive {
            return None;
        }
    }
    Some(pid)
}

/// Recover items left mid-flight across process death. Returns how many
/// queue items were touched.
fn recover_interrupted(vault_root: &Path, file: &mut QueueFile) -> usize {
    let mut n = 0usize;
    for item in file.items.iter_mut() {
        let mut touched = false;

        // Soft-cancelled mid-run: tasks may still be Running — park them.
        if item.status == ItemStatus::Cancelled {
            if item.translate.status == TaskStatus::Running {
                item.translate.status = TaskStatus::Cancelled;
                touched = true;
            }
            if item.summarize.status == TaskStatus::Running {
                item.summarize.status = TaskStatus::Cancelled;
                touched = true;
            }
            if touched {
                n += 1;
            }
            continue;
        }

        let interrupted = item.status == ItemStatus::Running
            || (item.translate.wanted && item.translate.status == TaskStatus::Running)
            || (item.summarize.wanted && item.summarize.status == TaskStatus::Running);
        if !interrupted {
            continue;
        }

        // Prefer Done when artifacts already on disk (idempotent skip).
        let work_rel =
            crate::source_work::work_rel_for(&item.sha256, item.title.as_deref());
        let dir = crate::source_work::work_abs(vault_root, &work_rel);
        if item.translate.wanted && item.translate.status == TaskStatus::Running {
            if dir.join("zh.md").is_file() {
                item.translate.status = TaskStatus::Done;
                item.translate.error = None;
            } else {
                item.translate.status = TaskStatus::Queued;
                item.translate.error = None;
            }
            touched = true;
        }
        if item.summarize.wanted && item.summarize.status == TaskStatus::Running {
            if dir.join("summary.md").is_file() {
                item.summarize.status = TaskStatus::Done;
                item.summarize.error = None;
            } else {
                item.summarize.status = TaskStatus::Queued;
                item.summarize.error = None;
            }
            touched = true;
        }
        if item.status == ItemStatus::Running {
            if item_tasks_terminal(item) {
                recompute_item_status(item);
            } else {
                item.status = ItemStatus::Queued;
                item.started_at = None;
            }
            touched = true;
        }
        if touched {
            n += 1;
        }
    }
    n
}

fn load_file(path: &Path) -> Option<QueueFile> {
    let raw = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

fn persist(path: &Path, file: &QueueFile) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir queue dir: {e}"))?;
    }
    let raw = serde_json::to_string_pretty(file).map_err(|e| e.to_string())?;
    // Unique temp name — concurrent test processes must not race on `.json.tmp`.
    let tmp = path.with_extension(format!(
        "json.tmp.{}",
        std::process::id()
    ));
    std::fs::write(&tmp, &raw).map_err(|e| format!("write queue tmp: {e}"))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("rename queue: {e}"))?;
    Ok(())
}

/// Like [`persist`], and refresh the caller's mtime tracker when available.
impl SourceWorkQueue {
    fn persist_tracked(&self, file: &QueueFile) -> Result<(), String> {
        persist(&self.path, file)?;
        if let Ok(m) = std::fs::metadata(&self.path).and_then(|m| m.modified()) {
            *self
                .disk_mtime
                .lock()
                .unwrap_or_else(|p| p.into_inner()) = Some(m);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp() -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("ovp-swq-{n}"));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    #[test]
    fn enqueue_merge_same_sha_queued() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q
            .enqueue(EnqueueRequest {
                sha256: "abc12345deadbeef".into(),
                title: Some("T".into()),
                translate: true,
                summarize: false,
                force: false,
                notify: true,
                priority: PRIORITY_INTERACTIVE,
            })
            .unwrap();
        let b = q
            .enqueue(EnqueueRequest {
                sha256: "abc12345deadbeef".into(),
                title: None,
                translate: false,
                summarize: true,
                force: false,
                notify: true,
                priority: PRIORITY_INTERACTIVE,
            })
            .unwrap();
        assert_eq!(a.id, b.id);
        assert!(b.translate.wanted && b.summarize.wanted);
        assert_eq!(q.snapshot().items.len(), 1);
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn claim_one_at_a_time() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        q.enqueue(EnqueueRequest {
            sha256: "sha-a-00000000".into(),
            title: None,
            translate: true,
            summarize: false,
            force: false,
            notify: true,
            priority: PRIORITY_NORMAL,
        })
        .unwrap();
        q.enqueue(EnqueueRequest {
            sha256: "sha-b-00000000".into(),
            title: None,
            translate: true,
            summarize: false,
            force: false,
            notify: true,
            priority: PRIORITY_NORMAL,
        })
        .unwrap();
        let first = q.claim_next().unwrap();
        assert!(q.claim_next().is_none());
        q.finish_task(&first.id, TaskKind::Translate, Ok(())).unwrap();
        let second = q.claim_next().unwrap();
        assert_ne!(first.sha256, second.sha256);
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn interactive_priority_jumps_ahead_of_backfill() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        q.enqueue(EnqueueRequest {
            sha256: "backfill1".into(),
            title: Some("BF".into()),
            translate: true,
            summarize: false,
            force: false,
            notify: false,
            priority: PRIORITY_BACKFILL,
        })
        .unwrap();
        // Later interactive job must still claim first.
        std::thread::sleep(std::time::Duration::from_millis(5));
        q.enqueue(EnqueueRequest {
            sha256: "ui-click".into(),
            title: Some("UI".into()),
            translate: true,
            summarize: false,
            force: false,
            notify: true,
            priority: PRIORITY_INTERACTIVE,
        })
        .unwrap();
        let first = q.claim_next().unwrap();
        assert_eq!(first.sha256, "ui-click");
        assert_eq!(first.priority, PRIORITY_INTERACTIVE);
        q.finish_task(&first.id, TaskKind::Translate, Ok(())).unwrap();
        let second = q.claim_next().unwrap();
        assert_eq!(second.sha256, "backfill1");
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn merge_bumps_priority_to_interactive() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        q.enqueue(EnqueueRequest {
            sha256: "same".into(),
            title: Some("BF".into()),
            translate: true,
            summarize: false,
            force: false,
            notify: false,
            priority: PRIORITY_BACKFILL,
        })
        .unwrap();
        let bumped = q
            .enqueue(EnqueueRequest {
                sha256: "same".into(),
                title: None,
                translate: false,
                summarize: true,
                force: false,
                notify: true,
                priority: PRIORITY_INTERACTIVE,
            })
            .unwrap();
        assert_eq!(bumped.priority, PRIORITY_INTERACTIVE);
        assert!(bumped.translate.wanted && bumped.summarize.wanted);
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn open_requeues_interrupted_running_items() {
        let vault = tmp();
        let path = vault.join(QUEUE_REL);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let file = QueueFile {
            schema: QUEUE_SCHEMA.into(),
            items: vec![QueueItem {
                id: "swq-stuck".into(),
                sha256: "deadbeef".into(),
                title: Some("stuck".into()),
                translate: TaskState {
                    wanted: true,
                    force: false,
                    status: TaskStatus::Running,
                    error: None,
                    attempts: 0,
                    not_before: None,
                },
                summarize: TaskState {
                    wanted: true,
                    force: false,
                    status: TaskStatus::Running,
                    error: None,
                    attempts: 0,
                    not_before: None,
                },
                status: ItemStatus::Running,
                created_at: 1,
                started_at: Some(2),
                finished_at: None,
                notify: true,
                notify_sent: false,
                priority: 0,
            }],
        };
        persist(&path, &file).unwrap();
        let q = SourceWorkQueue::open(&vault);
        let snap = q.snapshot();
        assert_eq!(snap.items.len(), 1);
        // No artifacts on disk → re-queued for retry.
        assert_eq!(snap.items[0].status, ItemStatus::Queued);
        assert_eq!(snap.items[0].translate.status, TaskStatus::Queued);
        assert_eq!(snap.items[0].summarize.status, TaskStatus::Queued);
        // Worker can claim again after recovery.
        let claimed = q.claim_next().unwrap();
        assert_eq!(claimed.id, "swq-stuck");
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn open_promotes_interrupted_when_artifacts_exist() {
        let vault = tmp();
        let path = vault.join(QUEUE_REL);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let title = "Has Artifacts";
        let sha = "cafebabe01234567";
        let work_rel = crate::source_work::work_rel_for(sha, Some(title));
        let dir = vault.join(&work_rel);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("zh.md"), "译").unwrap();
        std::fs::write(dir.join("summary.md"), "概").unwrap();
        let file = QueueFile {
            schema: QUEUE_SCHEMA.into(),
            items: vec![QueueItem {
                id: "swq-doneish".into(),
                sha256: sha.into(),
                title: Some(title.into()),
                translate: TaskState {
                    wanted: true,
                    force: false,
                    status: TaskStatus::Running,
                    error: None,
                    attempts: 0,
                    not_before: None,
                },
                summarize: TaskState {
                    wanted: true,
                    force: false,
                    status: TaskStatus::Running,
                    error: None,
                    attempts: 0,
                    not_before: None,
                },
                status: ItemStatus::Running,
                created_at: 1,
                started_at: Some(2),
                finished_at: None,
                notify: true,
                notify_sent: false,
                priority: 0,
            }],
        };
        persist(&path, &file).unwrap();
        let q = SourceWorkQueue::open(&vault);
        let snap = q.snapshot();
        assert_eq!(snap.items[0].status, ItemStatus::Done);
        assert_eq!(snap.items[0].translate.status, TaskStatus::Done);
        assert_eq!(snap.items[0].summarize.status, TaskStatus::Done);
        // Gate free — next claim can proceed.
        assert!(q.claim_next().is_none());
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn reorder_queued_only() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q
            .enqueue(EnqueueRequest {
                sha256: "aaaaaaaa".into(),
                title: Some("A".into()),
                translate: true,
                summarize: false,
                force: false,
                notify: true,
                priority: PRIORITY_NORMAL,
            })
            .unwrap();
        let b = q
            .enqueue(EnqueueRequest {
                sha256: "bbbbbbbb".into(),
                title: Some("B".into()),
                translate: true,
                summarize: false,
                force: false,
                notify: true,
                priority: PRIORITY_NORMAL,
            })
            .unwrap();
        q.reorder(&[b.id.clone(), a.id.clone()]).unwrap();
        let snap = q.snapshot();
        assert_eq!(snap.items[0].sha256, "bbbbbbbb");
        assert_eq!(snap.items[1].sha256, "aaaaaaaa");
        let _ = std::fs::remove_dir_all(&vault);
    }

    // ---- fail-back lifecycle (queue_failback-v1) ----

    fn enq(sha: &str, summarize: bool) -> EnqueueRequest {
        EnqueueRequest {
            sha256: sha.into(),
            title: None,
            translate: true,
            summarize,
            force: false,
            notify: false,
            priority: PRIORITY_NORMAL,
        }
    }

    /// Force a task's backoff window into the past, as if time had passed.
    fn expire_backoff(q: &SourceWorkQueue, id: &str, kind: TaskKind) {
        let mut g = q.state.lock().unwrap_or_else(|p| p.into_inner());
        let it = g.items.iter_mut().find(|i| i.id == id).unwrap();
        let task = match kind {
            TaskKind::Translate => &mut it.translate,
            TaskKind::Summarize => &mut it.summarize,
        };
        task.not_before = Some(now_secs().saturating_sub(1));
        q.persist_tracked(&g).unwrap();
    }

    #[test]
    fn classify_task_error_rules() {
        // Transient: transport, 429, 5xx, rate_limit/overloaded/unavailable —
        // same rules as ovp_llm::client::is_transient, on stringified errors.
        assert_eq!(
            classify_task_error("transport: connection reset"),
            FailureClass::Transient
        );
        assert_eq!(
            classify_task_error("provider error 429: slow down"),
            FailureClass::Transient
        );
        assert_eq!(
            classify_task_error("provider error 503: service unavailable"),
            FailureClass::Transient
        );
        assert_eq!(
            classify_task_error("provider error rate_limit_error: x"),
            FailureClass::Transient
        );
        assert_eq!(
            classify_task_error("provider error overloaded_error: x"),
            FailureClass::Transient
        );
        // Permanent: 4xx, decode, protocol, reasoning-budget, cache miss,
        // local/worker errors.
        assert_eq!(
            classify_task_error("provider error 400: invalid request"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("provider error 401: auth"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("decode: no text block"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("tool protocol violation: adjacency"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("budget exhausted: thinking used all tokens"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("cache miss for key abcd"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("llm not configured"),
            FailureClass::Permanent
        );
        assert_eq!(
            classify_task_error("translate task panicked"),
            FailureClass::Permanent
        );
        // Reserved budget-defer class (no producer today).
        assert_eq!(
            classify_task_error("daily budget exhausted: 1000000 tokens"),
            FailureClass::BudgetDefer
        );
    }

    /// CORE REGRESSION: an item backing off must never stall the queue.
    #[test]
    fn transient_retry_does_not_stall_other_items() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-a-transient", true)).unwrap();
        let b = q.enqueue(enq("sha-b-waits", true)).unwrap();
        let claimed = q.claim_next().unwrap();
        assert_eq!(claimed.id, a.id);
        // Both of A's tasks transient-fail → A re-queued with backoff.
        q.finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        q.finish_task(
            &a.id,
            TaskKind::Summarize,
            Err("provider error 429: slow down".into()),
        )
        .unwrap();
        let snap = q.snapshot();
        let a_now = snap.items.iter().find(|i| i.id == a.id).unwrap();
        assert_eq!(a_now.status, ItemStatus::Queued);
        assert_eq!(a_now.started_at, None);
        assert!(a_now.translate.not_before.is_some());
        // B is IMMEDIATELY claimable — the one-item-running gate is free.
        let next = q.claim_next().unwrap();
        assert_eq!(next.id, b.id);
        q.finish_task(&b.id, TaskKind::Translate, Ok(())).unwrap();
        q.finish_task(&b.id, TaskKind::Summarize, Ok(())).unwrap();
        // A still inside its backoff window: nothing to claim.
        assert!(q.claim_next().is_none());
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn transient_retry_backoff_then_terminal() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-retry", false)).unwrap();

        // 1st transient failure: re-queued, attempts=1, not_before≈now+60s.
        q.claim_next().unwrap();
        let before = now_secs();
        let item = q
            .finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        assert_eq!(item.translate.status, TaskStatus::Queued);
        assert_eq!(item.translate.attempts, 1);
        assert_eq!(item.translate.error.as_deref(), Some("transport: reset"));
        let nb = item.translate.not_before.unwrap();
        assert!(
            (before + RETRY_BACKOFF_BASE_SECS..=before + RETRY_BACKOFF_BASE_SECS + 30)
                .contains(&nb),
            "not_before {nb} not ≈ +60s from {before}"
        );
        // Item explicitly re-queued (never left Running), so the gate is free.
        assert_eq!(item.status, ItemStatus::Queued);
        assert_eq!(item.started_at, None);
        // Not claimable before the window.
        assert!(q.claim_next().is_none());

        // After the window: claimable again.
        expire_backoff(&q, &a.id, TaskKind::Translate);
        let c = q.claim_next().unwrap();
        assert_eq!(c.id, a.id);

        // 2nd transient failure: attempts=2, not_before≈now+120s.
        let before = now_secs();
        let item = q
            .finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        assert_eq!(item.translate.attempts, 2);
        let nb = item.translate.not_before.unwrap();
        assert!(
            (before + 2 * RETRY_BACKOFF_BASE_SECS..=before + 2 * RETRY_BACKOFF_BASE_SECS + 30)
                .contains(&nb),
            "not_before {nb} not ≈ +120s from {before}"
        );
        expire_backoff(&q, &a.id, TaskKind::Translate);
        q.claim_next().unwrap();

        // 3rd transient failure: terminal Failed, retry budget spent.
        let item = q
            .finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        assert_eq!(item.translate.status, TaskStatus::Failed);
        assert_eq!(item.translate.attempts, 2);
        assert_eq!(item.status, ItemStatus::Failed);
        assert!(item.finished_at.is_some());
        let _ = std::fs::remove_dir_all(&vault);
    }

    /// Item-level gate: translate backing off + summarize ready ⇒ NOT claimable
    /// (claim_next flips both tasks to Running at once).
    #[test]
    fn item_not_claimable_while_any_task_backs_off() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-gated", true)).unwrap();
        q.claim_next().unwrap();
        // translate transient-fails into backoff; summarize succeeds.
        q.finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        q.finish_task(&a.id, TaskKind::Summarize, Ok(())).unwrap();
        let snap = q.snapshot();
        let it = &snap.items[0];
        assert_eq!(it.translate.status, TaskStatus::Queued);
        assert_eq!(it.summarize.status, TaskStatus::Done);
        assert_eq!(it.status, ItemStatus::Queued);
        // One wanted+Queued task still in backoff ⇒ item not claimable.
        assert!(q.claim_next().is_none());
        // Window over ⇒ claimable; the Done sibling is left alone.
        expire_backoff(&q, &a.id, TaskKind::Translate);
        let c = q.claim_next().unwrap();
        assert_eq!(c.translate.status, TaskStatus::Running);
        assert_eq!(c.summarize.status, TaskStatus::Done);
        let _ = std::fs::remove_dir_all(&vault);
    }

    /// A sibling task still in flight gates the claim: translate backed off
    /// and its window expired, but summarize is still Running — claiming now
    /// would start a second summarize thread whose late finish would
    /// overwrite the newer state (CodeRabbit on PR #411).
    #[test]
    fn running_sibling_gates_claim_after_backoff() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-inflight", true)).unwrap();
        q.claim_next().unwrap();
        // translate transient-fails into backoff; summarize is STILL Running.
        q.finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        let snap = q.snapshot();
        let it = &snap.items[0];
        assert_eq!(it.status, ItemStatus::Queued);
        assert_eq!(it.summarize.status, TaskStatus::Running);
        // Even with translate's backoff expired, the in-flight sibling gates.
        expire_backoff(&q, &a.id, TaskKind::Translate);
        assert!(q.claim_next().is_none());
        // Once summarize finishes, the item is claimable again.
        q.finish_task(&a.id, TaskKind::Summarize, Ok(())).unwrap();
        let c = q.claim_next().unwrap();
        assert_eq!(c.translate.status, TaskStatus::Running);
        let _ = std::fs::remove_dir_all(&vault);
    }

    /// A task that fails after the item was CANCELLED takes a terminal
    /// result — re-queueing it under a Cancelled item would strand it
    /// forever and block the cancellation notification (CodeRabbit on #411).
    #[test]
    fn cancelled_item_takes_terminal_task_results() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-cancelled", false)).unwrap();
        q.claim_next().unwrap();
        q.cancel(&a.id).unwrap();
        let item = q
            .finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        assert_eq!(item.status, ItemStatus::Cancelled);
        assert_eq!(item.translate.status, TaskStatus::Failed);
        assert_eq!(item.translate.attempts, 0);
        assert_eq!(item.translate.not_before, None);
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn stale_recovery_preserves_attempts_and_not_before() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-stale", false)).unwrap();
        q.claim_next().unwrap();
        // Simulate: retried twice already (attempts=2 + future backoff), then
        // the process lost track — item went stale while Running.
        let future = now_secs() + 3600;
        {
            let mut g = q.state.lock().unwrap_or_else(|p| p.into_inner());
            let it = g.items.iter_mut().find(|i| i.id == a.id).unwrap();
            it.started_at = Some(now_secs() - STALE_RUNNING_SECS - 60);
            it.translate.attempts = 2;
            it.translate.not_before = Some(future);
            q.persist_tracked(&g).unwrap();
        }
        let snap = q.snapshot(); // triggers recover_stale_running
        let it = &snap.items[0];
        assert_eq!(it.status, ItemStatus::Queued);
        assert_eq!(it.translate.status, TaskStatus::Queued);
        // Retry budget and backoff survive stale recovery — no infinite
        // 12-minute retry loop, no pulled-forward not_before.
        assert_eq!(it.translate.attempts, 2);
        assert_eq!(it.translate.not_before, Some(future));
        assert!(q.claim_next().is_none());
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn permanent_error_fails_terminally_on_first_failure() {
        for err in [
            "provider error 400: invalid request",
            "decode: no text block",
            "budget exhausted: thinking used all tokens",
            "llm not configured",
        ] {
            let vault = tmp();
            let q = SourceWorkQueue::open(&vault);
            let a = q.enqueue(enq("sha-perm", false)).unwrap();
            q.claim_next().unwrap();
            let item = q
                .finish_task(&a.id, TaskKind::Translate, Err(err.into()))
                .unwrap();
            assert_eq!(item.translate.status, TaskStatus::Failed, "{err}");
            assert_eq!(item.translate.attempts, 0, "{err}");
            assert_eq!(item.translate.not_before, None, "{err}");
            assert_eq!(item.translate.error.as_deref(), Some(err));
            assert_eq!(item.status, ItemStatus::Failed, "{err}");
            let _ = std::fs::remove_dir_all(&vault);
        }
    }

    /// Manual backfill re-run (today's only recovery tool) must re-arm with a
    /// fresh retry budget — never inherit attempts=3 / a stale not_before.
    #[test]
    fn enqueue_rearm_resets_failback_state() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-rearm", false)).unwrap();
        // Simulate an item whose task exhausted retries: terminally Failed
        // with attempts spent and a stale backoff timestamp left behind.
        {
            let mut g = q.state.lock().unwrap_or_else(|p| p.into_inner());
            let it = g.items.iter_mut().find(|i| i.id == a.id).unwrap();
            it.translate.status = TaskStatus::Failed;
            it.translate.error = Some("provider error 500: boom".into());
            it.translate.attempts = 3;
            it.translate.not_before = Some(now_secs() + 3600);
            q.persist_tracked(&g).unwrap();
        }
        let b = q.enqueue(enq("sha-rearm", false)).unwrap();
        assert_eq!(b.id, a.id, "merged into the queued item, not a new one");
        assert_eq!(b.translate.status, TaskStatus::Queued);
        assert_eq!(b.translate.error, None);
        assert_eq!(b.translate.attempts, 0);
        assert_eq!(b.translate.not_before, None);
        // Retry mechanism fully alive again for this item.
        q.claim_next().unwrap();
        let item = q
            .finish_task(&a.id, TaskKind::Translate, Err("transport: reset".into()))
            .unwrap();
        assert_eq!(item.translate.status, TaskStatus::Queued);
        assert_eq!(item.translate.attempts, 1);
        assert!(item.translate.not_before.is_some());
        let _ = std::fs::remove_dir_all(&vault);
    }

    /// Reserved budget-defer mechanism: deferred to next local midnight,
    /// retry attempts untouched, item explicitly re-queued. (No producer today.)
    #[test]
    fn budget_defer_defers_to_next_local_midnight() {
        let vault = tmp();
        let q = SourceWorkQueue::open(&vault);
        let a = q.enqueue(enq("sha-budget", false)).unwrap();
        q.claim_next().unwrap();
        let before = now_secs();
        let item = q
            .finish_task(
                &a.id,
                TaskKind::Translate,
                Err("daily budget exhausted: 1000000 tokens".into()),
            )
            .unwrap();
        assert_eq!(item.translate.status, TaskStatus::Queued);
        assert_eq!(item.translate.attempts, 0, "defer must not burn retries");
        assert_eq!(item.status, ItemStatus::Queued);
        assert_eq!(item.started_at, None);
        let nb = item.translate.not_before.unwrap();
        assert!(nb > before && nb <= before + 36 * 3600, "nb={nb} before={before}");
        use chrono::TimeZone;
        let local = chrono::Local.timestamp_opt(nb as i64, 0).latest().unwrap();
        assert_eq!(
            local.time(),
            chrono::NaiveTime::from_hms_opt(0, 0, 0).unwrap(),
            "not_before must be local midnight"
        );
        assert!(q.claim_next().is_none());
        let _ = std::fs::remove_dir_all(&vault);
    }

    /// Pre-failback queue files (no attempts/not_before) load with defaults
    /// and behave exactly as a fresh item.
    #[test]
    fn legacy_queue_json_without_failback_fields_loads() {
        let vault = tmp();
        let path = vault.join(QUEUE_REL);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let raw = r#"{
          "schema": "ovp.source-work-queue/v1",
          "items": [
            {
              "id": "swq-legacy",
              "sha256": "legacysha",
              "translate": { "wanted": true, "force": false, "status": "queued" },
              "summarize": { "wanted": false, "status": "skipped" },
              "status": "queued",
              "created_at": 1
            }
          ]
        }"#;
        std::fs::write(&path, raw).unwrap();
        let q = SourceWorkQueue::open(&vault);
        let snap = q.snapshot();
        assert_eq!(snap.items.len(), 1);
        assert_eq!(snap.items[0].translate.attempts, 0);
        assert_eq!(snap.items[0].translate.not_before, None);
        // Claimable like any fresh item; a transient failure starts the retry
        // budget from zero.
        let c = q.claim_next().unwrap();
        assert_eq!(c.id, "swq-legacy");
        let item = q
            .finish_task(
                "swq-legacy",
                TaskKind::Translate,
                Err("provider error 429: slow down".into()),
            )
            .unwrap();
        assert_eq!(item.translate.status, TaskStatus::Queued);
        assert_eq!(item.translate.attempts, 1);
        assert!(item.translate.not_before.is_some());
        let _ = std::fs::remove_dir_all(&vault);
    }
}
