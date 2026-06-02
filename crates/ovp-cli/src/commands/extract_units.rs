//! `extract-units` — the M14a Grounded Unit extraction hand-harness, exposed as
//! a thin CLI shim. It does NOT go through GraphAssembler / RunCycle / a manifest
//! / `DomainBody`; it calls `ovp_domain::units` directly. It reuses the shared
//! `build_client` (replay cassette or live) so cassettes file under the
//! `unit_extract/v1` namespace exactly like the article paths.
//!
//! Output is a review pack under `--out` (see `docs/stage-m14a-grounded-units.md`).
//! Writes nothing to the vault / canonical store.

use std::path::PathBuf;

use ovp_domain::units::{run_unit_extraction, write_unit_review_pack};

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

    let extraction = run_unit_extraction(&source, client.as_mut())
        .map_err(|e| CliError::Io(format!("unit extraction call failed: {e}")))?;

    write_unit_review_pack(&args.out_dir, &source.body_markdown, &extraction, None)
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

    // A non-zero accepted_without_quote breaks the M14a hard invariant — fail loud.
    if r.accepted_without_quote > 0 {
        return Err(CliError::Io(format!(
            "M14a invariant violated: {} accepted unit(s) without a located quote",
            r.accepted_without_quote
        )));
    }
    Ok(())
}
