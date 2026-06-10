//! The durable per-run report (`.ovp/reports/<run_id>.json`): one JSON
//! document per daily run covering every phase — capture, plan, reader,
//! lifecycle, index/console refresh. The ledgers stay the per-source audit
//! authority; the report is the run-level view the console and the operator
//! read.

use std::path::Path;

use serde::{Deserialize, Serialize};

use ovp_domain::VaultLayout;
use ovp_intake::vaultops::{rel_to, write_new};
use ovp_intake::{PinboardSyncOutcome, SweepOutcome};

use crate::{DailyReport, DailyRunRecord};

pub const RUN_REPORT_SCHEMA: &str = "ovp.daily.run-report/v1";

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct IntakeSummary {
    pub ingested: usize,
    pub duplicates: usize,
    pub needs_content: usize,
    pub unparseable: usize,
    pub already_flagged: usize,
}

impl From<&SweepOutcome> for IntakeSummary {
    fn from(o: &SweepOutcome) -> Self {
        Self {
            ingested: o.ingested.len(),
            duplicates: o.duplicates.len(),
            needs_content: o.needs_content.len(),
            unparseable: o.unparseable.len(),
            already_flagged: o.already_flagged,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct PinboardSummary {
    pub origin: String,
    pub fetched: usize,
    pub new_notes: usize,
    pub skipped_known: usize,
}

impl From<&PinboardSyncOutcome> for PinboardSummary {
    fn from(o: &PinboardSyncOutcome) -> Self {
        Self {
            origin: o.origin.clone(),
            fetched: o.fetched,
            new_notes: o.new_notes.len(),
            skipped_known: o.skipped_known,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ReaderSummary {
    pub planned: usize,
    pub processed: usize,
    pub succeeded: usize,
    pub failed: usize,
    pub skipped: usize,
    pub blocked: usize,
    pub capped: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunReport {
    pub schema: String,
    pub run_id: String,
    pub date: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pinboard: Option<PinboardSummary>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub intake: Option<IntakeSummary>,
    pub reader: ReaderSummary,
    /// This run's per-source attempts (the same records appended to the
    /// daily ledger).
    pub records: Vec<DailyRunRecord>,
    #[serde(default)]
    pub lifecycle_warnings: Vec<String>,
    /// Vault-relative paths of the refreshed read model / console, when those
    /// phases ran.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index_file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub console_file: Option<String>,
}

impl RunReport {
    pub fn new(run_id: &str, date: &str) -> Self {
        Self {
            schema: RUN_REPORT_SCHEMA.into(),
            run_id: run_id.into(),
            date: date.into(),
            pinboard: None,
            intake: None,
            reader: ReaderSummary::default(),
            records: Vec::new(),
            lifecycle_warnings: Vec::new(),
            index_file: None,
            console_file: None,
        }
    }

    pub fn set_reader(&mut self, planned: usize, daily: &DailyReport) {
        self.reader = ReaderSummary {
            planned,
            processed: daily.processed.len(),
            succeeded: daily.processed.len() - daily.failed(),
            failed: daily.failed(),
            skipped: daily.skipped,
            blocked: daily.blocked,
            capped: daily.capped,
        };
        self.records = daily.processed.clone();
        self.lifecycle_warnings = daily.lifecycle_warnings.clone();
    }
}

/// Persist the report under `.ovp/reports/<run_id>.json` (collision-suffixed,
/// never overwritten). Returns the vault-relative path written.
pub fn write_run_report(vault_root: &Path, report: &RunReport) -> Result<String, String> {
    let layout = VaultLayout::new();
    let target = vault_root
        .join(layout.reports_dir())
        .join(format!("{}.json", report.run_id));
    let body = serde_json::to_string_pretty(report)
        .map_err(|e| format!("serializing run report: {e}"))?;
    let actual = write_new(&target, &format!("{body}\n"))?;
    Ok(rel_to(vault_root, &actual))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_round_trips_and_never_overwrites() {
        let dir = tempfile::tempdir().unwrap();
        let mut report = RunReport::new("daily-2026-06-09", "2026-06-09");
        report.intake = Some(IntakeSummary { ingested: 2, ..Default::default() });

        let rel = write_run_report(dir.path(), &report).unwrap();
        assert_eq!(rel, ".ovp/reports/daily-2026-06-09.json");
        let raw = std::fs::read_to_string(dir.path().join(&rel)).unwrap();
        let parsed: RunReport = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed.schema, RUN_REPORT_SCHEMA);
        assert_eq!(parsed.intake.unwrap().ingested, 2);

        let rel2 = write_run_report(dir.path(), &report).unwrap();
        assert_eq!(rel2, ".ovp/reports/daily-2026-06-09 -2.json", "append-only reports");
    }
}
