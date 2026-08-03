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
        // even if started_at was just cleared.
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
                    item.translate.status = TaskStatus::Queued;
                    item.translate.error = None;
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
        let Some(idx) = pick_next_queued_index(&g.items) else {
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
        let task = match kind {
            TaskKind::Translate => &mut item.translate,
            TaskKind::Summarize => &mut item.summarize,
        };
        match &result {
            Ok(()) => {
                task.status = TaskStatus::Done;
                task.error = None;
            }
            Err(e) => {
                task.status = TaskStatus::Failed;
                task.error = Some(e.clone());
            }
        }
        // If cancelled mid-run, keep cancelled item status but record task results.
        if item.status != ItemStatus::Cancelled {
            recompute_item_status(item);
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
            recompute_item_status(item);
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

/// Index of the next queued item: highest priority, then oldest created_at.
fn pick_next_queued_index(items: &[QueueItem]) -> Option<usize> {
    items
        .iter()
        .enumerate()
        .filter(|(_, i)| i.status == ItemStatus::Queued)
        .min_by(|(_, a), (_, b)| {
            // Prefer higher priority (so reverse cmp), then older created_at.
            b.priority
                .cmp(&a.priority)
                .then_with(|| a.created_at.cmp(&b.created_at))
        })
        .map(|(idx, _)| idx)
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
                },
                summarize: TaskState {
                    wanted: true,
                    force: false,
                    status: TaskStatus::Running,
                    error: None,
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
                },
                summarize: TaskState {
                    wanted: true,
                    force: false,
                    status: TaskStatus::Running,
                    error: None,
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
}
