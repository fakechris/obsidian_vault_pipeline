//! Live smoke test against the real OpenAI Chat Completions API. Ignored by
//! default (network + spend). Run with:
//!   OPENAI_API_KEY=… OVP_OPENAI_LIVE_MODEL=gpt-6-luna \
//!   cargo test -p ovp-llm --features anthropic --test openai_live -- --ignored --nocapture
#![cfg(feature = "anthropic")]

use ovp_llm::{
    AssistantBlock, ModelClient, ModelMessage, ModelRequest, OpenAiBlockingClient, StopReason,
    ToolDef, ToolResultBlock,
};
use serde_json::json;

fn client() -> OpenAiBlockingClient {
    let key = std::env::var("OPENAI_API_KEY").expect("OPENAI_API_KEY");
    let model = std::env::var("OVP_OPENAI_LIVE_MODEL").unwrap_or_else(|_| "gpt-6-luna".into());
    OpenAiBlockingClient::new(key).with_model_override(model)
}

fn base(messages: Vec<ModelMessage>) -> ModelRequest {
    ModelRequest {
        model: "ignored-overridden".into(),
        system: Some("Answer tersely.".into()),
        messages,
        max_tokens: 2000,
        temperature: Some(0.2),
        tools: None,
        cache_namespace: None,
    }
}

#[test]
#[ignore]
fn text_reply_with_temperature() {
    let reply = client()
        .call(&base(vec![ModelMessage::User {
            content: r#"Reply with exactly this JSON and nothing else: {"ok": true}"#.into(),
        }]))
        .expect("live call");
    println!("model={} usage={:?} text={}", reply.model, reply.usage, reply.text);
    assert!(reply.is_final_success(), "{:?}", reply.stop_reason);
    assert!(reply.text.contains("ok"));
    assert!(reply.usage.input_tokens > 0 && reply.usage.output_tokens > 0);
}

#[test]
#[ignore]
fn tool_call_round_trip() {
    let tool = ToolDef {
        name: "lookup_capital".into(),
        version: "v1".into(),
        description: "Return the capital city of a country.".into(),
        input_schema: json!({
            "type": "object",
            "properties": {"country": {"type": "string"}},
            "required": ["country"]
        }),
    };
    let mut c = client();
    let mut req = base(vec![ModelMessage::User {
        content: "Use the lookup_capital tool to find the capital of France, then answer.".into(),
    }]);
    req.tools = Some(vec![tool]);
    let first = c.call(&req).expect("first turn");
    assert_eq!(first.stop_reason, StopReason::ToolUse, "{first:?}");
    let calls = first.executable_tool_calls().expect("tool calls");
    println!("tool call: {} {}", calls[0].name, calls[0].input);
    let (id, name, input) = (calls[0].id.to_string(), calls[0].name.to_string(), calls[0].input.clone());
    req.messages.push(ModelMessage::AssistantBlocks {
        blocks: vec![AssistantBlock::ToolUse { id: id.clone(), name, input }],
    });
    req.messages.push(ModelMessage::ToolResults {
        results: vec![ToolResultBlock { tool_call_id: id, content: "Paris".into(), is_error: false }],
    });
    let second = c.call(&req).expect("second turn");
    println!("final: {}", second.text);
    assert!(second.is_final_success(), "{:?}", second.stop_reason);
    assert!(second.text.contains("Paris"));
}
