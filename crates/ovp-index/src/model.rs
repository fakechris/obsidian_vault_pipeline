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
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunStats {
    pub window_days: usize,
    pub total_runs: usize,
    pub succeeded: usize,
    pub failed: usize,
    pub success_rate_pct: f64,
    pub avg_processed_per_run: f64,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OpsState {
    pub blocked_sources: Vec<BlockedSource>,
    pub queue_depth: usize,
    pub run_stats: Option<RunStats>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IndexModel {
    pub schema: String,
    /// Date the model was built (caller-provided; keeps rebuilds deterministic).
    pub date: String,
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
