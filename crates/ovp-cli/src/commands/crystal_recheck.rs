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

use ovp_domain::crystal::recheck::RecheckReport;

use crate::CliError;

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
    ovp_domain::crystal::recheck::recheck_vault(
        vault_root,
        packs_dir.as_deref(),
        ledger.as_deref(),
        ovp_doctor::today_civil(),
    )
    .map_err(CliError::Io)
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

    let today = ovp_doctor::today_civil();
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
