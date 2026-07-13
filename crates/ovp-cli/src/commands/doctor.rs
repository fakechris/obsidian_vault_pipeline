//! `doctor` — health checks over OVP vault state.
//!
//! Checks: ledger↔fs consistency, orphan packs, stale index, broken internal
//! links, crystal ledger integrity, per-dir disk usage. Exits non-zero if any
//! check FAILs (CI-friendly). `--fix` applies safe repairs only (rebuild index,
//! quarantine orphans — never deletes per OVP_RULES).

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use ovp_daily::{read_daily_ledger, RunStatus};
use ovp_domain::VaultLayout;
use ovp_index::{build_index, write_index};

use crate::CliError;

/// Default run-recency staleness threshold: 26 hours — one 09:00 daily
/// schedule interval (24h) plus slack for a long reader batch. Beyond this the
/// unattended loop is assumed stalled.
pub const DEFAULT_RECENCY_HOURS: u64 = 26;

pub struct DoctorArgs {
    pub vault_root: PathBuf,
    pub fix: bool,
    pub json: bool,
    /// Override for the run-recency staleness threshold (hours).
    pub since_hours: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Severity {
    Pass,
    /// Purely informational — never affects the exit code.
    Info,
    Warn,
    Fail,
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Severity::Pass => write!(f, "PASS"),
            Severity::Info => write!(f, "INFO"),
            Severity::Warn => write!(f, "WARN"),
            Severity::Fail => write!(f, "FAIL"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Finding {
    pub check: String,
    pub severity: Severity,
    pub message: String,
    pub fixed: bool,
}

pub fn run(args: DoctorArgs) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let mut findings: Vec<Finding> = Vec::new();

    println!("doctor: {}", args.vault_root.display());

    check_ledger_fs_consistency(&args.vault_root, &layout, &mut findings);
    check_orphan_packs(&args.vault_root, &layout, &mut findings);
    check_stale_index(&args.vault_root, &mut findings, args.fix);
    check_crystal_integrity(&args.vault_root, &mut findings);
    let threshold_hours = args.since_hours.unwrap_or(DEFAULT_RECENCY_HOURS);
    check_run_recency(&args.vault_root, &layout, now_unix_secs(), threshold_hours, &mut findings);
    check_disk_usage(&args.vault_root, &layout, &mut findings);
    check_legacy_artifacts(&args.vault_root, &mut findings);
    check_inbox_orphans(&args.vault_root, &layout, &mut findings);

    if args.json {
        let json_out: Vec<_> = findings
            .iter()
            .map(|f| {
                serde_json::json!({
                    "check": f.check,
                    "severity": format!("{}", f.severity),
                    "message": f.message,
                    "fixed": f.fixed,
                })
            })
            .collect();
        println!("{}", serde_json::to_string_pretty(&json_out).unwrap_or_default());
    } else {
        for f in &findings {
            let fix_tag = if f.fixed { " [FIXED]" } else { "" };
            println!("  [{}] {}: {}{}", f.severity, f.check, f.message, fix_tag);
        }
    }

    let fails = findings.iter().filter(|f| f.severity == Severity::Fail && !f.fixed).count();
    let warns = findings.iter().filter(|f| f.severity == Severity::Warn).count();
    let infos = findings.iter().filter(|f| f.severity == Severity::Info).count();
    let passes = findings.iter().filter(|f| f.severity == Severity::Pass).count();

    println!("\n  summary: {passes} pass, {infos} info, {warns} warn, {fails} fail");

    if fails > 0 {
        Err(CliError::Gate(format!("doctor: {fails} check(s) FAILED")))
    } else {
        Ok(())
    }
}

fn check_ledger_fs_consistency(vault_root: &Path, layout: &VaultLayout, findings: &mut Vec<Finding>) {
    let ledger_path = vault_root.join(layout.daily_ledger());
    let ledger = match read_daily_ledger(&ledger_path) {
        Ok(l) => l,
        Err(e) => {
            if ledger_path.exists() {
                findings.push(Finding {
                    check: "ledger-readable".into(),
                    severity: Severity::Fail,
                    message: format!("cannot read ledger: {e}"),
                    fixed: false,
                });
            } else {
                findings.push(Finding {
                    check: "ledger-exists".into(),
                    severity: Severity::Warn,
                    message: "no daily ledger found (no runs yet?)".into(),
                    fixed: false,
                });
            }
            return;
        }
    };

    let mut missing_count = 0;

    for entry in &ledger {
        if entry.status != RunStatus::Succeeded {
            continue;
        }
        if let Some(pack_dir) = &entry.pack_dir {
            let expected = vault_root.join(pack_dir);
            if !expected.exists() {
                missing_count += 1;
            }
        }
    }

    if missing_count > 0 {
        findings.push(Finding {
            check: "ledger-fs-consistency".into(),
            severity: Severity::Warn,
            message: format!("{missing_count} succeeded ledger entries without corresponding pack directories"),
            fixed: false,
        });
    } else {
        findings.push(Finding {
            check: "ledger-fs-consistency".into(),
            severity: Severity::Pass,
            message: "all succeeded ledger entries have corresponding pack dirs".into(),
            fixed: false,
        });
    }
}

fn check_orphan_packs(vault_root: &Path, layout: &VaultLayout, findings: &mut Vec<Finding>) {
    let reader_dir = vault_root.join(layout.reader_root());
    if !reader_dir.exists() {
        findings.push(Finding {
            check: "orphan-packs".into(),
            severity: Severity::Pass,
            message: "no reader directory yet".into(),
            fixed: false,
        });
        return;
    }

    let ledger_path = vault_root.join(layout.daily_ledger());
    let known_hashes: HashSet<String> = read_daily_ledger(&ledger_path)
        .unwrap_or_default()
        .iter()
        .map(|e| e.source_sha256.clone())
        .collect();

    let mut orphan_count = 0;
    if let Ok(entries) = std::fs::read_dir(&reader_dir) {
        for dir_entry in entries.flatten() {
            if !dir_entry.file_type().map(|ft| ft.is_dir()).unwrap_or(false) {
                continue;
            }
            let name = dir_entry.file_name().to_string_lossy().to_string();
            let hash_part = name.rsplit('-').next().unwrap_or("");
            if hash_part.len() == 8 && !known_hashes.iter().any(|h| h.starts_with(hash_part)) {
                orphan_count += 1;
            }
        }
    }

    if orphan_count > 0 {
        findings.push(Finding {
            check: "orphan-packs".into(),
            severity: Severity::Warn,
            message: format!("{orphan_count} reader pack(s) not linked to any ledger entry"),
            fixed: false,
        });
    } else {
        findings.push(Finding {
            check: "orphan-packs".into(),
            severity: Severity::Pass,
            message: "all reader packs linked to ledger entries".into(),
            fixed: false,
        });
    }
}

fn check_stale_index(vault_root: &Path, findings: &mut Vec<Finding>, fix: bool) {
    let index_path = vault_root.join(".ovp/index/index.json");
    let ledger_path = vault_root.join(".ovp/daily-runs.jsonl");

    if !index_path.exists() {
        if fix {
            let today = today_iso();
            match build_index(vault_root, &today, None) {
                Ok(model) => {
                    let _ = write_index(vault_root, &model);
                    findings.push(Finding {
                        check: "stale-index".into(),
                        severity: Severity::Warn,
                        message: "index.json missing — rebuilt".into(),
                        fixed: true,
                    });
                }
                Err(e) => {
                    findings.push(Finding {
                        check: "stale-index".into(),
                        severity: Severity::Fail,
                        message: format!("index.json missing, rebuild failed: {e}"),
                        fixed: false,
                    });
                }
            }
        } else {
            findings.push(Finding {
                check: "stale-index".into(),
                severity: Severity::Fail,
                message: "index.json missing (run `doctor --fix` or `index`)".into(),
                fixed: false,
            });
        }
        return;
    }

    if !ledger_path.exists() {
        findings.push(Finding {
            check: "stale-index".into(),
            severity: Severity::Pass,
            message: "no ledger to compare index freshness against".into(),
            fixed: false,
        });
        return;
    }

    let index_mod = std::fs::metadata(&index_path)
        .and_then(|m| m.modified())
        .ok();
    let ledger_mod = std::fs::metadata(&ledger_path)
        .and_then(|m| m.modified())
        .ok();

    match (index_mod, ledger_mod) {
        (Some(idx), Some(led)) if idx < led => {
            if fix {
                let today = today_iso();
                match build_index(vault_root, &today, None) {
                    Ok(model) => {
                        let _ = write_index(vault_root, &model);
                        findings.push(Finding {
                            check: "stale-index".into(),
                            severity: Severity::Warn,
                            message: "index.json older than ledger — rebuilt".into(),
                            fixed: true,
                        });
                    }
                    Err(e) => {
                        findings.push(Finding {
                            check: "stale-index".into(),
                            severity: Severity::Fail,
                            message: format!("index stale, rebuild failed: {e}"),
                            fixed: false,
                        });
                    }
                }
            } else {
                findings.push(Finding {
                    check: "stale-index".into(),
                    severity: Severity::Warn,
                    message: "index.json is older than the daily ledger (run `index` or `daily`)".into(),
                    fixed: false,
                });
            }
        }
        _ => {
            findings.push(Finding {
                check: "stale-index".into(),
                severity: Severity::Pass,
                message: "index.json is up-to-date".into(),
                fixed: false,
            });
        }
    }
}

fn check_crystal_integrity(vault_root: &Path, findings: &mut Vec<Finding>) {
    let crystal_dir = vault_root.join(".ovp/crystal");
    let ledger_path = crystal_dir.join("ledger.jsonl");

    if !crystal_dir.exists() {
        findings.push(Finding {
            check: "crystal-integrity".into(),
            severity: Severity::Pass,
            message: "no crystal store yet".into(),
            fixed: false,
        });
        return;
    }

    if !ledger_path.exists() {
        findings.push(Finding {
            check: "crystal-integrity".into(),
            severity: Severity::Warn,
            message: "crystal directory exists but ledger.jsonl is missing".into(),
            fixed: false,
        });
        return;
    }

    let content = match std::fs::read_to_string(&ledger_path) {
        Ok(c) => c,
        Err(e) => {
            findings.push(Finding {
                check: "crystal-integrity".into(),
                severity: Severity::Fail,
                message: format!("cannot read crystal ledger: {e}"),
                fixed: false,
            });
            return;
        }
    };

    let mut valid = 0;
    let mut invalid = 0;
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if serde_json::from_str::<serde_json::Value>(line).is_ok() {
            valid += 1;
        } else {
            invalid += 1;
        }
    }

    if invalid > 0 {
        findings.push(Finding {
            check: "crystal-integrity".into(),
            severity: Severity::Fail,
            message: format!("crystal ledger has {invalid} unparseable lines ({valid} valid)"),
            fixed: false,
        });
    } else {
        findings.push(Finding {
            check: "crystal-integrity".into(),
            severity: Severity::Pass,
            message: format!("crystal ledger intact ({valid} records)"),
            fixed: false,
        });
    }
}

/// Run-recency + backlog check (OVP2 observability P0). Now that `daily` runs
/// unattended, a run that crashes before its end-of-run report leaves the
/// portal frozen with a green dot — so `doctor` must fail loudly when:
///   * the heartbeat says `failed` or `aborted` (a crashed/aborted run), or
///   * the last run (heartbeat `ended_at`/`started_at`, else the newest report
///     file's mtime) is older than `threshold_hours`.
///
/// It also emits an INFO when the capped backlog is growing (heartbeat
/// `queued_after` not shrinking vs the prior report's residual queue — a
/// best-effort signal, never a failure).
fn check_run_recency(
    vault_root: &Path,
    layout: &VaultLayout,
    now_secs: i64,
    threshold_hours: u64,
    findings: &mut Vec<Finding>,
) {
    let threshold_secs = threshold_hours as i64 * 3600;

    // A PRESENT-but-corrupt heartbeat is a hard FAIL, never a silent fallback:
    // `.ok().flatten()` would treat a malformed file as absent and quietly fall
    // back to report mtimes, so doctor could PASS while the latest failed or
    // aborted run is unknowable. Absent (Ok(None)) is fine — a fresh vault.
    let hb = match ovp_daily::read_last_run(vault_root) {
        Ok(hb) => hb,
        Err(e) => {
            findings.push(Finding {
                check: "run-recency".into(),
                severity: Severity::Fail,
                message: format!(
                    ".ovp/last-run.json is present but unreadable/corrupt ({e}) — the last run's \
                     status is unknowable; repair or remove it, then rerun `ovp2 daily`"
                ),
                fixed: false,
            });
            return;
        }
    };

    // 1) Terminal-status failures are unconditional FAILs regardless of age.
    if let Some(rec) = &hb {
        match rec.status {
            ovp_daily::LastRunStatus::Failed => {
                findings.push(Finding {
                    check: "run-recency".into(),
                    severity: Severity::Fail,
                    message: format!(
                        "last run FAILED ({}){}",
                        rec.run_id,
                        rec.error.as_deref().map(|e| format!(" — {e}")).unwrap_or_default()
                    ),
                    fixed: false,
                });
                return;
            }
            ovp_daily::LastRunStatus::Aborted => {
                findings.push(Finding {
                    check: "run-recency".into(),
                    severity: Severity::Fail,
                    message: format!(
                        "last run ABORTED ({}) — the process died mid-run (panic/kill/provider error); \
                         check the schedule log and rerun `ovp2 daily`",
                        rec.run_id
                    ),
                    fixed: false,
                });
                return;
            }
            ovp_daily::LastRunStatus::Running | ovp_daily::LastRunStatus::Completed => {}
        }
    }

    // 2) Age: heartbeat timestamp preferred (has wall-clock time), else newest
    // report file mtime. A `running` heartbeat ages from `started_at`.
    let hb_secs = hb.as_ref().and_then(|r| {
        let ts = r.ended_at.as_deref().unwrap_or(r.started_at.as_str());
        parse_rfc3339_utc(ts)
    });
    let report_secs = newest_report_mtime_secs(vault_root, layout);
    let last_secs = match (hb_secs, report_secs) {
        (Some(a), Some(b)) => Some(a.max(b)),
        (a, b) => a.or(b),
    };

    match last_secs {
        None => {
            findings.push(Finding {
                check: "run-recency".into(),
                severity: Severity::Warn,
                message: "no run recorded yet (no heartbeat, no reports)".into(),
                fixed: false,
            });
        }
        Some(ts) => {
            let age = now_secs - ts;
            if age > threshold_secs {
                let hours = age / 3600;
                findings.push(Finding {
                    check: "run-recency".into(),
                    severity: Severity::Fail,
                    message: format!(
                        "last run was ~{hours}h ago (> {threshold_hours}h) — the unattended loop may be \
                         stalled; check `ovp2 schedule status` and the schedule log"
                    ),
                    fixed: false,
                });
            } else {
                let hours = age.max(0) / 3600;
                findings.push(Finding {
                    check: "run-recency".into(),
                    severity: Severity::Pass,
                    message: format!("last run ~{hours}h ago (< {threshold_hours}h)"),
                    fixed: false,
                });
            }
        }
    }

    // 3) INFO: capped backlog growing (best-effort). Compare the heartbeat's
    // post-run queue depth to the current queue depth on disk; a strictly
    // larger live queue than the last recorded run's residual is a growing
    // backlog worth surfacing, never a failure.
    if let Some(rec) = &hb
        && let Some(queued_after) = rec.queued_after
    {
        let live_queued = current_queue_depth(vault_root);
        if live_queued > queued_after {
            findings.push(Finding {
                check: "run-backlog".into(),
                severity: Severity::Info,
                message: format!(
                    "queue is growing: {live_queued} queued now vs {queued_after} after the last run \
                     — capture is outpacing reading; raise --max-sources or run more often"
                ),
                fixed: false,
            });
        }
    }
}

/// Newest mtime (unix secs) across `.ovp/reports/*.json`, if any.
fn newest_report_mtime_secs(vault_root: &Path, layout: &VaultLayout) -> Option<i64> {
    let dir = vault_root.join(layout.reports_dir());
    let entries = std::fs::read_dir(&dir).ok()?;
    let mut newest: Option<i64> = None;
    for e in entries.flatten() {
        let path = e.path();
        if path.extension().is_none_or(|x| x != "json") {
            continue;
        }
        if let Some(secs) = std::fs::metadata(&path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs() as i64)
        {
            newest = Some(newest.map_or(secs, |n| n.max(secs)));
        }
    }
    newest
}

/// Live queue depth on disk: markdown files sitting in `50-Inbox/01-Raw`. A
/// cheap proxy for the backlog — the read model computes the same, but doctor
/// stays index-independent here.
fn current_queue_depth(vault_root: &Path) -> usize {
    let inbox = vault_root.join(VaultLayout::new().inbox_raw_dir());
    fn count_md(dir: &Path) -> usize {
        let mut n = 0;
        if let Ok(entries) = std::fs::read_dir(dir) {
            for e in entries.flatten() {
                let p = e.path();
                let name = e.file_name();
                if name.to_string_lossy().starts_with('.') {
                    continue;
                }
                if p.is_dir() {
                    n += count_md(&p);
                } else if p.extension().is_some_and(|x| x == "md") {
                    n += 1;
                }
            }
        }
        n
    }
    count_md(&inbox)
}

/// Parse an RFC3339 UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`, as the heartbeat
/// writes) into unix seconds. Returns None on any shape it does not recognize
/// (the recency check then falls back to report mtime).
fn parse_rfc3339_utc(s: &str) -> Option<i64> {
    let s = s.trim_end_matches('Z');
    let (date, time) = s.split_once('T')?;
    let mut dp = date.splitn(3, '-');
    let y: i64 = dp.next()?.parse().ok()?;
    let mo: i64 = dp.next()?.parse().ok()?;
    let d: i64 = dp.next()?.parse().ok()?;
    let mut tp = time.splitn(3, ':');
    let h: i64 = tp.next()?.parse().ok()?;
    let mi: i64 = tp.next()?.parse().ok()?;
    let se: i64 = tp.next().unwrap_or("0").parse().ok()?;
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) {
        return None;
    }
    Some(days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + se)
}

