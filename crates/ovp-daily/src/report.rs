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
    /// Sources closed as index-only (worth gate) — no `$` reader trunk.
    #[serde(default)]
    pub index_only: usize,
    /// Sources that actually ran the reader trunk (units/cards path).
    #[serde(default)]
    pub reader_ran: usize,
}

/// Per-run cost / thrift snapshot derived from daily records (0-cost).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct CostReport {
    pub attempted: usize,
    pub reader_ran: usize,
    pub index_only: usize,
    pub failed: usize,
    pub total_units: usize,
    pub total_cards: usize,
    /// Reasons for index-only closeouts (`not_worth:*` codes).
    #[serde(default)]
    pub index_only_reasons: std::collections::BTreeMap<String, usize>,
}

impl CostReport {
    /// Summarize thrift/cost from per-source daily records.
    pub fn from_records(records: &[DailyRunRecord]) -> Self {
        use crate::RunStatus;
        let mut out = Self {
            attempted: records.len(),
            ..Self::default()
        };
        for r in records {
            match r.status {
                RunStatus::Failed => out.failed += 1,
                RunStatus::Succeeded => {
                    if r.reason.as_deref().is_some_and(|s| s.starts_with("not_worth:")) {
                        out.index_only += 1;
                        let code = r.reason.clone().unwrap_or_default();
                        // Collapse body_too_short:N<M to body_too_short
                        let key = if code.starts_with("not_worth:body_too_short") {
                            "not_worth:body_too_short".into()
                        } else {
                            code
                        };
                        *out.index_only_reasons.entry(key).or_insert(0) += 1;
                    } else {
                        out.reader_ran += 1;
                        out.total_units += r.units;
                        out.total_cards += r.cards;
                    }
                }
            }
        }
        out
    }
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
    /// Thrift / cost snapshot (index-only vs reader-ran).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cost: Option<CostReport>,
    #[serde(default)]
    pub lifecycle_warnings: Vec<String>,
    /// Soft phase failures that did not abort the run (e.g. pinboard 5xx).
    /// Operator-visible in the report; run still ends `completed`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
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
            cost: None,
            lifecycle_warnings: Vec::new(),
            warnings: Vec::new(),
            index_file: None,
            console_file: None,
        }
    }

    pub fn set_reader(&mut self, planned: usize, daily: &DailyReport) {
        let cost = CostReport::from_records(&daily.processed);
        self.reader = ReaderSummary {
            planned,
            processed: daily.processed.len(),
            succeeded: daily.processed.len() - daily.failed(),
            failed: daily.failed(),
            skipped: daily.skipped,
            blocked: daily.blocked,
            capped: daily.capped,
            index_only: cost.index_only,
            reader_ran: cost.reader_ran,
        };
        self.cost = Some(cost);
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

    #[test]
    fn cost_report_counts_index_only_vs_reader() {
        use crate::{DailyRunRecord, RunStatus, DAILY_SCHEMA};
        let rec = |status, units, reason: Option<&str>| DailyRunRecord {
            schema: DAILY_SCHEMA.into(),
            run_id: "r".into(),
            date: "2026-08-01".into(),
            source_path: "x.md".into(),
            source_sha256: "ab".into(),
            status,
            pack_dir: None,
            moved_to: None,
            units,
            cards: units,
            reason: reason.map(String::from),
        };
        let records = vec![
            rec(RunStatus::Succeeded, 3, None),
            rec(
                RunStatus::Succeeded,
                0,
                Some("not_worth:body_too_short:10<200"),
            ),
            rec(RunStatus::Succeeded, 0, Some("not_worth:bookmark_shell")),
            rec(RunStatus::Failed, 0, Some("llm 500")),
        ];
        let cost = CostReport::from_records(&records);
        assert_eq!(cost.attempted, 4);
        assert_eq!(cost.reader_ran, 1);
        assert_eq!(cost.index_only, 2);
        assert_eq!(cost.failed, 1);
        assert_eq!(cost.total_units, 3);
        assert_eq!(
            cost.index_only_reasons.get("not_worth:body_too_short"),
            Some(&1)
        );
        assert_eq!(
            cost.index_only_reasons.get("not_worth:bookmark_shell"),
            Some(&1)
        );
    }
}
