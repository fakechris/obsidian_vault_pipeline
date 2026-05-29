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
        if block.get("type").and_then(|t| t.as_str()) == Some("text") {
            if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                text.push_str(t);
            }
        }
    }
    if text.is_empty() {
        return Err(CallError::Decode {
            detail: "no text content blocks in response".into(),
        });
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
        http: reqwest::blocking::Client,
    }

    impl AnthropicBlockingClient {
        /// Construct from an explicit API key.
        pub fn new(api_key: impl Into<String>) -> Self {
            Self {
                api_key: api_key.into(),
                base_url: DEFAULT_BASE_URL.to_string(),
                version: ANTHROPIC_VERSION.to_string(),
                http: reqwest::blocking::Client::new(),
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
    }

    impl ModelClient for AnthropicBlockingClient {
        fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
            let body = anthropic_request_body(request);
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
}
