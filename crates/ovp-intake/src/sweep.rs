//! The capture sweep: normalize every markdown file sitting in the capture
//! directories (`Clippings/`, `50-Inbox/00-Capture`, `50-Inbox/02-Pinboard`)
//! into the raw inbox the daily loop reads (`50-Inbox/01-Raw/<YYYY-MM>/`),
//! deduplicating by URL and content hash on the way.
//!
//! Dispositions (each one intake-ledger record + write-log event where a file
//! moved):
//! - **Ingested** — new content with enough body for the grounded reader →
//!   moved to `01-Raw/<YYYY-MM>/<date>_<title>-<hash8>.md`.
//! - **Duplicate** — content hash or URL already known → parked under
//!   `50-Inbox/03-Processed/duplicates/<YYYY-MM>/` (moved, never deleted).
//! - **NeedsContent** — parses but body is too thin to read (e.g. a bare
//!   pinboard bookmark) → left in place; flagged once per content hash.
//! - **Unparseable** — frontmatter YAML fails to parse → left in place;
//!   flagged once per content hash.
//!
//! Ordering invariant (audit): move FIRST, then the write-log event, then the
//! intake-ledger record — a ledger record always implies its event exists and
//! the move really happened.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use ovp_domain::units::read_source_from_path;
use ovp_domain::VaultLayout;

use crate::ledger::{
    append_intake_record, flagged_hashes, known_content_hashes, known_urls, read_intake_ledger,
    IntakeAction, IntakeRecord, INTAKE_SCHEMA,
};
use crate::vaultops::{append_pipeline_event, hex_sha256, rel_to, safe_move, PipelineLogEvent};

/// Minimum body size (chars) for a capture to be worth a grounded-reader run.
/// Below this it is flagged `needs_content` and left for the operator to
/// enrich (typical case: a bare pinboard bookmark with a one-line note).
pub const MIN_READER_BODY_CHARS: usize = 200;

#[derive(Debug, Clone)]
pub struct IntakeConfig {
    pub vault_root: PathBuf,
    /// ISO-8601 date stamped on records and used for fallback naming.
    pub date: String,
    pub run_id: String,
    pub min_reader_body_chars: usize,
}

impl IntakeConfig {
    pub fn new(vault_root: PathBuf, date: String, run_id: String) -> Self {
        Self { vault_root, date, run_id, min_reader_body_chars: MIN_READER_BODY_CHARS }
    }
}

/// Outcome of one sweep. Every vec holds the records APPENDED this run
/// (dry-run: the records that WOULD be appended).
#[derive(Debug, Default)]
pub struct SweepOutcome {
    pub ingested: Vec<IntakeRecord>,
    pub duplicates: Vec<IntakeRecord>,
    pub needs_content: Vec<IntakeRecord>,
    pub unparseable: Vec<IntakeRecord>,
    /// Files whose hash was already flagged needs_content/unparseable on an
    /// earlier sweep — still sitting in a capture dir, skipped quietly.
    pub already_flagged: usize,
    pub dry_run: bool,
}

impl SweepOutcome {
    pub fn total_new_records(&self) -> usize {
        self.ingested.len() + self.duplicates.len() + self.needs_content.len()
            + self.unparseable.len()
    }
}

