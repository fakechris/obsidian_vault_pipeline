//! `extract-units` — the M14a Grounded Unit extraction hand-harness, exposed as
//! a thin CLI shim. It does NOT go through GraphAssembler / RunCycle / a manifest
//! / `DomainBody`; it calls `ovp_domain::units` directly. It reuses the shared
//! `build_client` (replay cassette or live) so cassettes file under the
//! `unit_extract/v1` namespace exactly like the article paths.
//!
//! Output is a review pack under `--out` (see `docs/stage-m14a-grounded-units.md`).
//! Writes nothing to the vault / canonical store.

use std::path::PathBuf;

use ovp_domain::units::{run_unit_extraction, write_unit_review_pack, ValidationReport};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct ExtractUnitsArgs {
    pub input_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub client_kind: ClientKind,
}

pub fn run(args: ExtractUnitsArgs) -> Result<(), CliError> {
    let source = ovp_domain::units::read_source_from_path(&args.input_path)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.input_path.display())))?;

    let mut client = build_client(args.client_kind, &args.cache_dir)?;

    let run = run_unit_extraction(&source, client.as_mut())
        .map_err(|e| CliError::Io(format!("unit extraction call failed: {e}")))?;
    let extraction = run.extraction;

    // Always write the pack — including the RAW model reply, so a parse error /
    // malformed unit can be diagnosed as model-side vs parser-side.
    write_unit_review_pack(&args.out_dir, &source.body_markdown, &extraction, Some(&run.raw_reply))
        .map_err(|e| CliError::Io(format!("writing review pack to {}: {e}", args.out_dir.display())))?;

    let r = &extraction.report;
    println!("extract-units: {}", source.title);
    if let Some(err) = &r.parse_error {
        println!("  PARSE ERROR: {err}");
    }
    println!(
        "  total={} accepted={} needs_review={} rejected={}",
        r.total, r.accepted, r.needs_review, r.rejected
    );
    println!(
        "  quote_found_rate={:.1}%  accepted_without_quote={}  arg_locatable={:.1}%",
        r.quote_found_rate * 100.0,
        r.accepted_without_quote,
        r.argument_locatable_rate * 100.0
    );
    println!("  review pack: {}", args.out_dir.join("REVIEW.md").display());

    // The pack is written regardless, but a failed run must EXIT NON-ZERO so the
    // operator loop never mistakes a parse error / empty / invariant-violating
    // run for a clean one.
    if let Some(reason) = extraction_failure(r) {
        return Err(CliError::Io(format!("extract-units: {reason} (review pack written)")));
    }
    Ok(())
}

/// `Some(reason)` if the run must be treated as failed (non-zero exit) even
/// though the review pack was written: the model output did not parse, produced
/// zero units, or violated the quote-grounding invariant.
pub(crate) fn extraction_failure(r: &ValidationReport) -> Option<String> {
    if let Some(e) = &r.parse_error {
        return Some(format!("model output did not parse: {e}"));
    }
    if r.total == 0 {
        return Some("model produced zero units".into());
    }
    if r.accepted_without_quote > 0 {
        return Some(format!(
            "{} accepted unit(s) without a located quote — invariant violated",
            r.accepted_without_quote
        ));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn report(total: usize, accepted_without_quote: usize, parse_error: Option<&str>) -> ValidationReport {
        ValidationReport {
            total,
            accepted: 0,
            rejected: 0,
            needs_review: 0,
            quote_found_rate: 1.0,
            accepted_without_quote,
            argument_locatable_rate: 1.0,
            duplicate_groups: vec![],
            parse_error: parse_error.map(str::to_string),
        }
    }

    #[test]
    fn parse_error_is_a_failure() {
        assert!(extraction_failure(&report(0, 0, Some("not JSON"))).is_some());
    }

    #[test]
    fn zero_units_is_a_failure() {
        assert!(extraction_failure(&report(0, 0, None)).is_some());
    }

    #[test]
    fn invariant_violation_is_a_failure() {
        assert!(extraction_failure(&report(5, 1, None)).is_some());
    }

    #[test]
    fn a_healthy_run_is_not_a_failure() {
        assert!(extraction_failure(&report(5, 0, None)).is_none());
    }
}
