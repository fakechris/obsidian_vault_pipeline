//! `crystal-review apply` — M25 Crystal Review Workbench: turn human review
//! decisions into a REVISED Crystal candidate. The decision is never a durability
//! verdict; it only authors a new structured candidate that must re-enter the
//! full gate (crystal-lint + claim-strength + crystal-write). Fail-loud on
//! unknown claim ids. No model call, no write to the durable store.

use std::path::PathBuf;

use ovp_domain::crystal::{apply_decisions, CrystalCandidate, ReviewDecision};

use crate::CliError;

pub struct CrystalReviewArgs {
    /// The original candidate the caveated claims came from.
    pub candidate: PathBuf,
    /// Reviewer decisions JSON: `[ { claim_id, action, revisions:[...], note } ]`.
    pub decisions: PathBuf,
    /// Where to write the revised candidate (feeds the strength gate + crystal-write).
    pub out: PathBuf,
}

pub fn run(args: CrystalReviewArgs) -> Result<(), CliError> {
    let original: CrystalCandidate = serde_json::from_str(
        &std::fs::read_to_string(&args.candidate)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", args.candidate.display())))?,
    )
    .map_err(|e| CliError::Io(format!("parsing candidate: {e}")))?;
    let decisions: Vec<ReviewDecision> = serde_json::from_str(
        &std::fs::read_to_string(&args.decisions)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", args.decisions.display())))?,
    )
    .map_err(|e| CliError::Io(format!("parsing decisions: {e}")))?;

    // Queue-state actions (M36 R1) mutate the vault review queue — a thing
    // this candidate-file workbench cannot do. Accepting them here would
    // "succeed" with zero revised claims and no parking; refuse instead.
    let queue_only: Vec<&str> = decisions
        .iter()
        .filter(|d| {
            matches!(
                d.action,
                ovp_domain::crystal::ReviewAction::DemoteToSourceInsight
                    | ovp_domain::crystal::ReviewAction::DeferUntil
            )
        })
        .map(|d| d.claim_id.as_str())
        .collect();
    if !queue_only.is_empty() {
        return Err(CliError::Gate(format!(
            "decisions contain queue-state action(s) ({queue_only:?}) that only \
             `crystal-review-session-apply` can execute. Nothing written."
        )));
    }

    let outcome = apply_decisions(&original, &decisions);

    // Fail loud: a decision referencing a claim not in the candidate is an error,
    // never a silent drop.
    if !outcome.unknown.is_empty() {
        return Err(CliError::Gate(format!(
            "decisions reference unknown claim id(s): {:?}. Nothing written.",
            outcome.unknown
        )));
    }

    if let Some(parent) = args.out.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let s = serde_json::to_string_pretty(&outcome.revised).map_err(|e| CliError::Io(e.to_string()))?;
    std::fs::write(&args.out, format!("{s}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", args.out.display())))?;

    let n_rewrite = outcome.log.iter().filter(|(_, a, _)| matches!(a, ovp_domain::crystal::ReviewAction::Rewrite)).count();
    let n_split = outcome.log.iter().filter(|(_, a, _)| matches!(a, ovp_domain::crystal::ReviewAction::Split)).count();
    let n_keep = outcome.log.iter().filter(|(_, a, _)| matches!(a, ovp_domain::crystal::ReviewAction::KeepCaveated)).count();
    let n_reject = outcome.log.iter().filter(|(_, a, _)| matches!(a, ovp_domain::crystal::ReviewAction::Reject)).count();
    println!("crystal-review apply: {} decision(s)", outcome.log.len());
    println!("  rewrite={n_rewrite} split={n_split} keep_caveated={n_keep} reject={n_reject}");
    println!("  revised candidate: {} claim(s) → {}", outcome.revised.items.len(), args.out.display());
    println!("  NEXT: run the claim-strength gate on this candidate, then crystal-write — the gate (not this step) decides durability.");
    Ok(())
}
