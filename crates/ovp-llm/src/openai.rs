//! OpenAI Responses API client (`POST /v1/responses`).
//!
//! Same split as [`crate::anthropic`]: the request/response **mapping**
//! (provider-neutral `ModelRequest` → Responses JSON body, and the response
//! JSON → `ModelReply`) is pure, always compiled and unit-tested offline; the
//! HTTP-bearing [`OpenAiBlockingClient`] sits behind the same live-HTTP
//! feature (`anthropic`, which only gates `reqwest`).
//!
//! Why Responses and not Chat Completions: a live probe (2026-11-17) showed
//! `gpt-6-luna` on `/v1/chat/completions` rejects function tools while
//! reasoning is on ("use /v1/responses or set reasoning_effort to 'none'"),
//! and Ask needs tools with reasoning.
//!
//! Wire facts (OpenAI API reference, Responses `create`):
//! - system prompt → `instructions`; turns → `input` items: role messages,
//!   `function_call {call_id,name,arguments(JSON string)}` and
//!   `function_call_output {call_id,output}`;
//! - output ceiling `max_output_tokens`; `store: false` keeps vault content
//!   out of OpenAI-side response storage;
//! - reply `output[]` holds `message` items (`output_text` / `refusal`
//!   content), `function_call` items and `reasoning` items; `status` is
//!   `completed | incomplete | failed`, with `incomplete_details.reason` ∈
//!   `max_output_tokens | content_filter | …`; usage is
//!   `input_tokens` / `output_tokens`.

use serde_json::json;

use crate::anthropic::validate_tool_protocol;
use crate::client::CallError;
use crate::reply::{ModelReply, ReplyBlock, StopReason, Usage};
use crate::request::{AssistantBlock, ModelMessage, ModelRequest};

pub const DEFAULT_OPENAI_URL: &str = "https://api.openai.com/v1/responses";

/// Prefix on a tool result the caller marked as an error: the Responses API
/// has no `is_error` flag, so the model learns it from the content.
const TOOL_ERROR_PREFIX: &str = "ERROR: ";

/// Build the Responses request body. Pure — no I/O. Errs with
/// `CallError::Protocol` on a tool-use sequence violation (same contract as
/// the Anthropic mapping, checked before anything reaches the wire).
pub fn openai_request_body(req: &ModelRequest) -> Result<serde_json::Value, CallError> {
    validate_tool_protocol(&req.messages)?;
    let mut input: Vec<serde_json::Value> = Vec::new();
    for m in &req.messages {
        match m {
            ModelMessage::User { content } => {
                input.push(json!({ "role": "user", "content": content }));
            }
            ModelMessage::Assistant { content } => {
                input.push(json!({ "role": "assistant", "content": content }));
            }
            ModelMessage::AssistantBlocks { blocks } => {
                for b in blocks {
                    match b {
                        AssistantBlock::Text { text } => {
                            input.push(json!({ "role": "assistant", "content": text }));
                        }
                        AssistantBlock::ToolUse { id, name, input: args } => input.push(json!({
                            "type": "function_call",
                            "call_id": id,
                            "name": name,
                            "arguments": args.to_string(),
                        })),
                    }
                }
            }
            ModelMessage::ToolResults { results } => {
                for r in results {
                    let output = if r.is_error {
                        format!("{TOOL_ERROR_PREFIX}{}", r.content)
                    } else {
                        r.content.clone()
                    };
                    input.push(json!({
                        "type": "function_call_output",
                        "call_id": r.tool_call_id,
                        "output": output,
                    }));
                }
            }
        }
    }
    let mut body = json!({
        "model": req.model,
        "input": input,
        "max_output_tokens": req.max_tokens,
        "store": false,
    });
    let obj = body.as_object_mut().expect("json object");
    if let Some(system) = &req.system {
        obj.insert("instructions".into(), json!(system));
    }
    if let Some(temp) = req.temperature {
        obj.insert("temperature".into(), json!(temp));
    }
    if let Some(tools) = &req.tools {
        // strict:false — our schemas carry optional properties, which strict
        // mode would force to be required.
        obj.insert(
            "tools".into(),
            json!(tools
                .iter()
                .map(|tool| json!({
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": false,
                }))
                .collect::<Vec<_>>()),
        );
    }
    Ok(body)
}

