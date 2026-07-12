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

use ovp_console::{write_console, write_ops_pages};
use ovp_daily::{
    plan_daily, read_daily_ledger, run_daily_with_progress, succeeded_hashes, DailyConfig,
    DailyRunRecord, RunReport, RunStatus,
};
use ovp_domain::VaultLayout;
use ovp_index::{build_evidence, build_index, write_evidence, write_index};
use ovp_enrich::github::{
    enrich_github_repos, parse_github_repo_url, FixtureGitHubFetch, GitHubFetch,
};
use ovp_enrich::web_fetch::{enrich_needs_content, FixtureWebFetch, WebFetch};
use ovp_intake::{sweep_intake, sync_pinboard, FixturePinboardFetch, IntakeConfig, PinboardFetch};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

/// `println!` + explicit stdout flush. Daily runs are watched through nohup /
/// pipes (block-buffered stdout) and the reader phase can run for hours; every
/// phase header and per-source line must hit the log the moment it is printed —
/// a healthy 46-minute run was once killed as "hung" because nothing showed.
macro_rules! sayln {
    ($($arg:tt)*) => {{
        println!($($arg)*);
        use std::io::Write as _;
        let _ = std::io::stdout().flush();
    }};
}

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
    /// Pinboard capture: only bookmarks posted on/after this date
    /// (YYYY-MM-DD). Passthrough to the pinboard first-sync flood guard.
    pub pinboard_since: Option<String>,
    /// Pinboard capture: at most N of the newest new bookmarks.
    pub pinboard_max: Option<usize>,
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

/// Entry point. Wraps [`run_inner`] in the run-liveness heartbeat
/// ([`HeartbeatGuard`]): the guard is armed the moment a real (non-dry) run
/// starts, finalized `completed` / `failed` on the two clean exit paths, and
/// left to its RAII `Drop` — which writes `status: aborted` — if the run
/// panics or an error propagates out past the finalize. This is the OVP2
/// observability P0: an unattended run that crashes before its end-of-run
/// report is still visible in `.ovp/last-run.json`.
///
/// A dry run does no mutating work, takes no lock, and writes no heartbeat.
pub fn run(args: DailyArgs) -> Result<(), CliError> {
    if args.dry_run {
        return run_inner(args, None, None);
    }

    // Acquire the run lock BEFORE arming the heartbeat. One mutating run at a
    // time (cron + manual overlap would double-spend LLM calls and race the
    // lifecycle moves). A contender that can't get the lock must NOT touch
    // `.ovp/last-run.json` — otherwise it would write "running", then fail the
    // acquire, then finalize itself "failed", clobbering the legitimate active
    // run's heartbeat with a false failure. So the guard is only started once
    // the lock is held.
    let lock = ovp_intake::RunLock::acquire(&args.vault_root).map_err(CliError::Io)?;

    let (guard, warn) = ovp_daily::HeartbeatGuard::start(&args.vault_root, &args.run_id);
    if let Some(w) = warn {
        sayln!("  warn {w}");
    }
    // Track counts across the inner run so a clean completion can finalize with
    // real numbers. `finalize` is threaded through the Ok path; the Err path
    // and any panic fall to `finalize_failed` / the Drop-guard's abort write.
    let mut counts = ovp_daily::RunCounts::default();
    match run_inner(args, Some(lock), Some(&mut counts)) {
        Ok(()) => {
            if let Some(w) = guard.finalize_completed(counts) {
                sayln!("  warn {w}");
            }
            Ok(())
        }
        Err(e) => {
            // Record the terminal failure with the operator-facing message,
            // then re-propagate so the exit code and stderr are unchanged.
            let _ = guard.finalize_failed(&e.to_string());
            Err(e)
        }
    }
}

