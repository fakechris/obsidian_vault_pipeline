//! M31 capture/intake — the boundary where content enters the vault
//! lifecycle:
//!
//! ```text
//! Clippings/ ─┐
//! 50-Inbox/00-Capture ─┼─ sweep_intake ──▶ 50-Inbox/01-Raw/<YYYY-MM>/   (reader queue)
//! 50-Inbox/02-Pinboard ┘        │
//!        ▲                      ├─ duplicates ▶ 50-Inbox/03-Processed/duplicates/
//! pinboard-sync (trait-gated)   └─ thin/broken files stay put, flagged once
//! ```
//!
//! Everything is non-destructive (move/suffix, never delete/overwrite),
//! deduplicated by URL and content sha256, and recorded append-only in
//! `.ovp/intake.jsonl` / `.ovp/pinboard-sync.jsonl` plus the vault-wide
//! `60-Logs/pipeline.jsonl` write log (OVP_RULES). NOT the demoted M7–M13
//! path: no canonical store, no concept extraction — capture and lifecycle
//! only.

pub mod ledger;
pub mod pinboard;
pub mod sweep;
pub mod vaultops;

pub use ledger::{
    append_intake_record, flagged_hashes, known_content_hashes, known_urls, read_intake_ledger,
    IntakeAction, IntakeRecord, INTAKE_SCHEMA,
};
pub use pinboard::{
    read_pinboard_ledger, sync_pinboard, synced_urls, FixturePinboardFetch, PinboardFetch,
    PinboardPost, PinboardSyncOutcome, PinboardSyncRecord, PINBOARD_SCHEMA,
};
#[cfg(feature = "pinboard-live")]
pub use pinboard::LivePinboardFetch;
pub use sweep::{sweep_intake, IntakeConfig, SweepOutcome, MIN_READER_BODY_CHARS};
pub use vaultops::{
    append_jsonl, append_pipeline_event, hex_sha256, read_jsonl, rel_to, safe_move, write_new,
    PipelineLogEvent, RunLock,
};