/// Parse a Responses API body (success OR structured error) into a
/// `ModelReply`. Pure — no I/O.
pub fn parse_openai_reply(json_body: &str) -> Result<ModelReply, CallError> {
    let v: serde_json::Value = serde_json::from_str(json_body)
        .map_err(|e| CallError::Decode { detail: format!("response not JSON: {e}") })?;

    // HTTP errors: {"error":{"message","type","code"}}; a 200 with
    // status=failed carries the same object. `code` is the specific slug
    // (`rate_limit_exceeded`, `insufficient_quota`, `invalid_api_key`,
    // `context_length_exceeded`, `unsupported_value`), `type` the coarse one;
    // prefer `code` so the shared retry/failure classification sees it.
    if let Some(err) = v.get("error").filter(|e| !e.is_null()) {
        let code = err
            .get("code")
            .and_then(|c| c.as_str())
            .or_else(|| err.get("type").and_then(|t| t.as_str()))
            .unwrap_or("unknown")
            .to_string();
        let detail = err
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or("(no message)")
            .to_string();
        return Err(CallError::Provider { code, detail });
    }

    let model = v.get("model").and_then(|m| m.as_str()).unwrap_or("").to_string();
    let output = v
        .get("output")
        .and_then(|o| o.as_array())
        .ok_or_else(|| CallError::Decode { detail: "missing `output` array".into() })?;

    // Text: every assistant output_text, except interim `commentary`
    // messages when a final answer is present.
    let has_final = output.iter().any(|item| {
        item.get("type").and_then(|t| t.as_str()) == Some("message")
            && item.get("phase").and_then(|p| p.as_str()) != Some("commentary")
    });
    let mut text = String::new();
    let mut refusal = String::new();
    let mut blocks = Vec::new();
    let mut calls = 0usize;
    for item in output {
        match item.get("type").and_then(|t| t.as_str()) {
            Some("message") => {
                if has_final && item.get("phase").and_then(|p| p.as_str()) == Some("commentary") {
                    continue;
                }
                for part in item.get("content").and_then(|c| c.as_array()).into_iter().flatten() {
                    match part.get("type").and_then(|t| t.as_str()) {
                        Some("output_text") => {
                            if let Some(t) = part.get("text").and_then(|t| t.as_str()) {
                                text.push_str(t);
                                blocks.push(ReplyBlock::Text { text: t.to_string() });
                            }
                        }
                        Some("refusal") => {
                            if let Some(r) = part.get("refusal").and_then(|r| r.as_str()) {
                                refusal.push_str(r);
                            }
                        }
                        _ => {}
                    }
                }
            }
            Some("function_call") => {
                calls += 1;
                let id = item
                    .get("call_id")
                    .and_then(|i| i.as_str())
                    .ok_or_else(|| CallError::Decode {
                        detail: "function_call missing string `call_id`".into(),
                    })?
                    .to_string();
                let name = item
                    .get("name")
                    .and_then(|n| n.as_str())
                    .ok_or_else(|| CallError::Decode {
                        detail: "function_call missing string `name`".into(),
                    })?
                    .to_string();
                let raw_args = item.get("arguments").and_then(|a| a.as_str()).unwrap_or("{}");
                match serde_json::from_str(raw_args) {
                    Ok(input) => blocks.push(ReplyBlock::ToolUse { id, name, input }),
                    // Possibly cut off by the budget — decided below.
                    Err(_) if is_max_tokens(&v) => {}
                    Err(e) => {
                        return Err(CallError::Decode {
                            detail: format!("function_call `{name}` arguments are not JSON: {e}"),
                        });
                    }
                }
            }
            _ => {} // reasoning items etc.
        }
    }

    let status = v.get("status").and_then(|s| s.as_str());
    let reason = v.pointer("/incomplete_details/reason").and_then(|r| r.as_str());
    let (stop_reason, raw_stop_reason) = match (status, reason) {
        (Some("incomplete"), Some("max_output_tokens")) => (StopReason::MaxTokens, None),
        (Some("incomplete"), Some("content_filter")) => (StopReason::Refusal, None),
        (Some("incomplete"), other) => {
            (StopReason::Unknown, Some(format!("incomplete:{}", other.unwrap_or("unknown"))))
        }
        (Some("completed"), _) if calls > 0 => (StopReason::ToolUse, None),
        (Some("completed"), _) => (StopReason::EndTurn, None),
        (other, _) => (StopReason::Unknown, other.map(str::to_string)),
    };

    // A tool call cut off by the token ceiling may carry truncated arguments:
    // never executable, never cached as a success (same rule as Anthropic).
    if stop_reason == StopReason::MaxTokens && calls > 0 {
        return Err(CallError::BudgetExhausted {
            detail: "tool_use_truncated_by_max_tokens: the reply stopped at the token budget \
                     while emitting function call(s); retry with a larger max_tokens."
                .into(),
        });
    }
    if text.is_empty() && calls == 0 {
        // Reasoning models spend `max_output_tokens` on hidden reasoning
        // first; no answer at the ceiling means the budget ran out there —
        // BudgetExhausted so a larger-budget retry can recover it.
        if stop_reason == StopReason::MaxTokens {
            let reasoning = v
                .pointer("/usage/output_tokens_details/reasoning_tokens")
                .and_then(|n| n.as_u64())
                .unwrap_or(0);
            return Err(CallError::BudgetExhausted {
                detail: format!(
                    "thinking_budget_exhausted: no answer text (incomplete: max_output_tokens, \
                     {reasoning} reasoning token(s)) — raise OVP_LLM_MAX_TOKENS so the model \
                     can answer after reasoning."
                ),
            });
        }
        let detail = if refusal.is_empty() {
            format!(
                "no_text_content_blocks: response had no output text (status={})",
                status.unwrap_or("unknown")
            )
        } else {
            format!("model refused: {}", refusal.chars().take(300).collect::<String>())
        };
        return Err(CallError::Decode { detail });
    }

    let tokens = |key: &str| {
        v.pointer(&format!("/usage/{key}")).and_then(|n| n.as_u64()).unwrap_or(0) as u32
    };
    Ok(ModelReply {
        model,
        text,
        stop_reason,
        usage: Usage {
            input_tokens: tokens("input_tokens"),
            output_tokens: tokens("output_tokens"),
        },
        blocks: Some(blocks),
        raw_stop_reason,
    })
}

