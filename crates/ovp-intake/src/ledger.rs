//! The append-only intake ledger (`.ovp/intake.jsonl`): one record per
//! capture-file disposition. The authority for URL/content dedup at the
//! capture boundary, and the audit trail of how every file entered the vault
//! lifecycle.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::vaultops::{append_jsonl, read_jsonl};

/// Schema tag stamped on every intake record.
pub const INTAKE_SCHEMA: &str = "ovp.intake/v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntakeAction {
    /// Normalized and moved into `50-Inbox/01-Raw/<YYYY-MM>/`.
    Ingested,
    /// Content/URL already known — parked under the duplicates dir.
    Duplicate,
    /// Parses but has too little body for the grounded reader; left in place
    /// for the operator to enrich (hash-keyed: editing the file re-evaluates).
    NeedsContent,
    /// Frontmatter does not parse; left in place for the operator to fix.
    Unparseable,
    /// The capture carries the reserved skip tag: the operator bookmarked it as
    /// a quick entry point, not as something to read. Terminal and hash-keyed —
    /// removing the tag changes the hash and re-evaluates the file.
    ///
    /// This is INTENT, which no page heuristic can recover. A measurement over
    /// the real 1448-source corpus found structural signals (sentence density,
    /// separator density) could not separate brand/gallery pages from long
    /// technical articles: a conservative threshold flagged a 73k-char
    /// knowledge-base writeup and a 46k-char CUDA article alongside the three
    /// real navigation pages. So the operator says so, and the pipeline obeys.
    Skipped,
}

/// One capture-file disposition.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IntakeRecord {
    pub schema: String,
    pub run_id: String,
    /// ISO-8601 date of the intake run.
    pub date: String,
    pub action: IntakeAction,
    /// Vault-relative path the file was found at.
    pub from: String,
    /// Vault-relative path it was moved to (Ingested / Duplicate).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// sha256 of the file bytes at disposition time.
    pub sha256: String,
    /// What it duplicates: `url:<u>` or `sha256:<h>`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dup_of: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

pub fn read_intake_ledger(path: &Path) -> Result<Vec<IntakeRecord>, String> {
    read_jsonl(path)
}

pub fn append_intake_record(path: &Path, rec: &IntakeRecord) -> Result<(), String> {
    append_jsonl(path, rec)
}

/// Content hashes that must not be ingested again: everything ever Ingested
/// or already identified as a Duplicate.
pub fn known_content_hashes(records: &[IntakeRecord]) -> HashSet<String> {
    records
        .iter()
        .filter(|r| matches!(r.action, IntakeAction::Ingested | IntakeAction::Duplicate))
        .map(|r| r.sha256.clone())
        .collect()
}

/// URLs already ingested (URL identity dedup — a re-clip of the same page
/// with slightly different bytes is still a duplicate).
pub fn known_urls(records: &[IntakeRecord]) -> HashSet<String> {
    records
        .iter()
        .filter(|r| r.action == IntakeAction::Ingested)
        .filter_map(|r| r.url.clone())
        .filter(|u| !u.is_empty())
        .collect()
}

/// Hashes previously flagged NeedsContent / Unparseable / Skipped — skipped
/// quietly on later sweeps (the record exists once; editing the file changes
/// its hash and re-evaluates it).
///
/// The three are NOT equivalent downstream: NeedsContent/Unparseable are
/// PENDING (enrichment retries them), while Skipped is TERMINAL by operator
/// decision. Callers must branch on the returned action — see
/// [`is_pending_flag`].
pub fn flagged_hashes(records: &[IntakeRecord]) -> HashMap<String, IntakeAction> {
    records
        .iter()
        .filter(|r| {
            matches!(
                r.action,
                IntakeAction::NeedsContent | IntakeAction::Unparseable | IntakeAction::Skipped
            )
        })
        .map(|r| (r.sha256.clone(), r.action))
        .collect()
}

/// Whether a flagged capture is still WAITING on something (enrichment, an
/// operator fix) rather than closed. `Skipped` is closed: re-fetching a page
/// the operator has excluded is exactly the waste this tag exists to stop.
pub fn is_pending_flag(action: IntakeAction) -> bool {
    match action {
        IntakeAction::NeedsContent | IntakeAction::Unparseable => true,
        IntakeAction::Skipped => false,
        IntakeAction::Ingested | IntakeAction::Duplicate => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(action: IntakeAction, sha: &str, url: Option<&str>) -> IntakeRecord {
        IntakeRecord {
            schema: INTAKE_SCHEMA.into(),
            run_id: "intake-test".into(),
            date: "2026-06-09".into(),
            action,
            from: "Clippings/x.md".into(),
            to: None,
            url: url.map(|s| s.to_string()),
            sha256: sha.into(),
            dup_of: None,
            title: None,
            note: None,
        }
    }

    #[test]
    fn round_trip_and_dedup_sets() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".ovp/intake.jsonl");
        append_intake_record(&path, &rec(IntakeAction::Ingested, "h1", Some("https://a"))).unwrap();
        append_intake_record(&path, &rec(IntakeAction::Duplicate, "h2", Some("https://a"))).unwrap();
        append_intake_record(&path, &rec(IntakeAction::NeedsContent, "h3", None)).unwrap();

        let records = read_intake_ledger(&path).unwrap();
        assert_eq!(records.len(), 3);

        let hashes = known_content_hashes(&records);
        assert!(hashes.contains("h1") && hashes.contains("h2"));
        assert!(!hashes.contains("h3"), "flagged files stay re-evaluable by content");

        let urls = known_urls(&records);
        assert!(urls.contains("https://a"));

        let flagged = flagged_hashes(&records);
        assert_eq!(flagged.get("h3"), Some(&IntakeAction::NeedsContent));
    }
}
