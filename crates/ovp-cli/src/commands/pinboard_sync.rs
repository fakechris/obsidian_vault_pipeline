//! `pinboard-sync` — materialize Pinboard bookmarks as notes in
//! `50-Inbox/02-Pinboard/` (URL-deduped, append-only ledger). Offline by
//! default via a JSON export file; live API only with the `pinboard-live`
//! feature + `PINBOARD_TOKEN`.

use std::path::PathBuf;

use ovp_intake::{
    sync_pinboard, FixturePinboardFetch, IntakeConfig, PinboardFetch, PinboardSyncOptions,
    FIRST_SYNC_GUARD_MAX_NEW,
};

use crate::commands::daily::live_pinboard_fetch;
use crate::CliError;

pub struct PinboardSyncArgs {
    pub vault_root: PathBuf,
    pub fixture: Option<PathBuf>,
    pub live: bool,
    pub date: String,
    pub run_id: String,
    pub dry_run: bool,
    /// Only materialize bookmarks posted on/after this date (YYYY-MM-DD).
    pub since: Option<String>,
    /// Materialize at most N of the newest new bookmarks.
    pub max: Option<usize>,
    /// Override the first-sync flood guard and materialize everything.
    pub yes_all: bool,
}

pub fn run(args: PinboardSyncArgs) -> Result<(), CliError> {
    let mut fetch: Box<dyn PinboardFetch> = match (&args.fixture, args.live) {
        (Some(_), true) => {
            return Err(CliError::Io("pass either --fixture or --live, not both".into()))
        }
        (Some(path), false) => Box::new(FixturePinboardFetch::new(path)),
        (None, true) => live_pinboard_fetch()?,
        (None, false) => {
            return Err(CliError::Io(
                "pass --fixture <export.json> (offline) or --live (requires --features pinboard-live + PINBOARD_TOKEN)".into(),
            ))
        }
    };

    let _lock = if args.dry_run {
        None
    } else {
        Some(ovp_intake::RunLock::acquire(&args.vault_root).map_err(CliError::Io)?)
    };
    let cfg = IntakeConfig::new(args.vault_root.clone(), args.date.clone(), args.run_id);
    let opts = PinboardSyncOptions {
        since: args.since.clone(),
        max: args.max,
        yes_all: args.yes_all,
    };
    let outcome = sync_pinboard(&cfg, fetch.as_mut(), args.dry_run, &opts).map_err(CliError::Io)?;

    println!("pinboard-sync [{}]: {}", args.date, outcome.origin);
    for rec in &outcome.new_notes {
        println!("  new  {} → {}", rec.url, rec.to);
    }
    println!(
        "  done: {} fetched, {} new, {} known, {} without URL{}",
        outcome.fetched,
        outcome.new_notes.len(),
        outcome.skipped_known,
        outcome.skipped_empty_url,
        if outcome.dry_run { " — dry-run, nothing written" } else { "" },
    );
    if outcome.skipped_since > 0 || outcome.skipped_over_max > 0 {
        println!(
            "  filtered: {} before --since, {} beyond --max (left for later runs)",
            outcome.skipped_since, outcome.skipped_over_max,
        );
    }
    if outcome.guard_would_abort {
        println!(
            "  WARNING: a REAL run would ABORT — {} new bookmark(s) exceed the \
             {FIRST_SYNC_GUARD_MAX_NEW}-note first-sync guard; rerun with --since, --max, \
             or --yes-all",
            outcome.new_notes.len(),
        );
    }
    println!("  next: `ovp2 intake` (or `daily`) moves readable notes into 01-Raw");
    Ok(())
}
