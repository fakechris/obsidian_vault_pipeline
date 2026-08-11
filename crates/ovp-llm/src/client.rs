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
    /// The request violates the tool-use protocol (`tool_result_adjacency_all`):
    /// e.g. a tool_use turn not answered by exactly its results. Caller bug —
    /// failing loudly here beats a provider 4xx on an invalid request.
    Protocol { detail: String },
    /// A reasoning/thinking model spent its whole `max_tokens` budget on a
    /// thinking block and emitted NO text block (`stop_reason=max_tokens`). NOT
    /// transient — retrying with the SAME budget won't help — but recoverable by
    /// a higher-budget retry ([`BudgetEscalatingModelClient`]).
    BudgetExhausted { detail: String },
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
            CallError::Protocol { detail } => write!(f, "tool protocol violation: {detail}"),
            CallError::BudgetExhausted { detail } => write!(f, "budget exhausted: {detail}"),
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

    /// Forget any memoized reply for `request`. Pipelines call this when a
    /// reply that *transported* fine turned out to carry UNUSABLE content
    /// (parse failure, truth-layer violation, zero cards): under a recording
    /// cache the bad reply is already cassetted and — request keys being pure
    /// content hashes — every later retry would replay the identical failure
    /// forever. Forgetting makes the next attempt genuinely re-ask the model.
    ///
    /// Default: no-op. Live/fake clients memoize nothing; replay-only caches
    /// must NOT delete their (possibly committed) fixtures.
    fn invalidate(&mut self, _request: &ModelRequest) {}
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
                // `api_error` is Anthropic's structured 5xx — same retry
                // posture as its numeric form (failure_class agrees).
                c.contains("rate_limit")
                    || c.contains("overloaded")
                    || c.contains("unavailable")
                    || c == "api_error"
            }
        }
        // Protocol violations are caller bugs — retrying resends the same
        // invalid message sequence.
        CallError::CacheMiss { .. }
        | CallError::Decode { .. }
        | CallError::Protocol { .. }
        | CallError::BudgetExhausted { .. }
        | CallError::Unexpected { .. } => false,
    }
}

/// Coarse, user-actionable classification of a call failure. The slugs are a
/// stable wire vocabulary — surfaces (portal, CLI hints) map each to a
/// targeted fix suggestion instead of showing raw provider text:
///
/// `auth` | `rate_limited` | `context_exceeded` | `budget_exhausted` |
/// `overloaded` | `network` | `decode` | `protocol` | `cache_miss` |
/// `provider_error` | `internal`
///
/// Anthropic's client stamps `Provider.code` with the structured error type
/// (`authentication_error`, `overloaded_error`, `api_error`, …) when the body
/// parses, the raw HTTP status as a string when it doesn't, or `"no_api_key"`
/// — so both the numeric and the keyword branch must cover the same classes.
/// The prose fallback catches context overflow reported under a generic
/// `invalid_request_error` ("prompt is too long: …").
pub fn failure_class(err: &CallError) -> &'static str {
    match err {
        CallError::Provider { code, detail } => {
            let d = detail.to_ascii_lowercase();
            let context_exceeded =
                d.contains("too long") || d.contains("context length") || d.contains("context window");
            if let Ok(n) = code.parse::<u16>() {
                match n {
                    401 | 403 => "auth",
                    429 => "rate_limited",
                    400 | 413 if context_exceeded => "context_exceeded",
                    500..=599 => "overloaded",
                    _ => "provider_error",
                }
            } else {
                let c = code.to_ascii_lowercase();
                if c.contains("api_key") || c.contains("auth") || c.contains("permission") {
                    "auth"
                } else if c.contains("rate_limit") {
                    "rate_limited"
                // `api_error` is Anthropic's structured 5xx ("internal server
                // error") — same retry-later advice as overloaded.
                } else if c.contains("overloaded") || c.contains("unavailable") || c == "api_error" {
                    "overloaded"
                } else if c.contains("context") || c == "request_too_large" || context_exceeded {
                    "context_exceeded"
                } else {
                    "provider_error"
                }
            }
        }
        CallError::Transport { .. } => "network",
        CallError::Decode { .. } => "decode",
        CallError::Protocol { .. } => "protocol",
        CallError::BudgetExhausted { .. } => "budget_exhausted",
        CallError::CacheMiss { .. } => "cache_miss",
        CallError::Unexpected { .. } => "internal",
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

    fn invalidate(&mut self, request: &ModelRequest) {
        self.inner.invalidate(request);
    }
}

