//! Pinboard capture adapter (M31). The effect boundary is the
//! [`PinboardFetch`] trait:
//!
//! - [`FixturePinboardFetch`] reads a Pinboard JSON export file (the exact
//!   `posts/all?format=json` array) — the offline/replay path, always compiled.
//! - `LivePinboardFetch` (behind the `pinboard-live` feature) calls the real
//!   API. The token comes ONLY from the `PINBOARD_TOKEN` env var
//!   (`username:TOKEN`, same variable the legacy processor used) and is never
//!   logged, persisted, or echoed in errors.
//!
//! `sync_pinboard` materializes each NEW bookmark (URL-deduped against the
//! pinboard ledger AND the intake ledger) as a markdown note in
//! `50-Inbox/02-Pinboard/`, where the normal intake sweep picks it up: notes
//! with enough body text flow to `01-Raw` and the reader; bare bookmarks are
//! flagged `needs_content` for the operator to enrich.
//!
//! FIRST-SYNC FLOOD GUARD: `posts/all` returns the account's ENTIRE history,
//! so a first sync against an old Pinboard account can materialize tens of
//! thousands of notes in one run (observed live: 50,714 notes / 198MB) and
//! flood the next intake sweep + live web enrichment. `sync_pinboard` takes
//! [`PinboardSyncOptions`] (`since` / `until` / `max` narrowing) and, when
//! none is given, aborts before writing anything if more than
//! [`FIRST_SYNC_GUARD_MAX_NEW`] new bookmarks would be created — unless
//! `yes_all` is set. Dry runs report instead of aborting.
//!
//! ONGOING SYNC + BACKFILL: coverage is tracked on axis A (bookmark day),
//! derived from the append-only ledgers. The daily incremental sync resumes
//! from [`watermark_high`] (`--pinboard-since auto` in the CLI);
//! [`sync_pinboard_backfill`] walks history DOWN one day-window per run
//! below [`coverage_floor`], recording each completed window in
//! `.ovp/pinboard-backfill.jsonl` so empty days advance and a `max`-truncated
//! window is retried rather than skipped.

use std::collections::HashSet;
use std::path::Path;

use serde::{Deserialize, Serialize};

use ovp_domain::VaultLayout;

use crate::ledger::{known_urls, read_intake_ledger};
use crate::sweep::IntakeConfig;
use crate::vaultops::{
    append_jsonl, append_pipeline_event, hex_sha256, read_jsonl, rel_to, write_new,
    PipelineLogEvent,
};

/// Schema tag for pinboard-sync ledger records.
pub const PINBOARD_SCHEMA: &str = "ovp.pinboard/v1";

/// Hard ceiling on NEW bookmarks in an unfiltered sync. Beyond this the run
/// aborts before writing anything and the operator must narrow with
/// `since`/`max` or opt in explicitly with `yes_all` (see module docs for the
/// first-sync flood this prevents).
pub const FIRST_SYNC_GUARD_MAX_NEW: usize = 500;

/// Candidate-narrowing and flood-guard options for [`sync_pinboard`].
/// Filters only NARROW the candidate set — ledger/dedup semantics are
/// unchanged.
#[derive(Debug, Clone, Default)]
pub struct PinboardSyncOptions {
    /// Only materialize bookmarks whose Pinboard timestamp is on/after this
    /// date (`YYYY-MM-DD`). Bookmarks without a usable timestamp are
    /// excluded when this is set (they cannot be shown to be recent).
    pub since: Option<String>,
    /// Only materialize bookmarks whose Pinboard timestamp is on/before this
    /// date (`YYYY-MM-DD`). Combined with `since` this gives a day window —
    /// the shape day-by-day backfill uses.
    pub until: Option<String>,
    /// Materialize at most this many of the NEWEST new bookmarks; older ones
    /// are left for later runs.
    pub max: Option<usize>,
    /// Explicitly allow an unfiltered sync past the first-sync flood guard
    /// ([`FIRST_SYNC_GUARD_MAX_NEW`]).
    pub yes_all: bool,
}

/// One bookmark in Pinboard's `posts/all` JSON format (export file and live
/// API agree on this shape). Unknown fields are ignored.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PinboardPost {
    #[serde(default)]
    pub href: String,
    /// Pinboard calls the title "description".
    #[serde(default)]
    pub description: String,
    /// The free-text note body.
    #[serde(default)]
    pub extended: String,
    /// ISO-8601 bookmark time.
    #[serde(default)]
    pub time: String,
    /// Space-separated tags.
    #[serde(default)]
    pub tags: String,
}

/// The capture effect boundary: where bookmarks come from.
pub trait PinboardFetch {
    fn fetch_all(&mut self) -> Result<Vec<PinboardPost>, String>;
    /// Fetch posts on/after `since` (`YYYY-MM-DD`). Default filters
    /// [`fetch_all`] client-side; live adapters SHOULD push the bound to the
    /// API (`fromdt`) so large accounts do not 500 on full `posts/all`.
    fn fetch_since(&mut self, since: &str) -> Result<Vec<PinboardPost>, String> {
        let all = self.fetch_all()?;
        Ok(all
            .into_iter()
            .filter(|p| p.time.get(..10).is_some_and(|d| d >= since))
            .collect())
    }
    /// Human-readable origin for the run report (file path / "pinboard API").
    fn origin(&self) -> String;
}

