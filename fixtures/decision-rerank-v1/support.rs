// Shared synthetic fixture for transport/ordering regression, NOT quality evaluation.
use ovp_llm::decision::{runtime::*, *};
use ovp_memory::agent::ToolExecutor;
use ovp_memory::decision_rerank::DecisionReranker;
use ovp_memory::vault_tools::VaultTools;
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::Path, sync::Arc, time::Duration};

pub fn settings(mode: DecisionMode) -> DecisionSettings {
    DecisionSettings {
        profiles: BTreeMap::from([(
            "fixture".into(),
            DecisionProfile {
                id: "fixture".into(),
                provider: "fixture".into(),
                endpoint: "fixture://relevance".into(),
                model: "synthetic-v1".into(),
                credential_ref: "UNUSED".into(),
            },
        )]),
        capabilities: BTreeMap::from([(
            "evidence_relevance".into(),
            CapabilityConfig {
                mode,
                execution: ExecutionMode::Replay,
                profile: Some("fixture".into()),
                question_namespace: Some(ovp_domain::decision_relevance::NAMESPACE.into()),
                experiment: None,
            },
        )]),
    }
}
pub struct RecordFactory;
impl DecisionClientFactory for RecordFactory {
    fn supports(&self, _: &DecisionProfile, _: ExecutionMode) -> bool {
        true
    }
    fn build(
        &self,
        p: &DecisionProfile,
        _: ExecutionMode,
        cache: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError> {
        Ok(Box::new(CachedDecisionClient::record(
            Box::new(Synthetic(p.clone())),
            cache,
        )?))
    }
}
pub struct ReplayFactory;
impl DecisionClientFactory for ReplayFactory {
    fn supports(&self, _: &DecisionProfile, _: ExecutionMode) -> bool {
        true
    }
    fn build(
        &self,
        p: &DecisionProfile,
        _: ExecutionMode,
        cache: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError> {
        Ok(Box::new(CachedDecisionClient::replay(
            p.clone(),
            capabilities(),
            cache,
        )?))
    }
}
fn capabilities() -> DecisionCapabilities {
    DecisionCapabilities {
        boolean: false,
        choice: true,
        score: false,
        batch: true,
        probabilities: false,
    }
}
struct Synthetic(DecisionProfile);
impl DecisionClient for Synthetic {
    fn profile(&self) -> &DecisionProfile {
        &self.0
    }
    fn capabilities(&self) -> DecisionCapabilities {
        capabilities()
    }
    fn decide(&mut self, q: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
        let answers = q
            .questions
            .keys()
            .map(|id| {
                let index: usize = id.0[10..14].parse().unwrap();
                let direct = q.state["candidates"][format!("hit_{index:04}")]["quote"]
                    .as_str()
                    .unwrap()
                    .contains("does not");
                let choice = if id.0.ends_with("relevance") {
                    if direct { "direct" } else { "background" }
                } else if direct {
                    "contradicts"
                } else {
                    "contextual"
                };
                (
                    id.clone(),
                    DecisionAnswer::Choice {
                        selected: choice.into(),
                        probabilities: None,
                        confidence: None,
                    },
                )
            })
            .collect();
        Ok(DecisionReply {
            answers,
            receipt: DecisionReceipt {
                provider: self.0.identity(),
                request_key: decision_key(&self.0, q)?,
                question_namespace: q.namespace.clone(),
                evidence: q.evidence.clone(),
                calibration: Calibration::Unknown,
                confidence_semantics: None,
                evaluation_usage: None,
                evaluation_ms: 0,
                origin: DecisionOrigin::Fixture,
                network_attempts: 0,
            },
        })
    }
}
pub fn vault(root: &Path) -> ovp_index::IndexModel {
    std::fs::create_dir_all(root.join("50-Inbox/03-Processed")).unwrap();
    let sources = [
        (
            "aaaa1111",
            "A retrieval background",
            "Retrieval is a broad research topic.",
        ),
        (
            "bbbb2222",
            "B retrieval counterexample",
            "Retrieval does not guarantee factual correctness.",
        ),
    ]
    .into_iter()
    .map(|(id, title, body)| {
        let path = format!("50-Inbox/03-Processed/{id}.md");
        std::fs::write(
            root.join(&path),
            format!("---\nsha256: {id}\nannotation: PRIVATE_READER_NOTE\n---\n{body}\n"),
        )
        .unwrap();
        let mut row = ovp_index::SourceRow::blank(id, ovp_index::SourceStatus::Processed);
        row.title = Some(title.into());
        row.rel_path = Some(path);
        row.date = Some("2026-09-20".into());
        row
    })
    .collect();
    let model = ovp_index::IndexModel {
        schema: ovp_index::INDEX_SCHEMA.into(),
        date: "2026-09-22".into(),
        built_at: None,
        run_id: None,
        totals: Default::default(),
        sources,
        packs: vec![],
        claims: vec![],
        runs: vec![],
        ops: Default::default(),
    };
    ovp_index::write_index(root, &model).unwrap();
    model
}
pub fn reranker(root: &Path, mode: DecisionMode, replay: bool) -> DecisionReranker {
    let factory: Arc<dyn DecisionClientFactory + Send + Sync> = if replay {
        Arc::new(ReplayFactory)
    } else {
        Arc::new(RecordFactory)
    };
    DecisionReranker::new(root, settings(mode), factory, "fixture-unit".into()).unwrap()
}
pub fn search(root: &Path, mode: DecisionMode, replay: bool) -> Value {
    let mut tools = VaultTools::new(root).with_decision_reranker(reranker(root, mode, replay));
    let out = tools.execute(
        "search_sources",
        &json!({"query":"retrieval"}),
        Duration::from_secs(10),
    );
    let ovp_memory::agent::ToolOutcome::Ok(content) = out else {
        panic!("{out:?}")
    };
    serde_json::from_str(&content).unwrap()
}
pub fn install(root: &Path, mode: DecisionMode) {
    vault(root);
    let result = search(root, DecisionMode::Enabled, false);
    assert_eq!(result["hits"][0]["source_id"], "bbbb2222");
    std::fs::remove_dir_all(root.join(".ovp/decision-traces")).unwrap();
    std::fs::write(
        root.join(".ovp/decisions.json"),
        serde_json::to_vec(&settings(mode)).unwrap(),
    )
    .unwrap();
}

/// Exercises the real tool callback and inspects what the generating model sees.
pub struct AgentClient(pub bool);
impl ovp_llm::ModelClient for AgentClient {
    fn call(
        &mut self,
        q: &ovp_llm::ModelRequest,
    ) -> Result<ovp_llm::ModelReply, ovp_llm::CallError> {
        let result = q.messages.iter().rev().find_map(|m| match m {
            ovp_llm::ModelMessage::ToolResults { results } => results.first(),
            _ => None,
        });
        let (text, reason, blocks) = if let Some(result) = result {
            assert!(!result.is_error, "{}", result.content);
            let v: Value = serde_json::from_str(&result.content).unwrap();
            assert_eq!(
                v["hits"][0]["source_id"],
                if self.0 { "bbbb2222" } else { "aaaa1111" }
            );
            assert_eq!(v["hits"].as_array().unwrap().len(), 2);
            assert_eq!(v["hits"][0].get("semantic_evidence").is_some(), self.0);
            (
                "verified order [source:bbbb2222]".into(),
                ovp_llm::StopReason::EndTurn,
                None,
            )
        } else {
            (
                String::new(),
                ovp_llm::StopReason::ToolUse,
                Some(vec![ovp_llm::ReplyBlock::ToolUse {
                    id: "search-1".into(),
                    name: "search_sources".into(),
                    input: json!({"query":"retrieval"}),
                }]),
            )
        };
        Ok(ovp_llm::ModelReply {
            model: q.model.clone(),
            text,
            stop_reason: reason,
            blocks,
            raw_stop_reason: None,
            usage: ovp_llm::Usage {
                input_tokens: 1,
                output_tokens: 1,
            },
        })
    }
}