/// A `ModelClient` wrapper that recovers from [`CallError::BudgetExhausted`] —
/// a reasoning model that used its whole `max_tokens` on thinking and emitted no
/// text — by retrying ONCE with a raised `max_tokens` (`escalated_max_tokens`).
/// Any other error (and a success) passes straight through. Bounded to a single
/// retry: if the higher budget still exhausts, the error surfaces (fail loud).
///
/// Placement: wrap the live (or retrying) client and put a `CachedModelClient`
/// *outside*, so the cache keys on the ORIGINAL request and records the
/// successful higher-budget reply once. The escalation only takes effect if the
/// underlying client honors a per-request `max_tokens` at/above the escalated
/// value (the Anthropic client uses `max(request.max_tokens, env_override)`).
pub struct BudgetEscalatingModelClient<C: ModelClient> {
    inner: C,
    escalated_max_tokens: u32,
}

impl<C: ModelClient> BudgetEscalatingModelClient<C> {
    pub fn new(inner: C, escalated_max_tokens: u32) -> Self {
        Self { inner, escalated_max_tokens }
    }
}

impl<C: ModelClient> ModelClient for BudgetEscalatingModelClient<C> {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
        match self.inner.call(request) {
            Err(CallError::BudgetExhausted { .. }) if request.max_tokens < self.escalated_max_tokens => {
                let mut bumped = request.clone();
                bumped.max_tokens = self.escalated_max_tokens;
                self.inner.call(&bumped) // one bounded higher-budget retry
            }
            other => other,
        }
    }

    fn invalidate(&mut self, request: &ModelRequest) {
        self.inner.invalidate(request);
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
                blocks: None,
                raw_stop_reason: None,
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
            tools: None,
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
        // Structured 5xx (parsed body keeps /error/type, drops the status) —
        // retry posture must match failure_class="overloaded".
        assert!(is_transient(&CallError::Provider { code: "api_error".into(), detail: "x".into() }));
        assert!(!is_transient(&CallError::Provider { code: "400".into(), detail: "x".into() }));
        assert!(!is_transient(&CallError::Provider { code: "401".into(), detail: "x".into() }));
        assert!(!is_transient(&CallError::Decode { detail: "no text".into() }));
        assert!(!is_transient(&CallError::CacheMiss { key: "k".into() }));
        // M20: budget-exhausted is NOT transient (same-budget retry won't help).
        assert!(!is_transient(&CallError::BudgetExhausted { detail: "thinking".into() }));
    }

    // ---- M20 budget escalation ----

    /// Returns `BudgetExhausted` until a request arrives with `max_tokens >=
    /// threshold`, then succeeds. Records calls + the last max_tokens seen.
    struct BudgetClient {
        threshold: u32,
        calls: u32,
        last_max_tokens: u32,
    }
    impl ModelClient for BudgetClient {
        fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
            self.calls += 1;
            self.last_max_tokens = request.max_tokens;
            if request.max_tokens >= self.threshold {
                Ok(FlakyClient::reply())
            } else {
                Err(CallError::BudgetExhausted { detail: "thinking block, no text".into() })
            }
        }
    }

    #[test]
    fn escalates_budget_then_succeeds() {
        // First attempt at max_tokens=16 exhausts; the escalated 48000 succeeds.
        let inner = BudgetClient { threshold: 40_000, calls: 0, last_max_tokens: 0 };
        let mut client = BudgetEscalatingModelClient::new(inner, 48_000);
        let reply = client.call(&req()).expect("escalated retry should succeed");
        assert_eq!(reply.text, "ok");
        assert_eq!(client.inner.calls, 2, "1 initial + 1 escalated retry");
        assert_eq!(client.inner.last_max_tokens, 48_000, "retry raised max_tokens");
    }

    #[test]
    fn budget_escalation_is_one_shot_then_fails_loud() {
        // Even the escalated budget exhausts → surface the error after one retry.
        let inner = BudgetClient { threshold: 999_999, calls: 0, last_max_tokens: 0 };
        let mut client = BudgetEscalatingModelClient::new(inner, 48_000);
        let err = client.call(&req()).unwrap_err();
        assert!(matches!(err, CallError::BudgetExhausted { .. }));
        assert_eq!(client.inner.calls, 2, "exactly one escalated retry, then fail");
    }

    #[test]
    fn escalator_passes_through_non_budget_errors_and_success() {
        // A transport error is not the escalator's business → passed through, no retry.
        let flaky = FlakyClient::new(1, CallError::Transport { detail: "reset".into() });
        let mut client = BudgetEscalatingModelClient::new(flaky, 48_000);
        assert!(matches!(client.call(&req()).unwrap_err(), CallError::Transport { .. }));
        assert_eq!(client.inner.calls, 1, "non-budget error not retried by escalator");
    }

    #[test]
    fn failure_class_covers_the_wire_vocabulary() {
        let provider = |code: &str, detail: &str| CallError::Provider {
            code: code.into(),
            detail: detail.into(),
        };
        // Anthropic stamps numeric HTTP codes…
        assert_eq!(failure_class(&provider("401", "authentication_error")), "auth");
        assert_eq!(failure_class(&provider("403", "permission_error")), "auth");
        assert_eq!(failure_class(&provider("429", "rate_limit_error")), "rate_limited");
        assert_eq!(failure_class(&provider("529", "overloaded_error")), "overloaded");
        assert_eq!(failure_class(&provider("500", "api_error")), "overloaded");
        assert_eq!(
            failure_class(&provider("400", "prompt is too long: 250000 tokens")),
            "context_exceeded"
        );
        assert_eq!(failure_class(&provider("400", "invalid model")), "provider_error");
        // …or the structured `no_api_key` sentinel.
        assert_eq!(failure_class(&provider("no_api_key", "ANTHROPIC_API_KEY unset")), "auth");
        // Structured error types (the LIVE path when the body parses —
        // anthropic.rs keeps /error/type and drops the HTTP status).
        assert_eq!(failure_class(&provider("authentication_error", "bad key")), "auth");
        assert_eq!(failure_class(&provider("permission_error", "no access")), "auth");
        assert_eq!(failure_class(&provider("rate_limit_error", "slow down")), "rate_limited");
        assert_eq!(failure_class(&provider("overloaded_error", "busy")), "overloaded");
        assert_eq!(failure_class(&provider("api_error", "internal server error")), "overloaded");
        assert_eq!(
            failure_class(&provider("request_too_large", "request exceeds size limit")),
            "context_exceeded"
        );
        assert_eq!(
            failure_class(&provider(
                "invalid_request_error",
                "prompt is too long: 210000 tokens > 200000 maximum"
            )),
            "context_exceeded"
        );
        // Output-token validation is NOT context exhaustion (codex P2: the
        // old `maximum && token` heuristic misfiled it).
        assert_eq!(
            failure_class(&provider(
                "invalid_request_error",
                "max_tokens: 999999 exceeds the maximum allowed number of output tokens"
            )),
            "provider_error"
        );
        assert_eq!(failure_class(&CallError::Transport { detail: "timeout".into() }), "network");
        assert_eq!(
            failure_class(&CallError::BudgetExhausted { detail: "thinking".into() }),
            "budget_exhausted"
        );
        assert_eq!(failure_class(&CallError::Decode { detail: "bad json".into() }), "decode");
        assert_eq!(failure_class(&CallError::Protocol { detail: "adjacency".into() }), "protocol");
        assert_eq!(failure_class(&CallError::CacheMiss { key: "k".into() }), "cache_miss");
        assert_eq!(failure_class(&CallError::Unexpected { detail: "never".into() }), "internal");
    }
}