/// Offline fetcher over a Pinboard JSON export file.
pub struct FixturePinboardFetch {
    path: std::path::PathBuf,
}

impl FixturePinboardFetch {
    pub fn new(path: impl Into<std::path::PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

impl PinboardFetch for FixturePinboardFetch {
    fn fetch_all(&mut self) -> Result<Vec<PinboardPost>, String> {
        let raw = std::fs::read_to_string(&self.path)
            .map_err(|e| format!("reading pinboard export {}: {e}", self.path.display()))?;
        serde_json::from_str(&raw)
            .map_err(|e| format!("parsing pinboard export {}: {e}", self.path.display()))
    }

    fn origin(&self) -> String {
        format!("export file {}", self.path.display())
    }
}

/// Live Pinboard API fetcher. Compiled only with `--features pinboard-live`.
#[cfg(feature = "pinboard-live")]
pub struct LivePinboardFetch {
    token: String,
    base_url: String,
    timeout_secs: u64,
}

#[cfg(feature = "pinboard-live")]
impl LivePinboardFetch {
    /// Token from `PINBOARD_TOKEN` (format `username:TOKEN`). Optional
    /// `PINBOARD_API_BASE` override for testing. Fails loud when absent.
    pub fn from_env() -> Result<Self, String> {
        let token = std::env::var("PINBOARD_TOKEN")
            .ok()
            .map(|t| t.trim().to_string())
            .filter(|t| !t.is_empty())
            .ok_or_else(|| {
                "PINBOARD_TOKEN is not set (expected `username:TOKEN`; see docs/operator-runbook.md)"
                    .to_string()
            })?;
        let base_url = std::env::var("PINBOARD_API_BASE")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| "https://api.pinboard.in/v1".to_string());
        Ok(Self { token, base_url, timeout_secs: 60 })
    }
}

#[cfg(feature = "pinboard-live")]
impl LivePinboardFetch {
    fn get_posts_all(&self, fromdt: Option<&str>) -> Result<Vec<PinboardPost>, String> {
        // Pinboard's unfiltered `posts/all` returns the entire history and
        // frequently HTTP 500s on large accounts (observed live: token OK,
        // `posts/update` 200, bare `posts/all` 500; `fromdt` 200). When we
        // already have a watermark, always push it server-side.
        let mut url = format!(
            "{}/posts/all?format=json&auth_token={}",
            self.base_url, self.token
        );
        if let Some(day) = fromdt {
            // API accepts ISO datetime; day floor is enough for resume.
            url.push_str(&format!("&fromdt={day}T00:00:00Z"));
        }
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(self.timeout_secs))
            .build()
            .map_err(|e| format!("building pinboard http client: {}", e.without_url()))?;
        // NOTE: errors are reported `without_url()` so the auth token can
        // never leak into logs or messages.
        let resp = client
            .get(&url)
            .send()
            .map_err(|e| format!("pinboard request failed: {}", e.without_url()))?;
        let status = resp.status();
        if !status.is_success() {
            return Err(format!("pinboard API returned HTTP {status}"));
        }
        resp.json::<Vec<PinboardPost>>()
            .map_err(|e| format!("parsing pinboard API reply: {}", e.without_url()))
    }
}

#[cfg(feature = "pinboard-live")]
impl PinboardFetch for LivePinboardFetch {
    fn fetch_all(&mut self) -> Result<Vec<PinboardPost>, String> {
        self.get_posts_all(None)
    }

    fn fetch_since(&mut self, since: &str) -> Result<Vec<PinboardPost>, String> {
        self.get_posts_all(Some(since))
    }

    fn origin(&self) -> String {
        "pinboard API (posts/all)".to_string()
    }
}

/// One materialized bookmark.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PinboardSyncRecord {
    pub schema: String,
    pub run_id: String,
    pub date: String,
    pub url: String,
    /// Vault-relative note path.
    pub to: String,
    pub title: String,
    /// The bookmark's own timestamp.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub posted_at: Option<String>,
}

#[derive(Debug, Default)]
pub struct PinboardSyncOutcome {
    pub fetched: usize,
    pub new_notes: Vec<PinboardSyncRecord>,
    pub skipped_known: usize,
    pub skipped_empty_url: usize,
    /// Excluded by `since` (older than the cutoff, or no timestamp).
    pub skipped_since: usize,
    /// Excluded by `until` (newer than the window's last day).
    pub skipped_until: usize,
    /// New bookmarks beyond the `max` newest, left for later runs.
    pub skipped_over_max: usize,
    /// Oldest posted day across ALL fetched posts (before any filtering) —
    /// lets a backfill caller report "history exhausted" without re-fetching.
    pub oldest_post_day: Option<String>,
    /// Dry-run only: a REAL run with these options would hit the first-sync
    /// flood guard and abort.
    pub guard_would_abort: bool,
    pub origin: String,
    pub dry_run: bool,
}

