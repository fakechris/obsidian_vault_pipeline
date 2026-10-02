use ovp_llm::{
    client::{CallError, ModelClient},
    decision::{chat::*, *},
    reply::{ModelReply, StopReason, Usage},
    request::ModelRequest,
};
use serde_json::json;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
fn profile() -> DecisionProfile {
    DecisionProfile {
        id: "test".into(),
        provider: "chat".into(),
        endpoint: "https://example.com/v1/messages".into(),
        model: "pinned-1".into(),
        credential_ref: "TEST_KEY".into(),
    }
}
fn request() -> DecisionRequest {
    serde_json::from_value(json!({"namespace":"test/v1","state":{"quote":"Evidence"},"evidence":[{"source_id":"s","revision":"r","start_line":1,"end_line":2}],"questions":{
    "b":{"instructions":"Does it support?","kind":{"type":"boolean","yes":"supported","no":"not supported"}},
    "c":{"instructions":"Choose relevance","kind":{"type":"choice","options":{"a":"direct","b":"irrelevant"}}},
    "s":{"instructions":"Rate strength","kind":{"type":"score","levels":[{"id":"low","description":"weak"},{"id":"high","description":"strong"}]}}
}})).unwrap()
}
const GOOD: &str = r#"{"answers":{"b":{"type":"boolean","value":false},"c":{"type":"choice","selected":"a"},"s":{"type":"score","selected":"high"}}}"#;
fn response(text: &str) -> ModelReply {
    ModelReply {
        model: "pinned-1".into(),
        text: text.into(),
        stop_reason: StopReason::EndTurn,
        usage: Usage {
            input_tokens: 12,
            output_tokens: 8,
        },
        blocks: None,
        raw_stop_reason: None,
    }
}
struct Fake {
    calls: Arc<AtomicUsize>,
    reply: Result<ModelReply, CallError>,
}
impl ModelClient for Fake {
    fn call(&mut self, req: &ModelRequest) -> Result<ModelReply, CallError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        assert_eq!(req.model, "pinned-1");
        assert_eq!(req.messages.len(), 1);
        assert!(req.tools.is_none());
        assert_eq!(req.max_tokens, 8192);
        assert_eq!(req.temperature, None);
        self.reply.clone()
    }
}
#[test]
fn exact_batch_maps_discrete_answers_and_binds_receipt() {
    let reply = decode_reply(&profile(), &request(), &response(GOOD), 7).unwrap();
    assert_eq!(
        reply.answers[&"b".into()],
        DecisionAnswer::Boolean {
            value: Some(false),
            probability_true: None
        }
    );
    assert_eq!(
        reply.answers[&"s".into()],
        DecisionAnswer::Score {
            position: 1.,
            probabilities: None,
            confidence: None
        }
    );
    assert_eq!(reply.receipt.evaluation_ms, 7);
    assert_eq!(reply.receipt.network_attempts, 1);
    assert_eq!(reply.receipt.evaluation_usage.unwrap().input_tokens, 12);
    assert_eq!(reply.receipt.calibration, Calibration::Unknown);
    assert_eq!(
        reply.receipt.request_key,
        decision_key(&profile(), &request()).unwrap()
    );
    const { assert!(!CAPABILITIES.probabilities) };
    let mut other = request();
    other.evidence[0].revision = "other".into();
    assert_ne!(
        decision_key(&profile(), &other).unwrap(),
        reply.receipt.request_key
    );
}
#[test]
fn malformed_or_extra_output_fails_entire_batch() {
    for bad in [
        GOOD.replace("false", "\"false\""),
        GOOD.replace("\"a\"", "\"missing\""),
        GOOD.replace("\"high\"", "\"unknown\""),
        GOOD.replace("\"value\":false", "\"value\":false,\"reason\":\"because\""),
        GOOD.replace("\"value\":false", "\"value\":false,\"value\":true"),
        GOOD.replace(
            "\"selected\":\"a\"",
            "\"selected\":\"a\",\"confidence\":0.9",
        ),
        GOOD.replace("\"b\":{\"type\":\"boolean\",\"value\":false},", ""),
        GOOD.replace("\"b\":{", "\"extra\":{\"type\":\"abstain\"},\"b\":{"),
        format!("{GOOD} trailing"),
        format!("```json\n{GOOD}\n```"),
        GOOD.replace(
            "\"type\":\"boolean\",\"value\":false",
            "\"type\":\"choice\",\"selected\":\"a\"",
        ),
        GOOD.replace("\"answers\":", "\"reason\":\"x\",\"answers\":"),
    ] {
        assert!(
            decode_reply(&profile(), &request(), &response(&bad), 0).is_err(),
            "{bad}"
        );
    }
}
#[test]
fn abstain_is_explicit_and_truncated_or_wrong_model_replies_fail() {
    let text = GOOD.replace(
        "\"type\":\"boolean\",\"value\":false",
        "\"type\":\"abstain\"",
    );
    assert!(matches!(
        decode_reply(&profile(), &request(), &response(&text), 0)
            .unwrap()
            .answers[&"b".into()],
        DecisionAnswer::Abstain { .. }
    ));
    for stop in [
        StopReason::MaxTokens,
        StopReason::ToolUse,
        StopReason::Refusal,
        StopReason::Unknown,
    ] {
        let mut r = response(GOOD);
        r.stop_reason = stop;
        assert!(decode_reply(&profile(), &request(), &r, 0).is_err());
    }
    let mut r = response(GOOD);
    r.model = "wrong".into();
    assert!(decode_reply(&profile(), &request(), &r, 0).is_err());
}
#[test]
fn safe_profiles_and_redacted_errors() {
    for endpoint in [
        "http://remote.test/v1/messages",
        "https://user:secret@host/v1/messages",
        "https://host/v1/messages?secret=x",
        "https://host/v1/messages#x",
        "https://host:bad/v1/messages",
    ] {
        let mut p = profile();
        p.endpoint = endpoint.into();
        assert!(validate_profile(&p).is_err());
    }
    for endpoint in [
        "https://host/v1/messages",
        "http://127.0.0.1:123/v1/messages",
        "http://[::1]:123/v1/messages",
    ] {
        let mut p = profile();
        p.endpoint = endpoint.into();
        validate_profile(&p).unwrap();
    }
    let calls = Arc::new(AtomicUsize::new(0));
    let mut client = ChatDecisionClient::new(
        profile(),
        Box::new(Fake {
            calls: calls.clone(),
            reply: Err(CallError::Provider {
                code: "secret".into(),
                detail: "private quote".into(),
            }),
        }),
    )
    .unwrap();
    assert_eq!(
        client.decide(&request()).unwrap_err(),
        DecisionError::Transport
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}
#[test]
fn record_then_replay_has_zero_provider_calls_and_preserves_usage() {
    let dir = tempfile::tempdir().unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let client = ChatDecisionClient::new(
        profile(),
        Box::new(Fake {
            calls: calls.clone(),
            reply: Ok(response(GOOD)),
        }),
    )
    .unwrap();
    let mut record = CachedDecisionClient::record(Box::new(client), dir.path()).unwrap();
    let live = record.decide(&request()).unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let mut replay = CachedDecisionClient::replay(profile(), CAPABILITIES, dir.path()).unwrap();
    let cached = replay.decide(&request()).unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(cached.answers, live.answers);
    assert_eq!(cached.receipt.network_attempts, 0);
    assert_eq!(cached.receipt.origin, DecisionOrigin::Replay);
    assert_eq!(
        cached.receipt.evaluation_usage,
        live.receipt.evaluation_usage
    );
}
