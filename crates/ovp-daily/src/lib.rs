//! M30 — the daily loop: the first blessed Rust workflow on the real vault.
//!
//! `plan_daily` scans the inbox (`50-Inbox/01-Raw` by convention), hashes every
//! markdown source, and splits new work from already-succeeded sources using the
//! durable ledger. `run_daily` drives each new source through the validated
//! reader trunk (`ovp_domain::reader::pipeline`), writes the pack to the
//! vault-local product surface (`40-Resources/Reader/`), and appends one
//! [`ledger::DailyRunRecord`] per attempt plus one `pipeline.jsonl` write-log
//! event per pack write (the `OVP_RULES.md` contract).
//!
//! Hard lines: no canonical store / evergreen / MOC / Referent / RAG; no file
//! moves out of the inbox (dedup is by content hash, so leaving sources in
//! place is idempotent); per-source failures are recorded and the loop
//! continues — only configuration errors (unreadable ledger, client factory)
//! abort the run.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use ovp_domain::reader::{run_reader_pipeline, ReaderPipelineError};
use ovp_domain::units::read_source_from_path;
use ovp_domain::VaultLayout;
use ovp_llm::ModelClient;
use sha2::{Digest, Sha256};

pub mod ledger;

pub use ledger::{
    append_daily_record, append_pipeline_event, read_daily_ledger, succeeded_hashes,
    DailyRunRecord, PipelineLogEvent, RunStatus, DAILY_SCHEMA,
};

/// Configuration for one daily run.
#[derive(Debug, Clone)]
pub struct DailyConfig {
    pub vault_root: PathBuf,
    /// ISO-8601 date stamped on records and pack directories.
    pub date: String,
    pub run_id: String,
    /// Hard cap on sources processed in one run (`OVP_RULES.md`: never call a
    /// paid LLM API in a loop without a rate limit). `0` = unlimited.
    pub max_sources: usize,
}

/// One scanned inbox source.
#[derive(Debug, Clone, PartialEq)]
pub struct DailyItem {
    pub path: PathBuf,
    /// Vault-relative path when under the vault root, else the full path.
    pub rel: String,
    /// sha256 (hex) of the file bytes.
    pub sha256: String,
}

/// The dedup-gated work plan: `todo` is new content, `skipped` already succeeded.
#[derive(Debug, Default)]
pub struct DailyWork {
    pub todo: Vec<DailyItem>,
    pub skipped: Vec<DailyItem>,
}