pub fn read_pinboard_ledger(path: &Path) -> Result<Vec<PinboardSyncRecord>, String> {
    read_jsonl(path)
}

/// URLs already materialized by earlier syncs.
pub fn synced_urls(records: &[PinboardSyncRecord]) -> HashSet<String> {
    records.iter().map(|r| r.url.clone()).collect()
}

// ---------------------------------------------------------------------------
// Sync watermarks + day-by-day backfill. Coverage is axis A (bookmark day)
// and is DERIVED from the two append-only ledgers — there is no separate
// mutable cursor to drift out of sync.
// ---------------------------------------------------------------------------

/// Schema tag for backfill coverage records.
pub const PINBOARD_BACKFILL_SCHEMA: &str = "ovp.pinboard-backfill/v1";

/// Newest bookmark day materialized so far — the high watermark the daily
/// incremental sync resumes from (`--pinboard-since auto`). Overlap is free:
/// URL dedup skips the boundary day's already-known bookmarks.
pub fn watermark_high(records: &[PinboardSyncRecord]) -> Option<String> {
    records
        .iter()
        .filter_map(|r| r.posted_at.as_deref().and_then(posted_day))
        .max()
}

/// Oldest bookmark day materialized so far.
pub fn watermark_low(records: &[PinboardSyncRecord]) -> Option<String> {
    records
        .iter()
        .filter_map(|r| r.posted_at.as_deref().and_then(posted_day))
        .min()
}

/// One completed backfill window. The floor ("everything posted on/after
/// this day is covered") advances to `floor_after` only when the window ran
/// to completion — a `max`-truncated window records nothing and is simply
/// retried by the next run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PinboardBackfillRecord {
    pub schema: String,
    pub run_id: String,
    /// Axis B: the day this window ran.
    pub date: String,
    /// Axis A window, both bounds inclusive.
    pub window_since: String,
    pub window_until: String,
    /// The coverage floor after this window (== `window_since`).
    pub floor_after: String,
    pub new_notes: usize,
}

pub fn read_pinboard_backfill_ledger(path: &Path) -> Result<Vec<PinboardBackfillRecord>, String> {
    read_jsonl(path)
}

/// The coverage floor: everything posted on/after this day has been
/// materialized. The lowest completed backfill floor, seeded from the
/// pinboard ledger's low watermark + 1 day — so the FIRST backfill window
/// re-covers the watermark day itself, which a `max`-truncated seed sync may
/// have left partial (URL dedup makes that overlap free). `None` when
/// nothing has ever been synced: backfill has no edge to walk down from.
pub fn coverage_floor(
    pinboard: &[PinboardSyncRecord],
    backfill: &[PinboardBackfillRecord],
) -> Option<String> {
    let seed = watermark_low(pinboard).and_then(|low| shift_days(&low, 1));
    backfill
        .iter()
        .map(|r| r.floor_after.clone())
        .filter(|f| is_iso_day(f))
        .fold(seed, |acc, f| Some(acc.map_or(f.clone(), |a| a.min(f))))
}

/// One day-window of backfill work, axis A, both bounds inclusive.
#[derive(Debug, Clone, PartialEq)]
pub struct BackfillWindow {
    pub since: String,
    pub until: String,
    /// The floor to record once this window completes (== `since`).
    pub floor_after: String,
}

/// The window below the current floor: `[floor - days, floor - 1]`. Day
/// granularity keeps each run small and auditable; days with no bookmarks
/// still advance the floor (coverage is recorded per WINDOW, not per found
/// bookmark, so empty days cannot stall the walk).
pub fn plan_backfill_window(floor: &str, days: u32) -> Result<BackfillWindow, String> {
    if days == 0 {
        return Err("backfill days must be >= 1".to_string());
    }
    let since = shift_days(floor, -(i64::from(days)))
        .ok_or_else(|| format!("invalid coverage floor `{floor}`"))?;
    let until = shift_days(floor, -1).ok_or_else(|| format!("invalid coverage floor `{floor}`"))?;
    Ok(BackfillWindow { floor_after: since.clone(), since, until })
}

/// The outcome of one backfill window run.
#[derive(Debug)]
pub struct PinboardBackfillOutcome {
    pub window: BackfillWindow,
    pub sync: PinboardSyncOutcome,
    /// False when `max` truncated the window: the floor was NOT moved, so the
    /// next backfill run retries this window (URL dedup makes the overlap
    /// free) instead of skipping its un-materialized tail.
    pub floor_advanced: bool,
}

