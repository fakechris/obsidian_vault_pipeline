//! Anthropic Messages API client.
//!
//! The request/response **mapping** (provider-neutral `ModelRequest` →
//! Anthropic JSON body, and Anthropic JSON → `ModelReply`) is pure and
//! always compiled, so it is unit-tested in the default offline gauntlet
//! with no `reqwest` and no network.
//!
//! The HTTP-bearing [`AnthropicBlockingClient`] is gated behind the
//! `anthropic` feature so the default build (and CI tests) pull zero HTTP
//! deps. It uses `reqwest::blocking` — synchronous, no async runtime,
//! consistent with invariant #6 (async never leaks into the pipeline).

use serde_json::json;

use crate::client::CallError;
use crate::reply::{ModelReply, StopReason, Usage};
use crate::request::{ModelMessage, ModelRequest};

pub const DEFAULT_BASE_URL: &str = "https://api.anthropic.com/v1/messages";
pub const ANTHROPIC_VERSION: &str = "2023-06-01";

/// Build the Anthropic Messages API request body from a provider-neutral
/// `ModelRequest`. Pure — no I/O. `system` becomes a top-level field;
/// messages map role-for-role.
pub fn anthropic_request_body(req: &ModelRequest) -> serde_json::Value {
    let messages: Vec<serde_json::Value> = req
        .messages
        .iter()
        .map(|m| match m {
            ModelMessage::User { content } => json!({ "role": "user", "content": content }),
            ModelMessage::Assistant { content } => {
                json!({ "role": "assistant", "content": content })
            }
        })
        .collect();

    let mut body = json!({
        "model": req.model,
        "max_tokens": req.max_tokens,
        "messages": messages,
    });
    let obj = body.as_object_mut().expect("json object");
    if let Some(system) = &req.system {
        obj.insert("system".into(), json!(system));
    }
    if let Some(temp) = req.temperature {
        obj.insert("temperature".into(), json!(temp));
    }
    body
}

/// Parse an Anthropic Messages API success response body into a
/// `ModelReply`. Pure — no I/O. Concatenates all `text` content blocks.
pub fn parse_anthropic_reply(json_body: &str) -> Result<ModelReply, CallError> {
    let v: serde_json::Value = serde_json::from_str(json_body)
        .map_err(|e| CallError::Decode { detail: format!("response not JSON: {e}") })?;

    // A structured API error comes back as {"type":"error","error":{...}}.
    if v.get("type").and_then(|t| t.as_str()) == Some("error") {
        let code = v
            .pointer("/error/type")
            .and_then(|s| s.as_str())
            .unwrap_or("unknown")
            .to_string();
        let detail = v
            .pointer("/error/message")
            .and_then(|s| s.as_str())
            .unwrap_or("(no message)")
            .to_string();
        return Err(CallError::Provider { code, detail });
    }

    let model = v
        .get("model")
        .and_then(|m| m.as_str())
        .unwrap_or("")
        .to_string();

    let content = v
        .get("content")
        .and_then(|c| c.as_array())
        .ok_or_else(|| CallError::Decode { detail: "missing `content` array".into() })?;
    let mut text = String::new();
    for block in content {
        if block.get("type").and_then(|t| t.as_str()) == Some("text")
            && let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                text.push_str(t);
            }
    }
    if text.is_empty() {
        // Diagnose the common reasoning-model case: the provider returned only
        // `thinking` blocks (and no `text`), typically because it exhausted the
        // token budget while thinking. Make this loud + actionable rather than
        // a generic decode error — it is NOT a transient failure (retrying with
        // the same budget won't help; the fix is a larger OVP_LLM_MAX_TOKENS).
        let thinking_blocks = content
            .iter()
            .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("thinking"))
            .count();
        let stop = v.get("stop_reason").and_then(|s| s.as_str()).unwrap_or("unknown");
        // `stop_reason=max_tokens` with no text = the reasoning model spent its
        // whole budget thinking. Classify as `BudgetExhausted` (M20) so a
        // higher-budget retry can recover it; everything else is a genuine
        // `Decode` (no text for a non-budget reason → not retryable).
        if stop == "max_tokens" {
            let detail = format!(
                "thinking_budget_exhausted: response had {thinking_blocks} thinking block(s) and no \
                 text block (stop_reason=max_tokens). The provider is likely a reasoning/thinking model \
                 — raise OVP_LLM_MAX_TOKENS so it can emit text after thinking."
            );
            return Err(CallError::BudgetExhausted { detail });
        }
        let detail =
            format!("no_text_content_blocks: response had no text content block (stop_reason={stop})");
        return Err(CallError::Decode { detail });
    }

    let stop_reason = map_stop_reason(v.get("stop_reason").and_then(|s| s.as_str()));

    let input_tokens = v
        .pointer("/usage/input_tokens")
        .and_then(|n| n.as_u64())
        .unwrap_or(0) as u32;
    let output_tokens = v
        .pointer("/usage/output_tokens")
        .and_then(|n| n.as_u64())
        .unwrap_or(0) as u32;

    Ok(ModelReply {
        model,
        text,
        stop_reason,
        usage: Usage { input_tokens, output_tokens },
    })
}