/// Outcome of one daily run.
#[derive(Debug, Default)]
pub struct DailyReport {
    /// One record per attempted source, in processing order (all appended to
    /// the ledger before they appear here).
    pub processed: Vec<DailyRunRecord>,
    pub skipped: usize,
    /// Sources left unprocessed because `max_sources` capped the run.
    pub capped: usize,
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

/// Scan the inbox and split sources into new work vs already-succeeded content
/// (by sha256 against the ledger). Identical content appearing under two file
/// names is planned once — the duplicate is reported as skipped.
pub fn plan_daily(
    inbox: &Path,
    vault_root: &Path,
    records: &[DailyRunRecord],
) -> Result<DailyWork, String> {
    let done = succeeded_hashes(records);
    let mut seen_this_run: HashSet<String> = HashSet::new();
    let mut work = DailyWork::default();
    for path in scan_inbox(inbox)? {
        let bytes =
            std::fs::read(&path).map_err(|e| format!("reading {}: {e}", path.display()))?;
        let sha256 = hex_sha256(&bytes);
        let rel = path
            .strip_prefix(vault_root)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| path.to_string_lossy().into_owned());
        let item = DailyItem { path, rel, sha256: sha256.clone() };
        if done.contains(&sha256) || !seen_this_run.insert(sha256) {
            work.skipped.push(item);
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
    let layout = VaultLayout::new();
    let ledger_path = cfg.vault_root.join(layout.daily_ledger());
    let log_path = cfg.vault_root.join(layout.pipeline_log());

    let (batch, capped) = if cfg.max_sources > 0 && work.todo.len() > cfg.max_sources {
        (&work.todo[..cfg.max_sources], work.todo.len() - cfg.max_sources)
    } else {
        (&work.todo[..], 0)
    };

    let mut report =
        DailyReport { processed: Vec::new(), skipped: work.skipped.len(), capped };

    for item in batch {
        let record = process_one(cfg, &layout, item, make_client)?;
        append_daily_record(&ledger_path, &record)?;
        if let Some(pack_dir) = &record.pack_dir {
            append_pipeline_event(
                &log_path,
                &PipelineLogEvent {
                    event: "reader_pack_write".into(),
                    target: pack_dir.clone(),
                    reason: format!("ovp-next daily: new source {}", item.rel),
                    date: cfg.date.clone(),
                    run_id: cfg.run_id.clone(),
                },
            )?;
        }
        report.processed.push(record);
    }
    Ok(report)
}

/// Run the reader trunk for one source. `Err` only for a client-factory
/// failure; every pipeline outcome (including failures) becomes a record.
fn process_one<F>(
    cfg: &DailyConfig,
    layout: &VaultLayout,
    item: &DailyItem,
    make_client: &mut F,
) -> Result<DailyRunRecord, String>
where
    F: FnMut() -> Result<Box<dyn ModelClient>, String>,
{
    let record = |status, pack_dir, units, cards, reason| DailyRunRecord {
        schema: DAILY_SCHEMA.into(),
        run_id: cfg.run_id.clone(),
        date: cfg.date.clone(),
        source_path: item.rel.clone(),
        source_sha256: item.sha256.clone(),
        status,
        pack_dir,
        units,
        cards,
        reason,
    };

    let source = match read_source_from_path(&item.path) {
        Ok(s) => s,
        Err(e) => {
            return Ok(record(RunStatus::Failed, None, 0, 0, Some(format!("source parse: {e}"))))
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
                run.pack.n_accepted_units,
                run.pack.n_cards,
                Some(reason),
            )),
            None => Ok(record(
                RunStatus::Succeeded,
                Some(pack_rel),
                run.pack.n_accepted_units,
                run.pack.n_cards,
                None,
            )),
        },
        Err(e @ ReaderPipelineError::Client(_))
        | Err(e @ ReaderPipelineError::TruthLayer(_))
        | Err(e @ ReaderPipelineError::Io(_)) => {
            Ok(record(RunStatus::Failed, None, 0, 0, Some(e.to_string())))
        }
    }
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    for b in digest {
        s.push_str(&format!("{b:02x}"));
    }
    s
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

    #[test]
    fn plan_skips_succeeded_and_in_run_duplicates() {
        let dir = tempfile::tempdir().unwrap();
        let inbox = dir.path().join("50-Inbox/01-Raw");
        std::fs::create_dir_all(&inbox).unwrap();
        std::fs::write(inbox.join("done.md"), "already processed").unwrap();
        std::fs::write(inbox.join("new.md"), "fresh content").unwrap();
        std::fs::write(inbox.join("new-copy.md"), "fresh content").unwrap();

        let done_hash = hex_sha256(b"already processed");
        let records = vec![DailyRunRecord {
            schema: DAILY_SCHEMA.into(),
            run_id: "r".into(),
            date: "2026-06-09".into(),
            source_path: "50-Inbox/01-Raw/done.md".into(),
            source_sha256: done_hash,
            status: RunStatus::Succeeded,
            pack_dir: None,
            units: 0,
            cards: 0,
            reason: None,
        }];

        let work = plan_daily(&inbox, dir.path(), &records).unwrap();
        assert_eq!(work.todo.len(), 1, "duplicate content planned once");
        assert_eq!(work.todo[0].rel, "50-Inbox/01-Raw/new-copy.md");
        assert_eq!(work.skipped.len(), 2);
    }

    #[test]
    fn rel_paths_are_vault_relative() {
        let dir = tempfile::tempdir().unwrap();
        let inbox = dir.path().join("50-Inbox/01-Raw");
        std::fs::create_dir_all(&inbox).unwrap();
        std::fs::write(inbox.join("x.md"), "x").unwrap();
        let work = plan_daily(&inbox, dir.path(), &[]).unwrap();
        assert_eq!(work.todo[0].rel, "50-Inbox/01-Raw/x.md");
    }
}
