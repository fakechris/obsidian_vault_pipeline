//! `extract-units` — the M14a Grounded Unit extraction hand-harness, exposed as
//! a thin CLI shim. It does NOT go through GraphAssembler / RunCycle / a manifest
//! / `DomainBody`; it calls `ovp_domain::units` directly. It reuses the shared
//! `build_client` (replay cassette or live) so cassettes file under the
//! `unit_extract/v1` namespace exactly like the article paths.
//!
//! Output is a review pack under `--out` (see `docs/stage-m14a-grounded-units.md`).
//! Writes nothing to the vault / canonical store.

use std::path::PathBuf;

use ovp_domain::units::{
    run_unit_extraction, run_unit_extraction_repaired, write_unit_review_pack, RepairedRun,
    ValidationReport,
};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct ExtractUnitsArgs {
    pub input_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub client_kind: ClientKind,
    /// M14a.8 critic-assisted bounded repair (frozen-v5 base + critic + repair).
    pub repair: bool,
    /// Cassette root for the critic call (`unit_critic/v1`).
    pub critic_cache_dir: PathBuf,
}

pub fn run(args: ExtractUnitsArgs) -> Result<(), CliError> {
    if args.repair {
        return run_repaired(args);
    }
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
        "  ref_found={:.1}%  quote_found={:.1}%  accepted_without_quote={}",
        r.ref_found_rate * 100.0,
        r.quote_found_rate * 100.0,
        r.accepted_without_quote,
    );
    println!(
        "  span_window={}  ref_mismatch={}  near_match(review)={}  quote_not_found={}  arg_drift(advisory)={}",
        r.span_window_matches, r.ref_mismatch, r.near_match_needs_review, r.quote_not_found, r.argument_drift_advisory
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

/// M14a.8 critic-assisted bounded repair. Base = frozen v5 (ALWAYS replay over
/// `--cache-dir`, so the baseline is deterministic); critic = `--client` over
/// `--critic-cache-dir` (live records `unit_critic/v1`). Writes the repaired
/// review pack PLUS critic-reply.txt + repairs.json + a base-vs-repaired summary.
fn run_repaired(args: ExtractUnitsArgs) -> Result<(), CliError> {
    let source = ovp_domain::units::read_source_from_path(&args.input_path)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.input_path.display())))?;

    // Base ALWAYS replays (frozen v5); only the critic call may go live.
    let mut base_client = build_client(ClientKind::Replay, &args.cache_dir)?;
    let mut critic_client = build_client(args.client_kind, &args.critic_cache_dir)?;

    let run: RepairedRun =
        run_unit_extraction_repaired(&source, base_client.as_mut(), critic_client.as_mut())
            .map_err(|e| CliError::Io(format!("repaired extraction call failed: {e}")))?;

    write_unit_review_pack(&args.out_dir, &source.body_markdown, &run.extraction, Some(&run.base_reply))
        .map_err(|e| CliError::Io(format!("writing review pack to {}: {e}", args.out_dir.display())))?;
    // Repair-specific sidecars (all deterministic given the two replies).
    let w = |name: &str, body: &str| {
        std::fs::write(args.out_dir.join(name), body)
            .map_err(|e| CliError::Io(format!("writing {name}: {e}")))
    };
    w("critic-reply.txt", &run.critic_reply)?;
    let repairs = serde_json::to_string_pretty(&run.repair_log)
        .map_err(|e| CliError::Io(e.to_string()))?;
    w("repairs.json", &format!("{repairs}\n"))?;

    let b = &run.base.report;
    let r = &run.extraction.report;
    println!("extract-units --repair: {}", source.title);
    if let Some(err) = &r.parse_error {
        println!("  PARSE ERROR (base): {err}");
    }
    println!(
        "  base:     accepted={} needs_review={} rejected={}",
        b.accepted, b.needs_review, b.rejected
    );
    println!(
        "  repaired: accepted={} needs_review={} rejected={}  (trims={} adds_proposed={} unmatched_defects={})",
        r.accepted, r.needs_review, r.rejected,
        run.repair_log.trims, run.repair_log.adds_proposed, run.repair_log.defects_unmatched,
    );
    println!(
        "  quote_found={:.1}%  accepted_without_quote={}  near_match(review)={}  quote_not_found={}",
        r.quote_found_rate * 100.0, r.accepted_without_quote, r.near_match_needs_review, r.quote_not_found,
    );
    println!("  review pack: {}", args.out_dir.join("REVIEW.md").display());

    if let Some(reason) = extraction_failure(r) {
        return Err(CliError::Io(format!("extract-units --repair: {reason} (review pack written)")));
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
            ref_found_rate: 1.0,
            quote_found_rate: 1.0,
            quote_maps_to_original: 0,
            accepted_without_quote,
            ref_mismatch: 0,
            span_window_matches: 0,
            near_match_needs_review: 0,
            quote_not_found: 0,
            argument_drift_advisory: 0,
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
