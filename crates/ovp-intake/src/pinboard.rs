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
pub fn sync_pinboard(
    cfg: &IntakeConfig,
    fetch: &mut dyn PinboardFetch,
    dry_run: bool,
) -> Result<PinboardSyncOutcome, String> {
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

    for post in posts {
        let url = post.href.trim().to_string();
        if url.is_empty() {
            outcome.skipped_empty_url += 1;
            continue;
        }
        if known.contains(&url) {
            outcome.skipped_known += 1;
            continue;
        }

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
                reason: format!("ovp-next pinboard-sync: new bookmark {url}"),
                date: cfg.date.clone(),
                run_id: cfg.run_id.clone(),
            })?;
        }
        let rec = PinboardSyncRecord {
            schema: PINBOARD_SCHEMA.into(),
            run_id: cfg.run_id.clone(),
            date: cfg.date.clone(),
            url: url.clone(),
            to: to_rel,
            title,
            posted_at: (!post.time.is_empty()).then(|| post.time.clone()),
        };
        if !dry_run {
            append_jsonl(&pin_ledger_path, &rec)?;
        }
        known.insert(url);
        outcome.new_notes.push(rec);
    }
    Ok(outcome)
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