/// Run ONE backfill window below the current coverage floor. The floor is
/// derived from the two append-only ledgers; a completed window appends its
/// own coverage record so the next run walks one window further down.
/// Errors — rather than flooding the whole history — when nothing has ever
/// been synced: seed the watermark with an explicit `--since` (or `--max`)
/// run first.
pub fn sync_pinboard_backfill(
    cfg: &IntakeConfig,
    fetch: &mut dyn PinboardFetch,
    dry_run: bool,
    days: u32,
    max: Option<usize>,
) -> Result<PinboardBackfillOutcome, String> {
    let layout = VaultLayout::new();
    let backfill_path = cfg.vault_root.join(layout.pinboard_backfill_ledger());
    let pinboard = read_pinboard_ledger(&cfg.vault_root.join(layout.pinboard_ledger()))?;
    let backfill = read_pinboard_backfill_ledger(&backfill_path)?;
    let floor = coverage_floor(&pinboard, &backfill).ok_or_else(|| {
        "pinboard backfill has no coverage edge yet: run `ovp2 pinboard-sync --since \
         <YYYY-MM-DD>` (or --max) once to materialize recent bookmarks, then --backfill \
         walks the history down day by day"
            .to_string()
    })?;
    let window = plan_backfill_window(&floor, days)?;
    let opts = PinboardSyncOptions {
        since: Some(window.since.clone()),
        until: Some(window.until.clone()),
        max,
        yes_all: false,
    };
    let sync = sync_pinboard(cfg, fetch, dry_run, &opts)?;
    let floor_advanced = sync.skipped_over_max == 0;
    if !dry_run && floor_advanced {
        append_jsonl(&backfill_path, &PinboardBackfillRecord {
            schema: PINBOARD_BACKFILL_SCHEMA.into(),
            run_id: cfg.run_id.clone(),
            date: cfg.date.clone(),
            window_since: window.since.clone(),
            window_until: window.until.clone(),
            floor_after: window.floor_after.clone(),
            new_notes: sync.new_notes.len(),
        })?;
    }
    Ok(PinboardBackfillOutcome { window, sync, floor_advanced })
}

/// Resolve `--pinboard-since auto`: the high watermark of the pinboard sync
/// ledger. `None` when nothing has been synced yet — the caller decides
/// whether that is an error (explicit command) or a skip-with-guidance
/// (unattended daily).
pub fn auto_since(vault_root: &Path) -> Result<Option<String>, String> {
    let layout = VaultLayout::new();
    let records = read_pinboard_ledger(&vault_root.join(layout.pinboard_ledger()))?;
    Ok(watermark_high(&records))
}

/// `iso` (YYYY-MM-DD) shifted by `delta` days. Dep-free civil-date math
/// (Howard Hinnant's days-from-civil); shape-invalid input → `None`.
pub fn shift_days(iso: &str, delta: i64) -> Option<String> {
    let (y, m, d) = parse_iso_day(iso)?;
    let (y, m, d) = civil_from_days(days_from_civil(y, m, d) + delta);
    Some(format!("{y:04}-{m:02}-{d:02}"))
}

fn parse_iso_day(s: &str) -> Option<(i64, u32, u32)> {
    if !is_iso_day(s) {
        return None;
    }
    let y: i64 = s.get(..4)?.parse().ok()?;
    let m: u32 = s.get(5..7)?.parse().ok()?;
    let d: u32 = s.get(8..10)?.parse().ok()?;
    ((1..=12).contains(&m) && (1..=31).contains(&d)).then_some((y, m, d))
}

fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = ((m + 9) % 12) as i64;
    let doy = (153 * mp + 2) / 5 + i64::from(d) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Fetch all bookmarks and materialize the new ones as notes in
