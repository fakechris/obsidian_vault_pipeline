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
}

/// Process-local queue + wake for the background worker.
pub struct SourceWorkQueue {
    path: PathBuf,
    state: Mutex<QueueFile>,
    wake: Condvar,
}

impl SourceWorkQueue {
    pub fn open(vault_root: &Path) -> Self {
        let path = vault_root.join(QUEUE_REL);
        let file = load_file(&path).unwrap_or_default();
        Self {
            path,
            state: Mutex::new(file),
            wake: Condvar::new(),
        }
    }

    pub fn snapshot(&self) -> QueueFile {
        self.state
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
    }

    pub fn wake_worker(&self) {
        self.wake.notify_one();
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
        let sha = req.sha256.trim();
        if sha.is_empty() || sha.len() > 128 {
            return Err("invalid sha256".into());
        }
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
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
            let out = item.clone();
            persist(&self.path, &g)?;
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
        };
        g.items.push(item.clone());
        // Cap history: keep last 40 terminal + all active.
        prune_history(&mut g.items, 40);
        persist(&self.path, &g)?;
        drop(g);
        self.wake.notify_one();
        Ok(item)
    }

    /// Reorder: `ids` is the desired order of *queued* items (running is fixed first).
    pub fn reorder(&self, ids: &[String]) -> Result<QueueFile, String> {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
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
        for id in ids {
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
        persist(&self.path, &g)?;
        Ok(g.clone())
    }

    pub fn cancel(&self, id: &str) -> Result<QueueItem, String> {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
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
        persist(&self.path, &g)?;
        Ok(out)
    }

    pub fn remove(&self, id: &str) -> Result<(), String> {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
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
        persist(&self.path, &g)?;
        Ok(())
    }

    /// Claim the next queued article for the worker (marks Running).
    pub fn claim_next(&self) -> Option<QueueItem> {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        if g.items.iter().any(|i| i.status == ItemStatus::Running) {
            return None; // one article at a time
        }
        let item = g.items.iter_mut().find(|i| i.status == ItemStatus::Queued)?;
        item.status = ItemStatus::Running;
        item.started_at = Some(now_secs());
        if item.translate.wanted && item.translate.status == TaskStatus::Queued {
            item.translate.status = TaskStatus::Running;
        }
        if item.summarize.wanted && item.summarize.status == TaskStatus::Queued {
            item.summarize.status = TaskStatus::Running;
        }
        let out = item.clone();
        let _ = persist(&self.path, &g);
        Some(out)
    }

    pub fn finish_task(
        &self,
        id: &str,
        kind: TaskKind,
        result: Result<(), String>,
    ) -> Result<QueueItem, String> {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let item = g
            .items
            .iter_mut()
            .find(|i| i.id == id)
            .ok_or_else(|| format!("queue item not found: {id}"))?;
        let task = match kind {
            TaskKind::Translate => &mut item.translate,
            TaskKind::Summarize => &mut item.summarize,
        };
        match result {
            Ok(()) => {
                task.status = TaskStatus::Done;
                task.error = None;
            }
            Err(e) => {
                task.status = TaskStatus::Failed;
                task.error = Some(e);
            }
        }
        // If cancelled mid-run, keep cancelled item status but record task results.
        if item.status != ItemStatus::Cancelled {
            recompute_item_status(item);
        } else if item_tasks_terminal(item) {
            item.finished_at = item.finished_at.or_else(|| Some(now_secs()));
        }
        let out = item.clone();
        persist(&self.path, &g)?;
        Ok(out)
    }

    /// Items that need a client notification (terminal + notify + !notify_sent).
    pub fn take_notify_batch(&self) -> Vec<QueueItem> {
        let mut g = self.state.lock().unwrap_or_else(|p| p.into_inner());
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
            let _ = persist(&self.path, &g);
        }
        out
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
            let _ = persist(&self.path, &g);
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
        })
        .unwrap();
        q.enqueue(EnqueueRequest {
            sha256: "sha-b-00000000".into(),
            title: None,
            translate: true,
            summarize: false,
            force: false,
            notify: true,
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
            })
            .unwrap();
        q.reorder(&[b.id.clone(), a.id.clone()]).unwrap();
        let snap = q.snapshot();
        assert_eq!(snap.items[0].sha256, "bbbbbbbb");
        assert_eq!(snap.items[1].sha256, "aaaaaaaa");
        let _ = std::fs::remove_dir_all(&vault);
    }
}
