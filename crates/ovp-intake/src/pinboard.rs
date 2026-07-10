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
//! [`PinboardSyncOptions`] (`since` / `max` narrowing) and, when neither is
//! given, aborts before writing anything if more than
//! [`FIRST_SYNC_GUARD_MAX_NEW`] new bookmarks would be created — unless
//! `yes_all` is set. Dry runs report instead of aborting.

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
impl PinboardFetch for LivePinboardFetch {
    fn fetch_all(&mut self) -> Result<Vec<PinboardPost>, String> {
        let url = format!(
            "{}/posts/all?format=json&auth_token={}",
            self.base_url, self.token
        );
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
    /// New bookmarks beyond the `max` newest, left for later runs.
    pub skipped_over_max: usize,
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

/// Fetch all bookmarks and materialize the new ones as notes in
/// `50-Inbox/02-Pinboard/`. Write → write-log event → ledger record, per the
/// audit-ordering invariant. Idempotent: URL-known posts are skipped.
///
/// `opts` narrows the candidate set (`since`/`max`) and controls the
/// first-sync flood guard: without `since`/`max`/`yes_all`, more than
/// [`FIRST_SYNC_GUARD_MAX_NEW`] NEW bookmarks abort the run before any write
/// (dry runs report via [`PinboardSyncOutcome::guard_would_abort`] instead).
pub fn sync_pinboard(
    cfg: &IntakeConfig,
    fetch: &mut dyn PinboardFetch,
    dry_run: bool,
    opts: &PinboardSyncOptions,
) -> Result<PinboardSyncOutcome, String> {
    if let Some(since) = &opts.since {
        validate_since(since)?;
    }
    let layout = VaultLayout::new();
    let pin_ledger_path = cfg.vault_root.join(layout.pinboard_ledger());
    let intake_ledger_path = cfg.vault_root.join(layout.intake_ledger());
    let log_path = cfg.vault_root.join(layout.pipeline_log());

    let mut known = synced_urls(&read_pinboard_ledger(&pin_ledger_path)?);
    known.extend(known_urls(&read_intake_ledger(&intake_ledger_path)?));

    let mut posts = fetch.fetch_all()?;
    // Deterministic order: oldest first, then URL.
    posts.sort_by(|a, b| (a.time.as_str(), a.href.as_str()).cmp(&(b.time.as_str(), b.href.as_str())));

    let mut outcome = PinboardSyncOutcome {
        fetched: posts.len(),
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
        let date = post
            .time
            .get(..10)
            .filter(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit() || b == b'-'))
            .unwrap_or(&cfg.date)
            .to_string();
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
         web enrichment. Nothing was written. Narrow the run with --since <YYYY-MM-DD> or \
         --max <N> (daily: --pinboard-since / --pinboard-max), or pass --yes-all to \
         `ovp2 pinboard-sync` to materialize everything deliberately."
    )
}

/// `since` must be a plain ISO date so the lexicographic compare against the
/// bookmark timestamps is sound.
fn validate_since(s: &str) -> Result<(), String> {
    let b = s.as_bytes();
    let ok = b.len() == 10
        && b.iter().enumerate().all(|(i, c)| match i {
            4 | 7 => *c == b'-',
            _ => c.is_ascii_digit(),
        });
    if ok {
        Ok(())
    } else {
        Err(format!("--since must be an ISO date (YYYY-MM-DD), got `{s}`"))
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
}
