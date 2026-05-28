use serde::{Deserialize, Serialize};

use crate::reply::ModelReply;
use crate::request::ModelRequest;

/// Why a `ModelClient::call()` returned an error. Distinct from the
/// pipeline's `FilterError` so the LLM layer doesn't reach across into
/// pipeline concerns; `LLMInvoker` translates `CallError` → `FilterError`
/// at the boundary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CallError {
    /// Replay-only client received a request whose key is not in the cache.
    CacheMiss { key: String },
    /// The provider returned a structured error (auth, rate limit, server, etc.).
    Provider { code: String, detail: String },
    /// Transport-level failure (network, IO, decoding).
    Transport { detail: String },
    /// Response failed to parse into the wire `ModelReply` shape.
    Decode { detail: String },
    /// A client that should never be called was invoked. Bug indicator.
    Unexpected { detail: String },
}

impl std::fmt::Display for CallError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CallError::CacheMiss { key } => write!(f, "cache miss for key {}", &key[..key.len().min(16)]),
            CallError::Provider { code, detail } => write!(f, "provider error {code}: {detail}"),
            CallError::Transport { detail } => write!(f, "transport: {detail}"),
            CallError::Decode { detail } => write!(f, "decode: {detail}"),
            CallError::Unexpected { detail } => write!(f, "unexpected: {detail}"),
        }
    }
}

impl std::error::Error for CallError {}

/// The single I/O effect this crate exists for: take a request, return a reply.
/// Synchronous on purpose — the pipeline never sees async types.
///
/// `Send + Sync` so wrappers (cache, fanout, retry) can compose freely.
pub trait ModelClient: Send + Sync {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError>;
}

/// A client that errors on every call. Used as the inner client of a
/// `CachedModelClient` in `ReplayOnly` mode so that any cache miss
/// surfaces as an explicit error rather than silently reaching for a
/// network the test thought it didn't have.
pub struct NeverCallsClient;

impl ModelClient for NeverCallsClient {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
        Err(CallError::Unexpected {
            detail: format!(
                "NeverCallsClient invoked for model={} — replay-only cache miss?",
                request.model
            ),
        })
    }
}
