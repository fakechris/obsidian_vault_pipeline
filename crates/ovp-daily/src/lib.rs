//! The daily loop — the blessed Rust workflow on the real vault (M30, extended
//! by M31 into the full operator cycle):
//!
//! ```text
//! capture sweep (ovp-intake) ─▶ 50-Inbox/01-Raw ─▶ plan (hash dedup + retry cap)
//!   ─▶ reader trunk per source ─▶ 40-Resources/Reader/<pack>/
//!   ─▶ lifecycle move ─▶ 50-Inbox/03-Processed/<YYYY-MM>/
//!   ─▶ durable run report (.ovp/reports/) ─▶ index + console refresh (CLI)
//! ```
//!
//! `plan_daily` scans the inbox, hashes every markdown source, and splits new
//! work from already-succeeded content (durable ledger) and from sources
//! blocked by the retry cap. `run_daily` drives each planned source through
//! the validated reader trunk, with the M31 audit-ordering invariant: the
//! OVP_RULES write-log event is appended BEFORE the success ledger record, so
//! a `succeeded` record can never exist without its write-log entry.
//!
//! Failure semantics: a per-source failure is recorded `Failed` and retried on
//! the next run; after [`MAX_FAILURES_BEFORE_BLOCKED`] failures (without a
//! success) the source is *blocked* — skipped and surfaced for operator review
//! — until `retry_blocked` is set. Only configuration errors (unreadable
//! ledger, client factory) abort a run.
//!
//! Hard lines: no canonical store / evergreen / MOC / Referent / RAG.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use ovp_domain::reader::{run_reader_pipeline, ReaderPipelineError};
use ovp_domain::units::read_source_from_path;
use ovp_domain::VaultLayout;
use ovp_intake::vaultops::{hex_sha256, rel_to, safe_move};
use ovp_llm::ModelClient;

pub mod heartbeat;
pub mod ledger;
pub mod report;

pub use heartbeat::{
    read_last_run, write_last_run, HeartbeatGuard, LastRun, LastRunStatus, RunCounts,
    LAST_RUN_SCHEMA,
};
pub use ledger::{
    append_daily_record, append_pipeline_event, failed_counts, read_daily_ledger,
    succeeded_hashes, DailyRunRecord, PipelineLogEvent, RunStatus, DAILY_SCHEMA,
};
pub use report::{
    write_run_report, IntakeSummary, PinboardSummary, ReaderSummary, RunReport,
    RUN_REPORT_SCHEMA,
};

/// After this many failures without a success a source stops being retried
/// automatically (operator review; `--retry-blocked` overrides). Mirrors the
/// "stop after 3 attempts and reassess" working rule.
pub const MAX_FAILURES_BEFORE_BLOCKED: usize = 3;

/// Configuration for one daily run.
#[derive(Debug, Clone)]
pub struct DailyConfig {
    pub vault_root: PathBuf,
    /// ISO-8601 date stamped on records and pack directories.
    pub date: String,
    pub run_id: String,
    /// Hard cap on sources processed in one run (OVP_RULES: never call a paid
    /// LLM API in a loop without a rate limit). `0` = unlimited.
    pub max_sources: usize,
    /// Move a source to `50-Inbox/03-Processed/<YYYY-MM>/` after it succeeds.
    pub lifecycle_move: bool,
    /// Also plan sources blocked by the retry cap.
    pub retry_blocked: bool,
}

/// One scanned inbox source.
#[derive(Debug, Clone, PartialEq)]
pub struct DailyItem {
    pub path: PathBuf,
    /// Vault-relative path when under the vault root, else the full path.
    pub rel: String,
    /// sha256 (hex) of the file bytes.
    pub sha256: String,
    /// Failures recorded so far for this content (drives the retry cap).
    pub prior_failures: usize,
}