fn is_max_tokens(v: &serde_json::Value) -> bool {
    v.get("status").and_then(|s| s.as_str()) == Some("incomplete")
        && v.pointer("/incomplete_details/reason").and_then(|r| r.as_str())
            == Some("max_output_tokens")
}

/// Whether a provider error says the model rejects `temperature` (reasoning
/// models accept only the default). The client then resends without it.
pub fn rejects_temperature(err: &CallError) -> bool {
    matches!(err, CallError::Provider { code, detail }
        if (code == "unsupported_value" || code == "unsupported_parameter"
            || code == "invalid_request_error")
            && detail.contains("temperature"))
}

#[cfg(feature = "anthropic")]
pub use live::OpenAiBlockingClient;

#[cfg(feature = "anthropic")]
mod live {
    use super::*;
    use crate::client::ModelClient;

    /// Live Chat Completions client over `reqwest::blocking` — the OpenAI
    /// counterpart of `AnthropicBlockingClient`, with the same knobs
    /// (model / max-tokens override, timeout, proxy bypass, usage ledger).
    pub struct OpenAiBlockingClient {
        api_key: String,
        base_url: String,
        model_override: Option<String>,
        max_tokens_override: Option<u32>,
        timeout_secs: u64,
        no_proxy: bool,
        usage_ledger: Option<std::path::PathBuf>,
        /// Set once the model rejected `temperature`; later calls omit it.
        omit_temperature: bool,
        http: reqwest::blocking::Client,
    }

