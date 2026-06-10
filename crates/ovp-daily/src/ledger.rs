//! Durable, append-only state for the daily loop — two JSONL files in the vault:
//!
//! - `.ovp/daily-runs.jsonl` ([`DailyRunRecord`]): one record per source the loop
//!   ATTEMPTED. The authoritative dedup state (`succeeded` hashes are skipped on
//!   later runs; failures are retried) and the audit trail of what ran when.
//! - `60-Logs/pipeline.jsonl` ([`PipelineLogEvent`]): the vault-wide write log
//!   mandated by `OVP_RULES.md` — event type, target path, reason.
//!
//! Both are append-only: records are never rewritten or deleted (same contract
//! as the Crystal store ledger). A malformed ledger line is a hard error — this
//! is authoritative state, and silently skipping a line could re-run (and
//! re-bill) every source it covered.

use std::collections::HashSet;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

use serde::{Deserialize, Serialize};

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
    #[serde(default)]
    pub units: usize,
    #[serde(default)]
    pub cards: usize,
    /// Failure reason (present on `Failed`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// One `OVP_RULES.md` write-log event.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PipelineLogEvent {
    pub event: String,
    pub target: String,
    pub reason: String,
    pub date: String,
    pub run_id: String,
}

/// Read the full daily ledger. A missing file is an empty ledger (first run);
/// a malformed line is a hard error.
pub fn read_daily_ledger(path: &Path) -> Result<Vec<DailyRunRecord>, String> {
    let raw = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("reading ledger {}: {e}", path.display())),
    };
    let mut records = Vec::new();
    for (i, line) in raw.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let rec: DailyRunRecord = serde_json::from_str(line).map_err(|e| {
            format!("ledger {} line {}: malformed record: {e}", path.display(), i + 1)
        })?;
        records.push(rec);
    }
    Ok(records)
}

/// Append one record to the daily ledger (creating parent dirs on first use).
pub fn append_daily_record(path: &Path, rec: &DailyRunRecord) -> Result<(), String> {
    append_jsonl(path, rec)
}

/// Append one write-log event to `60-Logs/pipeline.jsonl`.
pub fn append_pipeline_event(path: &Path, event: &PipelineLogEvent) -> Result<(), String> {
    append_jsonl(path, event)
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

fn append_jsonl<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    let line = serde_json::to_string(value).map_err(|e| format!("serializing record: {e}"))?;
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("opening {}: {e}", path.display()))?;
    writeln!(f, "{line}").map_err(|e| format!("appending to {}: {e}", path.display()))?;
    f.flush().map_err(|e| format!("flushing {}: {e}", path.display()))
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
    fn malformed_line_is_a_hard_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("daily-runs.jsonl");
        std::fs::write(&path, "{not json}\n").unwrap();
        let err = read_daily_ledger(&path).unwrap_err();
        assert!(err.contains("line 1"), "got: {err}");
    }

    #[test]
    fn only_succeeded_hashes_dedup() {
        let records = vec![rec("h1", RunStatus::Succeeded), rec("h2", RunStatus::Failed)];
        let skip = succeeded_hashes(&records);
        assert!(skip.contains("h1"));
        assert!(!skip.contains("h2"), "failed sources must stay retryable");
    }
}
