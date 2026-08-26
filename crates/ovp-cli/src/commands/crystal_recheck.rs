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
use ovp_domain::crystal::{Citation, CrystalCandidate, CrystalClaim};

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

/// Rebuild a lintable candidate from the durable ledger.
///
/// Only `op: write` records with `final_class: durable` are in scope — the
/// point is to recheck what the vault currently asserts, not the history of
/// how it got there. A later record for the same `claim_key` supersedes an
/// earlier one.
fn durable_from_ledger(path: &PathBuf) -> Result<CrystalCandidate, CliError> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", path.display())))?;
    // BTreeMap: last write per claim_key wins, deterministic order out.
    let mut by_key: std::collections::BTreeMap<String, CrystalClaim> =
        std::collections::BTreeMap::new();
    let mut skipped = 0usize;
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: serde_json::Value = serde_json::from_str(line)
            .map_err(|e| CliError::Io(format!("{}:{}: {e}", path.display(), i + 1)))?;
        if v.get("op").and_then(|o| o.as_str()) != Some("write") {
            continue;
        }
        let Some(rec) = v.get("record") else {
            skipped += 1;
            continue;
        };
        if rec.get("final_class").and_then(|c| c.as_str()) != Some("durable") {
            continue;
        }
        let key = rec
            .get("claim_key")
            .or_else(|| rec.get("claim_id"))
            .and_then(|k| k.as_str())
            .unwrap_or_default()
            .to_string();
        if key.is_empty() {
            skipped += 1;
            continue;
        }
        let citations: Vec<Citation> = rec
            .get("citations")
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|c| {
                        Some(Citation {
                            case_id: c.get("case_id")?.as_str()?.to_string(),
                            unit_id: c.get("unit_id")?.as_str()?.to_string(),
                            quote: c.get("quote")?.as_str()?.to_string(),
                            claimed_line: None,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        by_key.insert(
            key.clone(),
            CrystalClaim {
                id: key,
                claim: rec
                    .get("claim")
                    .and_then(|c| c.as_str())
                    .unwrap_or_default()
                    .to_string(),
                theme: rec
                    .get("theme")
                    .and_then(|c| c.as_str())
                    .unwrap_or_default()
                    .to_string(),
                citations,
                caveat: None,
            },
        );
    }
    if skipped > 0 {
        // Loud, not silent: a record we could not key is a durable claim that
        // this recheck did NOT cover, and a report that hides its own gaps is
        // worse than no report.
        eprintln!("crystal-recheck: {skipped} durable record(s) had no usable claim key — NOT rechecked");
    }
    Ok(CrystalCandidate {
        items: by_key.into_values().collect(),
    })
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

    #[test]
    fn only_durable_write_records_are_rechecked() {
        let d = tmp("scope");
        let p = write_ledger(
            &d,
            &[
                r#"{"op":"write","record":{"claim_key":"ck-1","final_class":"durable","claim":"a","citations":[{"case_id":"2026-01-01_X-a","unit_id":"u-1","quote":"q"}]}}"#,
                r#"{"op":"write","record":{"claim_key":"ck-2","final_class":"caveated","claim":"b","citations":[]}}"#,
                r#"{"op":"revoke","record":{"claim_key":"ck-3","final_class":"durable","claim":"c","citations":[]}}"#,
                "",
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        let ids: Vec<&str> = c.items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, vec!["ck-1"], "caveated and non-write ops stay out");
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn a_later_write_supersedes_an_earlier_one_for_the_same_key() {
        // The ledger is append-only, so the same claim_key appears more than
        // once. Rechecking the stale historical copy would report defects that
        // the current vault does not actually assert.
        let d = tmp("supersede");
        let p = write_ledger(
            &d,
            &[
                r#"{"op":"write","record":{"claim_key":"ck-1","final_class":"durable","claim":"old","citations":[{"case_id":"2026-01-01_X-a","unit_id":"u-old","quote":"q"}]}}"#,
                r#"{"op":"write","record":{"claim_key":"ck-1","final_class":"durable","claim":"new","citations":[{"case_id":"2026-01-01_X-a","unit_id":"u-new","quote":"q"}]}}"#,
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        assert_eq!(c.items.len(), 1);
        assert_eq!(c.items[0].claim, "new");
        assert_eq!(c.items[0].citations[0].unit_id, "u-new");
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
