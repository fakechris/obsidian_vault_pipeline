//! `crystal-lint` — M22 Crystal pre-write gate runner. Loads a Crystal candidate
//! (structured-citation synthesis) + a packs directory of `units.accepted.json`,
//! builds the grounding index, runs the citation linter + deterministic
//! provenance scoring, prints a summary, and writes a JSON report. NO model call,
//! NO durable write — a gate, not a writer.

use std::path::PathBuf;

use ovp_domain::crystal::{
    final_routing, lint_candidate, score_candidate, strength_coverage, ClaimStrengthVerdict,
    CrystalCandidate, FinalClass, GroundingIndex, ProvenanceClass,
};
use ovp_domain::units::Unit;

use crate::CliError;

pub struct CrystalLintArgs {
    /// The Crystal candidate JSON (`{ "items": [ { id, claim, citations:[{case_id,unit_id,quote}] } ] }`).
    pub candidate: PathBuf,
    /// Directory with one subdir per case containing `units.accepted.json`.
    pub packs_dir: PathBuf,
    /// Where to write the lint+score report JSON.
    pub out: PathBuf,
    /// Optional claim-strength verdicts JSON (the labeled LLM gate's output:
    /// `[ { claim_id, strength, evidence_sufficient, rationale } ]`). When given,
    /// each claim gets a final routing (durable/caveated/reject) via the
    /// deterministic combiner.
    pub strength: Option<PathBuf>,
}

/// Build the grounding index by reading `<packs_dir>/<case>/units.accepted.json`.
fn build_index(packs_dir: &std::path::Path) -> Result<GroundingIndex, CliError> {
    let mut index = GroundingIndex::new();
    let entries = std::fs::read_dir(packs_dir)
        .map_err(|e| CliError::Io(format!("reading packs dir {}: {e}", packs_dir.display())))?;
    for entry in entries.flatten() {
        if !entry.path().is_dir() {
            continue;
        }
        let case_id = entry.file_name().to_string_lossy().to_string();
        let units_path = entry.path().join("units.accepted.json");
        if !units_path.exists() {
            continue;
        }
        let text = std::fs::read_to_string(&units_path)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", units_path.display())))?;
        let units: Vec<Unit> = serde_json::from_str(&text)
            .map_err(|e| CliError::Io(format!("parsing {}: {e}", units_path.display())))?;
        index.insert(case_id, units);
    }
    if index.is_empty() {
        return Err(CliError::Io(format!(
            "no units.accepted.json found under {}",
            packs_dir.display()
        )));
    }
    Ok(index)
}

