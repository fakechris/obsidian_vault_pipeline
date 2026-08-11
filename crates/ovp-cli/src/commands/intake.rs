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
    let _lock = if args.dry_run {
        None
    } else {
        Some(ovp_intake::RunLock::acquire(&args.vault_root).map_err(CliError::Io)?)
    };
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
        .chain(&sweep.skipped)
    {
        let verb = match rec.action {
            IntakeAction::Ingested => "ingested",
            IntakeAction::Duplicate => "duplicate",
            IntakeAction::NeedsContent => "needs-content",
            IntakeAction::Unparseable => "unparseable",
            IntakeAction::Skipped => "skipped",
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

/// `intake-dedup-urls` — late URL dedup for pre-intake legacy copies (the
/// intake ledger only reaches back to its go-live; see
/// `park_legacy_url_duplicates`). Dry-run unless `apply`.
pub fn run_dedup_urls(
    vault_root: &std::path::Path,
    apply: bool,
    date: String,
) -> Result<(), CliError> {
    let _lock = if apply {
        Some(ovp_intake::RunLock::acquire(vault_root).map_err(CliError::Io)?)
    } else {
        None
    };
    let cfg = IntakeConfig::new(
        vault_root.to_path_buf(),
        date.clone(),
        format!("intake-dedup-{date}"),
    );
    let groups =
        ovp_intake::park_legacy_url_duplicates(&cfg, !apply).map_err(CliError::Io)?;
    if groups.is_empty() {
        println!("intake-dedup-urls: no duplicate-URL groups in the processed tree");
        return Ok(());
    }
    let mut parked_total = 0usize;
    for g in &groups {
        println!("{}", g.url);
        println!("  keep {}", g.kept);
        for rec in &g.parked {
            parked_total += 1;
            match &rec.to {
                Some(to) => println!("  park {} → {to}", rec.from),
                None => println!("  park {}", rec.from),
            }
        }
    }
    println!(
        "intake-dedup-urls: {} group(s), {} copy(ies) parked{}",
        groups.len(),
        parked_total,
        if apply { " — run `ovp2 index` (or wait for daily) to refresh projections" }
        else { " — dry-run, nothing moved; pass --apply to execute" },
    );
    Ok(())
}
