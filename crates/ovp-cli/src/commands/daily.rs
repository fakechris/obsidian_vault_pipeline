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
use ovp_enrich::github::{
    enrich_github_repos, parse_github_repo_url, FixtureGitHubFetch, GitHubFetch,
};
use ovp_enrich::web_fetch::{enrich_needs_content, FixtureWebFetch, WebFetch};
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
    /// Web fetch fixture directory for enriching needs-content sources.
    pub web_fetch_fixture: Option<PathBuf>,
    /// Enrich needs-content sources via live web fetch.
    pub web_fetch_live: bool,
    /// GitHub enrichment fixture directory for repo URLs.
    pub github_fixture: Option<PathBuf>,
    /// Enrich GitHub repo URLs via live API (requires GITHUB_TOKEN env).
    pub github_live: bool,
    /// Skip image download post-processing for reader packs.
    pub no_images: bool,
    /// Image download fixture directory (offline testing).
    pub image_fixture: Option<PathBuf>,
    /// Download images via live HTTP (requires web-fetch-live feature).
    pub image_live: bool,
    /// Skip daily digest generation.
    pub no_digest: bool,
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
    let mut sweep_needs_content = Vec::new();
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
        sweep_needs_content = sweep.needs_content.clone();
        report.intake = Some((&sweep).into());
    }

    // Phase 2.5 — web fetch enrichment (optional).
    // Enriches needs-content sources (from the intake sweep) by fetching their
    // URLs. Successfully enriched files get enough body for plan_daily to pick
    // them up as reader candidates.
    if (args.web_fetch_fixture.is_some() || args.web_fetch_live) && !args.dry_run {
        let needs_content_items: Vec<(String, String)> = sweep_needs_content
            .iter()
            .filter_map(|rec| {
                rec.url.as_ref().map(|u| (rec.from.clone(), u.clone()))
            })
            .collect();
        if !needs_content_items.is_empty() {
            let mut fetcher = build_web_fetcher(&args)?;
            let results = enrich_needs_content(
                fetcher.as_mut(),
                &args.vault_root,
                &needs_content_items,
            );
            let enriched = results.iter().filter(|r| r.updated).count();
            let failed = results.iter().filter(|r| !r.updated).count();
            println!(
                "  enrich: {} needs-content URL(s), {} enriched, {} failed",
                needs_content_items.len(), enriched, failed,
            );
            for r in &results {
                if !r.updated {
                    if let Some(err) = &r.fetch.error {
                        println!("    skip {}: {err}", r.url);
                    }
                }
            }
        }
    }

    // Phase 2.6 — GitHub enrichment (optional).
    // Enriches needs-content sources whose URLs point to GitHub repos.
    if (args.github_fixture.is_some() || args.github_live) && !args.dry_run {
        let github_items: Vec<(String, String)> = sweep_needs_content
            .iter()
            .filter_map(|rec| {
                rec.url.as_ref().and_then(|u| {
                    parse_github_repo_url(u).map(|_| (rec.from.clone(), u.clone()))
                })
            })
            .collect();
        if !github_items.is_empty() {
            let mut fetcher = build_github_fetcher(&args)?;
            let results = enrich_github_repos(
                fetcher.as_mut(),
                &args.vault_root,
                &github_items,
            );
            let written = results.iter().filter(|r| r.written).count();
            let failed = results.iter().filter(|r| !r.written).count();
            println!(
                "  github: {} repo URL(s), {} enriched, {} failed",
                github_items.len(), written, failed,
            );
            for r in &results {
                if !r.written {
                    if let Some(err) = &r.fetch.error {
                        println!("    skip {}/{}: {err}", r.owner, r.repo);
                    }
                }
            }
        }
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

    // Phase 4.5 — image download for succeeded packs (optional).
    if !args.no_images && !args.dry_run {
        if let Some(mut downloader) = build_image_downloader(&args)? {
            let succeeded_packs: Vec<PathBuf> = daily
                .processed
                .iter()
                .filter(|r| r.status == RunStatus::Succeeded)
                .filter_map(|r| r.pack_dir.as_ref())
                .map(|d| args.vault_root.join(d))
                .collect();
            if !succeeded_packs.is_empty() {
                use ovp_enrich::image_download::{
                    process_pack_images, ImageDownloadConfig,
                };
                let img_config = ImageDownloadConfig {
                    attachments_dir: PathBuf::from("attachments"),
                    ..Default::default()
                };
                let mut total_images = 0usize;
                let mut total_downloaded = 0usize;
                for pack_dir in &succeeded_packs {
                    let results = process_pack_images(
                        pack_dir,
                        &args.vault_root,
                        downloader.as_mut(),
                        &img_config,
                    );
                    for r in &results {
                        total_images += r.images_found;
                        total_downloaded += r.images_downloaded;
                    }
                }
                if total_images > 0 {
                    println!(
                        "  images: {} found, {} downloaded across {} pack(s)",
                        total_images, total_downloaded, succeeded_packs.len()
                    );
                }
            }
        }
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

    // Phase 6 — optional daily digest (ephemeral reuse surface).
    if !args.no_digest {
        let data = ovp_memory::digest::collect_digest_data(&model, &args.date);
        let content = ovp_memory::digest::render_plain_digest(&data);
        if let Ok(dpath) = ovp_memory::digest::write_digest(&args.vault_root, &args.date, &content) {
            let drel = dpath.strip_prefix(&args.vault_root).unwrap_or(&dpath).display();
            println!("  digest: {drel}");
        }
    }

    // Phase 6b — working memory (ephemeral context package).
    {
        let wm_args = ovp_memory::working_memory::WorkingMemoryArgs {
            date: args.date.clone(),
            ..Default::default()
        };
        let wm_content = ovp_memory::working_memory::build_working_memory(&model, &wm_args);
        if let Ok(wm_path) = ovp_memory::working_memory::write_working_memory(&args.vault_root, &wm_content) {
            let wm_rel = wm_path.strip_prefix(&args.vault_root).unwrap_or(&wm_path).display();
            println!("  working-memory: {wm_rel}");
        }
    }

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

fn build_web_fetcher(args: &DailyArgs) -> Result<Box<dyn WebFetch>, CliError> {
    if args.web_fetch_live && args.web_fetch_fixture.is_some() {
        return Err(CliError::Io(
            "pass either --web-fetch-fixture or --web-fetch-live, not both".into(),
        ));
    }
    if let Some(path) = &args.web_fetch_fixture {
        return Ok(Box::new(FixtureWebFetch::new(path)));
    }
    live_web_fetch()
}

#[cfg(feature = "web-fetch-live")]
fn live_web_fetch() -> Result<Box<dyn WebFetch>, CliError> {
    use ovp_enrich::web_fetch::LiveWebFetch;
    Ok(Box::new(LiveWebFetch::with_defaults().map_err(CliError::Io)?))
}

#[cfg(not(feature = "web-fetch-live"))]
fn live_web_fetch() -> Result<Box<dyn WebFetch>, CliError> {
    Err(CliError::Io(
        "live web fetch requires a build with `--features web-fetch-live`; \
         offline runs use --web-fetch-fixture <dir>"
            .into(),
    ))
}

fn build_github_fetcher(args: &DailyArgs) -> Result<Box<dyn GitHubFetch>, CliError> {
    if args.github_live && args.github_fixture.is_some() {
        return Err(CliError::Io(
            "pass either --github-fixture or --github-live, not both".into(),
        ));
    }
    if let Some(path) = &args.github_fixture {
        return Ok(Box::new(FixtureGitHubFetch::new(path)));
    }
    live_github_fetch()
}

#[cfg(feature = "github-live")]
fn live_github_fetch() -> Result<Box<dyn GitHubFetch>, CliError> {
    use ovp_enrich::github::LiveGitHubFetch;
    Ok(Box::new(LiveGitHubFetch::from_env().map_err(CliError::Io)?))
}

#[cfg(not(feature = "github-live"))]
fn live_github_fetch() -> Result<Box<dyn GitHubFetch>, CliError> {
    Err(CliError::Io(
        "live GitHub fetch requires a build with `--features github-live`; \
         offline runs use --github-fixture <dir>"
            .into(),
    ))
}

fn build_image_downloader(
    args: &DailyArgs,
) -> Result<Option<Box<dyn ovp_enrich::image_download::ImageDownloader>>, CliError> {
    use ovp_enrich::image_download::FixtureImageDownloader;
    if args.image_live && args.image_fixture.is_some() {
        return Err(CliError::Io(
            "pass either --image-fixture or --image-live, not both".into(),
        ));
    }
    if let Some(path) = &args.image_fixture {
        return Ok(Some(Box::new(FixtureImageDownloader::new(path))));
    }
    if args.image_live {
        return Ok(Some(live_image_download()?));
    }
    Ok(None)
}

#[cfg(feature = "web-fetch-live")]
fn live_image_download() -> Result<Box<dyn ovp_enrich::image_download::ImageDownloader>, CliError> {
    use ovp_enrich::image_download::LiveImageDownloader;
    Ok(Box::new(LiveImageDownloader::new().map_err(CliError::Io)?))
}

#[cfg(not(feature = "web-fetch-live"))]
fn live_image_download() -> Result<Box<dyn ovp_enrich::image_download::ImageDownloader>, CliError> {
    Err(CliError::Io(
        "live image download requires a build with `--features web-fetch-live`; \
         offline runs use --image-fixture <dir>"
            .into(),
    ))
}
