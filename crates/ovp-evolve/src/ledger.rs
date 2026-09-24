use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::types::Decision;

pub const LEDGER_SCHEMA: &str = "ovp.evolution.ledger/v1";

/// One entry in the evolution ledger.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LedgerEntry {
    pub schema: String,
    pub timestamp: String,
    pub candidate_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub git_sha: Option<String>,
    pub component: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version_from: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version_to: Option<String>,
    pub decision: Decision,
    #[serde(default)]
    pub scorecard_summary: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rollback: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lessons: Option<String>,
}

impl LedgerEntry {
    pub fn new(candidate_id: &str, component: &str, decision: Decision) -> Self {
        Self {
            schema: LEDGER_SCHEMA.into(),
            timestamp: chrono::Utc::now().to_rfc3339(),
            candidate_id: candidate_id.into(),
            git_sha: None,
            component: component.into(),
            version_from: None,
            version_to: None,
            decision,
            scorecard_summary: Value::Null,
            rollback: None,
            lessons: None,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum LedgerError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json at line {line}: {source}")]
    JsonLine {
        line: usize,
        source: serde_json::Error,
    },
}

/// Append a ledger entry to the JSONL file.
///
/// The record and its `"\n"` go out in ONE write: `writeln!` would issue two
/// and let a crash or a concurrent appender leave a malformed line. A torn
/// tail (not ending in `"\n"`) gets its own line so the new record is never
/// glued to it. Mirrors `ovp_domain::jsonl::append_line`, which this crate
/// cannot use without depending on ovp-domain. The reader stays strict: this
/// is the append-only decision record, so a torn line fails loud (AGENTS.md).
pub fn append_entry(path: &Path, entry: &LedgerEntry) -> Result<(), LedgerError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    let line = serde_json::to_string(entry).map_err(|e| LedgerError::JsonLine { line: 0, source: e })?;
    let mut buf = Vec::with_capacity(line.len() + 2);
    if !ends_with_newline_or_empty(path)? {
        buf.push(b'\n');
    }
    buf.extend_from_slice(line.as_bytes());
    buf.push(b'\n');
    file.write_all(&buf)?;
    Ok(())
}

fn ends_with_newline_or_empty(path: &Path) -> std::io::Result<bool> {
    let mut f = File::open(path)?;
    if f.metadata()?.len() == 0 {
        return Ok(true);
    }
    f.seek(SeekFrom::End(-1))?;
    let mut last = [0u8; 1];
    f.read_exact(&mut last)?;
    Ok(last[0] == b'\n')
}

/// Read all ledger entries from a JSONL file.
pub fn read_entries(path: &Path) -> Result<Vec<LedgerEntry>, LedgerError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut entries = Vec::new();
    for (idx, line) in reader.lines().enumerate() {
        let line = line?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let entry: LedgerEntry =
            serde_json::from_str(trimmed).map_err(|e| LedgerError::JsonLine {
                line: idx + 1,
                source: e,
            })?;
        entries.push(entry);
    }
    Ok(entries)
}

/// Count entries by decision type.
pub fn summary(entries: &[LedgerEntry]) -> LedgerSummary {
    let mut s = LedgerSummary::default();
    for e in entries {
        match e.decision {
            Decision::Accept => s.accepted += 1,
            Decision::Reject => s.rejected += 1,
            Decision::NeedsAblation => s.needs_ablation += 1,
            Decision::NeedsHumanReview => s.needs_review += 1,
        }
    }
    s.total = entries.len();
    s
}

#[derive(Debug, Default)]
pub struct LedgerSummary {
    pub total: usize,
    pub accepted: usize,
    pub rejected: usize,
    pub needs_ablation: usize,
    pub needs_review: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn append_and_read_roundtrip() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("ledger.jsonl");

        let entry = LedgerEntry::new("test-001", "prompt.unit_extract", Decision::Accept);
        append_entry(&path, &entry).unwrap();

        let entry2 = LedgerEntry::new("test-002", "prompt.card_synth", Decision::Reject);
        append_entry(&path, &entry2).unwrap();

        let entries = read_entries(&path).unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].candidate_id, "test-001");
        assert_eq!(entries[0].decision, Decision::Accept);
        assert_eq!(entries[1].candidate_id, "test-002");
        assert_eq!(entries[1].decision, Decision::Reject);
    }

    #[test]
    fn read_nonexistent_returns_empty() {
        let entries = read_entries(Path::new("/tmp/does-not-exist-evolution.jsonl")).unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn summary_counts() {
        let entries = vec![
            LedgerEntry::new("a", "x", Decision::Accept),
            LedgerEntry::new("b", "x", Decision::Accept),
            LedgerEntry::new("c", "x", Decision::Reject),
        ];
        let s = summary(&entries);
        assert_eq!(s.total, 3);
        assert_eq!(s.accepted, 2);
        assert_eq!(s.rejected, 1);
    }
}
