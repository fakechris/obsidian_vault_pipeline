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

/// How many recent per-source outcomes the heartbeat ring keeps. This is the
/// portal's live "tail -f": the operator watching the run sees the last N
/// sources succeed/fail with their unit/card counts (or failure reason), at the
/// per-source write cadence (seconds), NOT gated on the coarse projection
/// rebuild. 20 is enough to see recent movement without bloating the sidecar (a
/// bounded ring — oldest entries drop off as new ones arrive).
pub const RECENT_RING_CAP: usize = 20;

/// One per-source outcome in the live activity ring. Populated from the
/// `DailyRunRecord` the reader phase produces per source; both success AND
/// failure appear so a run that starts failing is diagnosable from the portal
/// (the last entries + the terminal `failed`/`aborted` error) without SSHing in
/// to `tail -f` the log.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RecentSource {
    /// Monotonic 1-based sequence within the run (== processed_so_far at write).
    pub seq: usize,
    /// The source just finished (its vault-relative path / title).
    pub title: String,
    /// `"ok"` | `"failed"` — matches the two `RunStatus` variants.
    pub status: String,
    /// Units extracted (0 on failure).
    #[serde(default)]
    pub units: usize,
    /// Cards produced (0 on failure).
    #[serde(default)]
    pub cards: usize,
    /// Failure reason (present only on `failed`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    /// Wall-clock instant the source finished (UTC, RFC3339).
    pub at: String,
}

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
    /// LIVE in-run progress (only while `running`): sources finished so far in
    /// THIS run. Rewritten atomically after each source completes so the portal
    /// can show "18/90" instead of a frozen banner. Absent on terminal records.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processed_so_far: Option<usize>,
    /// LIVE in-run progress: total sources this run intends to process (the
    /// planned batch size after the `--max-sources` cap). Pairs with
    /// `processed_so_far` to render the fraction. Absent on terminal records.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub total_planned: Option<usize>,
    /// LIVE in-run progress: the source just finished (its title or rel path),
    /// so the portal can name what the run is chewing on. Absent on terminal
    /// records.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current: Option<String>,
    /// LIVE per-source activity ring (the portal's tail -f): the last
    /// [`RECENT_RING_CAP`] source outcomes, oldest→newest, rewritten per source
    /// while `running`. Empty (skipped in JSON) on the initial `running` stamp
    /// and on terminal records — the terminal summary is the authoritative
    /// counts, but the LAST progress write's ring stays readable until then so a
    /// failed/aborted run's final feed is the diagnosis. Serde-additive: an
    /// older reader ignores it.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub recent: Vec<RecentSource>,
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
    for (month_index, m) in months.iter().enumerate() {
        if days < *m {
            let month = month_index as u32 + 1;
            return (year, month, (days + 1) as u32);
        }
        days -= *m;
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
    // Atomic overwrite: a reader (the live server) or a mid-write crash must
    // never observe partial JSON. Write a sibling temp then rename over the
    // target — rename is atomic on the same filesystem, so the file is always
    // either the old complete record or the new complete record.
    let tmp = target.with_extension(format!("json.tmp.{}", std::process::id()));
    std::fs::write(&tmp, format!("{body}\n"))
        .map_err(|e| format!("writing {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &target)
        .map_err(|e| format!("renaming {} → {}: {e}", tmp.display(), target.display()))
}

/// Is `pid` a live process? Probes with `kill -0` (no signal sent; exit 0 =
/// alive). Conservative: if the probe itself can't run, assume ALIVE so a real
/// run is never falsely reported dead. `pid == 0` (unset) is treated as not a
/// real owner. Same primitive `RunLock` uses to reclaim stale locks.
pub fn pid_alive(pid: u32) -> bool {
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
            .unwrap_or(true)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

impl LastRun {
    /// The status ACCOUNTING FOR LIVENESS. A record still stamped `running`
    /// whose process is gone was killed hard enough that no destructor ran
    /// (SIGKILL / power loss / OOM), so the drop-guard never wrote a terminal
    /// status — it is really `aborted`. Every LIVE surface (portal run-activity,
    /// `schedule status`, `doctor`) must display THIS, not the raw field, or a
    /// dead run shows as "in progress" forever (the pid was stored for exactly
    /// this check).
    pub fn effective_status(&self) -> LastRunStatus {
        if self.status == LastRunStatus::Running && !pid_alive(self.pid) {
            LastRunStatus::Aborted
        } else {
            self.status
        }
    }

    /// True when [`effective_status`](Self::effective_status) downgraded a
    /// `running` record to `aborted` because its process is gone.
    pub fn is_stalled(&self) -> bool {
        self.status == LastRunStatus::Running
            && self.effective_status() == LastRunStatus::Aborted
    }
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
    /// The last activity ring written by `progress`, carried into the terminal
    /// record so a completed/failed/aborted run KEEPS its per-source feed for
    /// post-run diagnosis (codex review P1) instead of blanking it.
    last_recent: std::cell::RefCell<Vec<RecentSource>>,
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
            processed_so_far: None,
            total_planned: None,
            current: None,
            recent: Vec::new(),
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
                last_recent: std::cell::RefCell::new(Vec::new()),
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
            // Terminal records carry final counts, not live progress: the
            // fraction only means something while `running`.
            processed_so_far: None,
            total_planned: None,
            current: None,
            // Carry the last progress ring into the terminal record so the
            // per-source feed survives finalize — a completed run keeps its
            // outcomes, and a failed/aborted run's last entries ARE the
            // diagnosis (codex review P1).
            recent: self.last_recent.borrow().clone(),
            error,
        }
    }

    /// Rewrite the heartbeat as a LIVE `running` progress snapshot — called once
    /// per source as the run advances, so the portal shows "18/90 · <current>"
    /// instead of a banner frozen at the start instant. Takes `&self` (does NOT
    /// consume the guard): the terminal finalize still fires later. The write is
    /// atomic (temp+rename) like every other heartbeat write. A write failure is
    /// a returned warning, never a run-abort — the operator's day is not blocked
    /// by an observability side-channel.
    ///
    /// `current` is the source just finished (title or rel path); `None` clears
    /// it. `recent` is the caller's live activity ring (last [`RECENT_RING_CAP`]
    /// outcomes, oldest→newest) — the portal's tail -f. Reusing
    /// `started_at`/`pid`/`run_id` keeps this the SAME run record, only fresher —
    /// an older reader that ignores the new fields still sees a valid `running`
    /// heartbeat that ages in real time.
    pub fn progress(
        &self,
        processed_so_far: usize,
        total_planned: usize,
        current: Option<&str>,
        recent: &[RecentSource],
    ) -> Option<String> {
        let record = LastRun {
            schema: LAST_RUN_SCHEMA.into(),
            run_id: self.run_id.clone(),
            started_at: self.started_at.clone(),
            status: LastRunStatus::Running,
            ended_at: None,
            pid: self.pid,
            processed: None,
            failed: None,
            blocked: None,
            capped: None,
            queued_after: None,
            processed_so_far: Some(processed_so_far),
            total_planned: Some(total_planned),
            current: current.map(str::to_string),
            recent: recent.to_vec(),
            error: None,
        };
        *self.last_recent.borrow_mut() = recent.to_vec();
        write_last_run(&self.vault_root, &record)
            .err()
            .map(|e| format!("heartbeat: could not update in-run progress: {e}"))
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

    fn running_record(pid: u32) -> LastRun {
        LastRun {
            schema: "ovp.daily.last-run/v1".into(),
            run_id: "daily-x".into(),
            started_at: "2026-07-13T03:40:55Z".into(),
            status: LastRunStatus::Running,
            ended_at: None,
            pid,
            processed: None,
            failed: None,
            blocked: None,
            capped: None,
            queued_after: None,
            processed_so_far: Some(65),
            total_planned: Some(80),
            current: None,
            recent: vec![],
            error: None,
        }
    }

    #[test]
    fn effective_status_downgrades_dead_running_to_aborted() {
        // Our own pid is alive -> stays running.
        let live = running_record(std::process::id());
        assert_eq!(live.effective_status(), LastRunStatus::Running);
        assert!(!live.is_stalled());

        // pid 0 (unset) is treated as not a real owner -> stalled -> aborted.
        let dead = running_record(0);
        assert_eq!(dead.effective_status(), LastRunStatus::Aborted);
        assert!(dead.is_stalled());

        // A terminal record's liveness is irrelevant — never downgraded.
        let mut done = running_record(0);
        done.status = LastRunStatus::Completed;
        assert_eq!(done.effective_status(), LastRunStatus::Completed);
        assert!(!done.is_stalled());
    }

    #[test]
    fn pid_alive_true_for_self_false_for_zero() {
        assert!(pid_alive(std::process::id()));
        assert!(!pid_alive(0));
    }

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

    #[test]
    fn progress_updates_fraction_and_current_incrementally() {
        let dir = tempfile::tempdir().unwrap();
        let (guard, _) = HeartbeatGuard::start(dir.path(), "r");

        assert!(guard.progress(1, 3, Some("first source"), &[]).is_none());
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.status, LastRunStatus::Running);
        assert_eq!(rec.processed_so_far, Some(1));
        assert_eq!(rec.total_planned, Some(3));
        assert_eq!(rec.current.as_deref(), Some("first source"));
        // Still a live record: ages in real time, no terminal time yet.
        assert!(rec.ended_at.is_none());
        assert_eq!(rec.run_id, "r");

        // A later source advances the fraction and renames current.
        assert!(guard.progress(2, 3, Some("second source"), &[]).is_none());
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.processed_so_far, Some(2));
        assert_eq!(rec.current.as_deref(), Some("second source"));

        // Do not let it drop as aborted.
        assert!(guard.finalize_completed(RunCounts::default()).is_none());
    }

    fn recent(seq: usize, title: &str, status: &str) -> RecentSource {
        RecentSource {
            seq,
            title: title.into(),
            status: status.into(),
            units: if status == "ok" { 12 } else { 0 },
            cards: if status == "ok" { 8 } else { 0 },
            reason: (status == "failed").then(|| "boom".to_string()),
            at: now_rfc3339_utc(),
        }
    }

    #[test]
    fn progress_carries_recent_activity_ring() {
        let dir = tempfile::tempdir().unwrap();
        let (guard, _) = HeartbeatGuard::start(dir.path(), "r");

        // Two sources: one ok, one failed — both must appear in the ring.
        let ring = vec![recent(1, "first", "ok"), recent(2, "second", "failed")];
        assert!(guard.progress(2, 5, Some("second"), &ring).is_none());

        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.recent.len(), 2, "both outcomes surfaced live");
        assert_eq!(rec.recent[0].status, "ok");
        assert_eq!(rec.recent[0].units, 12);
        assert_eq!(rec.recent[1].status, "failed");
        assert_eq!(rec.recent[1].reason.as_deref(), Some("boom"));

        assert!(guard.finalize_completed(RunCounts::default()).is_none());
    }

    #[test]
    fn terminal_record_retains_the_activity_ring_for_diagnosis() {
        // Codex P1: the feed must survive finalize — a failed run's last
        // per-source outcomes ARE the diagnosis, not blanked on the way out.
        let dir = tempfile::tempdir().unwrap();
        let (guard, _) = HeartbeatGuard::start(dir.path(), "r");
        let ring = vec![recent(1, "ok-one", "ok"), recent(2, "bad-two", "failed")];
        assert!(guard.progress(2, 5, Some("bad-two"), &ring).is_none());
        assert!(guard.finalize_failed("provider outage").is_none());

        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.status, LastRunStatus::Failed);
        assert_eq!(rec.error.as_deref(), Some("provider outage"));
        assert_eq!(rec.recent.len(), 2, "the feed survives finalize");
        assert_eq!(rec.recent[1].status, "failed");
    }

    #[test]
    fn terminal_finalize_after_progress_drops_progress_fields() {
        let dir = tempfile::tempdir().unwrap();
        let (guard, _) = HeartbeatGuard::start(dir.path(), "r");
        assert!(guard.progress(2, 5, Some("mid source"), &[]).is_none());
        let counts = RunCounts { processed: 5, queued_after: 40, ..Default::default() };
        assert!(guard.finalize_completed(counts).is_none());
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        // Terminal record is authoritative: final counts, NOT the live fraction.
        assert_eq!(rec.status, LastRunStatus::Completed);
        assert_eq!(rec.processed, Some(5));
        assert_eq!(rec.queued_after, Some(40));
        assert!(rec.processed_so_far.is_none());
        assert!(rec.total_planned.is_none());
        assert!(rec.current.is_none());
    }

    #[test]
    fn drop_after_progress_still_writes_aborted() {
        let dir = tempfile::tempdir().unwrap();
        {
            let (guard, _) = HeartbeatGuard::start(dir.path(), "r");
            assert!(guard.progress(3, 9, Some("chewing on this"), &[]).is_none());
            // Guard drops here WITHOUT finalize — the abort drop-guard must fire
            // even after live progress writes.
        }
        let rec = read_last_run(dir.path()).unwrap().unwrap();
        assert_eq!(rec.status, LastRunStatus::Aborted);
        assert!(rec.error.is_some());
        assert!(rec.ended_at.is_some());
        // Progress fields are not carried onto the terminal abort record.
        assert!(rec.processed_so_far.is_none());
    }
}