fn map_stop_reason(s: Option<&str>) -> StopReason {
    match s {
        Some("end_turn") => StopReason::EndTurn,
        Some("max_tokens") => StopReason::MaxTokens,
        Some("stop_sequence") => StopReason::StopSequence,
        _ => StopReason::Unknown,
    }
}

#[cfg(feature = "anthropic")]
pub use live::AnthropicBlockingClient;

#[cfg(feature = "anthropic")]
mod live {
    use super::*;
    use crate::client::ModelClient;
    use crate::request::ModelRequest;

    /// Live Anthropic Messages API client over `reqwest::blocking`.
    ///
    /// Holds the API key + a blocking HTTP client. `call()` builds the
    /// body via [`super::anthropic_request_body`], POSTs it, and parses
    /// via [`super::parse_anthropic_reply`]. Network/transport failures
    /// become `CallError::Transport`; structured API errors become
    /// `CallError::Provider`.
    pub struct AnthropicBlockingClient {
        api_key: String,
        base_url: String,
        version: String,
        /// When set, overrides the model the domain put on each request — needed
        /// for Anthropic-compatible providers (e.g. MiniMax) whose model names
        /// differ from `claude-*`.
        model_override: Option<String>,
        /// When set, overrides the request's `max_tokens` on the wire — reasoning
        /// models (e.g. MiniMax-M2) spend the budget on `thinking` blocks and
        /// need a larger ceiling to also emit the final `text`. Does not change
        /// the cached `ModelRequest` (so cassette keys are unaffected).
        max_tokens_override: Option<u32>,
        /// Request timeout in seconds. Tracked as state so subsequent
        /// `with_no_proxy()` rebuilds preserve it. See [`DEFAULT_TIMEOUT_SECS`].
        timeout_secs: u64,
        /// Whether the HTTP client should bypass ambient `HTTP(S)_PROXY`. See
        /// [`Self::with_no_proxy`]. Tracked so the timeout isn't lost when
        /// the proxy setting changes.
        no_proxy: bool,
        http: reqwest::blocking::Client,
    }

    /// Default request timeout for the Anthropic client. Reasoning/thinking
    /// models (e.g. MiniMax-M2) can spend 30-90s on a single response while
    /// emitting `thinking` blocks before any text — a request that succeeds
    /// but takes longer than reqwest's `is_timeout` window fails as a
    /// `CallError::Transport` with no useful chain. 180s is generous enough
    /// for those models at the v2 prompt's expected `max_tokens` ceiling,
    /// and short enough to surface a genuinely-stuck request within an
    /// operator-driven run-cycle. Override via
    /// [`AnthropicBlockingClient::with_timeout`] or `OVP_LLM_TIMEOUT_SECS`.
    pub const DEFAULT_TIMEOUT_SECS: u64 = 180;