/// The dedup-gated work plan.
#[derive(Debug, Default)]
pub struct DailyWork {
    pub todo: Vec<DailyItem>,
    /// Already succeeded (or duplicate content within this scan).
    pub skipped: Vec<DailyItem>,
    /// Hit the retry cap; needs operator review (or `retry_blocked`).
    pub blocked: Vec<DailyItem>,
}

/// Outcome of one daily run.
#[derive(Debug, Default)]
pub struct DailyReport {
    /// One record per attempted source, in processing order (all appended to
    /// the ledger before they appear here).
    pub processed: Vec<DailyRunRecord>,
    pub skipped: usize,
    pub blocked: usize,
    /// Sources left unprocessed because `max_sources` capped the run.
    pub capped: usize,
    /// Non-fatal lifecycle problems (e.g. processed-move failed; the pack is
    /// still the product, so the run record stays `Succeeded`).
    pub lifecycle_warnings: Vec<String>,
}

impl DailyReport {
    pub fn failed(&self) -> usize {
        self.processed.iter().filter(|r| r.status == RunStatus::Failed).count()
    }
}

/// Recursively collect `.md` files under `inbox` (skipping dot-files and
/// dot-directories), sorted for deterministic processing order. A missing
/// inbox directory is a configuration error, not an empty plan.
pub fn scan_inbox(inbox: &Path) -> Result<Vec<PathBuf>, String> {
    if !inbox.is_dir() {
        return Err(format!("inbox directory not found: {}", inbox.display()));
    }
    let mut found = Vec::new();
    walk(inbox, &mut found)?;
    found.sort();
    Ok(found)
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries =
        std::fs::read_dir(dir).map_err(|e| format!("reading {}: {e}", dir.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("reading {}: {e}", dir.display()))?;
        let path = entry.path();
        let name = entry.file_name();
        if name.to_string_lossy().starts_with('.') {
            continue;
        }
        if path.is_dir() {
            walk(&path, out)?;
        } else if path.extension().is_some_and(|e| e == "md") {
            out.push(path);
        }
    }
    Ok(())
}

/// Scan the inbox and split sources into new work, skipped (succeeded /
/// duplicate content), and blocked (retry cap). `retry_blocked` folds blocked
/// sources back into `todo`.
pub fn plan_daily(
    inbox: &Path,
    vault_root: &Path,
    records: &[DailyRunRecord],
    retry_blocked: bool,
) -> Result<DailyWork, String> {
    let done = succeeded_hashes(records);
    let failures = failed_counts(records);
    let mut seen_this_run: HashSet<String> = HashSet::new();
    let mut work = DailyWork::default();
    for path in scan_inbox(inbox)? {
        let bytes =
            std::fs::read(&path).map_err(|e| format!("reading {}: {e}", path.display()))?;
        let sha256 = hex_sha256(&bytes);
        let rel = rel_to(vault_root, &path);
        let prior_failures = failures.get(&sha256).copied().unwrap_or(0);
        let item = DailyItem { path, rel, sha256: sha256.clone(), prior_failures };
        if done.contains(&sha256) || !seen_this_run.insert(sha256) {
            work.skipped.push(item);
        } else if prior_failures >= MAX_FAILURES_BEFORE_BLOCKED && !retry_blocked {
            work.blocked.push(item);
        } else {
            work.todo.push(item);
        }
    }
    Ok(work)
}

/// Process the planned work. `make_client` builds one `ModelClient` per stage
/// (base / critic / cards — three per source); a factory error is a
/// configuration problem and aborts the run, while a per-source pipeline
/// failure is appended to the ledger as `Failed` and the loop continues.
pub fn run_daily<F>(
    cfg: &DailyConfig,
    work: &DailyWork,
    make_client: &mut F,
) -> Result<DailyReport, String>
where
    F: FnMut() -> Result<Box<dyn ModelClient>, String>,
{
    run_daily_with_progress(cfg, work, make_client, &mut |_| {})
}

