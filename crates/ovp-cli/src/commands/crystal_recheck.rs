//! `crystal-recheck` — staleness recheck over the durable ledger.
//!
//! The pre-write gate proves a claim's citations ground the day it is written.
//! Nothing re-asks afterwards, so a durable claim whose supporting unit later
//! moved keeps asserting itself with a citation that no longer resolves — and
//! it does so silently, because the ledger is append-only and the linter never
//! runs again.
//!
//! This reconstructs a candidate from the durable ledger, re-lints it against
//! the CURRENT reader packs, and reports. Read-only by design: it never edits
//! the ledger, never rewrites a claim, never marks anything. A stale claim is
//! not a wrong claim — it is one whose evidence can no longer be assumed
//! without looking, and resolving that is a consolidation write that belongs
//! behind the same gate and the same human as any other durable write.

use std::path::PathBuf;

use ovp_domain::crystal::recheck::{RecheckReport, recheck};
use ovp_domain::crystal::{
    Citation, CrystalCandidate, CrystalClaim, CrystalStatus, FinalClass, StoreEvent, fold_ledger,
};

use crate::CliError;
use crate::commands::crystal_write::build_grounding_index;

pub struct CrystalRecheckArgs {
    pub vault_root: PathBuf,
    /// Reader packs to re-lint against. Defaults to `<vault>/40-Resources/Reader`.
    pub packs_dir: Option<PathBuf>,
    /// Durable ledger. Defaults to `<vault>/.ovp/crystal/ledger.jsonl`.
    pub ledger: Option<PathBuf>,
    /// Write the JSON report here (stdout summary always prints).
    pub out: Option<PathBuf>,
    /// Cap the per-claim listing in the printed summary.
    pub limit: usize,
}

/// Rebuild a lintable candidate from what the vault CURRENTLY asserts.
///
/// Goes through the domain's typed `StoreEvent` + [`fold_ledger`] rather than
/// reading `op` strings here. The ledger has three ops, not one: `Supersede`
/// also flips its predecessor to `Superseded`, and `Retract` marks a record
/// retracted. Re-implementing "latest write wins" in the CLI reproduces none
/// of that, so it would recheck retracted claims as if they were live and miss
/// the replacements — and today's vault happens to contain only `write`
/// records, so the mistake would have looked correct until the first
/// supersede landed.
///
/// Typed deserialization is also the point for citations: a hand-rolled
/// `filter_map` drops a malformed citation silently, and a claim that loses
/// all of them then reports as intact — staleness is measured from citation
/// defects, and a claim with no citations has none.
fn durable_from_ledger(path: &PathBuf) -> Result<CrystalCandidate, CliError> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", path.display())))?;
    let mut events: Vec<StoreEvent> = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let ev: StoreEvent = serde_json::from_str(line)
            .map_err(|e| CliError::Io(format!("{}:{}: {e}", path.display(), i + 1)))?;
        events.push(ev);
    }
    let items = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active && r.final_class == FinalClass::Durable)
        .map(|r| CrystalClaim {
            id: r.claim_key,
            claim: r.claim,
            theme: r.theme,
            citations: r
                .citations
                .into_iter()
                .map(|c| Citation {
                    case_id: c.case_id,
                    unit_id: c.unit_id,
                    quote: c.quote,
                    claimed_line: None,
                })
                .collect(),
            caveat: None,
        })
        .collect();
    Ok(CrystalCandidate { items })
}

fn print_summary(report: &RecheckReport, limit: usize) {
    println!(
        "crystal-recheck: {} durable claims — {} intact, {} stale",
        report.n_claims, report.n_intact, report.n_stale
    );
    if !report.by_defect.is_empty() {
        let parts: Vec<String> = report
            .by_defect
            .iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect();
        println!("  defects (claims, not citations): {}", parts.join(" "));
    }
    // Iterate the declared bucket order, not the map's: sorted by key,
    // "181-365d" prints before "91-180d" and the histogram reads backwards.
    let ages: Vec<String> = ovp_domain::crystal::recheck::AGE_BUCKET_NAMES
        .iter()
        .map(|k| format!("{k}={}", report.age_buckets.get(*k).copied().unwrap_or(0)))
        .collect();
    println!("  evidence age (oldest citation per claim): {}", ages.join(" "));
    if report.n_undated > 0 {
        println!("  undated evidence: {} claim(s)", report.n_undated);
    }
    for row in report.stale.iter().take(limit) {
        println!(
            "  STALE {} — {}/{} citations ground",
            row.claim_id, row.n_grounded, row.n_citations
        );
        for c in &row.stale_citations {
            println!("      {:?}  {} / {}", c.defect, c.case_id, c.unit_id);
        }
    }
    if report.stale.len() > limit {
        println!("  … {} more (see the JSON report)", report.stale.len() - limit);
    }
    // Staleness is a prompt to look, not a verdict that anything is wrong, so
    // this never fails the command. Gating on it would make a routine pack
    // rebuild look like corruption.
    println!("  read-only: nothing was rewritten. Stale claims stay stale until re-verified.");
}

/// Recheck a vault's durable claims. Shared with `doctor` so the health check
/// and the command can never drift into disagreeing about what is stale.
pub fn recheck_vault(
    vault_root: &std::path::Path,
    packs_dir: Option<PathBuf>,
    ledger: Option<PathBuf>,
) -> Result<RecheckReport, CliError> {
    let packs_dir = packs_dir.unwrap_or_else(|| vault_root.join("40-Resources/Reader"));
    let ledger = ledger.unwrap_or_else(|| vault_root.join(".ovp/crystal/ledger.jsonl"));
    let durable = durable_from_ledger(&ledger)?;
    let index = build_grounding_index(&packs_dir)?;
    Ok(recheck(&durable, &index, today_civil()))
}

