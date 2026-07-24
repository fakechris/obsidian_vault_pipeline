//! OVP Next effect boundary: a synchronous `ModelClient` trait plus three
//! impls — fixture (in-memory), cached (file-backed), and (post-C9) live
//! `AnthropicBlockingClient` behind the `anthropic` feature.
//!
//! Provider-neutral on purpose. `ovp-domain::LLMInvoker` maps domain
//! types (`PromptRequest` / `ModelResponse`) onto this crate's wire types
//! (`ModelRequest` / `ModelReply`) at the I/O boundary.

pub mod anthropic;
pub mod cache;
pub mod client;
pub mod fixture;
pub mod key;
pub mod reply;
pub mod request;

#[cfg(feature = "anthropic")]
pub mod live;

pub use cache::{CacheMode, CachedModelClient};
pub use client::{
    is_transient, BudgetEscalatingModelClient, CallError, ModelClient, NeverCallsClient,
    RetryingModelClient,
};
pub use fixture::FixtureModelClient;
pub use key::request_key;
pub use reply::{ExecutableToolCall, ModelReply, ReplyBlock, StopReason, Usage};
pub use request::{
    AssistantBlock, ModelMessage, ModelRequest, ToolDef, ToolResultBlock,
};

#[cfg(feature = "anthropic")]
pub use anthropic::AnthropicBlockingClient;
#[cfg(feature = "anthropic")]
pub use live::{
    build_recording_live_client, resolve_api_key, LiveClientConfig, LLM_NOT_CONFIGURED,
};
