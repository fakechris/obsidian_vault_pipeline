//! OVP Next effect boundary: a synchronous `ModelClient` trait plus three
//! impls — fixture (in-memory), cached (file-backed), and (post-C9) live
//! `AnthropicBlockingClient` behind the `anthropic` feature.
//!
//! Provider-neutral on purpose. `ovp-domain::LLMInvoker` maps domain
//! types (`PromptRequest` / `ModelResponse`) onto this crate's wire types
//! (`ModelRequest` / `ModelReply`) at the I/O boundary.

pub mod cache;
pub mod client;
pub mod fixture;
pub mod key;
pub mod reply;
pub mod request;

pub use cache::{CacheMode, CachedModelClient};
pub use client::{CallError, ModelClient, NeverCallsClient};
pub use fixture::FixtureModelClient;
pub use key::request_key;
pub use reply::{ModelReply, StopReason, Usage};
pub use request::{ModelMessage, ModelRequest};
