//! The read-model schema (`ovp.index/v1`). A PROJECTION: every row is derived
//! from the ledgers, the reader packs, and the Crystal store, and the whole
//! file is rebuilt from scratch on every refresh — it is never written to
//! directly and never the source of truth. Deleting `.ovp/index/` loses
//! nothing.

use serde::{Deserialize, Serialize};

pub const INDEX_SCHEMA: &str = "ovp.index/v2";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceStatus {
    /// Failed the retry cap (3+ failures) — needs operator review.
    Blocked,
    /// Attempted and failed; will be retried.
    Failed,
    /// In `01-Raw`, waiting for a reader run.
    Queued,
    /// Captured but too thin to read; operator should enrich.
    NeedsContent,
    /// Frontmatter does not parse; operator should fix.
    Unparseable,
    /// Reader pack produced (the happy path).
    Processed,
    /// Parked as a duplicate of known content/URL.
    Duplicate,
    /// Excluded by the operator's `ovp/skip` tag — a bookmark kept as a quick
    /// entry point, not to be read. Its OWN status, deliberately: folding it
    /// into `NeedsContent` would put it back in the enrichment queue, and
    /// folding it into `Duplicate` would lie about why it is here. The
    /// operator must be able to see, and undo, what they excluded.
    Skipped,
}

/// Timeline axes for a source (do not collapse these into one field):
///
/// | Axis | Field(s) | Meaning |
/// |------|----------|---------|
/// | **A content** | [`SourceRow::content_date`] | When the *content* is dated (bookmark `posted_at` / FM `published` / filename day). |
/// | **B pipeline** | [`SourceRow::captured_on`], [`SourceRow::processed_on`], [`SourceRow::last_run_id`] | When *our* pipeline touched it (intake day, last daily day). |
/// | **C subject** | *(not stored yet)* | What period the content is *about* (e.g. FY2025 Q2). Never invent from A/B. |
///
/// [`SourceRow::date`] is a **legacy alias** of the latest B activity
/// (`processed_on ?? captured_on`) kept so old clients and fixtures keep
/// working. Prefer the explicit fields in new UI/code.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SourceRow {
    pub sha256: String,
    pub status: SourceStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// Frontmatter author, wikilinks already stripped by the inbox parser
    /// (`[[Cerebras]]` → `Cerebras`; a list is joined with commas).
    ///
    /// Carried because "the article by X" is a way people actually look for
    /// things, and the field was not part of any query path: on the dogfood
    /// vault 699 of 1453 sources (48%) resolve an author, and none of it was
    /// searchable except incidentally, when the name happened to appear in
    /// the URL. `author_handle` is deliberately NOT a second field — it is a
    /// strict subset (zero sources carry a handle without an author), so it
    /// would add plumbing and no reach.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub author: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// Capture-origin facet: which capture mechanism brought this content in
    /// (`"pinboard"` today, matched by URL against the pinboard-sync ledger
    /// at build time). URL is the join key because it survives both the
    /// enrichment re-hash and lifecycle moves — matching by the note's
    /// current path loses every source the sweep moves out of `02-Pinboard`.
    /// Serde-additive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub origin: Option<String>,
    /// Current best-known vault-relative location.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rel_path: Option<String>,
    /// **Legacy B (pipeline):** last recorded pipeline activity day. Prefer
    /// [`Self::processed_on`] / [`Self::captured_on`]. Still written on every
    /// build so pre-explicit clients keep a sensible calendar signal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub date: Option<String>,
    /// **A (content/capture):** published / bookmark / filename day when known.
    /// Independent of when we later ran the reader. Serde-additive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content_date: Option<String>,
    /// **B (pipeline):** intake ledger day — when this content first entered
    /// the vault lifecycle (sweep into 01-Raw / flag). Serde-additive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub captured_on: Option<String>,
    /// **B (pipeline):** last daily-run ledger day for this source (success or
    /// fail). Pack production day is this when status is processed.
    /// Serde-additive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processed_on: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pack_dir: Option<String>,
    #[serde(default)]
    pub fail_count: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_reason: Option<String>,
    /// Canonical content tags: the note's CURRENT frontmatter tags (vault
    /// frontmatter is the per-source truth, re-read at build) normalized +
    /// alias-resolved via `.ovp/tags/aliases.toml`, boilerplate dropped.
    /// Serde-additive: pre-tag indexes deserialize to empty.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
    /// Machine-inferred tags (`tags-suggest` kNN vote, `.ovp/tags/inferred.json`)
    /// — attached ONLY while the source has no operator tags, so a later
    /// hand-tagging silently retires them. Kept strictly apart from `tags`;
    /// surfaces render them visibly weaker (`~#tag`, dashed chips).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags_inferred: Vec<String>,
    /// Generic tags this source rolls up to via `[implications]` (`autogen`
    /// source ⇒ `agent`). Derived from `tags`/`tags_inferred` at build, kept
    /// separate so operator tags stay pure; searching/faceting a generic
    /// matches these, surfaces render them as a weaker roll-up (`>#tag`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags_implied: Vec<String>,
    /// Tier-0 URL entity ids this source mentions (`github:owner/repo`,
    /// `arxiv:2504.19413`, …), extracted deterministically from the note's
    /// URL + body. Forward list for the SourceDetail chips + `find --entity`;
    /// the reverse index (entity → sources) lives in the entities projection.
    /// Serde-additive: pre-entity indexes deserialize to empty.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub entities: Vec<String>,
}

