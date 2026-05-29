//! v1.2 acceptance test: the paper_arxiv fixture through the UNIFIED
//! pipeline (RouteBySourceKind → both kind branches). A paper input must
//! route to the paper branch, produce one paper note, and satisfy
//! fixtures/paper_arxiv/expected/contract.yaml. Offline via cassette.
//!
//! Demonstrates routing: the same topology that handles articles handles
//! papers; the article branch drops the paper record at its builder/parser.

use ovp_core::{EventKind, GraphRunner, PipelineManifest, RunId, WriteOp};
use ovp_domain::testing::{assert_contract_paper, load_contract};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use serde::Deserialize;

fn repo_root() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir).ancestors().nth(2).unwrap().to_path_buf()
}

fn run_pipeline() -> ovp_core::RunReport {
    let root = repo_root();
    let manifest_toml =
        std::fs::read_to_string(root.join("manifests/unified.pipeline.toml")).unwrap();
    let manifest = PipelineManifest::parse(&manifest_toml).unwrap();
    let run_id = RunId::new("paper-arxiv-test");

    let cassette_dir = root.join("crates/ovp-domain/tests/cassettes");
    // Single-kind input (a paper) → only paper prompts reach the model,
    // so the PAPER_PROMPT_ID namespace is correct for this run. (A mixed
    // inbox would file both kinds under one namespace; request keys still
    // differ by the full request hash, so no collision.)
    let cached = CachedModelClient::new(
        NeverCallsClient,
        &cassette_dir,
        PAPER_PROMPT_ID,
        CacheMode::ReplayOnly,
    )
    .unwrap();
    let client: Box<dyn ModelClient> = Box::new(cached);

    let mut runner: GraphRunner<DomainBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source(
        "markdown_inbox",
        MarkdownInboxSource::new(
            "markdown_inbox",
            run_id.clone(),
            root.join("fixtures/paper_arxiv/input.md"),
        ),
    );
    runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
    runner.register_transform("route_by_source_kind", RouteBySourceKind::new("route_by_source_kind"));
    runner.register_transform("article_prompt_builder", PromptBuilder::new("article_prompt_builder"));
    runner.register_transform("paper_prompt_builder", PaperPromptBuilder::new("paper_prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform(
        "article_parser",
        ArticleParser::new("article_parser", "ai", "2026-05-29"),
    );
    runner.register_transform("paper_parser", PaperParser::new("paper_parser", "2026-05-29"));
    runner.register_transform(
        "concept_resolver",
        ConceptResolver::from_slugs("concept_resolver", &[]),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()),
    );
    runner.register_sink(
        "paper_vault_plan",
        PaperVaultPlanSink::new("paper_vault_plan", run_id.clone()),
    );
    runner.run().unwrap()
}

/// Parse the rendered paper note's frontmatter into a PaperDoc (sections
/// left empty — the contract's body_sections_present reads the rendered
/// body from the WritePlan, not the reconstructed doc).
fn paper_from_rendered(plan: &ovp_core::WritePlan) -> PaperDoc {
    let body = match &plan.ops[0] {
        WriteOp::VaultCreate(o) => &o.body,
        other => panic!("expected VaultCreate, got {other:?}"),
    };
    let trimmed = body.strip_prefix("---\n").expect("frontmatter start");
    let end = trimmed.find("\n---\n").expect("frontmatter end");
    let fm_str = &trimmed[..end];

    #[derive(Deserialize)]
    struct Fm {
        title: String,
        source: String,
        arxiv_id: String,
        date: String,
        #[serde(default)]
        source_date: Option<String>,
        #[serde(default)]
        authors: Vec<String>,
        #[serde(default)]
        categories: Vec<String>,
        #[serde(default)]
        tags: Vec<String>,
    }
    let fm: Fm = serde_yaml::from_str(fm_str).expect("paper frontmatter parses");
    PaperDoc {
        title: fm.title,
        source_url: fm.source,
        arxiv_id: fm.arxiv_id,
        authors: fm.authors,
        categories: fm.categories,
        date: fm.date,
        source_date: fm.source_date,
        tags: fm.tags,
        sections: PaperSections {
            metadata: String::new(),
            core_contribution: String::new(),
            background: String::new(),
            method: String::new(),
            experiments: String::new(),
            key_insights: String::new(),
            reproduction: String::new(),
            limitations: String::new(),
            related_work: String::new(),
            personal_notes: String::new(),
        },
    }
}

#[test]
fn paper_routes_and_produces_one_paper_note() {
    let report = run_pipeline();
    // Exactly one op: the paper note. The article branch dropped the
    // paper record at its builder/parser, so no article note.
    assert_eq!(report.write_plan.len(), 1, "events: {:?}", report.events);
    let op = match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => o,
        other => panic!("expected VaultCreate, got {other:?}"),
    };
    assert!(
        op.path.as_str().starts_with("20-Areas/AI-Research/Papers/"),
        "unexpected path: {}",
        op.path.as_str()
    );
    // The routing decision was observable.
    let routed = report.events.iter().any(|e| {
        matches!(&e.kind, EventKind::SourceRouted { source_kind, .. } if source_kind == "paper")
    });
    assert!(routed, "expected a source_routed=paper event");
}

#[test]
fn contract_must_clauses_pass() {
    let report = run_pipeline();
    let paper = paper_from_rendered(&report.write_plan);
    let contract = load_contract(
        &repo_root().join("fixtures/paper_arxiv/expected/contract.yaml"),
    )
    .expect("contract loads");

    let result = assert_contract_paper(&contract, Some(&paper), &report.write_plan, &report.events);
    if !result.must_clean() {
        for f in &result.must_failed {
            eprintln!("MUST FAILED — {}: {}", f.clause, f.detail);
        }
        panic!("{} MUST clause(s) failed", result.must_failed.len());
    }
    assert!(result.must_passed.len() >= 5, "expected >=5 MUST passes, got {}", result.must_passed.len());
}

#[test]
fn contract_should_clauses_pass() {
    let report = run_pipeline();
    let paper = paper_from_rendered(&report.write_plan);
    let contract = load_contract(
        &repo_root().join("fixtures/paper_arxiv/expected/contract.yaml"),
    )
    .expect("contract loads");

    let result = assert_contract_paper(&contract, Some(&paper), &report.write_plan, &report.events);
    if !result.should_failed.is_empty() {
        for f in &result.should_failed {
            eprintln!("SHOULD failed — {}: {}", f.clause, f.detail);
        }
        panic!("{} SHOULD clause(s) failed", result.should_failed.len());
    }
}
