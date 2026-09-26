//! Typed semantic decisions, independent of generative `ModelClient` and vendors.
//!
//! This is an additive effect boundary: no production workflow constructs it by
//! default. Domain code owns questions, thresholds, abstention policy and actions.
//! Probabilities describe the provider's judgment, never authorization or truth.

mod cache;
#[cfg(feature = "decision-live")]
mod live;
mod types;
pub mod typesafe;
pub mod runtime;

pub use cache::{CachedDecisionClient, DecisionCacheMode, FixtureDecisionClient, decision_key};
#[cfg(feature = "decision-live")]
pub use live::{HttpOptions, TypeSafeDecisionClient};
pub use types::*;

/// Synchronous, injectable boundary. Implementations must validate requests and
/// replies; consumers may also validate an untrusted third-party implementation.
pub trait DecisionClient: Send + Sync {
    fn profile(&self) -> &DecisionProfile;
    fn capabilities(&self) -> DecisionCapabilities;
    fn decide(&mut self, request: &DecisionRequest) -> Result<DecisionReply, DecisionError>;
}