/// [`run_daily`] with a live progress hook: `on_source` is invoked exactly once
/// per attempted source — succeeded or failed — immediately after that source
/// finishes (record durable, lifecycle move done) and BEFORE the next source
/// starts. A reader batch can run for hours; without this hook the caller has
/// nothing to show the operator until the whole run returns.
pub fn run_daily_with_progress<F>(
    cfg: &DailyConfig,
    work: &DailyWork,
    make_client: &mut F,
    on_source: &mut dyn FnMut(&DailyRunRecord),
) -> Result<DailyReport, String>
where
    F: FnMut() -> Result<Box<dyn ModelClient>, String>,
{
    let layout = VaultLayout::new();
    let ledger_path = cfg.vault_root.join(layout.daily_ledger());
    let log_path = cfg.vault_root.join(layout.pipeline_log());

    let (batch, capped) = if cfg.max_sources > 0 && work.todo.len() > cfg.max_sources {
        (&work.todo[..cfg.max_sources], work.todo.len() - cfg.max_sources)
    } else {
        (&work.todo[..], 0)
    };

    let mut daily_report = DailyReport {
        processed: Vec::new(),
        skipped: work.skipped.len(),
        blocked: work.blocked.len(),
        capped,
        lifecycle_warnings: Vec::new(),
    };

    for item in batch {
        let mut record = process_one(cfg, &layout, item, make_client, &log_path)?;
        // The Succeeded record is made durable BEFORE the lifecycle move: a
        // crash after this point leaves (at worst) a processed file lingering
        // in 01-Raw, which the dedup gate skips forever — never an orphaned
        // source in 03-Processed without a record.
        append_daily_record(&ledger_path, &record)?;
        if record.status == RunStatus::Succeeded && cfg.lifecycle_move {
            record.moved_to = move_to_processed(cfg, &layout, item, &log_path,
                &mut daily_report.lifecycle_warnings);
        }
        on_source(&record);
        daily_report.processed.push(record);
    }
    Ok(daily_report)
}

/// Run the reader trunk for one source. `Err` only for a client-factory or
/// audit-log failure; every pipeline outcome (including failures) becomes a
/// record.
///
/// Audit ordering on success (M31): pack written → `reader_pack_write` event →
/// (caller appends the Succeeded ledger record) → lifecycle move →
/// `source_processed_move` event. A success record therefore always implies
/// its pack-write log entry exists, and the source can never be moved out of
/// the queue without a durable record: a crash at any point either re-runs the
/// source (still in 01-Raw, no record) or leaves a recorded success whose file
/// lingers in 01-Raw (dedup-skipped forever, harmless).
fn process_one<F>(
    cfg: &DailyConfig,
    layout: &VaultLayout,
    item: &DailyItem,
    make_client: &mut F,
    log_path: &Path,
) -> Result<DailyRunRecord, String>
where
    F: FnMut() -> Result<Box<dyn ModelClient>, String>,
{
    let record = |status, pack_dir, moved_to, units, cards, reason| DailyRunRecord {
        schema: DAILY_SCHEMA.into(),
        run_id: cfg.run_id.clone(),
        date: cfg.date.clone(),
        source_path: item.rel.clone(),
        source_sha256: item.sha256.clone(),
        status,
        pack_dir,
        moved_to,
        units,
        cards,
        reason,
    };

    // Guard the plan→run window: a reader batch can take minutes, and the
    // vault is live (Obsidian, sync). If the bytes changed since planning,
    // the plan-time sha256 would mis-attribute the pack and poison dedup —
    // record a retryable failure instead (the next plan picks up the new hash).
    match std::fs::read(&item.path) {
        Ok(bytes) if hex_sha256(&bytes) != item.sha256 => {
            return Ok(record(RunStatus::Failed, None, None, 0, 0,
                Some("source changed since plan (sha256 mismatch); requeued under its new content".into())));
        }
        Err(e) => {
            return Ok(record(RunStatus::Failed, None, None, 0, 0,
                Some(format!("source unreadable since plan: {e}"))))
        }
        Ok(_) => {}
    }

    let source = match read_source_from_path(&item.path) {
        Ok(s) => s,
        Err(e) => {
            return Ok(record(RunStatus::Failed, None, None, 0, 0,
                Some(format!("source parse: {e}"))))
        }
    };

    let hash8 = &item.sha256[..8];
    let pack_rel = layout.reader_pack_dir(&cfg.date, &source.title, hash8);
    let out_dir = cfg.vault_root.join(&pack_rel);

    let mut base = make_client()?;
    let mut critic = make_client()?;
    let mut cards = make_client()?;

    match run_reader_pipeline(&source, base.as_mut(), critic.as_mut(), cards.as_mut(), &out_dir) {
        Ok(run) => match run.card_failure {
            // Pack + audit artifacts are on disk, but the run is not a success:
            // record Failed (no pack_dir → no write-log event) so the source is
            // retried on the next run.
            Some(reason) => Ok(record(
                RunStatus::Failed,
                None,
                None,
                run.pack.n_accepted_units,
                run.pack.n_cards,
                Some(reason),
            )),
            None => {
                // Write-log FIRST (the success record must imply it exists).
                append_pipeline_event(log_path, &PipelineLogEvent {
                    event_type: "reader_pack_write".into(),
                    target: pack_rel.clone(),
                    reason: format!("ovp2 daily: new source {}", item.rel),
                    date: cfg.date.clone(),
                    run_id: cfg.run_id.clone(),
                })?;
                // The lifecycle move happens in run_daily AFTER this record is
                // durable; `moved_to` is populated on the report copy only.
                Ok(record(
                    RunStatus::Succeeded,
                    Some(pack_rel),
                    None,
                    run.pack.n_accepted_units,
                    run.pack.n_cards,
                    None,
                ))
            }
        },
        Err(e @ ReaderPipelineError::Client(_))
        | Err(e @ ReaderPipelineError::TruthLayer(_))
        | Err(e @ ReaderPipelineError::Io(_)) => {
            Ok(record(RunStatus::Failed, None, None, 0, 0, Some(e.to_string())))
        }
    }
}

