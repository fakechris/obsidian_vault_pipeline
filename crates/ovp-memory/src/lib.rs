//! `ovp-memory` — ephemeral reuse surfaces over OVP product state.
//!
//! Provides retrieval, digest, ask, and working-memory capabilities.
//! Reads from the JSON index and Crystal store. **Never** writes to the Crystal
//! ledger or drives projection — all outputs are derived, ephemeral views.

pub mod ask;
pub mod digest;
pub mod working_memory;