fn run_inner(
    args: DailyArgs,
    // The run lock, acquired by `run()` BEFORE the heartbeat (so a lock
    // contender never writes a heartbeat). Held for the lifetime of this call.
    // None only for dry runs, which take no lock.
    _lock: Option<ovp_intake::RunLock>,
    mut counts: Option<&mut ovp_daily::RunCounts>,
) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let inbox = args.inbox.clone().unwrap_or_else(|| args.vault_root.join(layout.inbox_raw_dir()));
    let ledger_path = args.vault_root.join(layout.daily_ledger());
    let intake_cfg = IntakeConfig::new(args.vault_root.clone(), args.date.clone(), args.run_id.clone());

    let mut report = RunReport::new(&args.run_id, &args.date);
    sayln!("daily [{}]: vault {}", args.date, args.vault_root.display());

    // Phase 1 — pinboard capture (optional). Inherits the first-sync flood
    // guard: an unfiltered sync with >500 NEW bookmarks fails this phase
    // loudly (no --yes-all here; a deliberate full sync is `ovp2
    // pinboard-sync --yes-all`).
    if args.pinboard_fixture.is_some() || args.pinboard_live {
        let mut fetch = build_pinboard_fetch(&args)?;
        let opts = ovp_intake::PinboardSyncOptions {
            since: args.pinboard_since.clone(),
            max: args.pinboard_max,
            yes_all: false,
        };
        let outcome = sync_pinboard(&intake_cfg, fetch.as_mut(), args.dry_run, &opts)
            .map_err(CliError::Io)?;
        sayln!(
            "  pinboard: {} fetched, {} new note(s), {} known ({})",
            outcome.fetched, outcome.new_notes.len(), outcome.skipped_known, outcome.origin
        );
        if outcome.guard_would_abort {
            sayln!(
                "  WARNING: a REAL run would ABORT at the pinboard phase — {} new bookmark(s) \
                 exceed the {}-note first-sync guard; pass --pinboard-since or --pinboard-max \
                 (or run `ovp2 pinboard-sync --yes-all` once, deliberately)",
                outcome.new_notes.len(),
                ovp_intake::FIRST_SYNC_GUARD_MAX_NEW,
            );
        }
        report.pinboard = Some((&outcome).into());
    }

    // Phase 2 — intake sweep (capture dirs → 01-Raw).
    let mut dry_run_pending_ingest = 0usize;
    let mut sweep_needs_content = Vec::new();
    if !args.no_intake {
        let done = succeeded_hashes(&read_daily_ledger(&ledger_path).map_err(CliError::Io)?);
        let sweep = sweep_intake(&intake_cfg, &done, args.dry_run).map_err(CliError::Io)?;
        sayln!(
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
            sayln!(
                "  enrich: {} needs-content URL(s), {} enriched, {} failed",
                needs_content_items.len(), enriched, failed,
            );
            for r in &results {
                if !r.updated
                    && let Some(err) = &r.fetch.error {
                        sayln!("    skip {}: {err}", r.url);
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
            sayln!(
                "  github: {} repo URL(s), {} enriched, {} failed",
                github_items.len(), written, failed,
            );
            for r in &results {
                if !r.written
                    && let Some(err) = &r.fetch.error {
                        sayln!("    skip {}/{}: {err}", r.owner, r.repo);
                    }
            }
        }
    }

    // Phase 3 — plan.
    let ledger = read_daily_ledger(&ledger_path).map_err(CliError::Io)?;
    let work = plan_daily(&inbox, &args.vault_root, &ledger, args.retry_blocked)
        .map_err(CliError::Io)?;
    sayln!(
        "  plan: {} new source(s), {} skipped, {} blocked",
        work.todo.len(), work.skipped.len(), work.blocked.len()
    );
    for item in &work.blocked {
        sayln!("    blocked ({} failures): {} — rerun with --retry-blocked after review",
            item.prior_failures, item.rel);
    }
    if args.dry_run {
        for item in &work.todo {
            sayln!("  would process: {} ({})", item.rel, &item.sha256[..8]);
        }
        if dry_run_pending_ingest > 0 {
            sayln!(
                "  note: {dry_run_pending_ingest} capture(s) above would ALSO be ingested into \
                 01-Raw and then planned on a real run (dry-run intake moves nothing)"
            );
        }
        sayln!("  dry-run: nothing written.");
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
    // Live per-source progress: the reader phase is the long one (up to hours
    // at high --max-sources), so each ok/FAIL line prints — flushed — the
    // moment its source finishes, not after run_daily returns.
    let mut on_source = |rec: &DailyRunRecord| match rec.status {
        RunStatus::Succeeded => sayln!(
            "  ok   {} → {} (units={} cards={}){}",
            rec.source_path,
            rec.pack_dir.as_deref().unwrap_or("?"),
            rec.units,
            rec.cards,
            rec.moved_to.as_deref().map(|m| format!(" moved→{m}")).unwrap_or_default(),
        ),
        RunStatus::Failed => sayln!(
            "  FAIL {} — {}",
            rec.source_path,
            rec.reason.as_deref().unwrap_or("unknown")
        ),
    };
    let daily = run_daily_with_progress(&cfg, &work, &mut make_client, &mut on_source)
        .map_err(CliError::Io)?;

    for w in &daily.lifecycle_warnings {
        sayln!("  warn {w}");
    }
    if daily.capped > 0 {
        sayln!(
            "  capped: {} source(s) left for the next run (--max-sources {})",
            daily.capped, cfg.max_sources
        );
    }

    // Phase 4.5 — image download for succeeded packs (optional).
    if !args.no_images && !args.dry_run
        && let Some(mut downloader) = build_image_downloader(&args)? {
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
                    sayln!(
                        "  images: {} found, {} downloaded across {} pack(s)",
                        total_images, total_downloaded, succeeded_packs.len()
                    );
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

    // Heartbeat counts: populated once the model is built so BOTH the clean
    // completion and the failed-source (Gate) exit below finalize with real
    // numbers. `queued_after` reads the rebuilt backlog gauge (post-run queue
    // depth), not just this run's cap remainder.
    if let Some(c) = counts.take() {
        *c = ovp_daily::RunCounts {
            processed: daily.processed.len(),
            failed: daily.failed(),
            blocked: daily.blocked,
            capped: daily.capped,
            queued_after: model.totals.queued,
        };
    }

    let index_rel = write_index(&args.vault_root, &model).map_err(CliError::Io)?;
    let evidence = build_evidence(&args.vault_root, &args.date, &model).map_err(CliError::Io)?;
    let evidence_rel = write_evidence(&args.vault_root, &evidence).map_err(CliError::Io)?;
    let console_rel = write_console(&args.vault_root, &model).map_err(CliError::Io)?;
    let _ops_pages = write_ops_pages(&args.vault_root, &model).map_err(CliError::Io)?;

    // Phase 6 — optional daily digest (ephemeral reuse surface).
    if !args.no_digest {
        let data = ovp_memory::digest::collect_digest_data(&model, &args.date);
        let content = ovp_memory::digest::render_plain_digest(&data);
        if let Ok(dpath) = ovp_memory::digest::write_digest(&args.vault_root, &args.date, &content) {
            let drel = dpath.strip_prefix(&args.vault_root).unwrap_or(&dpath).display();
            sayln!("  digest: {drel}");
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
            sayln!("  working-memory: {wm_rel}");
        }
    }

    let failed = daily.failed();
    sayln!(
        "  done: {} processed, {failed} failed, {} skipped (report: {report_rel})",
        daily.processed.len(), daily.skipped
    );
    sayln!("  index: {index_rel} · evidence: {evidence_rel} · console: {console_rel}");

    // Semantic-theme staleness HINT (never an action): daily must not
    // auto-run crystal-themes — a cold model cache means a surprise ~450MB
    // download — but the operator should know when new packs are unthemed.
    if let Some(n) = stale_theme_packs(&args.vault_root, &model)
        && n > 0 {
            sayln!(
                "  themes: {n} pack(s) not in .ovp/crystal/themes.json — run `ovp2 crystal-themes` to re-theme"
            );
        }

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

/// How many packs in the read model are missing from the semantic-themes
/// projection. `None` = nothing to hint about (no packs); a missing or
/// corrupt themes.json counts every pack as unthemed (the hint is exactly
/// how the operator learns to run `crystal-themes`). Pure read; never blocks.
fn stale_theme_packs(
    vault_root: &std::path::Path,
    model: &ovp_index::IndexModel,
) -> Option<usize> {
    if model.packs.is_empty() {
        return None;
    }
    let themes = ovp_domain::crystal::themes::ThemesFile::load(
        &vault_root.join(".ovp/crystal/themes.json"),
    )
    .ok()
    .flatten();
    let count = match &themes {
        Some(t) => model
            .packs
            .iter()
            .filter(|p| {
                let case_id = p.pack_dir.rsplit('/').next().unwrap_or(&p.pack_dir);
                !t.packs.contains_key(case_id)
            })
            .count(),
        None => model.packs.len(),
    };
    Some(count)
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

#[cfg(test)]
mod tests {
    use super::{run, stale_theme_packs, DailyArgs};
    use crate::commands::client::ClientKind;

    fn min_args(vault: std::path::PathBuf) -> DailyArgs {
        DailyArgs {
            vault_root: vault,
            inbox: None,
            cache_dir: None,
            client_kind: ClientKind::Replay,
            date: "2026-07-12".into(),
            run_id: "daily-2026-07-12".into(),
            dry_run: false,
            max_sources: 0,
            no_intake: true,
            pinboard_fixture: None,
            pinboard_live: false,
            pinboard_since: None,
            pinboard_max: None,
            no_lifecycle: false,
            retry_blocked: false,
            web_fetch_fixture: None,
            web_fetch_live: false,
            github_fixture: None,
            github_live: false,
            no_images: true,
            image_fixture: None,
            image_live: false,
            no_digest: true,
        }
    }

    /// P2 regression: a contender that cannot get the run lock must NEVER touch
    /// `.ovp/last-run.json` — otherwise it would clobber the active run's
    /// legitimate "running" heartbeat with a false "failed". The lock is
    /// acquired BEFORE the heartbeat guard is armed, so the contender errors
    /// out first and the heartbeat is left exactly as the active run wrote it.
    #[test]
    fn lock_contender_does_not_clobber_active_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().to_path_buf();

        // Simulate the legitimate active run: hold the lock and write its
        // "running" heartbeat.
        let _held = ovp_intake::RunLock::acquire(&vault).expect("acquire lock");
        let (active_guard, _) =
            ovp_daily::HeartbeatGuard::start(&vault, "daily-active");
        // Keep the active guard alive (its Drop would otherwise write aborted).
        let active = ovp_daily::read_last_run(&vault).unwrap().unwrap();
        assert_eq!(active.status, ovp_daily::LastRunStatus::Running);
        assert_eq!(active.run_id, "daily-active");

        // The contender cannot get the lock → errors, writes NOTHING.
        let err = run(min_args(vault.clone()));
        assert!(err.is_err(), "contender must fail to acquire the lock");

        // The heartbeat is still the ACTIVE run's untouched "running" record.
        let after = ovp_daily::read_last_run(&vault).unwrap().unwrap();
        assert_eq!(after.status, ovp_daily::LastRunStatus::Running);
        assert_eq!(after.run_id, "daily-active", "contender must not overwrite the heartbeat");

        // Finalize the active run so its guard's Drop doesn't write aborted.
        active_guard.finalize_completed(ovp_daily::RunCounts::default());
    }

    fn model_with_packs(dirs: &[&str]) -> ovp_index::IndexModel {
        ovp_index::IndexModel {
            schema: "test".into(),
            date: "2026-07-10".into(),
            run_id: None,
            totals: Default::default(),
            sources: vec![],
            packs: dirs
                .iter()
                .map(|d| ovp_index::PackRow {
                    pack_dir: (*d).to_string(),
                    title: "t".into(),
                    date: None,
                    units: 1,
                    cards: 1,
                    json_repaired: false,
                    card_titles: vec![],
                    source_sha256: None,
                })
                .collect(),
            claims: vec![],
            runs: vec![],
            ops: Default::default(),
        }
    }

    #[test]
    fn stale_hint_counts_unthemed_packs() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        // No packs → no hint at all.
        assert_eq!(stale_theme_packs(vault, &model_with_packs(&[])), None);
        // Packs but no themes.json → everything is unthemed.
        let model = model_with_packs(&[
            "40-Resources/Reader/case-a",
            "40-Resources/Reader/case-b",
        ]);
        assert_eq!(stale_theme_packs(vault, &model), Some(2));
        // themes.json covering case-a only → one stale pack.
        let store = vault.join(".ovp/crystal");
        std::fs::create_dir_all(&store).unwrap();
        std::fs::write(
            store.join("themes.json"),
            serde_json::json!({
                "schema": "ovp.themes/v1",
                "model": "m",
                "params": {"k": 10, "cosine_threshold": 0.5, "resolution": 1.5,
                            "seed": 42, "text_prefix": "", "head_chars": 1500},
                "generated_from": "h",
                "packs": {"case-a": 0},
                "communities": [{"id": 0, "label": "L", "label_zh": "L",
                                  "keywords": [], "size": 1}]
            })
            .to_string(),
        )
        .unwrap();
        assert_eq!(stale_theme_packs(vault, &model), Some(1));
    }
}
