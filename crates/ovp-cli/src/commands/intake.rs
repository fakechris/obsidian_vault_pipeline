//! `intake` — run the capture sweep alone (no model calls): normalize +
//! dedup whatever sits in `Clippings/`, `50-Inbox/00-Capture`, and
//! `50-Inbox/02-Pinboard` into `50-Inbox/01-Raw`.

use std::path::PathBuf;

use ovp_daily::{read_daily_ledger, succeeded_hashes};
use ovp_domain::VaultLayout;
use ovp_intake::{sweep_intake, IntakeAction, IntakeConfig};

use crate::CliError;

pub struct IntakeArgs {
    pub vault_root: PathBuf,
    pub date: String,
    pub run_id: String,
    pub dry_run: bool,
}

pub fn run(args: IntakeArgs) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let done = succeeded_hashes(
        &read_daily_ledger(&args.vault_root.join(layout.daily_ledger())).map_err(CliError::Io)?,
    );
    let cfg = IntakeConfig::new(args.vault_root.clone(), args.date.clone(), args.run_id);
    let sweep = sweep_intake(&cfg, &done, args.dry_run).map_err(CliError::Io)?;

    println!("intake [{}]: vault {}", args.date, args.vault_root.display());
    for rec in sweep
        .ingested
        .iter()
        .chain(&sweep.duplicates)
        .chain(&sweep.needs_content)
        .chain(&sweep.unparseable)
    {
        let verb = match rec.action {
            IntakeAction::Ingested => "ingested",
            IntakeAction::Duplicate => "duplicate",
            IntakeAction::NeedsContent => "needs-content",
            IntakeAction::Unparseable => "unparseable",
        };
        match &rec.to {
            Some(to) => println!("  {verb:13} {} → {to}", rec.from),
            None => println!(
                "  {verb:13} {}{}",
                rec.from,
                rec.note.as_deref().map(|n| format!(" ({n})")).unwrap_or_default()
            ),
        }
    }
    println!(
        "  done: {} ingested, {} duplicate(s), {} needs-content, {} unparseable{}{}",
        sweep.ingested.len(),
        sweep.duplicates.len(),
        sweep.needs_content.len(),
        sweep.unparseable.len(),
        if sweep.already_flagged > 0 {
            format!(" ({} previously flagged)", sweep.already_flagged)
        } else {
            String::new()
        },
        if sweep.dry_run { " — dry-run, nothing written" } else { "" },
    );
    Ok(())
}
