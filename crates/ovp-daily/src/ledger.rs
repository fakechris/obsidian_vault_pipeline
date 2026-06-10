//! The durable daily-run ledger (`.ovp/daily-runs.jsonl`): one record per
//! source the loop ATTEMPTED. The authoritative dedup state (`succeeded`
//! hashes are skipped on later runs; failures are retried, and 3+ failures
//! block a source pending operator review) and the audit trail of what ran
//! when.
//!
//! Append-only, like the Crystal store ledger; a malformed line is a hard
//! error. JSONL primitives + the OVP_RULES write-log event live in
//! `ovp_intake::vaultops` (shared with the capture boundary) and are
//! re-exported here for compatibility.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use serde::{Deserialize, Serialize};

use ovp_intake::vaultops::{append_jsonl, read_jsonl};

pub use ovp_intake::vaultops::{append_pipeline_event, PipelineLogEvent};

/// Schema tag stamped on every daily-run record.
pub const DAILY_SCHEMA: &str = "ovp.daily/v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Succeeded,
    Failed,
}

/// One attempted source in one daily run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DailyRunRecord {
    pub schema: String,
    pub run_id: String,
    /// ISO-8601 date of the run (`YYYY-MM-DD`).
    pub date: String,
    /// Source path, vault-relative when the inbox is inside the vault.
    pub source_path: String,
    /// sha256 of the source file bytes — the dedup identity.
    pub source_sha256: String,
    pub status: RunStatus,
    /// Vault-relative reader-pack directory (present on success).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pack_dir: Option<String>,
    /// Where the lifecycle phase moved the source after success
    /// (`50-Inbox/03-Processed/<YYYY-MM>/…`). The LEDGER copy of a record is
    /// always `None` — the record is made durable BEFORE the move so a crash
    /// can never orphan a source; the run-report copy carries the actual
    /// destination (and the index reads it from there).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub moved_to: Option<String>,
    #[serde(default)]
    pub units: usize,
    #[serde(default)]
    pub cards: usize,
    /// Failure reason (present on `Failed`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// Read the full daily ledger. A missing file is an empty ledger (first run);
/// a malformed line is a hard error.
pub fn read_daily_ledger(path: &Path) -> Result<Vec<DailyRunRecord>, String> {
    read_jsonl(path)
}

/// Append one record to the daily ledger (creating parent dirs on first use).
pub fn append_daily_record(path: &Path, rec: &DailyRunRecord) -> Result<(), String> {
    append_jsonl(path, rec)
}

/// The content hashes the dedup gate skips: every source that has EVER
/// succeeded. Failed attempts stay eligible for retry.
pub fn succeeded_hashes(records: &[DailyRunRecord]) -> HashSet<String> {
    records
        .iter()
        .filter(|r| r.status == RunStatus::Succeeded)
        .map(|r| r.source_sha256.clone())
        .collect()
}

/// Failure count per content hash (for the retry cap: 3+ failures without a
/// success ⇒ blocked pending operator review).
pub fn failed_counts(records: &[DailyRunRecord]) -> HashMap<String, usize> {
    let mut counts = HashMap::new();
    for r in records.iter().filter(|r| r.status == RunStatus::Failed) {
        *counts.entry(r.source_sha256.clone()).or_insert(0) += 1;
    }
    counts
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(hash: &str, status: RunStatus) -> DailyRunRecord {
        DailyRunRecord {
            schema: DAILY_SCHEMA.into(),
            run_id: "daily-2026-06-09".into(),
            date: "2026-06-09".into(),
            source_path: "50-Inbox/01-Raw/a.md".into(),
            source_sha256: hash.into(),
            status,
            pack_dir: Some("40-Resources/Reader/x".into()),
            moved_to: None,
            units: 3,
            cards: 2,
            reason: None,
        }
    }

    #[test]
    fn missing_ledger_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read_daily_ledger(&dir.path().join("none.jsonl")).unwrap(), vec![]);
    }

    #[test]
    fn append_then_read_round_trips() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".ovp/daily-runs.jsonl");
        append_daily_record(&path, &rec("h1", RunStatus::Succeeded)).unwrap();
        append_daily_record(&path, &rec("h2", RunStatus::Failed)).unwrap();
        let got = read_daily_ledger(&path).unwrap();
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].source_sha256, "h1");
        assert_eq!(got[1].status, RunStatus::Failed);
    }

    #[test]
    fn m30_records_without_moved_to_still_parse() {
        // Backwards compatibility: ovp.daily/v1 records written before the
        // lifecycle phase existed have no `moved_to` key.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("daily-runs.jsonl");
        std::fs::write(&path, "{\"schema\":\"ovp.daily/v1\",\"run_id\":\"r\",\"date\":\"2026-06-09\",\"source_path\":\"a.md\",\"source_sha256\":\"h\",\"status\":\"succeeded\",\"units\":1,\"cards\":1}\n").unwrap();
        let got = read_daily_ledger(&path).unwrap();
        assert_eq!(got[0].moved_to, None);
    }

    #[test]
    fn malformed_line_is_a_hard_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("daily-runs.jsonl");
        std::fs::write(&path, "{not json}\n").unwrap();
        let err = read_daily_ledger(&path).unwrap_err();
        assert!(err.contains("line 1"), "got: {err}");
    }

    #[test]
    fn only_succeeded_hashes_dedup_and_failures_count() {
        let records = vec![
            rec("h1", RunStatus::Succeeded),
            rec("h2", RunStatus::Failed),
            rec("h2", RunStatus::Failed),
        ];
        let skip = succeeded_hashes(&records);
        assert!(skip.contains("h1"));
        assert!(!skip.contains("h2"), "failed sources must stay retryable");
        assert_eq!(failed_counts(&records).get("h2"), Some(&2));
    }
}