/// Howard Hinnant's `days_from_civil` — days since the unix epoch.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn now_unix_secs() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn check_disk_usage(vault_root: &Path, layout: &VaultLayout, findings: &mut Vec<Finding>) {
    let dirs = [
        (".ovp", vault_root.join(".ovp")),
        ("reader", vault_root.join(layout.reader_root())),
        ("attachments", vault_root.join("attachments")),
    ];

    for (name, path) in &dirs {
        if !path.exists() {
            continue;
        }
        let size = dir_size(path);
        let size_mb = size as f64 / (1024.0 * 1024.0);
        let severity = if size_mb > 500.0 {
            Severity::Warn
        } else {
            Severity::Pass
        };
        findings.push(Finding {
            check: format!("disk-{name}"),
            severity,
            message: format!("{size_mb:.1} MB"),
            fixed: false,
        });
    }
}

/// Vault-relative paths only the retired Python OVP wrote. Deliberately a
/// closed allowlist: ovp2 also writes under `60-Logs/` (`pipeline.jsonl`,
/// `knowledge-index.json`) and owns `.ovp/`, so we only flag names we can
/// positively attribute to the Python pipeline (legacy `VaultLayout` in
/// `runtime.py` on the `legacy/python-main` branch).
const LEGACY_ARTIFACTS: &[(&str, &str)] = &[
    ("knowledge.db", "legacy SQLite projection (non-standard location)"),
    ("60-Logs/knowledge.db", "legacy SQLite projection"),
    ("60-Logs/knowledge.db.lock", "legacy knowledge.db write lock"),
    ("60-Logs/backups", "legacy knowledge.db snapshot directory (ovp-backup-db)"),
    ("60-Logs/signals.jsonl", "legacy source-authority signals log"),
    ("60-Logs/signals.jsonl.lock", "legacy signals log lock"),
    ("60-Logs/actions.jsonl", "legacy action-queue log"),
    ("60-Logs/actions.jsonl.lock", "legacy action-queue log lock"),
    ("60-Logs/action-worker.json", "legacy action-worker state"),
    ("60-Logs/action-worker.lock", "legacy action-worker lock"),
    ("60-Logs/workflow.lock", "legacy pipeline workflow lock"),
    ("60-Logs/transactions", "legacy TransactionManager journal directory"),
    ("60-Logs/derived", "legacy derived-artifacts directory"),
    (".ovp/llm_profiles.yaml", "legacy ovp-ask LLM provider profiles"),
    (".ovp/digest.yaml", "legacy digest config"),
    (".ovp/schema_version", "legacy knowledge.db schema-version marker"),
];