    fn build_http_client(timeout_secs: u64, no_proxy: bool) -> reqwest::blocking::Client {
        let mut b = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs));
        if no_proxy {
            b = b.no_proxy();
        }
        b.build().unwrap_or_else(|_| reqwest::blocking::Client::new())
    }

    impl OpenAiBlockingClient {
        pub fn new(api_key: impl Into<String>) -> Self {
            let timeout_secs = crate::anthropic::DEFAULT_TIMEOUT_SECS;
            Self {
                api_key: api_key.into(),
                base_url: DEFAULT_OPENAI_URL.to_string(),
                model_override: None,
                max_tokens_override: None,
                timeout_secs,
                no_proxy: false,
                usage_ledger: None,
                omit_temperature: false,
                http: build_http_client(timeout_secs, false),
            }
        }

        pub fn with_base_url(mut self, url: impl Into<String>) -> Self {
            self.base_url = url.into();
            self
        }

        pub fn with_model_override(mut self, model: impl Into<String>) -> Self {
            self.model_override = Some(model.into());
            self
        }

        pub fn with_max_tokens(mut self, max_tokens: u32) -> Self {
            self.max_tokens_override = Some(max_tokens);
            self
        }

        pub fn with_usage_ledger(mut self, dir: impl Into<std::path::PathBuf>) -> Self {
            self.usage_ledger = Some(dir.into());
            self
        }

        pub fn with_timeout(mut self, secs: u64) -> Self {
            self.timeout_secs = secs;
            self.http = build_http_client(secs, self.no_proxy);
            self
        }

        pub fn with_no_proxy(mut self) -> Self {
            self.no_proxy = true;
            self.http = build_http_client(self.timeout_secs, true);
            self
        }

        fn meter(
            &self,
            request: &ModelRequest,
            started: std::time::Instant,
            body: Option<&str>,
            result: &Result<ModelReply, CallError>,
        ) {
            let Some(dir) = &self.usage_ledger else {
                return;
            };
            let (input_tokens, output_tokens) =
                body.map(crate::usage::body_usage_tokens).unwrap_or((None, None));
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let row = crate::usage::UsageRow {
                ts,
                lane: crate::usage::lane_for_namespace(request.cache_namespace.as_deref()),
                ns: request.cache_namespace.clone(),
                model: self.model_override.clone().unwrap_or_else(|| request.model.clone()),
                ok: result.is_ok(),
                input_tokens,
                output_tokens,
                ms: started.elapsed().as_millis() as u64,
                err_kind: result.as_ref().err().map(crate::usage::err_kind_of).map(str::to_string),
            };
            crate::usage::record_usage_side_channel(dir, &row);
        }
    }

    impl OpenAiBlockingClient {
        /// One POST. `deadline` (when set) caps this request below the
        /// client timeout, so a resend never extends the caller's wall-clock
        /// bound (`build_recording_live_client_bounded`).
        fn send(
            &self,
            request: &ModelRequest,
            body: &serde_json::Value,
            deadline: Option<std::time::Duration>,
        ) -> Result<ModelReply, CallError> {
            let started = std::time::Instant::now();
            let mut builder = self
                .http
                .post(&self.base_url)
                .bearer_auth(&self.api_key)
                .header("content-type", "application/json")
                .json(body);
            if let Some(d) = deadline {
                builder = builder.timeout(d);
            }
            let resp = match builder.send() {
                Ok(resp) => resp,
                Err(e) => {
                    let err = CallError::Transport { detail: format!("send: {e}") };
                    self.meter(request, started, None, &Err(err.clone()));
                    return Err(err);
                }
            };
            let status = resp.status();
            let text = match resp.text() {
                Ok(text) => text,
                Err(e) => {
                    let err = CallError::Transport { detail: format!("read body: {e}") };
                    self.meter(request, started, None, &Err(err.clone()));
                    return Err(err);
                }
            };
            let result = if !status.is_success() {
                match parse_openai_reply(&text) {
                    // 5xx: the numeric status carries the retry posture.
                    Err(CallError::Provider { code, detail }) if status.is_server_error() => {
                        Err(CallError::Provider {
                            code: status.as_u16().to_string(),
                            detail: format!("{code}: {detail}"),
                        })
                    }
                    Err(e @ CallError::Provider { .. }) => Err(e),
                    _ => Err(CallError::Provider {
                        code: status.as_u16().to_string(),
                        detail: text.chars().take(500).collect(),
                    }),
                }
            } else {
                parse_openai_reply(&text)
            };
            self.meter(request, started, Some(&text), &result);
            result
        }
    }

    impl ModelClient for OpenAiBlockingClient {
        fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
            let mut body = openai_request_body(request)?;
            if let Some(model) = &self.model_override {
                body["model"] = json!(model);
            }
            if let Some(mt) = self.max_tokens_override {
                body["max_output_tokens"] = json!(mt.max(request.max_tokens));
            }
            if self.omit_temperature {
                body.as_object_mut().expect("json object").remove("temperature");
            }
            let started = std::time::Instant::now();
            match self.send(request, &body, None) {
                // Reasoning models accept only the default temperature: drop
                // it once, remember, and resend (wire-only — cassette keys
                // hash the ModelRequest, not this body). The resend shares
                // the ORIGINAL timeout budget: no time left → the original
                // error stands.
                Err(e) if body.get("temperature").is_some() && rejects_temperature(&e) => {
                    eprintln!(
                        "ovp-llm: {} rejects `temperature`; omitting it for this client",
                        body["model"].as_str().unwrap_or("model")
                    );
                    self.omit_temperature = true;
                    body.as_object_mut().expect("json object").remove("temperature");
                    let deadline = match self.timeout_secs {
                        0 => None, // no timeout configured
                        secs => {
                            let left = std::time::Duration::from_secs(secs)
                                .saturating_sub(started.elapsed());
                            if left.is_zero() {
                                return Err(e);
                            }
                            Some(left)
                        }
                    };
                    self.send(request, &body, deadline)
                }
                other => other,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::request::{ToolDef, ToolResultBlock};

    fn req() -> ModelRequest {
        ModelRequest {
            model: "gpt-6-luna".into(),
            system: Some("be brief".into()),
            messages: vec![ModelMessage::User { content: "hi".into() }],
            max_tokens: 800,
            temperature: None,
            tools: None,
            cache_namespace: None,
        }
    }

    #[test]
    fn request_body_shape() {
        let body = openai_request_body(&req()).unwrap();
        assert_eq!(body["model"], "gpt-6-luna");
        assert_eq!(body["max_output_tokens"], 800);
        assert_eq!(body["store"], false, "vault content is not stored provider-side");
        assert_eq!(body["instructions"], "be brief");
        assert!(body.get("temperature").is_none());
        assert_eq!(body["input"], json!([{"role": "user", "content": "hi"}]));
    }

    #[test]
    fn tool_round_trip_maps_to_function_call_items() {
        let mut r = req();
        r.temperature = Some(0.2);
        r.tools = Some(vec![ToolDef {
            name: "search".into(),
            version: "v1".into(),
            description: "find notes".into(),
            input_schema: json!({"type": "object"}),
        }]);
        r.messages.push(ModelMessage::AssistantBlocks {
            blocks: vec![
                AssistantBlock::Text { text: "looking".into() },
                AssistantBlock::ToolUse {
                    id: "call_1".into(),
                    name: "search".into(),
                    input: json!({"q": "memory"}),
                },
            ],
        });
        r.messages.push(ModelMessage::ToolResults {
            results: vec![ToolResultBlock {
                tool_call_id: "call_1".into(),
                content: "no hits".into(),
                is_error: true,
            }],
        });
        let body = openai_request_body(&r).unwrap();
        let temp = body["temperature"].as_f64().unwrap();
        assert!((temp - 0.2).abs() < 1e-6, "{temp}");
        assert_eq!(
            body["tools"][0],
            json!({"type": "function", "name": "search", "description": "find notes",
                   "parameters": {"type": "object"}, "strict": false})
        );
        let input = body["input"].as_array().unwrap();
        assert_eq!(input[1], json!({"role": "assistant", "content": "looking"}));
        assert_eq!(
            input[2],
            json!({"type": "function_call", "call_id": "call_1", "name": "search",
                   "arguments": r#"{"q":"memory"}"#})
        );
        assert_eq!(
            input[3],
            json!({"type": "function_call_output", "call_id": "call_1", "output": "ERROR: no hits"})
        );
    }

    #[test]
    fn unanswered_tool_call_is_a_protocol_error_before_the_wire() {
        let mut r = req();
        r.messages.push(ModelMessage::AssistantBlocks {
            blocks: vec![AssistantBlock::ToolUse {
                id: "c".into(),
                name: "search".into(),
                input: json!({}),
            }],
        });
        assert!(matches!(openai_request_body(&r), Err(CallError::Protocol { .. })));
    }

    #[test]
    fn parses_text_reply_and_usage_skipping_reasoning_and_commentary() {
        let body = r#"{"model":"gpt-6-luna","status":"completed","error":null,"output":[
            {"type":"reasoning","summary":[]},
            {"type":"message","role":"assistant","phase":"commentary","content":[{"type":"output_text","text":"thinking aloud "}]},
            {"type":"message","role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"{\"label\":\"x\"}"}]}],
            "usage":{"input_tokens":12,"output_tokens":5}}"#;
        let r = parse_openai_reply(body).unwrap();
        assert_eq!(r.text, r#"{"label":"x"}"#);
        assert_eq!(r.stop_reason, StopReason::EndTurn);
        assert_eq!((r.usage.input_tokens, r.usage.output_tokens), (12, 5));
        assert!(r.is_final_success());
        assert_eq!(crate::usage::body_usage_tokens(body), (Some(12), Some(5)));
    }

    #[test]
    fn parses_function_calls_as_executable() {
        let body = r#"{"model":"m","status":"completed","output":[
            {"type":"function_call","id":"fc_1","call_id":"call_abc","name":"search","arguments":"{\"q\":\"x\"}"}],
            "usage":{"input_tokens":1,"output_tokens":1}}"#;
        let r = parse_openai_reply(body).unwrap();
        assert_eq!(r.stop_reason, StopReason::ToolUse);
        let calls = r.executable_tool_calls().unwrap();
        assert_eq!(calls[0].id, "call_abc", "call_id, not the item id, answers the call");
        assert_eq!(calls[0].input, &json!({"q": "x"}));
    }

    #[test]
    fn budget_exhaustion_is_retryable_with_a_larger_budget() {
        let thinking_only = r#"{"model":"m","status":"incomplete",
            "incomplete_details":{"reason":"max_output_tokens"},"output":[{"type":"reasoning"}],
            "usage":{"input_tokens":1,"output_tokens":800,"output_tokens_details":{"reasoning_tokens":800}}}"#;
        match parse_openai_reply(thinking_only) {
            Err(CallError::BudgetExhausted { detail }) => assert!(detail.contains("800"), "{detail}"),
            other => panic!("{other:?}"),
        }
        let truncated_call = r#"{"model":"m","status":"incomplete",
            "incomplete_details":{"reason":"max_output_tokens"},"output":[
            {"type":"function_call","call_id":"c","name":"s","arguments":"{\"q\":"}]}"#;
        assert!(matches!(
            parse_openai_reply(truncated_call),
            Err(CallError::BudgetExhausted { .. })
        ));
    }

    #[test]
    fn structured_errors_keep_the_specific_code() {
        let quota = r#"{"error":{"message":"You exceeded your current quota","type":"insufficient_quota","code":"insufficient_quota"}}"#;
        let err = parse_openai_reply(quota).unwrap_err();
        assert!(matches!(&err, CallError::Provider { code, .. } if code == "insufficient_quota"));
        // A spent quota is not transient — retrying burns time, not money.
        assert!(!crate::client::is_transient(&err));
        let rl = r#"{"error":{"message":"slow down","type":"requests","code":"rate_limit_exceeded"}}"#;
        let err = parse_openai_reply(rl).unwrap_err();
        assert!(crate::client::is_transient(&err));
        assert_eq!(crate::client::failure_class(&err), "rate_limited");
        let key = r#"{"error":{"message":"bad key","type":"invalid_request_error","code":"invalid_api_key"}}"#;
        assert_eq!(crate::client::failure_class(&parse_openai_reply(key).unwrap_err()), "auth");
        // The live gpt-6-luna rejection that triggers the temperature fallback.
        let temp = r#"{"error":{"message":"Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported.","type":"invalid_request_error","code":"unsupported_value"}}"#;
        assert!(rejects_temperature(&parse_openai_reply(temp).unwrap_err()));
        assert!(!rejects_temperature(&parse_openai_reply(quota).unwrap_err()));
    }

    #[test]
    fn refusal_without_text_is_reported() {
        let body = r#"{"model":"m","status":"completed","output":[{"type":"message","role":"assistant",
            "content":[{"type":"refusal","refusal":"I can't help with that."}]}]}"#;
        match parse_openai_reply(body) {
            Err(CallError::Decode { detail }) => assert!(detail.contains("refused"), "{detail}"),
            other => panic!("{other:?}"),
        }
    }
}