impl SourceRow {
    /// Minimal row for tests / fold seeds — all optional axes empty.
    pub fn blank(sha256: impl Into<String>, status: SourceStatus) -> Self {
        Self {
            sha256: sha256.into(),
            status,
            title: None,
            author: None,
            url: None,
            origin: None,
            rel_path: None,
            date: None,
            content_date: None,
            captured_on: None,
            processed_on: None,
            last_run_id: None,
            pack_dir: None,
            fail_count: 0,
            last_reason: None,
            tags: Vec::new(),
            tags_inferred: Vec::new(),
            tags_implied: Vec::new(),
            entities: Vec::new(),
        }
    }

    /// Refresh legacy [`Self::date`] from the explicit B fields.
    pub fn sync_legacy_date(&mut self) {
        self.date = self
            .processed_on
            .clone()
            .or_else(|| self.captured_on.clone())
            .or_else(|| self.date.clone());
    }
}

/// Best-effort calendar day from a `run_id` (`daily-2026-07-26`,
/// `crystal-full-20260709`). Pure; never invents a day when none is present.
pub fn run_date_from_run_id(run_id: &str) -> Option<String> {
    // Prefer dashed ISO so we don't mis-read other digit runs.
    if let Some(i) = run_id.find("20") {
        let slice = &run_id[i..];
        if slice.len() >= 10 {
            let cand = &slice[..10];
            if is_iso_day(cand) {
                return Some(cand.to_string());
            }
        }
        if slice.len() >= 8 {
            let cand = &slice[..8];
            if cand.bytes().all(|b| b.is_ascii_digit()) {
                return Some(format!(
                    "{}-{}-{}",
                    &cand[..4],
                    &cand[4..6],
                    &cand[6..8]
                ));
            }
        }
    }
    None
}

/// True when `s` is `YYYY-MM-DD` with zero-padded month/day digits.
pub fn is_iso_day(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b.iter().enumerate().all(|(i, c)| match i {
            4 | 7 => true,
            _ => c.is_ascii_digit(),
        })
}