/// `50-Inbox/02-Pinboard/`. Write → write-log event → ledger record, per the
/// audit-ordering invariant. Idempotent: URL-known posts are skipped.
///
/// `opts` narrows the candidate set (`since`/`until`/`max`) and controls the
/// first-sync flood guard: without `since`/`until`/`max`/`yes_all`, more than
/// [`FIRST_SYNC_GUARD_MAX_NEW`] NEW bookmarks abort the run before any write
/// (dry runs report via [`PinboardSyncOutcome::guard_would_abort`] instead).
pub fn sync_pinboard(
    cfg: &IntakeConfig,
    fetch: &mut dyn PinboardFetch,
    dry_run: bool,
    opts: &PinboardSyncOptions,
) -> Result<PinboardSyncOutcome, String> {
    if let Some(since) = &opts.since {
        validate_iso_day("--since", since)?;
    }
    if let Some(until) = &opts.until {
        validate_iso_day("--until", until)?;
    }
    if let (Some(since), Some(until)) = (&opts.since, &opts.until)
        && since > until
    {
        return Err(format!("--since ({since}) is after --until ({until}) — empty window"));
    }
    let layout = VaultLayout::new();
    let pin_ledger_path = cfg.vault_root.join(layout.pinboard_ledger());
    let intake_ledger_path = cfg.vault_root.join(layout.intake_ledger());
    let log_path = cfg.vault_root.join(layout.pipeline_log());

    let mut known = synced_urls(&read_pinboard_ledger(&pin_ledger_path)?);
    known.extend(known_urls(&read_intake_ledger(&intake_ledger_path)?));

    // Prefer server-side `fromdt` for incremental daily sync (since set, no
    // until). Full-history `posts/all` 500s on large accounts (LivePinboardFetch).
    // Backfill windows set both since+until and still need the full export so
    // `oldest_post_day` can detect exhaustion below the coverage floor.
    let mut posts = match (&opts.since, &opts.until) {
        (Some(since), None) => fetch.fetch_since(since)?,
        _ => fetch.fetch_all()?,
    };
    // Deterministic order: oldest first, then URL.
    posts.sort_by(|a, b| (a.time.as_str(), a.href.as_str()).cmp(&(b.time.as_str(), b.href.as_str())));

    let mut outcome = PinboardSyncOutcome {
        fetched: posts.len(),
        oldest_post_day: posts.iter().filter_map(|p| posted_day(&p.time)).min(),
        origin: fetch.origin(),
        dry_run,
        ..Default::default()
    };

    // Narrow + dedup into the NEW candidate set BEFORE writing anything, so
    // the flood guard can abort with nothing on disk. Stays oldest-first.
    let mut candidates: Vec<(String, PinboardPost)> = Vec::new();
    for post in posts {
        let url = post.href.trim().to_string();
        if url.is_empty() {
            outcome.skipped_empty_url += 1;
            continue;
        }
        if let Some(since) = &opts.since {
            let on_or_after = post.time.get(..10).is_some_and(|d| d >= since.as_str());
            if !on_or_after {
                outcome.skipped_since += 1;
                continue;
            }
        }
        if let Some(until) = &opts.until {
            let on_or_before = post.time.get(..10).is_some_and(|d| d <= until.as_str());
            if !on_or_before {
                outcome.skipped_until += 1;
                continue;
            }
        }
        if known.contains(&url) {
            outcome.skipped_known += 1;
            continue;
        }
        known.insert(url.clone());
        candidates.push((url, post));
    }

    // `max`: keep only the N newest new bookmarks. Candidates are sorted
    // oldest-first, so the newest sit at the tail; processing order (and thus
    // filenames/ledger) stays oldest-first.
    if let Some(max) = opts.max
        && candidates.len() > max {
            outcome.skipped_over_max = candidates.len() - max;
            candidates.drain(..candidates.len() - max);
        }

    // First-sync flood guard: no narrowing flags + a huge NEW set means this
    // is almost certainly `posts/all` history, not a daily delta. Abort
    // before any write; dry runs report instead.
    if opts.since.is_none()
        && opts.until.is_none()
        && opts.max.is_none()
        && !opts.yes_all
        && candidates.len() > FIRST_SYNC_GUARD_MAX_NEW
    {
        if dry_run {
            outcome.guard_would_abort = true;
        } else {
            return Err(first_sync_guard_message(candidates.len()));
        }
    }

    for (url, post) in candidates {
        let title = if post.description.trim().is_empty() {
            url.clone()
        } else {
            post.description.trim().to_string()
        };
        let date = posted_day(&post.time).unwrap_or_else(|| cfg.date.clone());
        let url_hash8 = hex_sha256(url.as_bytes())[..8].to_string();
        let name = layout.normalized_source_name(&date, &title, &url_hash8);
        let target = cfg.vault_root.join(layout.pinboard_dir()).join(&name);
        let contents = render_bookmark_note(&title, &url, &date, &cfg.date, &post.tags, &post.extended);

        let to_rel;
        if dry_run {
            to_rel = rel_to(&cfg.vault_root, &target);
        } else {
            let actual = write_new(&target, &contents)?;
            to_rel = rel_to(&cfg.vault_root, &actual);
            append_pipeline_event(&log_path, &PipelineLogEvent {
                event_type: "pinboard_note_write".into(),
                target: to_rel.clone(),
                reason: format!("ovp2 pinboard-sync: new bookmark {url}"),
                date: cfg.date.clone(),
                run_id: cfg.run_id.clone(),
            })?;
        }
        let rec = PinboardSyncRecord {
            schema: PINBOARD_SCHEMA.into(),
            run_id: cfg.run_id.clone(),
            date: cfg.date.clone(),
            url,
            to: to_rel,
            title,
            posted_at: (!post.time.is_empty()).then(|| post.time.clone()),
        };
        if !dry_run {
            append_jsonl(&pin_ledger_path, &rec)?;
        }
        outcome.new_notes.push(rec);
    }
    Ok(outcome)
}

/// The abort message for the first-sync flood guard. States the count and
/// every explicit way forward (per-command flag spellings included so the
/// operator can act from either `pinboard-sync` or `daily`).
fn first_sync_guard_message(new_count: usize) -> String {
    format!(
        "pinboard-sync guard: {new_count} NEW bookmarks would be materialized (limit without \
         filters is {FIRST_SYNC_GUARD_MAX_NEW}). A first sync against a long-lived Pinboard \
         account pulls the ENTIRE history and floods the vault, the next intake sweep, and live \
         web enrichment. Nothing was written. Narrow the run with --since/--until <YYYY-MM-DD> \
         or --max <N> (daily: --pinboard-since / --pinboard-max; `--pinboard-since auto` follows \
         the last sync's watermark), or pass --yes-all to `ovp2 pinboard-sync` to materialize \
         everything deliberately."
    )
}

