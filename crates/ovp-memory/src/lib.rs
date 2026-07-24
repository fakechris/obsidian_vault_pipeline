//! `ovp-memory` — ephemeral reuse surfaces over OVP product state.
//!
//! Provides retrieval, digest, ask, and working-memory capabilities.
//! Reads from the JSON index and Crystal store. **Never** writes to the Crystal
//! ledger or drives projection — all outputs are derived, ephemeral views.

pub mod agent;
pub mod agent_transcript;
pub mod ask;
pub mod digest;
pub mod intent;
pub mod vault_tools;
pub mod verify;
pub mod working_memory;
