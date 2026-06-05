use std::time::Duration;

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

/// Whether a `CallError` is worth retrying. Only clearly-transient faults:
/// transport errors (timeout/connect/reset), HTTP 429, and HTTP 5xx (plus the
/// equivalent structured provider error types). NEVER retry 4xx, decode/parse
/// errors, cache misses, or `Unexpected` — retrying those just wastes calls.
pub fn is_transient(err: &CallError) -> bool {
    match err {
        CallError::Transport { .. } => true,
        CallError::Provider { code, .. } => {
            if let Ok(n) = code.parse::<u16>() {
                n == 429 || (500..=599).contains(&n)
            } else {
                let c = code.to_ascii_lowercase();
                c.contains("rate_limit") || c.contains("overloaded") || c.contains("unavailable")
            }
        }
        CallError::CacheMiss { .. } | CallError::Decode { .. } | CallError::Unexpected { .. } => {
            false
        }
    }
}

/// A `ModelClient` wrapper that retries the inner client on transient failures
/// (see [`is_transient`]) up to `max_retries` times, with a linear backoff
/// (`backoff * attempt`). Non-transient errors fail immediately.
///
/// Cache semantics are preserved by placement: wrap the LIVE client and put a
/// `CachedModelClient` *outside* this — a cache hit never reaches `call()`
/// (so no retry), and a cache miss only records once the retried live call
/// finally succeeds. A failed call records nothing.
pub struct RetryingModelClient<C: ModelClient> {
    inner: C,
    max_retries: u32,
    backoff: Duration,
}

impl<C: ModelClient> RetryingModelClient<C> {
    /// `max_retries` is the number of RETRIES after the first attempt (so total
    /// attempts = `max_retries + 1`). A zero `backoff` sleeps not at all.
    pub fn new(inner: C, max_retries: u32, backoff: Duration) -> Self {
        Self { inner, max_retries, backoff }
    }
}

impl<C: ModelClient> ModelClient for RetryingModelClient<C> {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
        let mut attempt: u32 = 0;
        loop {
            match self.inner.call(request) {
                Ok(reply) => return Ok(reply),
                Err(err) => {
                    if attempt < self.max_retries && is_transient(&err) {
                        attempt += 1;
                        if !self.backoff.is_zero() {
                            std::thread::sleep(self.backoff * attempt);
                        }
                        continue;
                    }
                    return Err(err);
                }
            }
        }
    }
}

#[cfg(test)]
mod retry_tests {
    use super::*;
    use crate::reply::{StopReason, Usage};

    /// Returns `err` for the first `fail_times` calls, then a fixed reply.
    /// Records how many times it was called.
    struct FlakyClient {
        fail_times: u32,
        calls: u32,
        err: CallError,
    }

    impl FlakyClient {
        fn new(fail_times: u32, err: CallError) -> Self {
            Self { fail_times, calls: 0, err }
        }
        fn reply() -> ModelReply {
            ModelReply {
                model: "m".into(),
                text: "ok".into(),
                stop_reason: StopReason::EndTurn,
                usage: Usage { input_tokens: 1, output_tokens: 1 },
            }
        }
    }

    impl ModelClient for FlakyClient {
        fn call(&mut self, _request: &ModelRequest) -> Result<ModelReply, CallError> {
            self.calls += 1;
            if self.calls <= self.fail_times {
                Err(self.err.clone())
            } else {
                Ok(Self::reply())
            }
        }
    }

    fn req() -> ModelRequest {
        ModelRequest {
            model: "m".into(),
            system: None,
            messages: vec![],
            max_tokens: 16,
            temperature: None,
            cache_namespace: None,
        }
    }

    #[test]
    fn retries_transient_then_succeeds() {
        // Two transport errors, then success; 2 retries is enough (3 attempts).
        let flaky = FlakyClient::new(2, CallError::Transport { detail: "reset".into() });
        let mut client = RetryingModelClient::new(flaky, 2, Duration::ZERO);
        let reply = client.call(&req()).expect("should succeed on the 3rd attempt");
        assert_eq!(reply.text, "ok");
        assert_eq!(client.inner.calls, 3, "1 initial + 2 retries");
    }

    #[test]
    fn does_not_retry_non_transient() {
        // A 400 (invalid request) must fail immediately — no retries.
        let flaky = FlakyClient::new(5, CallError::Provider { code: "400".into(), detail: "bad".into() });
        let mut client = RetryingModelClient::new(flaky, 3, Duration::ZERO);
        let err = client.call(&req()).unwrap_err();
        assert!(matches!(err, CallError::Provider { .. }));
        assert_eq!(client.inner.calls, 1, "non-transient error must not be retried");
    }

    #[test]
    fn fails_after_exhausting_retry_budget() {
        // Transient every time; with 2 retries we make 3 attempts then give up.
        let flaky = FlakyClient::new(99, CallError::Provider { code: "503".into(), detail: "down".into() });
        let mut client = RetryingModelClient::new(flaky, 2, Duration::ZERO);
        let err = client.call(&req()).unwrap_err();
        assert!(matches!(err, CallError::Provider { .. }));
        assert_eq!(client.inner.calls, 3, "1 initial + 2 retries, then fail");
    }

    #[test]
    fn transient_classification() {
        assert!(is_transient(&CallError::Transport { detail: "x".into() }));
        assert!(is_transient(&CallError::Provider { code: "429".into(), detail: "x".into() }));
        assert!(is_transient(&CallError::Provider { code: "503".into(), detail: "x".into() }));
        assert!(is_transient(&CallError::Provider { code: "overloaded_error".into(), detail: "x".into() }));
        assert!(!is_transient(&CallError::Provider { code: "400".into(), detail: "x".into() }));
        assert!(!is_transient(&CallError::Provider { code: "401".into(), detail: "x".into() }));
        assert!(!is_transient(&CallError::Decode { detail: "no text".into() }));
        assert!(!is_transient(&CallError::CacheMiss { key: "k".into() }));
    }
}