/// The bookmark day (axis A) of a Pinboard ISO-8601 timestamp: the leading
/// `YYYY-MM-DD`, shape-checked so a lexicographic compare against window
/// bounds is sound. `None` for missing/malformed timestamps.
pub fn posted_day(time: &str) -> Option<String> {
    let day = time.get(..10)?;
    is_iso_day(day).then(|| day.to_string())
}

fn is_iso_day(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 10
        && b.iter().enumerate().all(|(i, c)| match i {
            4 | 7 => *c == b'-',
            _ => c.is_ascii_digit(),
        })
}

/// `--since` / `--until` must be plain ISO dates so the lexicographic compare
/// against the bookmark timestamps is sound.
fn validate_iso_day(flag: &str, s: &str) -> Result<(), String> {
    if is_iso_day(s) {
        Ok(())
    } else {
        Err(format!("{flag} must be an ISO date (YYYY-MM-DD), got `{s}`"))
    }
}

/// Render the bookmark note in the exact frontmatter dialect the clipping
/// parser reads (`title`/`source`/`published`/`created`/`tags`). Extra keys
/// (`clipped_from`) are ignored by the parser but useful to humans.
fn render_bookmark_note(
    title: &str,
    url: &str,
    published: &str,
    created: &str,
    tags: &str,
    body: &str,
) -> String {
    let mut tag_lines = String::from("  - \"clippings\"\n  - \"pinboard\"\n");
    for t in tags.split_whitespace() {
        tag_lines.push_str(&format!("  - \"{}\"\n", yaml_escape(t)));
    }
    format!(
        "---\ntitle: \"{}\"\nsource: \"{}\"\npublished: {}\ncreated: {}\nclipped_from: pinboard\ntags:\n{}---\n{}\n",
        yaml_escape(title),
        yaml_escape(url),
        published,
        created,
        tag_lines,
        body.trim_end(),
    )
}