/// Move a succeeded source `01-Raw → 03-Processed/<YYYY-MM>/` (keeping its
/// filename), logging the move. Runs strictly AFTER the Succeeded ledger
/// record is durable, so every problem here — the move itself or its log
/// append — is a WARNING, never a run failure or an abort: the pack is the
/// product, the record exists, and a leftover raw file is dedup-skipped on
/// every future scan.
fn move_to_processed(
    cfg: &DailyConfig,
    layout: &VaultLayout,
    item: &DailyItem,
    log_path: &Path,
    warnings: &mut Vec<String>,
) -> Option<String> {
    let month = cfg.date.get(..7).unwrap_or(&cfg.date);
    let file_name = item
        .path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "source.md".into());
    let target = cfg.vault_root.join(layout.processed_dir(month)).join(file_name);
    match safe_move(&item.path, &target) {
        Ok(actual) => {
            let to_rel = rel_to(&cfg.vault_root, &actual);
            if let Err(e) = append_pipeline_event(log_path, &PipelineLogEvent {
                event_type: "source_processed_move".into(),
                target: to_rel.clone(),
                reason: format!("ovp2 daily: source {} processed", item.rel),
                date: cfg.date.clone(),
                run_id: cfg.run_id.clone(),
            }) {
                warnings.push(format!("move of {} succeeded but logging it failed: {e}", item.rel));
            }
            Some(to_rel)
        }
        Err(e) => {
            warnings.push(format!("lifecycle move failed for {}: {e}", item.rel));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_filters_sorts_and_skips_hidden() {
        let dir = tempfile::tempdir().unwrap();
        let inbox = dir.path().join("inbox");
        std::fs::create_dir_all(inbox.join("sub")).unwrap();
        std::fs::create_dir_all(inbox.join(".hidden")).unwrap();
        std::fs::write(inbox.join("b.md"), "b").unwrap();
        std::fs::write(inbox.join("a.md"), "a").unwrap();
        std::fs::write(inbox.join("sub/c.md"), "c").unwrap();
        std::fs::write(inbox.join("image.png"), "x").unwrap();
        std::fs::write(inbox.join(".hidden/d.md"), "d").unwrap();
        std::fs::write(inbox.join(".dot.md"), "d").unwrap();
        let got = scan_inbox(&inbox).unwrap();
        let names: Vec<_> = got
            .iter()
            .map(|p| p.strip_prefix(&inbox).unwrap().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, vec!["a.md", "b.md", "sub/c.md"]);
    }

    #[test]
    fn missing_inbox_fails_loud() {
        let dir = tempfile::tempdir().unwrap();
        assert!(scan_inbox(&dir.path().join("nope")).is_err());
    }

    fn ledger_record(hash: &str, status: RunStatus) -> DailyRunRecord {
        DailyRunRecord {
            schema: DAILY_SCHEMA.into(),
            run_id: "r".into(),
            date: "2026-06-09".into(),
            source_path: "x".into(),
            source_sha256: hash.into(),
            status,
            pack_dir: None,
            moved_to: None,
            units: 0,
            cards: 0,
            reason: None,
        }
    }

    #[test]
    fn plan_skips_succeeded_and_in_run_duplicates() {
        let dir = tempfile::tempdir().unwrap();
        let inbox = dir.path().join("50-Inbox/01-Raw");
        std::fs::create_dir_all(&inbox).unwrap();
        std::fs::write(inbox.join("done.md"), "already processed").unwrap();
        std::fs::write(inbox.join("new.md"), "fresh content").unwrap();
        std::fs::write(inbox.join("new-copy.md"), "fresh content").unwrap();

        let records =
            vec![ledger_record(&hex_sha256(b"already processed"), RunStatus::Succeeded)];
        let work = plan_daily(&inbox, dir.path(), &records, false).unwrap();
        assert_eq!(work.todo.len(), 1, "duplicate content planned once");
        assert_eq!(work.todo[0].rel, "50-Inbox/01-Raw/new-copy.md");
        assert_eq!(work.skipped.len(), 2);
    }

    #[test]
    fn retry_cap_blocks_after_three_failures_unless_overridden() {
        let dir = tempfile::tempdir().unwrap();
        let inbox = dir.path().join("50-Inbox/01-Raw");
        std::fs::create_dir_all(&inbox).unwrap();
        std::fs::write(inbox.join("flaky.md"), "flaky content").unwrap();
        let h = hex_sha256(b"flaky content");

        let two = vec![
            ledger_record(&h, RunStatus::Failed),
            ledger_record(&h, RunStatus::Failed),
        ];
        let work = plan_daily(&inbox, dir.path(), &two, false).unwrap();
        assert_eq!(work.todo.len(), 1, "2 failures still retried");
        assert_eq!(work.todo[0].prior_failures, 2);

        let three = vec![
            ledger_record(&h, RunStatus::Failed),
            ledger_record(&h, RunStatus::Failed),
            ledger_record(&h, RunStatus::Failed),
        ];
        let work = plan_daily(&inbox, dir.path(), &three, false).unwrap();
        assert!(work.todo.is_empty());
        assert_eq!(work.blocked.len(), 1, "3 failures ⇒ blocked");

        let work = plan_daily(&inbox, dir.path(), &three, true).unwrap();
        assert_eq!(work.todo.len(), 1, "--retry-blocked folds it back in");
    }

    #[test]
    fn rel_paths_are_vault_relative() {
        let dir = tempfile::tempdir().unwrap();
        let inbox = dir.path().join("50-Inbox/01-Raw");
        std::fs::create_dir_all(&inbox).unwrap();
        std::fs::write(inbox.join("x.md"), "x").unwrap();
        let work = plan_daily(&inbox, dir.path(), &[], false).unwrap();
        assert_eq!(work.todo[0].rel, "50-Inbox/01-Raw/x.md");
    }
}
