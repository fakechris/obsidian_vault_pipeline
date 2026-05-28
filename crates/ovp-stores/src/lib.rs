//! OVP Next store impls. v1 ships only `VaultFsPlanApplier`; future
//! crates of this shape will add canonical-store and event-log
//! appliers behind the same `ovp_core::PlanApplier` trait.

pub mod vault_fs;

pub use vault_fs::VaultFsPlanApplier;
