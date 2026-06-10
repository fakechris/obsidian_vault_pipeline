//! `daily` — the M30 blessed daily loop on the real vault. Thin shim over
//! `ovp-daily`: plan (scan + content-hash dedup against the durable ledger),
//! optionally stop at `--dry-run`, else process each new source through the
//! reader trunk, writing packs to `40-Resources/Reader/` and appending to
//! `.ovp/daily-runs.jsonl` + `60-Logs/pipeline.jsonl`.
//!
//! Replay is the default client (consistent with every other command); a real
//! daily run on fresh content is `--client live`. `--max-sources` is the
//! OVP_RULES rate limit on LLM loops.

use std::path::PathBuf;

use ovp_daily::{plan_daily, read_daily_ledger, run_daily, DailyConfig, RunStatus};
use ovp_domain::VaultLayout;

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
}

pub fn run(args: DailyArgs) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let inbox = args.inbox.unwrap_or_else(|| args.vault_root.join(layout.inbox_raw_dir()));
    let ledger_path = args.vault_root.join(layout.daily_ledger());

    let ledger = read_daily_ledger(&ledger_path).map_err(CliError::Io)?;
    let work = plan_daily(&inbox, &args.vault_root, &ledger).map_err(CliError::Io)?;

    println!("daily [{}]: inbox {}", args.date, inbox.display());
    println!(
        "  plan: {} new source(s), {} skipped (already succeeded / duplicate content)",
        work.todo.len(),
        work.skipped.len()
    );
    if args.dry_run {
        for item in &work.todo {
            println!("  would process: {} ({})", item.rel, &item.sha256[..8]);
        }
        println!("  dry-run: nothing written.");
        return Ok(());
    }

    let cache_dir =
        args.cache_dir.unwrap_or_else(|| args.vault_root.join(layout.daily_cassette_dir()));
    let mut make_client =
        || build_client(args.client_kind, &cache_dir).map_err(|e| e.to_string());

    let cfg = DailyConfig {
        vault_root: args.vault_root.clone(),
        date: args.date,
        run_id: args.run_id,
        max_sources: args.max_sources,
    };
    let report = run_daily(&cfg, &work, &mut make_client).map_err(CliError::Io)?;

    for rec in &report.processed {
        match rec.status {
            RunStatus::Succeeded => println!(
                "  ok   {} → {} (units={} cards={})",
                rec.source_path,
                rec.pack_dir.as_deref().unwrap_or("?"),
                rec.units,
                rec.cards
            ),
            RunStatus::Failed => println!(
                "  FAIL {} — {}",
                rec.source_path,
                rec.reason.as_deref().unwrap_or("unknown")
            ),
        }
    }
    if report.capped > 0 {
        println!(
            "  capped: {} source(s) left for the next run (--max-sources {})",
            report.capped, cfg.max_sources
        );
    }
    println!(
        "  done: {} processed, {} failed, {} skipped (ledger: {})",
        report.processed.len(),
        report.failed(),
        report.skipped,
        ledger_path.display()
    );

    let failed = report.failed();
    if failed > 0 {
        return Err(CliError::Gate(format!(
            "daily: {failed} source(s) failed (recorded in the ledger; they will be retried next run)"
        )));
    }
    Ok(())
}
