//! M31 read model — a deterministic JSON projection over product state:
//!
//! ```text
//! .ovp/daily-runs.jsonl ─┐
//! .ovp/intake.jsonl ─────┤
//! 50-Inbox/01-Raw scan ──┼─ build_index ─▶ IndexModel ─▶ .ovp/index/index.json
//! 40-Resources/Reader ───┤                     │
//! .ovp/crystal store ────┤                     └─▶ run_query (find) / console
//! .ovp/reports ──────────┘
//! ```
//!
//! Design decision (M31): a file/JSON projection, NOT SQLite. At product
//! scale (hundreds of sources, dozens of claims) a full rebuild is
//! milliseconds; the projection is diffable, greppable, has no migration
//! machinery (rebuild IS the migration), and cannot become a hidden truth
//! source — the ledgers and packs stay authoritative. Revisit only when
//! query latency or FTS needs prove otherwise; the `read_index` boundary is
//! where a different backend would slot in.

pub mod build;
pub mod evidence;
pub mod model;
pub mod query;
pub mod score;

pub use build::{build_index, read_index, write_index};
pub use evidence::{build_evidence, read_evidence, write_evidence, EvidenceModel};
pub use model::{
    BlockedSource, ClaimRow, ClaimStatus, IndexModel, OpsState, PackRow, RunRow, RunStats,
    SourceRow, SourceStatus, Totals, INDEX_SCHEMA,
};
pub use query::{
    claim_status_str, run_evidence_query, run_query, source_status_str, Hit, Query, QueryKind,
};