/// Sweep all capture dirs. `extra_known_hashes` lets the caller fold in other
/// dedup authorities (the daily ledger's succeeded set — content the reader
/// already processed must never be re-ingested).
pub fn sweep_intake(
    cfg: &IntakeConfig,
    extra_known_hashes: &HashSet<String>,
    dry_run: bool,
) -> Result<SweepOutcome, String> {
    let layout = VaultLayout::new();
    let ledger_path = cfg.vault_root.join(layout.intake_ledger());
    let log_path = cfg.vault_root.join(layout.pipeline_log());

    let existing = read_intake_ledger(&ledger_path)?;
    let mut known_hashes = known_content_hashes(&existing);
    known_hashes.extend(extra_known_hashes.iter().cloned());
    let mut urls = known_urls(&existing);
    let flagged = flagged_hashes(&existing);

    let mut outcome = SweepOutcome { dry_run, ..Default::default() };

    for dir_rel in layout.capture_dirs() {
        let dir = cfg.vault_root.join(dir_rel);
        if !dir.is_dir() {
            continue; // a vault without e.g. Clippings/ is fine
        }
        for path in collect_markdown(&dir)? {
            let bytes =
                std::fs::read(&path).map_err(|e| format!("reading {}: {e}", path.display()))?;
            let sha256 = hex_sha256(&bytes);
            let from = rel_to(&cfg.vault_root, &path);

            if flagged.contains_key(&sha256) {
                outcome.already_flagged += 1;
                continue;
            }
            if known_hashes.contains(&sha256) {
                let rec = dispose_duplicate(
                    cfg, &layout, &path, &from, &sha256,
                    format!("sha256:{sha256}"),
                    &ledger_path, &log_path, dry_run,
                )?;
                outcome.duplicates.push(rec);
                continue;
            }

            let source = match read_source_from_path(&path) {
                Ok(s) => s,
                Err(e) => {
                    let rec = record(cfg, IntakeAction::Unparseable, &from, None, None, &sha256,
                        None, Some(format!("parse: {e}")));
                    if !dry_run {
                        append_intake_record(&ledger_path, &rec)?;
                    }
                    outcome.unparseable.push(rec);
                    continue;
                }
            };

            let url = (!source.source_url.is_empty()).then(|| source.source_url.clone());
            if let Some(u) = &url {
                if urls.contains(u) {
                    let rec = dispose_duplicate(
                        cfg, &layout, &path, &from, &sha256,
                        format!("url:{u}"),
                        &ledger_path, &log_path, dry_run,
                    )?;
                    outcome.duplicates.push(rec);
                    continue;
                }
            }

            let body_chars = source.body_markdown.trim().chars().count();
            if body_chars < cfg.min_reader_body_chars {
                let rec = record(cfg, IntakeAction::NeedsContent, &from, None, url, &sha256,
                    Some(source.title.clone()),
                    Some(format!("body {body_chars} chars < {}", cfg.min_reader_body_chars)));
                if !dry_run {
                    append_intake_record(&ledger_path, &rec)?;
                }
                outcome.needs_content.push(rec);
                continue;
            }

            // Ingest: normalize name, move into 01-Raw/<YYYY-MM>/.
            let date_for_name = pick_date(source.published.as_deref(), &cfg.date);
            let month = date_for_name.get(..7).unwrap_or(&cfg.date);
            let name = layout.normalized_source_name(date_for_name, &source.title, &sha256[..8]);
            let target = cfg
                .vault_root
                .join(layout.inbox_raw_dir())
                .join(month)
                .join(&name);

            let to_rel;
            if dry_run {
                to_rel = rel_to(&cfg.vault_root, &target);
            } else {
                let actual = safe_move(&path, &target)?;
                to_rel = rel_to(&cfg.vault_root, &actual);
                append_pipeline_event(&log_path, &PipelineLogEvent {
                    event_type: "intake_move".into(),
                    target: to_rel.clone(),
                    reason: format!("ovp2 intake: normalized capture {from}"),
                    date: cfg.date.clone(),
                    run_id: cfg.run_id.clone(),
                })?;
            }
            let rec = record(cfg, IntakeAction::Ingested, &from, Some(to_rel), url.clone(),
                &sha256, Some(source.title.clone()), None);
            if !dry_run {
                append_intake_record(&ledger_path, &rec)?;
            }
            known_hashes.insert(sha256);
            if let Some(u) = url {
                urls.insert(u);
            }
            outcome.ingested.push(rec);
        }
    }
    Ok(outcome)
}

