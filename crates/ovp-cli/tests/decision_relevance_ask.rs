#[allow(dead_code)]
mod fixture {
    include!("../../../fixtures/decision-rerank-v1/support.rs");
}
use ovp_llm::decision::runtime::DecisionMode;
use ovp_llm::{
    CacheMode, CachedModelClient, ModelClient, ModelReply, ModelRequest, StopReason, Usage,
};
use ovp_memory::ask::{AskArgs, ask_with_decision_reranker};
use std::process::Command;

struct VerifyContext;
impl ModelClient for VerifyContext {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, ovp_llm::CallError> {
        let text = match request.messages.last().unwrap() {
            ovp_llm::ModelMessage::User { content } => content,
            _ => panic!("expected user context"),
        };
        let b = text.find("[source:bbbb2222]").unwrap();
        let a = text.find("[source:aaaa1111]").unwrap();
        assert!(b < a, "enabled must send reranked context to LLM: {text}");
        assert!(text.contains("contradicts"));
        Ok(ModelReply {
            model: request.model.clone(),
            text: "answer [source:bbbb2222]".into(),
            stop_reason: StopReason::EndTurn,
            usage: Usage {
                input_tokens: 1,
                output_tokens: 1,
            },
            blocks: None,
            raw_stop_reason: None,
        })
    }
}
#[test]
fn cli_replays_same_decision_and_generation_cassettes() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    fixture::install(root, DecisionMode::Enabled);
    let model = ovp_index::read_index(root).unwrap();
    let question = "帮我找 retrieval";
    let args = AskArgs {
        question: question.into(),
        ..Default::default()
    };
    let reranker = ovp_memory::decision_rerank::DecisionReranker::new(
        root,
        fixture::settings(DecisionMode::Enabled),
        std::sync::Arc::new(fixture::RecordFactory),
        question.into(),
    )
    .unwrap();
    let cache = root.join(".ovp/cassettes/ask");
    let mut generation =
        CachedModelClient::new(VerifyContext, &cache, "ask/v4", CacheMode::Record).unwrap();
    let result = ask_with_decision_reranker(
        &model,
        None,
        &mut generation,
        &args,
        root,
        None,
        Some(&reranker),
    )
    .unwrap();
    assert_eq!(result.evidence[0].id, "bbbb2222");
    let output = Command::new(env!("CARGO_BIN_EXE_ovp2"))
        .args([
            "ask",
            "--vault-root",
            root.to_str().unwrap(),
            question,
            "--client",
            "replay",
            "--cache-dir",
            cache.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        "answer [source:bbbb2222]"
    );
    assert!(!root.join(".ovp/usage").exists());
}