pub fn run(args: CrystalLintArgs) -> Result<(), CliError> {
    let text = std::fs::read_to_string(&args.candidate)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.candidate.display())))?;
    let candidate: CrystalCandidate = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing candidate {}: {e}", args.candidate.display())))?;

    let index = build_index(&args.packs_dir)?;
    let report = lint_candidate(&candidate, &index);
    let scores = score_candidate(&report);

    let durable = scores.iter().filter(|s| s.class == ProvenanceClass::Durable).count();
    let caveated = scores.iter().filter(|s| s.class == ProvenanceClass::Caveated).count();
    let quarantine = scores.iter().filter(|s| s.class == ProvenanceClass::Quarantine).count();
    let total_citations: usize = report.claims.iter().map(|c| c.n_citations).sum();
    let grounded_citations: usize = report.claims.iter().map(|c| c.n_grounded).sum();

    // Optional claim-strength gate: combine the semantic verdicts with the
    // deterministic provenance class for a final routing per claim.
    let strength_verdicts: Vec<ClaimStrengthVerdict> = match &args.strength {
        None => Vec::new(),
        Some(p) => {
            let t = std::fs::read_to_string(p)
                .map_err(|e| CliError::Io(format!("reading {}: {e}", p.display())))?;
            serde_json::from_str(&t)
                .map_err(|e| CliError::Io(format!("parsing strength {}: {e}", p.display())))?
        }
    };
    // Strength-verdict completeness. A full pre-write run requires --strength AND
    // complete coverage (no missing/duplicate/unknown). Partial verdicts must not
    // silently downgrade-then-pass.
    let claim_ids: Vec<String> = candidate.items.iter().map(|c| c.id.clone()).collect();
    let coverage = strength_coverage(&claim_ids, &strength_verdicts);
    let strength_gate_applied = args.strength.is_some();
    let strength_verdict_complete = strength_gate_applied && coverage.complete();

    let final_routes: Vec<(String, FinalClass)> = scores
        .iter()
        .map(|s| {
            let v = strength_verdicts.iter().find(|v| v.claim_id == s.claim_id);
            (s.claim_id.clone(), final_routing(s.class, v))
        })
        .collect();
    let final_durable = final_routes.iter().filter(|(_, f)| *f == FinalClass::Durable).count();
    let final_caveated = final_routes.iter().filter(|(_, f)| *f == FinalClass::Caveated).count();
    let final_reject = final_routes.iter().filter(|(_, f)| *f == FinalClass::Reject).count();
    // Eligible for durable write iff: full pre-write run + complete verdicts + no
    // citation defects + no rejected claims.
    let eligible_for_durable_write = strength_verdict_complete
        && report.n_with_defects == 0
        && final_reject == 0;

    println!("crystal-lint: {} claims over {} cases", report.n_claims, index.len());
    println!(
        "  citations: {grounded_citations}/{total_citations} grounded verbatim to an accepted unit"
    );
    println!(
        "  claims: {} fully-grounded / {} with defects",
        report.n_fully_grounded, report.n_with_defects
    );
    println!("  provenance class: durable={durable} caveated={caveated} quarantine={quarantine}");
    if strength_gate_applied {
        println!(
            "  final routing (with claim-strength gate): durable={final_durable} caveated={final_caveated} reject={final_reject}"
        );
        if !coverage.complete() {
            println!(
                "  strength coverage INCOMPLETE: missing={:?} duplicate={:?} unknown={:?}",
                coverage.missing, coverage.duplicate, coverage.unknown
            );
        }
        println!(
            "  eligible_for_durable_write={eligible_for_durable_write} (complete={strength_verdict_complete})"
        );
    } else {
        println!("  citation/provenance-only run (no --strength): diagnostic, NOT a full pre-write pass; not durable-eligible");
    }

    if let Some(parent) = args.out.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let combined = serde_json::json!({
        "report": report,
        "scores": scores,
        "final_routing": final_routes.iter().map(|(id, f)| serde_json::json!({"claim_id": id, "final": f})).collect::<Vec<_>>(),
        "strength_coverage": {
            "missing": coverage.missing,
            "duplicate": coverage.duplicate,
            "unknown": coverage.unknown,
        },
        "summary": {
            "n_claims": report.n_claims,
            "n_fully_grounded": report.n_fully_grounded,
            "total_citations": total_citations,
            "grounded_citations": grounded_citations,
            "durable": durable,
            "caveated": caveated,
            "quarantine": quarantine,
            "strength_gate_applied": strength_gate_applied,
            "strength_verdict_complete": strength_verdict_complete,
            "final_durable": final_durable,
            "final_caveated": final_caveated,
            "final_reject": final_reject,
            "eligible_for_durable_write": eligible_for_durable_write,
        }
    });
    let s = serde_json::to_string_pretty(&combined).map_err(|e| CliError::Io(e.to_string()))?;
    std::fs::write(&args.out, format!("{s}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", args.out.display())))?;
    println!("  report: {}", args.out.display());

    // Fail-loud gate: the report is always written (for inspection), but the
    // command exits non-zero if ANY claim has a citation defect — so CI / a
    // future durable writer can never treat a candidate with quarantined claims
    // as a passed gate. `caveated` does NOT fail (it routes to review/non-durable,
    // not a hard failure). A durable writer must additionally require
    // `quarantine == 0` and write only `Durable`-class claims.
    if report.n_with_defects > 0 {
        return Err(CliError::Gate(format!(
            "{} of {} claim(s) have citation defects ({quarantine} quarantined) — \
             candidate did NOT pass the pre-write gate. See {}",
            report.n_with_defects,
            report.n_claims,
            args.out.display()
        )));
    }
    // Fail loud on an incomplete/duplicate/unknown strength verdict set — a
    // partial semantic pass must never quietly exit 0.
    if strength_gate_applied && !coverage.complete() {
        return Err(CliError::Gate(format!(
            "strength verdicts incomplete — missing={:?} duplicate={:?} unknown={:?}. \
             A full pre-write run requires exactly one verdict per claim. See {}",
            coverage.missing, coverage.duplicate, coverage.unknown, args.out.display()
        )));
    }
    Ok(())
}