/// First `YYYY-MM-DD` substring in a path or title (filename-day heuristic for A).
/// Char-boundary safe — vault paths often contain CJK titles.
pub fn first_iso_day_in(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    if bytes.len() < 10 {
        return None;
    }
    for i in 0..=bytes.len() - 10 {
        if !s.is_char_boundary(i) || !s.is_char_boundary(i + 10) {
            continue;
        }
        let cand = &s[i..i + 10];
        if is_iso_day(cand) {
            return Some(cand.to_string());
        }
    }
    None
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PackRow {
    /// Vault-relative pack directory (contains reader.html / reader.md).
    pub pack_dir: String,
    pub title: String,
    /// **B (pipeline):** day prefix of the pack directory name — when the
    /// reader run wrote this pack (`cfg.date` of that daily), not the
    /// article's publish day.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub date: Option<String>,
    pub units: usize,
    pub cards: usize,
    #[serde(default)]
    pub json_repaired: bool,
    /// Card titles — the searchable surface of the pack.
    #[serde(default)]
    pub card_titles: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_sha256: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimStatus {
    Durable,
    Superseded,
    Retracted,
    /// From review.json — grounded but not durable; pending review/partner.
    Caveated,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimRow {
    pub claim_id: String,
    /// The claim's STABLE ledger identity (`ck-…`, deterministic over claim
    /// text + citation set). Additive: pre-existing indexes deserialize as
    /// None; claim_ids can collide across runs, claim_keys cannot — surfaces
    /// that need an unambiguous address (MCP ask citations, claim pages)
    /// prefer this.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_key: Option<String>,
    pub claim: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub theme: Option<String>,
    pub status: ClaimStatus,
    /// Case ids (pack dirs / source cases) the claim cites.
    #[serde(default)]
    pub sources: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strength: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    /// **B (pipeline):** calendar day of the run that produced this claim,
    /// when derivable from `run_id` (`daily-YYYY-MM-DD` / compact forms).
    /// Crystal ledger has no written-at; this is the only honest B signal.
    /// Serde-additive. Axis C (subject period) is not stored.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_date: Option<String>,
    /// Review lane for caveated claims (`review` | `source_insight`).
    /// None for durable/superseded/retracted rows and pre-M35 indexes.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lane: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunRow {
    pub run_id: String,
    pub date: String,
    /// Vault-relative report file.
    pub report_file: String,
    pub succeeded: usize,
    pub failed: usize,
    pub skipped: usize,
    pub blocked: usize,
    #[serde(default)]
    pub ingested: usize,
    #[serde(default)]
    pub pinboard_new: usize,
    #[serde(default)]
    pub lifecycle_warnings: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Totals {
    pub sources: usize,
    pub queued: usize,
    pub processed: usize,
    pub failed: usize,
    pub blocked: usize,
    pub needs_content: usize,
    pub unparseable: usize,
    pub duplicates: usize,
    pub packs: usize,
    pub claims_durable: usize,
    pub claims_caveated: usize,
    pub runs: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BlockedSource {
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    pub fail_count: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_attempt: Option<String>,
    /// Whole days since `last_attempt` (build date − last_attempt). `None` when
    /// the date is unknown/unparseable. The aging signal the console/portal use
    /// to escalate chronic blocks visually (amber, then red past a threshold).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub days_stuck: Option<usize>,
}

/// A source stuck outside the reader trunk because it lacks fetchable content
/// (a bare bookmark / needs-content flag that enrichment has not resolved).
/// Distinct from `BlockedSource` (which is 3-strikes reader failure): a stuck
/// source never entered the reader loop. Carries the same `days_stuck` aging
/// so "needs content 12d" can escalate the same way.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StuckSource {
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// First time this source was seen queued/flagged (the ledger date).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub first_seen: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub days_stuck: Option<usize>,
}

/// Days-stuck thresholds for visual escalation. A field, not a render — the
/// portal/console decide the colors, but the amber→red boundary lives here so
/// every surface agrees. `< AMBER` = fresh, `[AMBER, RED)` = amber (warn),
/// `>= RED` = red (chronic).
pub const DAYS_STUCK_AMBER: usize = 3;
pub const DAYS_STUCK_RED: usize = 7;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunStats {
    pub window_days: usize,
    pub total_runs: usize,
    pub succeeded: usize,
    pub failed: usize,
    pub success_rate_pct: f64,
    pub avg_processed_per_run: f64,
}

/// Run-liveness heartbeat surfaced into the read model (OVP2 observability P0).
/// Mirrors `.ovp/last-run.json`; `minutes_since` is deliberately NOT stored —
/// the portal computes age client-side from `started_at`/`ended_at` + now, so
/// the banner ages without a rebuild. Serde-additive: a pre-P0 index has no
/// `last_run` field and deserializes to `None`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LastRunModel {
    pub run_id: String,
    /// Wall-clock start (UTC, RFC3339).
    pub started_at: String,
    /// Wall-clock terminal time (UTC, RFC3339); None while `running`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ended_at: Option<String>,
    /// `running` | `completed` | `failed` | `aborted`.
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processed: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failed: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub blocked: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capped: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub queued_after: Option<usize>,
    /// LIVE in-run progress (only while `running`): sources finished so far this
    /// run. The portal renders `processed_so_far / total_planned` so a long run
    /// shows movement instead of a frozen banner. Absent on terminal records.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processed_so_far: Option<usize>,
    /// LIVE in-run progress: total sources planned this run (post `--max-sources`
    /// cap). Pairs with `processed_so_far`. Absent on terminal records.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub total_planned: Option<usize>,
    /// LIVE in-run progress: the source just finished (title or rel path).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current: Option<String>,
    /// LIVE per-source activity ring (the portal's tail -f): the last ~20 source
    /// outcomes, oldest→newest, while `running`. Empty on terminal records.
    /// Mirrors the heartbeat `recent[]`; the SPA renders the ✓/✗ feed from it.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub recent: Vec<RecentSourceModel>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// One per-source outcome surfaced into the read model — the SPA's live feed
/// entry. Mirrors the heartbeat `RecentSource`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RecentSourceModel {
    pub seq: usize,
    pub title: String,
    /// `"ok"` | `"failed"`.
    pub status: String,
    #[serde(default)]
    pub units: usize,
    #[serde(default)]
    pub cards: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    /// Wall-clock instant the source finished (UTC, RFC3339).
    pub at: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OpsState {
    pub blocked_sources: Vec<BlockedSource>,
    /// Needs-content sources aging in place (bare bookmarks enrichment has not
    /// resolved). Ordered most-stuck first.
    #[serde(default)]
    pub stuck_sources: Vec<StuckSource>,
    pub queue_depth: usize,
    /// Sources the most recent run left unprocessed because `--max-sources`
    /// capped it. Non-zero with a non-empty queue = the backlog is not draining
    /// — the "why is nothing moving" signal the operator was otherwise blind to.
    #[serde(default)]
    pub capped: usize,
    pub run_stats: Option<RunStats>,
    /// The run-liveness heartbeat (`.ovp/last-run.json`) at build time. None on
    /// a fresh vault (no runs yet) or a pre-P0 index.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_run: Option<LastRunModel>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IndexModel {
    pub schema: String,
    /// Date the model was built (caller-provided; keeps rebuilds deterministic).
    pub date: String,
    /// Wall-clock build instant (UTC RFC3339). Unlike `date` (a day string,
    /// deterministic on purpose) this is a true instant, so three runs on the
    /// same day are distinguishable and a stale projection no longer renders
    /// identically to a fresh one. Serde-additive: pre-P1 indexes deserialize
    /// with `None` and every surface shows "unknown age".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub built_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    pub totals: Totals,
    pub sources: Vec<SourceRow>,
    pub packs: Vec<PackRow>,
    pub claims: Vec<ClaimRow>,
    pub runs: Vec<RunRow>,
    #[serde(default)]
    pub ops: OpsState,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_date_from_run_id_parses_dashed_and_compact() {
        assert_eq!(
            run_date_from_run_id("daily-2026-07-26"),
            Some("2026-07-26".into())
        );
        assert_eq!(
            run_date_from_run_id("crystal-full-20260709"),
            Some("2026-07-09".into())
        );
        assert_eq!(run_date_from_run_id("ck-abcdef"), None);
    }

    #[test]
    fn first_iso_day_in_path_and_is_iso_day() {
        assert!(is_iso_day("2026-05-17"));
        assert!(!is_iso_day("2026-5-17"));
        assert_eq!(
            first_iso_day_in("50-Inbox/02-Pinboard/2026-05-17_Title-ab12.md"),
            Some("2026-05-17".into())
        );
        assert_eq!(first_iso_day_in("no-date-here.md"), None);
        // Month-only folder is not a full ISO day; CJK titles must not panic.
        assert_eq!(
            first_iso_day_in("50-Inbox/03-Processed/2026-04/你不知道的 Agent.md"),
            None
        );
        assert_eq!(
            first_iso_day_in("50-Inbox/03-Processed/2026-04/2026-04-15_你不知道的.md"),
            Some("2026-04-15".into())
        );
    }

    #[test]
    fn sync_legacy_date_prefers_processed_then_captured() {
        let mut r = SourceRow::blank("x", SourceStatus::Queued);
        r.captured_on = Some("2026-01-01".into());
        r.sync_legacy_date();
        assert_eq!(r.date.as_deref(), Some("2026-01-01"));
        r.processed_on = Some("2026-02-02".into());
        r.sync_legacy_date();
        assert_eq!(r.date.as_deref(), Some("2026-02-02"));
    }
}