fn yaml_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bookmark_note_round_trips_through_clipping_parser() {
        let note = render_bookmark_note(
            "Title with \"quotes\"",
            "https://example.com/x?a=1",
            "2026-06-01",
            "2026-06-09",
            "rust testing",
            "A body of notes.",
        );
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("n.md");
        std::fs::write(&p, &note).unwrap();
        let doc = ovp_domain::units::read_source_from_path(&p).expect("parses");
        assert_eq!(doc.title, "Title with \"quotes\"");
        assert_eq!(doc.source_url, "https://example.com/x?a=1");
        assert_eq!(doc.published.as_deref(), Some("2026-06-01"));
        assert!(doc.tags.contains(&"pinboard".to_string()));
        assert!(doc.tags.contains(&"rust".to_string()));
        assert_eq!(doc.body_markdown.trim(), "A body of notes.");
    }

    #[test]
    fn fixture_fetch_parses_export_format() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("export.json");
        std::fs::write(&p, r#"[
          {"href":"https://a.example/post","description":"A post","extended":"note","meta":"m","hash":"h","time":"2026-06-01T10:00:00Z","shared":"yes","toread":"no","tags":"ai rust"},
          {"href":"https://b.example","description":"","extended":"","time":"","tags":""}
        ]"#).unwrap();
        let posts = FixturePinboardFetch::new(&p).fetch_all().unwrap();
        assert_eq!(posts.len(), 2);
        assert_eq!(posts[0].description, "A post");
        assert_eq!(posts[0].tags, "ai rust");
    }

    // --- axis-A windows, watermarks, and day-by-day backfill ---------------

    struct VecFetch(Vec<PinboardPost>);

    impl PinboardFetch for VecFetch {
        fn fetch_all(&mut self) -> Result<Vec<PinboardPost>, String> {
            Ok(self.0.clone())
        }
        fn origin(&self) -> String {
            "vec".into()
        }
    }

    fn post(url: &str, time: &str) -> PinboardPost {
        PinboardPost {
            href: url.into(),
            description: url.into(),
            extended: "body".into(),
            time: time.into(),
            tags: String::new(),
        }
    }

    fn rec(url: &str, posted_at: &str) -> PinboardSyncRecord {
        PinboardSyncRecord {
            schema: PINBOARD_SCHEMA.into(),
            run_id: "r".into(),
            date: "2026-07-27".into(),
            url: url.into(),
            to: "50-Inbox/02-Pinboard/x.md".into(),
            title: url.into(),
            posted_at: Some(posted_at.into()),
        }
    }

    fn backfill_rec(floor_after: &str) -> PinboardBackfillRecord {
        PinboardBackfillRecord {
            schema: PINBOARD_BACKFILL_SCHEMA.into(),
            run_id: "r".into(),
            date: "2026-07-27".into(),
            window_since: floor_after.into(),
            window_until: floor_after.into(),
            floor_after: floor_after.into(),
            new_notes: 0,
        }
    }

    fn test_cfg(root: &Path) -> crate::IntakeConfig {
        crate::IntakeConfig::new(root.to_path_buf(), "2026-07-27".into(), "pinboard-test".into())
    }

    #[test]
    fn posted_day_shape_checks() {
        assert_eq!(posted_day("2026-06-01T10:00:00Z").as_deref(), Some("2026-06-01"));
        assert_eq!(posted_day("2026-06-01").as_deref(), Some("2026-06-01"));
        assert_eq!(posted_day(""), None);
        assert_eq!(posted_day("2026-06-0"), None);
        assert_eq!(posted_day("2026/06/01xx"), None);
    }

    #[test]
    fn shift_days_crosses_month_year_and_leap_boundaries() {
        assert_eq!(shift_days("2026-07-01", -1).as_deref(), Some("2026-06-30"));
        assert_eq!(shift_days("2026-01-01", -1).as_deref(), Some("2025-12-31"));
        assert_eq!(shift_days("2026-03-01", -1).as_deref(), Some("2026-02-28"));
        // 2024 is a leap year.
        assert_eq!(shift_days("2024-03-01", -1).as_deref(), Some("2024-02-29"));
        assert_eq!(shift_days("2024-02-29", 1).as_deref(), Some("2024-03-01"));
        assert_eq!(shift_days("2026-07-27", 0).as_deref(), Some("2026-07-27"));
        assert_eq!(shift_days("2026-13-01", 1), None);
        assert_eq!(shift_days("not-a-date", 1), None);
    }

    #[test]
    fn watermarks_track_bookmark_days_not_run_days() {
        let records = vec![
            rec("https://a", "2026-06-07T08:00:00Z"),
            rec("https://b", "2026-07-26T09:00:00Z"),
            rec("https://c", "2026-06-15T10:00:00Z"),
        ];
        assert_eq!(watermark_high(&records).as_deref(), Some("2026-07-26"));
        assert_eq!(watermark_low(&records).as_deref(), Some("2026-06-07"));
        assert_eq!(watermark_high(&[]), None);
    }

    #[test]
    fn coverage_floor_seeds_one_day_above_low_watermark() {
        let pin = vec![rec("https://a", "2026-06-07T08:00:00Z")];
        // Seed: low watermark + 1 → first backfill window re-covers 06-07
        // itself (a max-truncated seed sync may have left that day partial).
        assert_eq!(coverage_floor(&pin, &[]).as_deref(), Some("2026-06-08"));
        // Completed windows take the floor below the seed.
        let bf = vec![backfill_rec("2026-06-01"), backfill_rec("2026-05-20")];
        assert_eq!(coverage_floor(&pin, &bf).as_deref(), Some("2026-05-20"));
        assert_eq!(coverage_floor(&[], &[]), None);
    }

    #[test]
    fn backfill_window_is_days_wide_below_the_floor() {
        let w = plan_backfill_window("2026-06-08", 1).unwrap();
        assert_eq!(
            w,
            BackfillWindow {
                since: "2026-06-07".into(),
                until: "2026-06-07".into(),
                floor_after: "2026-06-07".into(),
            }
        );
        let w3 = plan_backfill_window("2026-06-08", 3).unwrap();
        assert_eq!(w3.since, "2026-06-05");
        assert_eq!(w3.until, "2026-06-07");
        assert!(plan_backfill_window("2026-06-08", 0).is_err());
    }

    #[test]
    fn until_excludes_newer_bookmarks_and_disarms_guard() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let posts = vec![
            post("https://old.example", "2026-06-01T08:00:00Z"),
            post("https://new.example", "2026-07-26T08:00:00Z"),
        ];
        let mut fetch = VecFetch(posts);
        let opts = PinboardSyncOptions {
            until: Some("2026-06-30".into()),
            ..Default::default()
        };
        let out = sync_pinboard(&test_cfg(root), &mut fetch, false, &opts).unwrap();
        assert_eq!(out.new_notes.len(), 1);
        assert_eq!(out.new_notes[0].url, "https://old.example");
        assert_eq!(out.skipped_until, 1);
        assert_eq!(out.oldest_post_day.as_deref(), Some("2026-06-01"));
        // A since/until window with bounds inverted is a hard error.
        let bad = PinboardSyncOptions {
            since: Some("2026-07-01".into()),
            until: Some("2026-06-01".into()),
            ..Default::default()
        };
        assert!(sync_pinboard(&test_cfg(root), &mut fetch, true, &bad).is_err());
    }

    #[test]
    fn backfill_walks_down_one_window_per_run_and_completes() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let posts = vec![
            post("https://d1.example", "2026-06-05T08:00:00Z"),
            post("https://d2.example", "2026-06-06T08:00:00Z"),
            post("https://d3.example", "2026-06-07T08:00:00Z"),
        ];
        // Seed: materialize the newest day via an explicit since sync.
        let seed = PinboardSyncOptions { since: Some("2026-06-07".into()), ..Default::default() };
        let out = sync_pinboard(&test_cfg(root), &mut VecFetch(posts.clone()), false, &seed).unwrap();
        assert_eq!(out.new_notes.len(), 1);

        // First backfill run: floor seeded at 06-08 → window [06-07, 06-07],
        // completing the (here already complete) watermark day.
        let r1 = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(posts.clone()), false, 1, None).unwrap();
        assert_eq!(r1.window.since, "2026-06-07");
        assert_eq!(r1.window.until, "2026-06-07");
        assert!(r1.floor_advanced);
        assert_eq!(r1.sync.new_notes.len(), 0, "06-07 already materialized — dedup");

        // Second run: window [06-06, 06-06] picks up one bookmark.
        let r2 = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(posts.clone()), false, 1, None).unwrap();
        assert_eq!(r2.window.since, "2026-06-06");
        assert_eq!(r2.sync.new_notes.len(), 1);
        assert_eq!(r2.sync.new_notes[0].url, "https://d2.example");
        // Exhaustion check input: oldest post overall is 06-05, still below
        // the new floor (06-06) → NOT complete yet.
        assert!(r2.sync.oldest_post_day.as_deref() < Some(r2.window.floor_after.as_str()));

        // Third run covers 06-05 — the oldest bookmark in the account.
        let r3 = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(posts.clone()), false, 1, None).unwrap();
        assert_eq!(r3.window.since, "2026-06-05");
        assert_eq!(r3.sync.new_notes.len(), 1);
        assert_eq!(r3.sync.oldest_post_day.as_deref(), Some("2026-06-05"));

        let bf = read_pinboard_backfill_ledger(&root.join(".ovp/pinboard-backfill.jsonl")).unwrap();
        assert_eq!(bf.len(), 3);
        assert_eq!(bf.last().unwrap().floor_after, "2026-06-05");
        assert!(bf.iter().all(|r| r.schema == PINBOARD_BACKFILL_SCHEMA));
    }

    #[test]
    fn backfill_advances_past_bookmark_free_days() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        // A 3-day gap with no bookmarks between the seed day and the older one.
        let posts = vec![
            post("https://old.example", "2026-06-01T08:00:00Z"),
            post("https://seed.example", "2026-06-05T08:00:00Z"),
        ];
        let seed = PinboardSyncOptions { since: Some("2026-06-05".into()), ..Default::default() };
        sync_pinboard(&test_cfg(root), &mut VecFetch(posts.clone()), false, &seed).unwrap();
        // Floor 06-06 → windows 06-05 (empty), 06-04 (empty), 06-03 (empty),
        // 06-02 (empty) — each advances the floor despite zero new notes…
        for (i, want) in ["2026-06-05", "2026-06-04", "2026-06-03", "2026-06-02"].iter().enumerate() {
            let r = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(posts.clone()), false, 1, None).unwrap();
            assert_eq!(&r.window.since, want, "run {i}");
            assert_eq!(r.sync.new_notes.len(), 0);
            assert!(r.floor_advanced);
        }
        // …until the walk reaches the day that HAS a bookmark.
        let r = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(posts.clone()), false, 1, None).unwrap();
        assert_eq!(r.window.since, "2026-06-01");
        assert_eq!(r.sync.new_notes.len(), 1);
    }

    #[test]
    fn backfill_does_not_advance_floor_when_max_truncates() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let posts = vec![
            post("https://a.example", "2026-06-07T08:00:00Z"),
            post("https://b.example", "2026-06-07T09:00:00Z"),
        ];
        let seed = PinboardSyncOptions { max: Some(1), ..Default::default() };
        sync_pinboard(&test_cfg(root), &mut VecFetch(posts.clone()), false, &seed).unwrap();
        // Floor seeded at 06-08 → window [06-07, 06-07] has 1 remaining new
        // bookmark but max=0-equivalent truncation: cap at 0 impossible, so
        // cap at 1 — wait, exactly 1 fits. Use max on a 2-new window instead.
        let r = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(posts.clone()), false, 1, Some(1)).unwrap();
        // The window holds exactly the 1 un-materialized bookmark → no
        // truncation → floor advances and picks it up.
        assert!(r.floor_advanced);
        assert_eq!(r.sync.new_notes.len(), 1);

        // Now craft real truncation: two bookmarks left, max 1.
        let posts2 = vec![
            post("https://c.example", "2026-06-06T08:00:00Z"),
            post("https://d.example", "2026-06-06T09:00:00Z"),
        ];
        let mut all = posts.clone();
        all.extend(posts2);
        let r2 = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(all), false, 1, Some(1)).unwrap();
        assert_eq!(r2.window.since, "2026-06-06");
        assert_eq!(r2.sync.skipped_over_max, 1);
        assert!(!r2.floor_advanced, "truncated window must not move the floor");
        let bf = read_pinboard_backfill_ledger(&root.join(".ovp/pinboard-backfill.jsonl")).unwrap();
        assert_eq!(bf.len(), 1, "only the first (complete) window was recorded");
    }

    #[test]
    fn backfill_without_any_coverage_is_a_guided_error() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let err = sync_pinboard_backfill(&test_cfg(root), &mut VecFetch(vec![]), false, 1, None)
            .unwrap_err();
        assert!(err.contains("--since"), "got: {err}");
    }
}
