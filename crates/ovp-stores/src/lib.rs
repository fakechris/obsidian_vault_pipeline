//! OVP Next store impls — `ovp_core::PlanApplier` backends.
//!
//! - `VaultFsPlanApplier` — vault markdown files (VaultCreate/Update).
//! - `CanonicalFsStoreApplier` — canonical-concept records (CanonicalUpsert).
//! - `CompositePlanApplier` — routes a plan's ops across backends that
//!   handle disjoint kinds, so a full plan applies with no Unsupported.

pub mod canonical_fs;
pub mod composite;
pub mod vault_fs;
pub mod vault_scan;

pub use canonical_fs::CanonicalFsStoreApplier;
pub use composite::CompositePlanApplier;
pub use vault_fs::VaultFsPlanApplier;
pub use vault_scan::{backlinks_from_files, scan_backlinks, walk_markdown};
