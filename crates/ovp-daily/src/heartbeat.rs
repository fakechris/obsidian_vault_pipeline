//! Run-liveness heartbeat (OVP2 observability P0) — `<vault>/.ovp/last-run.json`.
//!
//! Now that `daily` runs UNATTENDED (launchd/systemd `ovp2 schedule`), a run
//! that CRASHES before it writes its end-of-run report is otherwise completely
//! invisible: no report is written, the index/console are not rebuilt, and the
//! portal stays byte-identical to yesterday with a green health dot. This
//! heartbeat is the one thing the operator passively sees that ages by itself.
//!
//! Contract:
//!   * `write_running` is called ONCE, at the very START of `daily`, before any
//!     work — it stamps `status: running` with the pid so a stuck run is
//!     distinguishable from a crashed one.
//!   * a terminal write overwrites it: `completed` (with counts) on success,
//!     `failed` (with the error string) on a handled error.
//!   * the ABORT case — panic, SIGKILL, an error propagating out of `run()`
//!     without an explicit finalize — is caught by [`HeartbeatGuard`], an RAII
//!     drop-guard that, if it drops without `finalize`, writes `status: aborted`
//!     so the file is never left saying "running" forever.
//!
//! Time is wall-clock (`SystemTime::now`) BY NATURE — a heartbeat that says
//! "8 hours ago" must reflect real elapsed time, so this is deliberately NOT one
//! of the determinism-pinned, caller-dated projections.

use std::path::Path;

use serde::{Deserialize, Serialize};

use ovp_domain::VaultLayout;

pub const LAST_RUN_SCHEMA: &str = "ovp.daily.last-run/v1";

/// Terminal (and in-flight) run status surfaced by the heartbeat.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LastRunStatus {
    /// Written at the start of the run; still in flight (or the process died
    /// hard enough that even the drop-guard never ran — e.g. SIGKILL/power loss).
    Running,
    /// Finished cleanly (counts populated).
    Completed,
    /// A handled error ended the run (`error` populated).
    Failed,
    /// The command dropped without an explicit finalize — a panic or an error
    /// propagating out of `run()`. The drop-guard wrote this.
    Aborted,
}

/// The heartbeat document. Serde-additive: every field beyond the required
/// three is optional so an older reader tolerates a newer file and vice versa.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LastRun {
    pub schema: String,
    pub run_id: String,
    /// Wall-clock start (UTC, RFC3339).
    pub started_at: String,
    pub status: LastRunStatus,
    /// Wall-clock terminal time (UTC, RFC3339); None while `running`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ended_at: Option<String>,
    #[serde(default)]
    pub pid: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processed: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failed: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub blocked: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capped: Option<usize>,
    /// Sources still queued after this run (backlog gauge).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub queued_after: Option<usize>,
    /// Populated on `failed`; a short note on `aborted`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Terminal counts captured on a clean completion.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RunCounts {
    pub processed: usize,
    pub failed: usize,
    pub blocked: usize,
    pub capped: usize,
    pub queued_after: usize,
}

/// Current wall-clock instant as an RFC3339 UTC string (`YYYY-MM-DDTHH:MM:SSZ`).
/// Deliberately wall-clock — a liveness heartbeat MUST age in real time.
pub fn now_rfc3339_utc() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format_rfc3339_utc(secs as i64)
}

/// Format unix seconds as an RFC3339 UTC timestamp. Pure/testable; the same
/// civil-date arithmetic used elsewhere in the codebase, extended to seconds.
pub fn format_rfc3339_utc(unix_secs: i64) -> String {
    let days = unix_secs.div_euclid(86_400);
    let rem = unix_secs.rem_euclid(86_400);
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let (y, mo, d) = days_to_ymd(days);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

fn days_to_ymd(mut days: i64) -> (i32, u32, u32) {
    let mut year: i32 = 1970;
    loop {
        let dy = if is_leap(year) { 366 } else { 365 };
        if days < dy {
            break;
        }
        days -= dy;
        year += 1;
    }
    let months: [i64; 12] = [
        31,
        if is_leap(year) { 29 } else { 28 },
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    ];
    let mut month: u32 = 1;
    for m in months.iter() {
        if days < *m {
            return (year, month, (days + 1) as u32);
        }
        days -= *m;
        month += 1;
    }
    (year, 12, 31)
}

fn is_leap(y: i32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0)
}

