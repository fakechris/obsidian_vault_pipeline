//! `console` — refresh the product console (`.ovp/console/index.html`) from
//! product state. Builds a fresh read model, persists it (so `find` and the
//! console always agree), then renders.

use std::path::PathBuf;

use ovp_console::{write_console, write_ops_pages};
use ovp_index::{build_evidence, build_index_at, now_rfc3339, write_evidence, write_index};

use crate::CliError;

pub struct ConsoleArgs {
    pub vault_root: PathBuf,
    pub date: String,
}

pub fn run(args: ConsoleArgs) -> Result<(), CliError> {
    // Stamp the instant once, and name the producer `console-<built_at>` so an
    // ad-hoc console rebuild is never a silently-anonymous projection.
    let built_at = now_rfc3339();
    let run_id = format!("console-{built_at}");
    let model = build_index_at(&args.vault_root, &args.date, Some(&run_id), &built_at)
        .map_err(CliError::Io)?;
    let index_rel = write_index(&args.vault_root, &model).map_err(CliError::Io)?;
    let evidence = build_evidence(&args.vault_root, &args.date, &model).map_err(CliError::Io)?;
    let evidence_rel = write_evidence(&args.vault_root, &evidence).map_err(CliError::Io)?;
    let console_rel = write_console(&args.vault_root, &model).map_err(CliError::Io)?;
    let ops_pages = write_ops_pages(&args.vault_root, &model).map_err(CliError::Io)?;
    println!(
        "console [{}]: {}",
        args.date,
        args.vault_root.join(&console_rel).display()
    );
    println!("  index refreshed: {index_rel}");
    println!(
        "  evidence refreshed: {evidence_rel} (cards={} units={})",
        evidence.cards.len(),
        evidence.units.len()
    );
    for p in &ops_pages {
        println!("  ops page: {p}");
    }
    println!(
        "  sources={} packs={} durable={} caveated={} runs={}",
        model.totals.sources,
        model.totals.packs,
        model.totals.claims_durable,
        model.totals.claims_caveated,
        model.totals.runs
    );
    Ok(())
}
