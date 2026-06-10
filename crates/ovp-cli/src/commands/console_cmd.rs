//! `console` — refresh the product console (`.ovp/console/index.html`) from
//! product state. Builds a fresh read model, persists it (so `find` and the
//! console always agree), then renders.

use std::path::PathBuf;

use ovp_console::write_console;
use ovp_index::{build_index, write_index};

use crate::CliError;

pub struct ConsoleArgs {
    pub vault_root: PathBuf,
    pub date: String,
}

pub fn run(args: ConsoleArgs) -> Result<(), CliError> {
    let model = build_index(&args.vault_root, &args.date, None).map_err(CliError::Io)?;
    let index_rel = write_index(&args.vault_root, &model).map_err(CliError::Io)?;
    let console_rel = write_console(&args.vault_root, &model).map_err(CliError::Io)?;
    println!("console [{}]: {}", args.date, args.vault_root.join(&console_rel).display());
    println!("  index refreshed: {index_rel}");
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
