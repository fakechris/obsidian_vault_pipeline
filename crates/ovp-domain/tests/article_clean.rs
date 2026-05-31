//! C v1 acceptance test: run the full article pipeline against the
//! `article_clean` fixture and assert against its `contract.yaml`.
//!
//! Offline: uses `CachedModelClient(NeverCallsClient, ReplayOnly)`
//! against the committed cassette. No network, no API key.
//!
//! **Single-pipeline discipline (post-codex-review fix):** all assertions
//! run against the same `RunReport` from one `runner.run()` call. The
//! contract engine reads field values by parsing the rendered
//! `VaultCreate.body` frontmatter, not by re-driving the pipeline. A
//! sink or frontmatter regression now fails the test instead of hiding
//! behind a parallel reconstruction.

use ovp_core::{GraphRunner, PipelineManifest, RunId, WriteOp};
use ovp_domain::testing::{assert_contract, load_contract};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use serde::Deserialize;

fn repo_root() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir)
        .ancestors()
        .nth(2)
        .unwrap()
        .to_path_buf()
}

fn run_pipeline() -> ovp_core::RunReport {
    let root = repo_root();
    let manifest_toml = std::fs::read_to_string(root.join("manifests/article.pipeline.toml"))
        .expect("manifest exists");
    let manifest = PipelineManifest::parse(&manifest_toml).expect("manifest parses");
    let run_id = RunId::new("article-clean-test");

    let cassette_dir = root.join("crates/ovp-domain/tests/cassettes");
    let cached = CachedModelClient::new(
        NeverCallsClient,
        &cassette_dir,
        ARTICLE_PROMPT_ID,
        CacheMode::ReplayOnly,
    )
    .expect("open cache");
    let client: Box<dyn ModelClient> = Box::new(cached);

    let mut runner: GraphRunner<DomainBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source(
        "markdown_inbox",
        MarkdownInboxSource::new(
            "markdown_inbox",
            run_id.clone(),
            root.join("fixtures/article_clean/input.md"),
        ),
    );
    runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform(
        "article_parser",
        ArticleParser::new("article_parser", "ai", "2026-05-04"),
    );
    runner.register_transform(
        "concept_resolver",
        ConceptResolver::from_slugs("concept_resolver", &[]),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()),
    );

    runner.run().expect("pipeline runs")
}

/// Parse the rendered VaultCreate body's YAML frontmatter back into an
/// `InterpretedDoc`. Used by the contract assertion to test the sink's
/// actual output (not a parallel reconstruction). Dimensions are
/// placeholder because contract.yaml doesn't assert on them.
fn interp_from_rendered(plan: &ovp_core::WritePlan) -> InterpretedDoc {
    let body = match &plan.ops[0] {
        WriteOp::VaultCreate(o) => &o.body,
        other => panic!("expected VaultCreate, got {other:?}"),
    };
    let trimmed = body
        .strip_prefix("---\n")
        .expect("body starts with `---`");
    let end = trimmed
        .find("\n---\n")
        .expect("body has terminating `---`");
    let fm_str = &trimmed[..end];

    #[derive(Deserialize)]
    struct RenderedFm {
        title: String,
        source: String,
        #[serde(default)]
        author: Option<String>,
        date: String,
        #[serde(rename = "type")]
        doc_type: String,
        area: String,
        #[serde(default)]
        tags: Vec<String>,
        #[serde(default)]
        canonical_concepts: Vec<String>,
        #[serde(default)]
        concept_candidates: Vec<String>,
    }

    let fm: RenderedFm = serde_yaml::from_str(fm_str).expect("frontmatter parses");
    InterpretedDoc {
        title: fm.title,
        source_url: fm.source,
        author: fm.author,
        date: fm.date,
        doc_type: fm.doc_type,
        area: fm.area,
        tags: fm.tags,
        canonical_concepts: fm.canonical_concepts,
        concept_candidates: fm.concept_candidates,
        // Not asserted by contract.yaml; placeholder.
        dimensions: Dimensions {
            one_liner: String::new(),
            explanation: Explanation {
                what: String::new(),
                why: String::new(),
                how: String::new(),
            },
            details: vec![],
            structure: None,
            actions: vec![],
            linked_concepts: vec![],
        },
        concepts: Vec::new(),
    }
}

#[test]
fn full_pipeline_runs_against_cassette() {
    let report = run_pipeline();
    assert_eq!(report.records_dropped, 0, "unexpected drops: {:?}", report.events);
    assert_eq!(report.records_forwarded_to_sinks, 1);
    assert_eq!(report.write_plan.len(), 1);

    let op = match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => o,
        other => panic!("expected VaultCreate, got {other:?}"),
    };
    assert!(
        op.path.as_str().starts_with("20-Areas/AI-Research/Topics/2026-05/"),
        "unexpected path: {}",
        op.path.as_str()
    );
}

#[test]
fn contract_must_clauses_pass() {
    let report = run_pipeline();
    let interp = interp_from_rendered(&report.write_plan);

    let root = repo_root();
    let contract = load_contract(&root.join("fixtures/article_clean/expected/contract.yaml"))
        .expect("contract loads");

    let result =
        assert_contract(&contract, Some(&interp), &report.write_plan, &report.events);

    if !result.must_clean() {
        for f in &result.must_failed {
            eprintln!("MUST FAILED — {}: {}", f.clause, f.detail);
        }
        panic!(
            "{} MUST clause(s) failed (see stderr)",
            result.must_failed.len()
        );
    }
    assert!(
        result.must_passed.len() >= 10,
        "expected ≥10 MUST passes, got {}",
        result.must_passed.len()
    );
}

#[test]
fn contract_should_clauses_pass() {
    let report = run_pipeline();
    let interp = interp_from_rendered(&report.write_plan);

    let root = repo_root();
    let contract = load_contract(&root.join("fixtures/article_clean/expected/contract.yaml"))
        .expect("contract loads");

    let result =
        assert_contract(&contract, Some(&interp), &report.write_plan, &report.events);

    if !result.should_failed.is_empty() {
        for f in &result.should_failed {
            eprintln!("SHOULD failed — {}: {}", f.clause, f.detail);
        }
        panic!("{} SHOULD clause(s) failed", result.should_failed.len());
    }
}