fn last_run_path(vault_root: &Path) -> std::path::PathBuf {
    vault_root.join(VaultLayout::new().last_run_file())
}

/// Overwrite the heartbeat file (create parent dirs as needed). Overwrite is
/// correct: this is a single liveness snapshot, not a ledger.
pub fn write_last_run(vault_root: &Path, record: &LastRun) -> Result<(), String> {
    let target = last_run_path(vault_root);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string_pretty(record)
        .map_err(|e| format!("serializing last-run heartbeat: {e}"))?;
    std::fs::write(&target, format!("{body}\n"))
        .map_err(|e| format!("writing {}: {e}", target.display()))
}

/// Read the heartbeat if present. `Ok(None)` on a fresh vault (no file yet);
/// `Err` only on a present-but-unparseable file (corruption should be loud).
pub fn read_last_run(vault_root: &Path) -> Result<Option<LastRun>, String> {
    let path = last_run_path(vault_root);
    match std::fs::read_to_string(&path) {
        Ok(raw) => serde_json::from_str(&raw)
            .map(Some)
            .map_err(|e| format!("parsing {}: {e}", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("reading {}: {e}", path.display())),
    }
}

/// RAII heartbeat guard. Construct it at the start of `daily` (which writes the
/// `running` record). Call [`HeartbeatGuard::finalize`] with a terminal status
/// on every non-panicking exit path; if the guard is dropped WITHOUT a finalize
/// — a panic, or `?` propagating an error past it — its `Drop` writes
/// `status: aborted`, so the heartbeat is never stranded on "running".
pub struct HeartbeatGuard {
    vault_root: std::path::PathBuf,
    run_id: String,
    started_at: String,
    pid: u32,
    /// Set once finalize runs — suppresses the abort write in Drop.
    finalized: bool,
}

impl HeartbeatGuard {
    /// Stamp `status: running` and return the guard. A write failure here is a
    /// warning, not a run-abort: the operator's day should not be blocked by an
    /// observability side-channel. The caller prints the returned warning.
    pub fn start(vault_root: &Path, run_id: &str) -> (Self, Option<String>) {
        let started_at = now_rfc3339_utc();
        let pid = std::process::id();
        let record = LastRun {
            schema: LAST_RUN_SCHEMA.into(),
            run_id: run_id.into(),
            started_at: started_at.clone(),
            status: LastRunStatus::Running,
            ended_at: None,
            pid,
            processed: None,
            failed: None,
            blocked: None,
            capped: None,
            queued_after: None,
            error: None,
        };
        let warn = write_last_run(vault_root, &record)
            .err()
            .map(|e| format!("heartbeat: could not write last-run.json: {e}"));
        (
            Self {
                vault_root: vault_root.to_path_buf(),
                run_id: run_id.into(),
                started_at,
                pid,
                finalized: false,
            },
            warn,
        )
    }

    fn terminal(&self, status: LastRunStatus, counts: Option<RunCounts>, error: Option<String>) -> LastRun {
        LastRun {
            schema: LAST_RUN_SCHEMA.into(),
            run_id: self.run_id.clone(),
            started_at: self.started_at.clone(),
            status,
            ended_at: Some(now_rfc3339_utc()),
            pid: self.pid,
            processed: counts.map(|c| c.processed),
            failed: counts.map(|c| c.failed),
            blocked: counts.map(|c| c.blocked),
            capped: counts.map(|c| c.capped),
            queued_after: counts.map(|c| c.queued_after),
            error,
        }
    }

    /// Overwrite the heartbeat with `completed` + counts. Consumes the guard so
    /// Drop cannot also fire an abort. Returns a warning string on write failure.
    pub fn finalize_completed(mut self, counts: RunCounts) -> Option<String> {
        self.finalized = true;
        let record = self.terminal(LastRunStatus::Completed, Some(counts), None);
        write_last_run(&self.vault_root, &record)
            .err()
            .map(|e| format!("heartbeat: could not finalize last-run.json (completed): {e}"))
    }

    /// Overwrite the heartbeat with `failed` + the error string. Consumes the
    /// guard. Returns a warning string on write failure.
    pub fn finalize_failed(mut self, error: &str) -> Option<String> {
        self.finalized = true;
        let record = self.terminal(LastRunStatus::Failed, None, Some(error.to_string()));
        write_last_run(&self.vault_root, &record)
            .err()
            .map(|e| format!("heartbeat: could not finalize last-run.json (failed): {e}"))
    }
}

impl Drop for HeartbeatGuard {
    fn drop(&mut self) {
        if self.finalized {
            return;
        }
        // Reached only on an un-finalized exit: a panic unwinding through the
        // guard, or an error propagating out of the daily command past it.
        let record = self.terminal(
            LastRunStatus::Aborted,
            None,
            Some("run ended without a terminal status (panic, kill, or error propagating out of daily)".into()),
        );
        // Best-effort: Drop must not panic. If this write fails the file stays
        // on "running", which the reader still treats as stale/suspect.
        let _ = write_last_run(&self.vault_root, &record);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc3339_formats_epoch_and_a_known_instant() {
        assert_eq!(format_rfc3339_utc(0), "1970-01-01T00:00:00Z");
        // 2026-07-12T09:00:00Z
        assert_eq!(format_rfc3339_utc(1_783_846_800), "2026-07-12T09:00:00Z");
    }

    #[test]
    fn start_writes_running_with_pid() {
        let dir = tempfile::tempdir().unwrap();
        let (guard, warn) = HeartbeatGuard::start(dir.path(), "daily-2026-07-12");
        assert!(warn.is_none(), "clean start should not warn: {warn:?}");
        let rec = read_last_run(dir.path()).unwrap().expect("heartbeat written");
        assert_eq!(rec.status, LastRunStatus::Running);
        assert_eq!(rec.run_id, "daily-2026-07-12");
        assert!(rec.ended_at.is_none());
        assert_eq!(rec.pid, std::process::id());
        // Do NOT let the guard drop as aborted — finalize it.
        assert!(guard.finalize_completed(RunCounts::default()).is_none());
    }

    #[test]
    fn finalize_completed_overwrites_with_counts() {
        let dir = tempfile::tempdir().unwrap();
        let (guard, _) = HeartbeatGuard::start(dir.path(), "r");
        let counts = RunCounts {
            processed: 8,
            failed: 0,
            blocked: 1,
            capped: 2,
            queued_after: 180,
        };
        assert!(guard.finalize_completed(counts).is_none());
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.status, LastRunStatus::Completed);
        assert_eq!(rec.processed, Some(8));
        assert_eq!(rec.queued_after, Some(180));
        assert!(rec.ended_at.is_some());
        assert!(rec.error.is_none());
    }

    #[test]
    fn finalize_failed_records_error() {
        let dir = tempfile::tempdir().unwrap();
        let (guard, _) = HeartbeatGuard::start(dir.path(), "r");
        assert!(guard.finalize_failed("ANTHROPIC_API_KEY expired").is_none());
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.status, LastRunStatus::Failed);
        assert_eq!(rec.error.as_deref(), Some("ANTHROPIC_API_KEY expired"));
    }

    #[test]
    fn drop_without_finalize_writes_aborted() {
        let dir = tempfile::tempdir().unwrap();
        {
            let (_guard, _) = HeartbeatGuard::start(dir.path(), "r");
            // Simulate a panic / early-return: guard drops here WITHOUT finalize.
        }
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.status, LastRunStatus::Aborted, "drop-guard must catch the abort");
        assert!(rec.error.is_some());
        assert!(rec.ended_at.is_some());
    }

    #[test]
    fn read_absent_is_none_not_error() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read_last_run(dir.path()).unwrap(), None);
    }
}