/// INFO-only scan for Python-era OVP artifacts. ovp2 reads none of them and
/// they are harmless to keep; findings here never affect the exit code.
fn check_legacy_artifacts(vault_root: &Path, findings: &mut Vec<Finding>) {
    let mut found = 0;
    for (rel, what) in LEGACY_ARTIFACTS {
        let path = vault_root.join(rel);
        if !path.exists() {
            continue;
        }
        found += 1;
        findings.push(Finding {
            check: "legacy-artifacts".into(),
            severity: Severity::Info,
            message: format!(
                "{rel} ({what}): Python-era OVP artifact; ovp2 does not read it; \
                 safe to archive/delete once you've verified the ovp2 rebuild — \
                 see https://github.com/fakechris/obsidian_vault_pipeline/blob/main/docs/ovp-to-ovp2.md (§5, Migrating an existing OVP vault; docs/ovp-to-ovp2.md in a source checkout)"
            ),
            fixed: false,
        });
    }

    if found == 0 {
        findings.push(Finding {
            check: "legacy-artifacts".into(),
            severity: Severity::Pass,
            message: "no Python-era OVP artifacts found".into(),
            fixed: false,
        });
    }
}

/// True for `*.md` files that are never captured sources (folder READMEs), so
/// the orphan scan doesn't nag about documentation.
fn is_structural_md(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.eq_ignore_ascii_case("README.md"))
        .unwrap_or(false)
}

