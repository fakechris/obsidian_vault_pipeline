#[allow(dead_code)]
mod fixture {
    include!("../../../fixtures/decision-rerank-v1/support.rs");
}
use ovp_llm::decision::runtime::*;
use serde_json::{Value, json};
use std::{sync::Arc, time::Duration};
fn traces(root: &std::path::Path) -> Vec<Value> {
    std::fs::read_dir(root.join(".ovp/decision-traces"))
        .unwrap()
        .map(|p| serde_json::from_slice(&std::fs::read(p.unwrap().path()).unwrap()).unwrap())
        .collect()
}
#[test]
fn off_shadow_and_replay_preserve_candidates_quotes_and_private_annotation() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    fixture::vault(root);
    let baseline = fixture::search(root, DecisionMode::Off, true);
    assert!(!root.join(".ovp/decision-traces").exists());
    let shadow = fixture::search(root, DecisionMode::Shadow, false);
    assert_eq!(shadow, baseline);
    let records = traces(root);
    assert_eq!(records[0]["status"], "shadow");
    assert_eq!(records[0]["candidate_order"], json!([1, 0]));
    assert!(!records[0].to_string().contains("PRIVATE_READER_NOTE"));
    let enabled = fixture::search(root, DecisionMode::Enabled, true);
    assert_eq!(enabled["hits"][0]["source_id"], "bbbb2222");
    assert_eq!(
        enabled["hits"][0]["semantic_evidence"]["relation"],
        "contradicts"
    );
    for (i, j) in [(0, 1), (1, 0)] {
        let mut hit = enabled["hits"][i].clone();
        hit.as_object_mut().unwrap().remove("semantic_evidence");
        assert_eq!(hit, baseline["hits"][j]);
    }
    assert_eq!(fixture::search(root, DecisionMode::Off, true), baseline);
    assert!(
        traces(root)
            .iter()
            .any(|r| r["observation"]["candidate"]["receipt"]["origin"] == "replay")
    );
}
#[test]
fn missing_cassette_budget_cap_and_trace_failure_retain_baseline() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let model = fixture::vault(root);
    let baseline = fixture::search(root, DecisionMode::Off, true);
    assert_eq!(fixture::search(root, DecisionMode::Enabled, true), baseline);
    assert_eq!(traces(root)[0]["status"], "fallback");
    fixture::search(root, DecisionMode::Shadow, false);
    let rerank = fixture::reranker(root, DecisionMode::Enabled, true);
    for (budget, cap) in [
        (Duration::from_millis(900), usize::MAX),
        (Duration::from_secs(2), baseline.to_string().len()),
    ] {
        let mut value = baseline.clone();
        rerank.rerank_tool(&model, "retrieval", &mut value, "test", budget, cap);
        assert_eq!(value, baseline);
    }
    assert!(
        traces(root)
            .iter()
            .any(|t| t["reason"] == "reranked result exceeds delivery cap")
    );
    std::fs::remove_dir_all(root.join(".ovp/decision-traces")).unwrap();
    std::fs::write(root.join(".ovp/decision-traces"), "blocked").unwrap();
    assert_eq!(fixture::search(root, DecisionMode::Enabled, true), baseline);
}
#[test]
fn control_does_not_read_sources_or_construct_provider() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let model = fixture::vault(root);
    let mut settings = fixture::settings(DecisionMode::Enabled);
    settings
        .capabilities
        .get_mut("evidence_relevance")
        .unwrap()
        .experiment = Some(Experiment {
        id: "e1".into(),
        seed: "seed".into(),
        candidate_basis_points: 0,
    });
    let rerank = ovp_memory::decision_rerank::DecisionReranker::new(
        root,
        settings,
        Arc::new(fixture::RecordFactory),
        "unit".into(),
    )
    .unwrap();
    std::fs::remove_dir_all(root.join("50-Inbox")).unwrap();
    let mut value = json!({"hits":[{"source_id":"aaaa1111","title":"A"}]});
    let baseline = value.clone();
    rerank.rerank_tool(
        &model,
        "retrieval",
        &mut value,
        "test",
        Duration::from_secs(2),
        usize::MAX,
    );
    assert_eq!(value, baseline);
    assert_eq!(traces(root)[0]["status"], "control");
    assert!(!root.join(".ovp/cassettes").exists());
}
#[test]
fn legacy_context_preserves_citations_and_shadow_is_byte_identical() {
    use ovp_memory::ask::{EvidenceItem, EvidenceKind};
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let model = fixture::vault(root);
    let original: Vec<_> = model
        .sources
        .iter()
        .map(|s| EvidenceItem {
            id: s.sha256.clone(),
            kind: EvidenceKind::Source,
            title: s.title.clone().unwrap(),
            body: "original body".into(),
            quote: Some("original quote".into()),
            path: s.rel_path.clone(),
        })
        .collect();
    let mut items = original.clone();
    fixture::reranker(root, DecisionMode::Shadow, false).rerank_ask(
        &model,
        None,
        "retrieval",
        &mut items,
    );
    assert_eq!(items, original);
    fixture::reranker(root, DecisionMode::Enabled, true).rerank_ask(
        &model,
        None,
        "retrieval",
        &mut items,
    );
    assert_eq!(items[0].id, original[1].id);
    assert_eq!(items[0].quote, original[1].quote);
    assert_eq!(items[0].path, original[1].path);
    assert!(items[0].body.starts_with("original body"));
    assert!(items[0].body.contains("contradicts"));
}
