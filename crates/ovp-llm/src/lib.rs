//! OVP Next effect boundary: a synchronous `ModelClient` trait plus three
//! impls — fixture (in-memory), cached (file-backed), and live HTTP clients
//! (`AnthropicBlockingClient`, `OpenAiBlockingClient`) behind the `anthropic`
//! feature (which only gates `reqwest`).
//!
//! Provider-neutral on purpose. `ovp-domain::LLMInvoker` maps domain
//! types (`PromptRequest` / `ModelResponse`) onto this crate's wire types
//! (`ModelRequest` / `ModelReply`) at the I/O boundary.

pub mod anthropic;
pub mod cache;
pub mod client;
pub mod decision;
pub mod fixture;
pub mod key;
pub mod openai;
pub mod provider;
pub mod reply;
pub mod request;
pub mod usage;

#[cfg(feature = "anthropic")]
pub mod live;

pub use cache::{CacheMode, CachedModelClient};
pub use client::{
    BudgetEscalatingModelClient, CallError, ModelClient, NeverCallsClient, RetryingModelClient,
    failure_class, is_transient,
};
pub use fixture::FixtureModelClient;
pub use key::request_key;
pub use provider::{LLM_NOT_CONFIGURED, LlmProvider, resolve_api_key};
pub use reply::{ExecutableToolCall, ModelReply, ReplyBlock, StopReason, Usage};
pub use request::{AssistantBlock, ModelMessage, ModelRequest, ToolDef, ToolResultBlock};
pub use usage::UsageRow;

#[cfg(feature = "anthropic")]
pub use anthropic::AnthropicBlockingClient;
#[cfg(feature = "anthropic")]
pub use openai::OpenAiBlockingClient;
#[cfg(feature = "anthropic")]
pub use live::{
    LiveClientConfig, LiveHttpClient, build_recording_live_client,
    build_recording_live_client_bounded,
};