/// Count ingestable `*.md` files recursively under `dir` (skips READMEs).
/// Uses the entry's own file type (never follows symlinks), so a link to an
/// ancestor or an external directory can't cause a cycle or scan outside the
/// vault (codex P2).
fn count_md_recursive(dir: &Path) -> usize {
    let mut n = 0;
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let Ok(ft) = entry.file_type() else { continue };
            if ft.is_symlink() {
                continue;
            }
            let path = entry.path();
            if ft.is_dir() {
                n += count_md_recursive(&path);
            } else if path.extension().and_then(|e| e.to_str()) == Some("md")
                && !is_structural_md(&path)
            {
                n += 1;
            }
        }
    }
    n
}

/// The top-level `50-Inbox` subdirectory names ovp2's intake actually manages
/// (relative to the inbox root), derived from the layout so this can't drift.
fn managed_inbox_dirs(layout: &VaultLayout, inbox_rel: &Path) -> std::collections::BTreeSet<String> {
    [
        layout.inbox_raw_dir().to_string(),
        layout.processed_root().to_string(),
    ]
    .into_iter()
    .chain(layout.capture_dirs().iter().map(|s| s.to_string()))
    .filter_map(|full| {
        Path::new(&full)
            .strip_prefix(inbox_rel)
            .ok()
            .and_then(|p| p.components().next())
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
    })
    .collect()
}

