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
    /// Only materialize bookmarks posted on/after this date (YYYY-MM-DD), or
    /// `auto` for the last sync's high watermark.
    pub since: Option<String>,
    /// Only materialize bookmarks posted on/before this date (YYYY-MM-DD).
    pub until: Option<String>,
    /// Materialize at most N of the newest new bookmarks.
    pub max: Option<usize>,
    /// Override the first-sync flood guard and materialize everything.
    pub yes_all: bool,
    /// Backfill one day-window below the coverage floor.
    pub backfill: bool,
    /// Days per backfill window.
    pub backfill_days: u32,
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
    let cfg = IntakeConfig::new(args.vault_root.clone(), args.date.clone(), args.run_id.clone());

    if args.backfill {
        return run_backfill(&args, &cfg, fetch.as_mut());
    }

    // `--since auto`: resume from the ledger high watermark. An explicit
    // command with no watermark yet fails loud with seeding guidance (the
    // unattended `daily` loop skips gracefully instead).
    let since = match args.since.as_deref() {
        Some(s) if s.eq_ignore_ascii_case("auto") => Some(
            ovp_intake::auto_since(&args.vault_root)
                .map_err(CliError::Io)?
                .ok_or_else(|| {
                    CliError::Io(
                        "--since auto: no pinboard sync watermark yet — run once with an \
                         explicit --since <YYYY-MM-DD> (or --max) to seed, then `auto` \
                         resumes from the newest materialized bookmark day"
                            .into(),
                    )
                })?,
        ),
        other => other.map(str::to_string),
    };
    let opts = PinboardSyncOptions {
        since,
        until: args.until.clone(),
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
    if outcome.skipped_since > 0 || outcome.skipped_until > 0 || outcome.skipped_over_max > 0 {
        println!(
            "  filtered: {} before --since, {} after --until, {} beyond --max (left for later runs)",
            outcome.skipped_since, outcome.skipped_until, outcome.skipped_over_max,
        );
    }
    if outcome.guard_would_abort {
        println!(
            "  WARNING: a REAL run would ABORT — {} new bookmark(s) exceed the \
             {FIRST_SYNC_GUARD_MAX_NEW}-note first-sync guard; rerun with --since, --until, \
             --max, or --yes-all",
            outcome.new_notes.len(),
        );
    }
    println!("  next: `ovp2 intake` (or `daily`) moves readable notes into 01-Raw");
    Ok(())
}

/// One backfill window: sync `[floor - days, floor - 1]` and advance the
/// coverage floor only when the window completed (no --max truncation).
fn run_backfill(
    args: &PinboardSyncArgs,
    cfg: &IntakeConfig,
    fetch: &mut dyn PinboardFetch,
) -> Result<(), CliError> {
    let out = ovp_intake::sync_pinboard_backfill(
        cfg,
        fetch,
        args.dry_run,
        args.backfill_days,
        args.max,
    )
    .map_err(CliError::Io)?;

    println!(
        "pinboard-backfill [{}]: window {} → {} ({})",
        args.date, out.window.since, out.window.until, out.sync.origin
    );
    for rec in &out.sync.new_notes {
        println!("  new  {} → {}", rec.url, rec.to);
    }
    println!(
        "  done: {} fetched, {} new, {} known{}",
        out.sync.fetched,
        out.sync.new_notes.len(),
        out.sync.skipped_known,
        if args.dry_run { " — dry-run, nothing written" } else { "" },
    );
    if !out.floor_advanced {
        println!(
            "  floor NOT advanced ({} bookmark(s) beyond --max {}) — rerun to retry this window",
            out.sync.skipped_over_max,
            args.max.map(|m| m.to_string()).unwrap_or_default(),
        );
    } else {
        println!("  floor → {} (recorded in .ovp/pinboard-backfill.jsonl)", out.window.floor_after);
    }
    // History-exhaustion hint: the oldest bookmark in the whole account sits
    // inside/above this window, so nothing older remains below the new floor.
    if let Some(oldest) = &out.sync.oldest_post_day
        && oldest >= &out.window.since
    {
        println!("  complete: oldest bookmark {oldest} reached — no history below this window");
    }
    println!("  next: `ovp2 intake` (or `daily`) moves readable notes into 01-Raw");
    Ok(())
}
