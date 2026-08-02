//! `ovp-memory` — ephemeral reuse surfaces over OVP product state.
//!
//! Provides retrieval, digest, ask, and working-memory capabilities.
//! Reads from the JSON index and Crystal store. **Never** writes to the Crystal
//! ledger or drives projection — all outputs are derived, ephemeral views.

pub mod agent;
pub mod agent_policy;
pub mod agent_transcript;
pub mod ask;
pub mod digest;
#[cfg(feature = "embed")]
pub mod embed_lane;
pub mod intent;
pub mod receipts;
pub mod retrieve;
pub mod bilingual;
pub mod source_work;
pub mod source_work_auto;
pub mod source_work_config;
pub mod source_work_queue;
pub mod vault_tools;
pub mod verify;
pub mod working_memory;