pub fn run(args: CrystalRecheckArgs) -> Result<(), CliError> {
    let packs_dir = args
        .packs_dir
        .clone()
        .unwrap_or_else(|| args.vault_root.join("40-Resources/Reader"));
    let ledger = args
        .ledger
        .clone()
        .unwrap_or_else(|| args.vault_root.join(".ovp/crystal/ledger.jsonl"));

    let today = today_civil();
    let report = recheck_vault(&args.vault_root, args.packs_dir, args.ledger)?;

    print_summary(&report, args.limit);

    if let Some(out) = &args.out {
        if let Some(parent) = out.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let body = serde_json::json!({
            "schema": "ovp.crystal.recheck/v1",
            "as_of": format!("{:04}-{:02}-{:02}", today.0, today.1, today.2),
            "packs_dir": packs_dir.display().to_string(),
            "ledger": ledger.display().to_string(),
            "report": report,
        });
        let s = serde_json::to_string_pretty(&body).map_err(|e| CliError::Io(e.to_string()))?;
        std::fs::write(out, format!("{s}\n"))
            .map_err(|e| CliError::Io(format!("writing {}: {e}", out.display())))?;
        println!("  report: {}", out.display());
    }
    Ok(())
}

/// Today as a civil date in LOCAL time — the same wall clock the scheduler and
/// the `case_id` date prefixes use, so an age never comes out off-by-one
/// against the dates a human reads in the vault.
fn today_civil() -> (i32, u32, u32) {
    let now = chrono::Local::now().date_naive();
    use chrono::Datelike;
    (now.year(), now.month(), now.day())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_ledger(dir: &std::path::Path, lines: &[&str]) -> PathBuf {
        let p = dir.join("ledger.jsonl");
        std::fs::write(&p, lines.join("\n")).unwrap();
        p
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ovp2-recheck-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// A full durable record — the typed `StoreEvent` shape the ledger really
    /// carries, not the trimmed JSON a hand-rolled parser would have accepted.
    fn rec(key: &str, claim: &str, class: &str, unit: &str) -> String {
        format!(
            r#"{{"claim_key":"{key}","claim_id":"{key}","claim":"{claim}","theme":"t",
                 "source_cases":["2026-01-01_X-a"],
                 "citations":[{{"case_id":"2026-01-01_X-a","unit_id":"{unit}","quote":"q","resolved_line":1}}],
                 "provenance_score":1.0,"provenance_class":"durable","strength":"supported",
                 "strength_rationale":"r","final_class":"{class}","run_id":"r1","status":"active"}}"#
        )
        .replace('\n', "")
    }

    #[test]
    fn only_active_durable_records_are_rechecked() {
        let d = tmp("scope");
        let p = write_ledger(
            &d,
            &[
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-1", "a", "durable", "u-1")),
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-2", "b", "caveated", "u-2")),
                "",
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        let ids: Vec<&str> = c.items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, vec!["ck-1"], "caveated stays out");
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_retracted_claim_is_not_rechecked() {
        // Retract is a real ledger op. Rechecking a retracted claim reports
        // defects the vault does not assert — and "latest write wins" cannot
        // see it, which is why this goes through the domain's fold.
        let d = tmp("retract");
        let p = write_ledger(
            &d,
            &[
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-1", "a", "durable", "u-1")),
                &format!(r#"{{"op":"retract","record":{}}}"#, rec("ck-1", "a", "durable", "u-1")),
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        assert!(c.items.is_empty(), "retracted claims are not live assertions");
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_supersede_swaps_in_the_replacement_and_drops_the_predecessor() {
        let d = tmp("supersede");
        let p = write_ledger(
            &d,
            &[
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-old", "old", "durable", "u-old")),
                &format!(
                    r#"{{"op":"supersede","record":{},"supersedes":"ck-old"}}"#,
                    rec("ck-new", "new", "durable", "u-new")
                ),
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        let ids: Vec<&str> = c.items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, vec!["ck-new"], "the superseded predecessor drops out");
        assert_eq!(c.items[0].citations[0].unit_id, "u-new");
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_malformed_citation_fails_loudly_instead_of_vanishing() {
        // Dropping it would leave a claim with zero citations, and staleness is
        // measured from citation DEFECTS — a claim with none reports intact.
        let d = tmp("badcit");
        let p = write_ledger(
            &d,
            &[r#"{"op":"write","record":{"claim_key":"ck-1","claim_id":"ck-1","claim":"a","theme":"t","source_cases":[],"citations":[{"case_id":"c","unit_id":123,"quote":"q"}],"provenance_score":1.0,"provenance_class":"durable","strength":"supported","strength_rationale":"r","final_class":"durable","run_id":"r1","status":"active"}}"#],
        );
        assert!(durable_from_ledger(&p).is_err());
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_corrupt_ledger_line_fails_loudly() {
        // Skipping unparseable lines would let the report under-count silently
        // and still print a confident "all intact".
        let d = tmp("corrupt");
        let p = write_ledger(&d, &["not json at all"]);
        assert!(durable_from_ledger(&p).is_err());
        std::fs::remove_dir_all(&d).ok();
    }
}