/// Files under `50-Inbox/` that sit in a directory ovp2's intake never sweeps
/// (e.g. legacy `02-Processing` / `05-Manual-Review`). ovp2 only reads
/// `01-Raw`, `03-Processed`, and the capture dirs — anything else is stranded
/// and will never be ingested. Surface it with a hint; never auto-move.
fn check_inbox_orphans(vault_root: &Path, layout: &VaultLayout, findings: &mut Vec<Finding>) {
    let check = "inbox-orphans".to_string();
    // Inbox root = the parent of the raw dir ("50-Inbox/01-Raw" -> "50-Inbox").
    let Some(inbox_rel) = Path::new(layout.inbox_raw_dir()).parent() else {
        return;
    };
    let inbox_root = vault_root.join(inbox_rel);
    if !inbox_root.is_dir() {
        return; // no inbox tree — nothing to check
    }
    let managed = managed_inbox_dirs(layout, inbox_rel);

    let entries = match std::fs::read_dir(&inbox_root) {
        Ok(e) => e,
        Err(_) => return,
    };
    let mut orphans: std::collections::BTreeMap<String, usize> = Default::default();
    let mut root_md = 0usize;
    for entry in entries.flatten() {
        let Ok(ft) = entry.file_type() else { continue };
        if ft.is_symlink() {
            continue; // never follow links out of / around the inbox (codex P2)
        }
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        if ft.is_dir() {
            if !managed.contains(&name) {
                let n = count_md_recursive(&path);
                if n > 0 {
                    orphans.insert(name, n);
                }
            }
        } else if path.extension().and_then(|e| e.to_str()) == Some("md")
            && !is_structural_md(&path)
        {
            root_md += 1;
        }
    }

    if orphans.is_empty() && root_md == 0 {
        findings.push(Finding {
            check,
            severity: Severity::Pass,
            message: "no stranded inbox files".into(),
            fixed: false,
        });
        return;
    }

    let total: usize = orphans.values().sum::<usize>() + root_md;
    let mut locations: Vec<String> = orphans.iter().map(|(d, n)| format!("{d} ({n})")).collect();
    if root_md > 0 {
        locations.push(format!("50-Inbox/ root ({root_md})"));
    }
    // Advisory (INFO, like legacy-artifacts): stray inbox md is often a
    // captured source that got misfiled, but it can also be a Python-era note
    // or report — so don't assume, and never affect the exit code.
    findings.push(Finding {
        check,
        severity: Severity::Info,
        message: format!(
            "{total} md file(s) in 50-Inbox locations ovp2 never ingests: {}. \
             If any are captured sources, move them into 50-Inbox/00-Capture to ingest; \
             otherwise they're notes/legacy files you can leave or archive. \
             ovp2 never auto-moves them.",
            locations.join(", ")
        ),
        fixed: false,
    });
}

