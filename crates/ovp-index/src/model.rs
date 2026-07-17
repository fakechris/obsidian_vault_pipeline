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
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SourceRow {
    pub sha256: String,
    pub status: SourceStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// Current best-known vault-relative location.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rel_path: Option<String>,
    /// Date of the last recorded activity for this source.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub date: Option<String>,
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
    /// Tier-0 URL entity ids this source mentions (`github:owner/repo`,
    /// `arxiv:2504.19413`, …), extracted deterministically from the note's
    /// URL + body. Forward list for the SourceDetail chips + `find --entity`;
    /// the reverse index (entity → sources) lives in the entities projection.
    /// Serde-additive: pre-entity indexes deserialize to empty.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub entities: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PackRow {
    /// Vault-relative pack directory (contains reader.html / reader.md).
    pub pack_dir: String,
    pub title: String,
    /// Date prefix of the pack directory name.
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