    fn build_http_client(timeout_secs: u64, no_proxy: bool) -> reqwest::blocking::Client {
        let mut b = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs));
        if no_proxy {
            b = b.no_proxy();
        }
        // Best-effort: a builder refusal (e.g. invalid URL) should not crash
        // construction; fall back to defaults. Matches the historical
        // `Client::new()` behavior.
        b.build().unwrap_or_else(|_| reqwest::blocking::Client::new())
    }

    impl AnthropicBlockingClient {
        /// Construct from an explicit API key.
        pub fn new(api_key: impl Into<String>) -> Self {
            let timeout_secs = DEFAULT_TIMEOUT_SECS;
            Self {
                api_key: api_key.into(),
                base_url: DEFAULT_BASE_URL.to_string(),
                version: ANTHROPIC_VERSION.to_string(),
                model_override: None,
                max_tokens_override: None,
                timeout_secs,
                no_proxy: false,
                http: build_http_client(timeout_secs, false),
            }
        }

        /// Construct by reading `ANTHROPIC_API_KEY` from the environment.
        /// Returns `CallError::Provider{code:"no_api_key"}` if unset, so
        /// the caller can distinguish "no creds" from a real call failure.
        pub fn from_env() -> Result<Self, CallError> {
            let key = std::env::var("ANTHROPIC_API_KEY").map_err(|_| CallError::Provider {
                code: "no_api_key".into(),
                detail: "ANTHROPIC_API_KEY is not set".into(),
            })?;
            if key.trim().is_empty() {
                return Err(CallError::Provider {
                    code: "no_api_key".into(),
                    detail: "ANTHROPIC_API_KEY is empty".into(),
                });
            }
            Ok(Self::new(key))
        }

        pub fn with_base_url(mut self, url: impl Into<String>) -> Self {
            self.base_url = url.into();
            self
        }

        /// Override the request model (for Anthropic-compatible providers).
        pub fn with_model_override(mut self, model: impl Into<String>) -> Self {
            self.model_override = Some(model.into());
            self
        }

        /// Override `max_tokens` on the wire (for reasoning models that need
        /// headroom beyond the domain default to emit text after thinking).
        pub fn with_max_tokens(mut self, max_tokens: u32) -> Self {
            self.max_tokens_override = Some(max_tokens);
            self
        }

        /// Override the request timeout. Reasoning/thinking models can spend
        /// 30-90s on a single response while emitting `thinking` blocks; a
        /// request that succeeds in spirit but trips reqwest's
        /// `is_timeout` window fails as a useless `CallError::Transport`.
        /// See [`DEFAULT_TIMEOUT_SECS`] for the rationale and the default.
        /// `0` disables the timeout (use only for local dev against a mock).
        pub fn with_timeout(mut self, secs: u64) -> Self {
            self.timeout_secs = secs;
            self.http = build_http_client(secs, self.no_proxy);
            self
        }

        /// Rebuild the HTTP client to BYPASS any ambient `HTTP(S)_PROXY` — for a
        /// directly-reachable provider whose endpoint the ambient proxy can't
        /// tunnel (mirrors the Nowledge adapter). Off by default so setups that
        /// require a proxy to reach the provider keep working.
        ///
        /// Preserves any timeout previously set via [`Self::with_timeout`].
        pub fn with_no_proxy(mut self) -> Self {
            self.no_proxy = true;
            self.http = build_http_client(self.timeout_secs, true);
            self
        }
    }

    impl ModelClient for AnthropicBlockingClient {
        fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
            let mut body = anthropic_request_body(request);
            if let Some(model) = &self.model_override {
                body["model"] = serde_json::json!(model);
            }
            if let Some(mt) = self.max_tokens_override {
                // The env override RAISES the budget; honor an explicit request
                // that asks for even more (M20 budget escalation bumps
                // `request.max_tokens` above the override on a retry).
                body["max_tokens"] = serde_json::json!(mt.max(request.max_tokens));
            }
            let resp = self
                .http
                .post(&self.base_url)
                .header("x-api-key", &self.api_key)
                .header("anthropic-version", &self.version)
                .header("content-type", "application/json")
                .json(&body)
                .send()
                .map_err(|e| CallError::Transport { detail: format!("send: {e}") })?;

            let status = resp.status();
            let text = resp
                .text()
                .map_err(|e| CallError::Transport { detail: format!("read body: {e}") })?;

            if !status.is_success() {
                // Non-2xx: try to extract the structured error; fall back
                // to the raw body if it isn't the expected shape.
                return match parse_anthropic_reply(&text) {
                    Err(e @ CallError::Provider { .. }) => Err(e),
                    _ => Err(CallError::Provider {
                        code: status.as_u16().to_string(),
                        detail: text.chars().take(500).collect(),
                    }),
                };
            }

            parse_anthropic_reply(&text)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req() -> ModelRequest {
        ModelRequest {
            model: "claude-sonnet-4-6".into(),
            system: Some("you are terse".into()),
            messages: vec![ModelMessage::User { content: "hi".into() }],
            max_tokens: 1024,
            temperature: None,
            cache_namespace: None,
        }
    }

    #[test]
    fn request_body_shape() {
        let body = anthropic_request_body(&req());
        assert_eq!(body["model"], "claude-sonnet-4-6");
        assert_eq!(body["max_tokens"], 1024);
        assert_eq!(body["system"], "you are terse");
        assert_eq!(body["messages"][0]["role"], "user");
        assert_eq!(body["messages"][0]["content"], "hi");
        // No temperature key when None.
        assert!(body.get("temperature").is_none());
    }

    #[test]
    fn request_body_includes_temperature_when_set() {
        let mut r = req();
        r.temperature = Some(0.5);
        let body = anthropic_request_body(&r);
        assert_eq!(body["temperature"], 0.5);
    }

    #[test]
    fn parse_success_reply() {
        let json = r#"{
            "model": "claude-sonnet-4-6",
            "content": [{"type":"text","text":"hello "},{"type":"text","text":"world"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 34}
        }"#;
        let reply = parse_anthropic_reply(json).unwrap();
        assert_eq!(reply.model, "claude-sonnet-4-6");
        assert_eq!(reply.text, "hello world");
        assert_eq!(reply.stop_reason, StopReason::EndTurn);
        assert_eq!(reply.usage.input_tokens, 12);
        assert_eq!(reply.usage.output_tokens, 34);
    }

    #[test]
    fn parse_max_tokens_stop_reason() {
        let json = r#"{"model":"m","content":[{"type":"text","text":"x"}],"stop_reason":"max_tokens","usage":{"input_tokens":1,"output_tokens":2}}"#;
        let reply = parse_anthropic_reply(json).unwrap();
        assert_eq!(reply.stop_reason, StopReason::MaxTokens);
    }

    #[test]
    fn parse_structured_error() {
        let json = r#"{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}"#;
        match parse_anthropic_reply(json) {
            Err(CallError::Provider { code, detail }) => {
                assert_eq!(code, "rate_limit_error");
                assert_eq!(detail, "slow down");
            }
            other => panic!("expected Provider error, got {other:?}"),
        }
    }

    #[test]
    fn parse_non_json_is_decode_error() {
        match parse_anthropic_reply("<html>502</html>") {
            Err(CallError::Decode { .. }) => {}
            other => panic!("expected Decode error, got {other:?}"),
        }
    }

    #[test]
    fn parse_missing_content_is_decode_error() {
        let json = r#"{"model":"m","stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}"#;
        match parse_anthropic_reply(json) {
            Err(CallError::Decode { .. }) => {}
            other => panic!("expected Decode error, got {other:?}"),
        }
    }

    #[test]
    fn parse_empty_text_is_decode_error() {
        let json = r#"{"model":"m","content":[],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}"#;
        match parse_anthropic_reply(json) {
            Err(CallError::Decode { .. }) => {}
            other => panic!("expected Decode error, got {other:?}"),
        }
    }

    #[test]
    fn parse_thinking_only_response_is_budget_exhausted() {
        // M20: a reasoning model that spent its whole budget thinking
        // (`stop_reason=max_tokens`, only a `thinking` block, no `text`) is
        // classified `BudgetExhausted` — recoverable by a higher-budget retry —
        // not a generic Decode. Loud + actionable.
        let json = r#"{"model":"MiniMax-M2","content":[{"type":"thinking","thinking":"hmm...","signature":"s"}],"stop_reason":"max_tokens","usage":{"input_tokens":50,"output_tokens":64}}"#;
        match parse_anthropic_reply(json) {
            Err(CallError::BudgetExhausted { detail }) => {
                assert!(detail.contains("thinking_budget_exhausted"), "got {detail}");
                assert!(detail.contains("thinking block"), "should mention thinking blocks: {detail}");
                assert!(detail.contains("OVP_LLM_MAX_TOKENS"), "should hint the fix: {detail}");
            }
            other => panic!("expected BudgetExhausted error, got {other:?}"),
        }
    }

    #[test]
    fn parse_empty_text_non_budget_stop_is_decode_error() {
        // No text but stop_reason != max_tokens → genuine Decode (not retryable).
        let json = r#"{"model":"m","content":[{"type":"thinking","thinking":"x","signature":"s"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}"#;
        assert!(matches!(parse_anthropic_reply(json), Err(CallError::Decode { .. })));
    }
}
