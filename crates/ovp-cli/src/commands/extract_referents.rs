//! `extract-referents` — M14b local ReferentCandidate extraction (experimental).
//! Input is an M14a.8 `units.accepted.json`; it classifies the OBJECTS those
//! accepted units talk about into LOCAL referents and writes a review pack. It
//! does NOT canonicalize, promote, or write the vault/canonical store, and does
//! NOT go through a manifest / GraphAssembler / RunCycle. Default client is
//! replay-only; `--client live` records cassettes under `referent_classify/v1`.

use std::path::PathBuf;

use ovp_domain::referents::{
    read_accepted_units, run_referent_extraction, write_referent_review_pack,
};
use ovp_domain::referents::ReferentReport;

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct ExtractReferentsArgs {
    /// Path to an M14a.8 `units.accepted.json`.
    pub units_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub client_kind: ClientKind,
}

pub fn run(args: ExtractReferentsArgs) -> Result<(), CliError> {
    let units = read_accepted_units(&args.units_path).map_err(CliError::Io)?;
    if units.is_empty() {
        return Err(CliError::Io(format!("no accepted units in {}", args.units_path.display())));
    }
    let case_id = case_id_of(&args.units_path);

    let mut client = build_client(args.client_kind, &args.cache_dir)?;
    let run = run_referent_extraction(&units, &case_id, client.as_mut())
        .map_err(|e| CliError::Io(format!("referent classification call failed: {e}")))?;
    let ex = run.extraction;

    write_referent_review_pack(&args.out_dir, &units, &ex, Some(&run.raw_reply))
        .map_err(|e| CliError::Io(format!("writing review pack to {}: {e}", args.out_dir.display())))?;

    let r = &ex.report;
    let k = &r.kind_counts;
    println!("extract-referents: {case_id}");
    if let Some(err) = &r.parse_error {
        println!("  PARSE ERROR: {err}");
    }
    println!(
        "  units_in={} | total_candidates={} live={} rejected={}",
        units.len(), r.total_candidates, r.live, r.rejected
    );
    println!(
        "  kinds — entity={} concept={} ambiguous={} local_phrase={} noise={}",
        k.entity, k.concept, k.ambiguous, k.local_phrase, k.noise
    );
    println!(
        "  referents_ungrounded={}  concept_rate={:.0}%  ambiguous_rate={:.0}%  grouped={}  dedup={}",
        r.referents_ungrounded, r.concept_rate * 100.0, r.ambiguous_rate * 100.0,
        r.grouped_candidates, r.duplicates_collapsed,
    );
    println!("  review pack: {}", args.out_dir.join("REVIEW.md").display());

    if let Some(reason) = referent_failure(r) {
        return Err(CliError::Io(format!("extract-referents: {reason} (review pack written)")));
    }
    Ok(())
}

/// Derive a case id from the parent directory of the units file
/// (`.run/m14.8/extract/<case>/units.accepted.json` → `<case>`).
fn case_id_of(units_path: &std::path::Path) -> String {
    units_path
        .parent()
        .and_then(|p| p.file_name())
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "case".into())
}

/// `Some(reason)` if the run must exit non-zero: the model output did not parse,
/// or the grounding invariant was violated.
pub(crate) fn referent_failure(r: &ReferentReport) -> Option<String> {
    if let Some(e) = &r.parse_error {
        return Some(format!("model output did not parse: {e}"));
    }
    if r.referents_ungrounded > 0 {
        return Some(format!("{} live referent(s) ungrounded — invariant violated", r.referents_ungrounded));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::referents::{KindCounts, ReferentReport};

    fn report(parse_error: Option<&str>, ungrounded: usize) -> ReferentReport {
        ReferentReport {
            total_candidates: 1, live: 1, rejected: 0, referents_ungrounded: ungrounded,
            kind_counts: KindCounts::default(), concept_rate: 0.0, ambiguous_rate: 0.0,
            grouped_candidates: 0, duplicates_collapsed: 0, parse_error: parse_error.map(str::to_string),
        }
    }

    #[test]
    fn parse_error_and_ungrounded_are_failures() {
        assert!(referent_failure(&report(Some("bad"), 0)).is_some());
        assert!(referent_failure(&report(None, 2)).is_some());
        assert!(referent_failure(&report(None, 0)).is_none());
    }

    #[test]
    fn case_id_from_path() {
        assert_eq!(case_id_of(std::path::Path::new(".run/m14.8/extract/rag_wrong/units.accepted.json")), "rag_wrong");
    }
}