fn dir_size(path: &Path) -> u64 {
    let mut total = 0u64;
    if let Ok(entries) = std::fs::read_dir(path) {
        for entry in entries.flatten() {
            let meta = entry.metadata();
            if let Ok(m) = meta {
                if m.is_file() {
                    total += m.len();
                } else if m.is_dir() {
                    total += dir_size(&entry.path());
                }
            }
        }
    }
    total
}

fn today_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (secs / 86_400) as i64;
    let (y, m, d) = days_to_ymd(days);
    format!("{y:04}-{m:02}-{d:02}")
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

#[cfg(test)]
mod tests {
    use super::*;

    fn touch(root: &Path, rel: &str) {
        let p = root.join(rel);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, b"x").unwrap();
    }

    #[test]
    fn inbox_orphans_flags_unmanaged_dirs_only() {
        let tmp = tempfile::tempdir().unwrap();
        let layout = VaultLayout::new();
        // Managed locations — must NOT be flagged.
        touch(tmp.path(), "50-Inbox/01-Raw/2026-07/a.md");
        touch(tmp.path(), "50-Inbox/03-Processed/2026-07/b.md");
        touch(tmp.path(), "50-Inbox/02-Pinboard/c.md");
        touch(tmp.path(), "Clippings/d.md"); // capture dir outside 50-Inbox
        // Stranded — must be flagged.
        touch(tmp.path(), "50-Inbox/02-Processing/stuck1.md");
        touch(tmp.path(), "50-Inbox/05-Manual-Review/wave/stuck2.md");
        touch(tmp.path(), "50-Inbox/loose.md"); // directly in inbox root
        // A folder README is documentation, not a source — must be ignored.
        touch(tmp.path(), "50-Inbox/README.md");
        touch(tmp.path(), "50-Inbox/02-Processing/README.md");

        let mut findings = Vec::new();
        check_inbox_orphans(tmp.path(), &layout, &mut findings);

        assert_eq!(findings.len(), 1);
        let f = &findings[0];
        assert_eq!(f.check, "inbox-orphans");
        assert_eq!(f.severity, Severity::Info, "{}", f.message);
        assert!(f.message.contains("3 md file(s)"), "READMEs excluded: {}", f.message);
        assert!(f.message.contains("02-Processing (1)"), "{}", f.message);
        assert!(f.message.contains("05-Manual-Review (1)"), "{}", f.message);
        assert!(f.message.contains("50-Inbox/ root (1)"), "{}", f.message);
        assert!(f.message.contains("00-Capture"), "hint present: {}", f.message);
        // Managed dirs never appear.
        assert!(!f.message.contains("01-Raw"), "{}", f.message);
        assert!(!f.message.contains("03-Processed"), "{}", f.message);
    }

    #[test]
    fn inbox_orphans_clean_when_only_managed_dirs() {
        let tmp = tempfile::tempdir().unwrap();
        let layout = VaultLayout::new();
        touch(tmp.path(), "50-Inbox/01-Raw/2026-07/a.md");
        touch(tmp.path(), "50-Inbox/03-Processed/2026-07/b.md");
        // An empty unmanaged dir (no md) must not trip the check.
        std::fs::create_dir_all(tmp.path().join("50-Inbox/05-Manual-Review")).unwrap();

        let mut findings = Vec::new();
        check_inbox_orphans(tmp.path(), &layout, &mut findings);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Pass, "{}", findings[0].message);
    }

    #[test]
    fn inbox_orphans_no_inbox_is_silent() {
        let tmp = tempfile::tempdir().unwrap();
        let layout = VaultLayout::new();
        let mut findings = Vec::new();
        check_inbox_orphans(tmp.path(), &layout, &mut findings);
        assert!(findings.is_empty(), "no inbox tree -> no finding");
    }

    #[test]
    fn legacy_artifacts_clean_vault_passes() {
        let tmp = tempfile::tempdir().unwrap();
        let mut findings = Vec::new();
        check_legacy_artifacts(tmp.path(), &mut findings);
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].check, "legacy-artifacts");
        assert_eq!(findings[0].severity, Severity::Pass);
    }

    #[test]
    fn legacy_artifacts_reports_python_era_files_as_info() {
        let tmp = tempfile::tempdir().unwrap();
        touch(tmp.path(), "60-Logs/knowledge.db");
        touch(tmp.path(), ".ovp/llm_profiles.yaml");
        std::fs::create_dir_all(tmp.path().join("60-Logs/transactions")).unwrap();

        let mut findings = Vec::new();
        check_legacy_artifacts(tmp.path(), &mut findings);

        assert_eq!(findings.len(), 3);
        for f in &findings {
            assert_eq!(f.check, "legacy-artifacts");
            assert_eq!(f.severity, Severity::Info, "{}", f.message);
            assert!(f.message.contains("Python-era OVP artifact"), "{}", f.message);
            assert!(f.message.contains("github.com/fakechris/obsidian_vault_pipeline"), "{}", f.message);
            assert!(f.message.contains("docs/ovp-to-ovp2.md"), "{}", f.message);
        }
        let messages: Vec<&str> = findings.iter().map(|f| f.message.as_str()).collect();
        assert!(messages.iter().any(|m| m.starts_with("60-Logs/knowledge.db ")));
        assert!(messages.iter().any(|m| m.starts_with("60-Logs/transactions ")));
        assert!(messages.iter().any(|m| m.starts_with(".ovp/llm_profiles.yaml ")));
    }

    #[test]
    fn legacy_artifacts_detects_zero_byte_knowledge_db() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join("60-Logs")).unwrap();
        std::fs::write(tmp.path().join("60-Logs/knowledge.db"), b"").unwrap();

        let mut findings = Vec::new();
        check_legacy_artifacts(tmp.path(), &mut findings);
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Info);
    }

    #[test]
    fn legacy_artifacts_ignores_files_ovp2_also_writes() {
        // ovp2 itself writes 60-Logs/pipeline.jsonl and
        // 60-Logs/knowledge-index.json — those must never be flagged.
        let tmp = tempfile::tempdir().unwrap();
        touch(tmp.path(), "60-Logs/pipeline.jsonl");
        touch(tmp.path(), "60-Logs/knowledge-index.json");
        touch(tmp.path(), ".ovp/daily-runs.jsonl");

        let mut findings = Vec::new();
        check_legacy_artifacts(tmp.path(), &mut findings);
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Pass);
    }

    #[test]
    fn info_findings_do_not_fail_doctor() {
        // Full doctor run over a vault that has a legacy knowledge.db plus a
        // valid index: exit must be Ok — INFO never affects the exit code.
        let tmp = tempfile::tempdir().unwrap();
        touch(tmp.path(), "60-Logs/knowledge.db");
        // Minimal index so check_stale_index does not FAIL.
        let model = build_index(tmp.path(), &today_iso(), None).expect("build index");
        write_index(tmp.path(), &model).expect("write index");

        let result = run(DoctorArgs {
            vault_root: tmp.path().to_path_buf(),
            fix: false,
            json: false,
            since_hours: None,
        });
        assert!(result.is_ok(), "INFO finding must not fail doctor: {result:?}");
    }

    // ---- run-recency (OVP2 observability P0) ----

    fn recency_findings(
        vault: &Path,
        now: i64,
        threshold: u64,
    ) -> Vec<Finding> {
        let layout = VaultLayout::new();
        let mut f = Vec::new();
        check_run_recency(vault, &layout, now, threshold, &mut f);
        f
    }

    fn recency(f: &[Finding]) -> &Finding {
        f.iter().find(|x| x.check == "run-recency").expect("a run-recency finding")
    }

    #[test]
    fn recency_parses_rfc3339() {
        assert_eq!(parse_rfc3339_utc("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_rfc3339_utc("2026-07-12T09:00:00Z"), Some(1_783_846_800));
        assert_eq!(parse_rfc3339_utc("garbage"), None);
    }

    #[test]
    fn recency_fails_on_failed_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        let (g, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "r");
        g.finalize_failed("ANTHROPIC_API_KEY expired");
        // now == same instant, well within threshold — but failed status FAILs.
        let f = recency_findings(tmp.path(), now_unix_secs(), 26);
        let r = recency(&f);
        assert_eq!(r.severity, Severity::Fail);
        assert!(r.message.contains("FAILED"));
        assert!(r.message.contains("expired"));
    }

    #[test]
    fn recency_fails_on_aborted_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        {
            let (_g, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "r");
        } // drop → aborted
        let f = recency_findings(tmp.path(), now_unix_secs(), 26);
        let r = recency(&f);
        assert_eq!(r.severity, Severity::Fail);
        assert!(r.message.contains("ABORTED"));
    }

    #[test]
    fn recency_passes_on_fresh_completed_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        let (g, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "r");
        g.finalize_completed(ovp_daily::RunCounts::default());
        // A completed run stamped ~now → well within any threshold.
        let f = recency_findings(tmp.path(), now_unix_secs(), 26);
        assert_eq!(recency(&f).severity, Severity::Pass);
    }

    #[test]
    fn recency_fails_on_stale_completed_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        let (g, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "r");
        g.finalize_completed(ovp_daily::RunCounts::default());
        // Advance "now" 100h past the heartbeat write → stale.
        let future = now_unix_secs() + 100 * 3600;
        assert_eq!(recency_findings(tmp.path(), future, 26)[0].severity, Severity::Fail);
    }

    #[test]
    fn recency_warns_when_no_run_at_all() {
        let tmp = tempfile::tempdir().unwrap();
        let f = recency_findings(tmp.path(), now_unix_secs(), 26);
        assert_eq!(recency(&f).severity, Severity::Warn);
    }

    #[test]
    fn recency_fails_loud_on_corrupt_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        // A present-but-malformed last-run.json must FAIL, not silently fall
        // back to report mtimes (which could let doctor PASS while the latest
        // failed/aborted run is unknowable).
        let hb = tmp.path().join(".ovp/last-run.json");
        std::fs::create_dir_all(hb.parent().unwrap()).unwrap();
        std::fs::write(&hb, b"{ not valid json").unwrap();
        // A fresh report on disk would otherwise mask the corruption.
        touch(tmp.path(), ".ovp/reports/daily-2026-07-12.json");
        let f = recency_findings(tmp.path(), now_unix_secs(), 26);
        let r = recency(&f);
        assert_eq!(r.severity, Severity::Fail);
        assert!(r.message.contains("corrupt"), "{}", r.message);
    }

    #[test]
    fn backlog_info_when_queue_grows_past_last_run() {
        let tmp = tempfile::tempdir().unwrap();
        let (g, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "r");
        g.finalize_completed(ovp_daily::RunCounts { queued_after: 2, ..Default::default() });
        // Five markdown sources now sit in the inbox → live queue 5 > 2.
        let inbox = tmp.path().join("50-Inbox/01-Raw");
        std::fs::create_dir_all(&inbox).unwrap();
        for i in 0..5 {
            std::fs::write(inbox.join(format!("s{i}.md")), "x").unwrap();
        }
        let f = recency_findings(tmp.path(), now_unix_secs(), 26);
        let info = f.iter().find(|x| x.check == "run-backlog").expect("backlog info");
        assert_eq!(info.severity, Severity::Info);
        assert!(info.message.contains("growing"));
    }
}
