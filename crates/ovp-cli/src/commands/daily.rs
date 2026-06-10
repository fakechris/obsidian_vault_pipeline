//! `daily` — the blessed operator loop on the real vault (M30 core, M31 full
//! cycle). Thin composition root over the product crates:
//!
//!   1. pinboard capture (optional, `--pinboard-fixture` / `--pinboard-live`)
//!   2. intake sweep (`ovp-intake`): Clippings/00-Capture/02-Pinboard → 01-Raw
//!   3. plan + reader trunk per new source (`ovp-daily`)
//!   4. lifecycle move to 03-Processed + durable run report (`.ovp/reports/`)
//!   5. read-model + console refresh (`ovp-index` / `ovp-console`)
//!
//! Replay is the default client (consistent with every other command); a real
//! daily run on fresh content is `--client live`. `--max-sources` is the
//! OVP_RULES rate limit on LLM loops.

use std::path::PathBuf;

use ovp_console::write_console;
use ovp_daily::{
    plan_daily, read_daily_ledger, run_daily, succeeded_hashes, DailyConfig, RunReport,
    RunStatus,
};
use ovp_domain::VaultLayout;
use ovp_index::{build_index, write_index};
use ovp_intake::{sweep_intake, sync_pinboard, FixturePinboardFetch, IntakeConfig, PinboardFetch};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct DailyArgs {
    pub vault_root: PathBuf,
    /// Inbox override; defaults to `<vault>/50-Inbox/01-Raw`.
    pub inbox: Option<PathBuf>,
    /// Cassette root override; defaults to `<vault>/.ovp/cassettes/daily`.
    pub cache_dir: Option<PathBuf>,
    pub client_kind: ClientKind,
    pub date: String,
    pub run_id: String,
    pub dry_run: bool,
    pub max_sources: usize,
    pub no_intake: bool,
    pub pinboard_fixture: Option<PathBuf>,
    pub pinboard_live: bool,
    pub no_lifecycle: bool,
    pub retry_blocked: bool,
}

