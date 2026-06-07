//! `crystal-lint` — M22 Crystal pre-write gate runner. Loads a Crystal candidate
//! (structured-citation synthesis) + a packs directory of `units.accepted.json`,
//! builds the grounding index, runs the citation linter + deterministic
//! provenance scoring, prints a summary, and writes a JSON report. NO model call,
//! NO durable write — a gate, not a writer.

use std::path::PathBuf;

use ovp_domain::crystal::{
    lint_candidate, score_candidate, CrystalCandidate, GroundingIndex, ProvenanceClass,
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

    println!("crystal-lint: {} claims over {} cases", report.n_claims, index.len());
    println!(
        "  citations: {grounded_citations}/{total_citations} grounded verbatim to an accepted unit"
    );
    println!(
        "  claims: {} fully-grounded / {} with defects",
        report.n_fully_grounded, report.n_with_defects
    );
    println!("  provenance class: durable={durable} caveated={caveated} quarantine={quarantine}");

    if let Some(parent) = args.out.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let combined = serde_json::json!({
        "report": report,
        "scores": scores,
        "summary": {
            "n_claims": report.n_claims,
            "n_fully_grounded": report.n_fully_grounded,
            "total_citations": total_citations,
            "grounded_citations": grounded_citations,
            "durable": durable,
            "caveated": caveated,
            "quarantine": quarantine,
        }
    });
    let s = serde_json::to_string_pretty(&combined).map_err(|e| CliError::Io(e.to_string()))?;
    std::fs::write(&args.out, format!("{s}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", args.out.display())))?;
    println!("  report: {}", args.out.display());
    Ok(())
}