#[allow(clippy::too_many_arguments)]
fn dispose_duplicate(
    cfg: &IntakeConfig,
    layout: &VaultLayout,
    path: &Path,
    from: &str,
    sha256: &str,
    dup_of: String,
    ledger_path: &Path,
    log_path: &Path,
    dry_run: bool,
) -> Result<IntakeRecord, String> {
    let month = cfg.date.get(..7).unwrap_or(&cfg.date);
    let file_name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "duplicate.md".into());
    let target = cfg.vault_root.join(layout.duplicates_dir(month)).join(file_name);

    let to_rel;
    if dry_run {
        to_rel = rel_to(&cfg.vault_root, &target);
    } else {
        let actual = safe_move(path, &target)?;
        to_rel = rel_to(&cfg.vault_root, &actual);
        append_pipeline_event(log_path, &PipelineLogEvent {
            event_type: "intake_duplicate_move".into(),
            target: to_rel.clone(),
            reason: format!("ovp2 intake: duplicate of {dup_of} (was {from})"),
            date: cfg.date.clone(),
            run_id: cfg.run_id.clone(),
        })?;
    }
    let mut rec = record(cfg, IntakeAction::Duplicate, from, Some(to_rel), None, sha256, None, None);
    rec.dup_of = Some(dup_of);
    if !dry_run {
        append_intake_record(ledger_path, &rec)?;
    }
    Ok(rec)
}

#[allow(clippy::too_many_arguments)]
fn record(
    cfg: &IntakeConfig,
    action: IntakeAction,
    from: &str,
    to: Option<String>,
    url: Option<String>,
    sha256: &str,
    title: Option<String>,
    note: Option<String>,
) -> IntakeRecord {
    IntakeRecord {
        schema: INTAKE_SCHEMA.into(),
        run_id: cfg.run_id.clone(),
        date: cfg.date.clone(),
        action,
        from: from.to_string(),
        to,
        url,
        sha256: sha256.to_string(),
        dup_of: None,
        title,
        note,
    }
}

/// Use the source's `published:` date when it looks like an ISO date,
/// otherwise the run date.
fn pick_date<'a>(published: Option<&'a str>, fallback: &'a str) -> &'a str {
    match published {
        Some(p) if looks_like_iso_date(p) => &p[..10],
        _ => fallback,
    }
}

fn looks_like_iso_date(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() >= 10
        && b[..10]
            .iter()
            .enumerate()
            .all(|(i, c)| if i == 4 || i == 7 { *c == b'-' } else { c.is_ascii_digit() })
}

/// Recursively collect `.md` files under `dir` (skipping dot-entries),
/// sorted for deterministic processing order.
fn collect_markdown(dir: &Path) -> Result<Vec<PathBuf>, String> {
    let mut found = Vec::new();
    walk(dir, &mut found)?;
    found.sort();
    Ok(found)
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries =
        std::fs::read_dir(dir).map_err(|e| format!("reading {}: {e}", dir.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("reading {}: {e}", dir.display()))?;
        let path = entry.path();
        if entry.file_name().to_string_lossy().starts_with('.') {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_date_detection() {
        assert!(looks_like_iso_date("2026-06-01"));
        assert!(looks_like_iso_date("2026-06-01T10:00:00Z"));
        assert!(!looks_like_iso_date("yesterday"));
        assert!(!looks_like_iso_date("2026/06/01"));
        assert!(!looks_like_iso_date(""));
    }

    #[test]
    fn pick_date_prefers_valid_published() {
        assert_eq!(pick_date(Some("2026-05-30"), "2026-06-09"), "2026-05-30");
        assert_eq!(pick_date(Some("2026-05-30T01:02:03Z"), "2026-06-09"), "2026-05-30");
        assert_eq!(pick_date(Some("not a date"), "2026-06-09"), "2026-06-09");
        assert_eq!(pick_date(None, "2026-06-09"), "2026-06-09");
    }
}