pub fn run(args: DailyArgs) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let inbox = args.inbox.clone().unwrap_or_else(|| args.vault_root.join(layout.inbox_raw_dir()));
    let ledger_path = args.vault_root.join(layout.daily_ledger());
    let intake_cfg = IntakeConfig::new(args.vault_root.clone(), args.date.clone(), args.run_id.clone());

    // One mutating run at a time (cron + manual overlap would double-spend
    // LLM calls and race the lifecycle moves). Dry runs read only.
    let _lock = if args.dry_run {
        None
    } else {
        Some(ovp_intake::RunLock::acquire(&args.vault_root).map_err(CliError::Io)?)
    };

    let mut report = RunReport::new(&args.run_id, &args.date);
    println!("daily [{}]: vault {}", args.date, args.vault_root.display());

    // Phase 1 — pinboard capture (optional).
    if args.pinboard_fixture.is_some() || args.pinboard_live {
        let mut fetch = build_pinboard_fetch(&args)?;
        let outcome = sync_pinboard(&intake_cfg, fetch.as_mut(), args.dry_run)
            .map_err(CliError::Io)?;
        println!(
            "  pinboard: {} fetched, {} new note(s), {} known ({})",
            outcome.fetched, outcome.new_notes.len(), outcome.skipped_known, outcome.origin
        );
        report.pinboard = Some((&outcome).into());
    }

    // Phase 2 — intake sweep (capture dirs → 01-Raw).
    let mut dry_run_pending_ingest = 0usize;
    if !args.no_intake {
        let done = succeeded_hashes(&read_daily_ledger(&ledger_path).map_err(CliError::Io)?);
        let sweep = sweep_intake(&intake_cfg, &done, args.dry_run).map_err(CliError::Io)?;
        println!(
            "  intake: {} ingested, {} duplicate(s), {} needs-content, {} unparseable{}{}",
            sweep.ingested.len(), sweep.duplicates.len(), sweep.needs_content.len(),
            sweep.unparseable.len(),
            if sweep.already_flagged > 0 {
                format!(" ({} previously flagged)", sweep.already_flagged)
            } else {
                String::new()
            },
            if args.dry_run { " — dry-run, nothing moved" } else { "" },
        );
        dry_run_pending_ingest = if args.dry_run { sweep.ingested.len() } else { 0 };
        report.intake = Some((&sweep).into());
    }

    // Phase 3 — plan.
    let ledger = read_daily_ledger(&ledger_path).map_err(CliError::Io)?;
    let work = plan_daily(&inbox, &args.vault_root, &ledger, args.retry_blocked)
        .map_err(CliError::Io)?;
    println!(
        "  plan: {} new source(s), {} skipped, {} blocked",
        work.todo.len(), work.skipped.len(), work.blocked.len()
    );
    for item in &work.blocked {
        println!("    blocked ({} failures): {} — rerun with --retry-blocked after review",
            item.prior_failures, item.rel);
    }
    if args.dry_run {
        for item in &work.todo {
            println!("  would process: {} ({})", item.rel, &item.sha256[..8]);
        }
        if dry_run_pending_ingest > 0 {
            println!(
                "  note: {dry_run_pending_ingest} capture(s) above would ALSO be ingested into \
                 01-Raw and then planned on a real run (dry-run intake moves nothing)"
            );
        }
        println!("  dry-run: nothing written.");
        return Ok(());
    }

    // Phase 4 — reader trunk.
    let cache_dir = args
        .cache_dir
        .clone()
        .unwrap_or_else(|| args.vault_root.join(layout.daily_cassette_dir()));
    let mut make_client = || build_client(args.client_kind, &cache_dir).map_err(|e| e.to_string());
    let cfg = DailyConfig {
        vault_root: args.vault_root.clone(),
        date: args.date.clone(),
        run_id: args.run_id.clone(),
        max_sources: args.max_sources,
        lifecycle_move: !args.no_lifecycle,
        retry_blocked: args.retry_blocked,
    };
    let planned = work.todo.len();
    let daily = run_daily(&cfg, &work, &mut make_client).map_err(CliError::Io)?;

    for rec in &daily.processed {
        match rec.status {
            RunStatus::Succeeded => println!(
                "  ok   {} → {} (units={} cards={}){}",
                rec.source_path,
                rec.pack_dir.as_deref().unwrap_or("?"),
                rec.units,
                rec.cards,
                rec.moved_to.as_deref().map(|m| format!(" moved→{m}")).unwrap_or_default(),
            ),
            RunStatus::Failed => println!(
                "  FAIL {} — {}",
                rec.source_path,
                rec.reason.as_deref().unwrap_or("unknown")
            ),
        }
    }
    for w in &daily.lifecycle_warnings {
        println!("  warn {w}");
    }
    if daily.capped > 0 {
        println!(
            "  capped: {} source(s) left for the next run (--max-sources {})",
            daily.capped, cfg.max_sources
        );
    }

    // Phase 5 — durable run report FIRST (so the rebuilt index includes this
    // run), then read model + console refresh. The report does NOT claim the
    // refresh happened — index/console paths are printed, not recorded, since
    // they are written after it.
    report.set_reader(planned, &daily);
    let report_rel =
        ovp_daily::write_run_report(&args.vault_root, &report).map_err(CliError::Io)?;

    let model = build_index(&args.vault_root, &args.date, Some(&args.run_id))
        .map_err(CliError::Io)?;
    let index_rel = write_index(&args.vault_root, &model).map_err(CliError::Io)?;
    let console_rel = write_console(&args.vault_root, &model).map_err(CliError::Io)?;

    let failed = daily.failed();
    println!(
        "  done: {} processed, {failed} failed, {} skipped (report: {report_rel})",
        daily.processed.len(), daily.skipped
    );
    println!("  index: {index_rel} · console: {console_rel}");

    if failed > 0 {
        // Honest retry guidance: a 3rd failure means the source is now
        // BLOCKED, not silently retried.
        let prior: std::collections::HashMap<&str, usize> =
            work.todo.iter().map(|i| (i.sha256.as_str(), i.prior_failures)).collect();
        let newly_blocked = daily
            .processed
            .iter()
            .filter(|r| r.status == RunStatus::Failed)
            .filter(|r| {
                prior.get(r.source_sha256.as_str()).copied().unwrap_or(0) + 1
                    >= ovp_daily::MAX_FAILURES_BEFORE_BLOCKED
            })
            .count();
        let retryable = failed - newly_blocked;
        let mut msg = format!("daily: {failed} source(s) failed (recorded in the ledger");
        if retryable > 0 {
            msg.push_str(&format!("; {retryable} will be retried next run"));
        }
        if newly_blocked > 0 {
            msg.push_str(&format!(
                "; {newly_blocked} now BLOCKED after {} failures — review and rerun with --retry-blocked",
                ovp_daily::MAX_FAILURES_BEFORE_BLOCKED
            ));
        }
        msg.push(')');
        return Err(CliError::Gate(msg));
    }
    Ok(())
}

fn build_pinboard_fetch(args: &DailyArgs) -> Result<Box<dyn PinboardFetch>, CliError> {
    if args.pinboard_live && args.pinboard_fixture.is_some() {
        return Err(CliError::Io("pass either --pinboard-fixture or --pinboard-live, not both".into()));
    }
    if let Some(path) = &args.pinboard_fixture {
        return Ok(Box::new(FixturePinboardFetch::new(path)));
    }
    live_pinboard_fetch()
}

#[cfg(feature = "pinboard-live")]
pub fn live_pinboard_fetch() -> Result<Box<dyn PinboardFetch>, CliError> {
    Ok(Box::new(ovp_intake::LivePinboardFetch::from_env().map_err(CliError::Io)?))
}

#[cfg(not(feature = "pinboard-live"))]
pub fn live_pinboard_fetch() -> Result<Box<dyn PinboardFetch>, CliError> {
    Err(CliError::Io(
        "live pinboard requires a build with `--features pinboard-live` \
         (and PINBOARD_TOKEN in the environment); offline runs use --pinboard-fixture <export.json>"
            .into(),
    ))
}
