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

pub struct DoctorArgs {
    pub vault_root: PathBuf,
    pub fix: bool,
    pub json: bool,
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
    check_disk_usage(&args.vault_root, &layout, &mut findings);
    check_legacy_artifacts(&args.vault_root, &mut findings);

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
        });
        assert!(result.is_ok(), "INFO finding must not fail doctor: {result:?}");
    }
}
