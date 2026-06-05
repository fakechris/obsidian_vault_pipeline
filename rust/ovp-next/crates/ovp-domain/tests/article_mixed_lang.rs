//! v1.1 acceptance test: article_mixed_lang fixture through the pipeline.
//! Stresses SourceResolver (Twitter URL → underlying article URL),
//! ConceptResolver (promotion to canonical_concepts), and full UTF-8
//! handling across title / tags / body / filename.

use ovp_core::{GraphRunner, PipelineManifest, RunId, WriteOp};
use ovp_domain::testing::{assert_contract, load_contract};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use serde::Deserialize;

const V1_1_CANONICAL_SLUGS: &[&str] = &["ai-agent", "competitive-advantage"];

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
    let run_id = RunId::new("mixed-lang-test");

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
            root.join("fixtures/article_mixed_lang/input.md"),
        ),
    );
    runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform(
        "article_parser",
        ArticleParser::new("article_parser", "ai", "2026-05-05"),
    );
    runner.register_transform(
        "concept_resolver",
        ConceptResolver::from_slugs("concept_resolver", V1_1_CANONICAL_SLUGS),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()),
    );

    runner.run().expect("pipeline runs")
}

fn interp_from_rendered(plan: &ovp_core::WritePlan) -> InterpretedDoc {
    let body = match &plan.ops[0] {
        WriteOp::VaultCreate(o) => &o.body,
        other => panic!("expected VaultCreate, got {other:?}"),
    };
    let trimmed = body.strip_prefix("---\n").expect("body starts with `---`");
    let end = trimmed.find("\n---\n").expect("body has terminating `---`");
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
        schema: InterpretationSchema::ArticleV1,
        concepts: Vec::new(),
    }
}

#[test]
fn pipeline_runs_and_emits_source_resolution() {
    use ovp_core::EventKind;
    let report = run_pipeline();
    assert_eq!(report.records_dropped, 0, "unexpected drops: {:?}", report.events);
    assert_eq!(report.records_forwarded_to_sinks, 1);
    assert_eq!(report.write_plan.len(), 1);

    let resolution_event = report.events.iter().find_map(|e| match &e.kind {
        EventKind::SourceResolution { original_url, resolved_url, reason, .. } => {
            Some((original_url.clone(), resolved_url.clone(), reason.clone()))
        }
        _ => None,
    });
    let (orig, resolved, reason) =
        resolution_event.expect("SourceResolution event must be emitted");
    assert!(
        orig.contains("x.com/dotey"),
        "original URL should be the Twitter clip, got {orig}"
    );
    assert!(
        resolved.contains("danielmiessler.com"),
        "resolved URL should be the underlying article, got {resolved}"
    );
    assert_eq!(reason, "source_resolver.twitter_to_article");
}

#[test]
fn pipeline_promotes_canonical_concepts() {
    let report = run_pipeline();
    let interp = interp_from_rendered(&report.write_plan);

    // ConceptResolver should have promoted ai-agent + competitive-advantage.
    assert!(
        interp.canonical_concepts.iter().any(|s| s == "ai-agent"),
        "ai-agent not promoted: {:?}",
        interp.canonical_concepts
    );
    assert!(
        interp.canonical_concepts.iter().any(|s| s == "competitive-advantage"),
        "competitive-advantage not promoted: {:?}",
        interp.canonical_concepts
    );
    // And they should NOT remain in candidates.
    assert!(
        !interp.concept_candidates.iter().any(|s| s == "ai-agent"),
        "ai-agent should not be a candidate after promotion: {:?}",
        interp.concept_candidates
    );
}

#[test]
fn pipeline_preserves_utf8_throughout() {
    let report = run_pipeline();
    let interp = interp_from_rendered(&report.write_plan);

    // Title is Chinese (interp's reframe).
    assert!(
        interp.title.contains("AI Readiness Gap"),
        "title missing reframed English handle: {}",
        interp.title
    );
    assert!(
        interp.title.contains("组织清晰度"),
        "title missing Chinese subtitle: {}",
        interp.title
    );
    // Path contains the Chinese title; filesystem-safe characters only.
    let op = match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => o,
        _ => unreachable!(),
    };
    assert!(op.path.as_str().contains("组织清晰度"));
}

#[test]
fn contract_must_clauses_pass() {
    let report = run_pipeline();
    let interp = interp_from_rendered(&report.write_plan);

    let contract = load_contract(
        &repo_root().join("fixtures/article_mixed_lang/expected/contract.yaml"),
    )
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
        result.must_passed.len() >= 8,
        "expected ≥8 MUST passes, got {}",
        result.must_passed.len()
    );
}

#[test]
fn contract_should_clauses_pass() {
    let report = run_pipeline();
    let interp = interp_from_rendered(&report.write_plan);

    let contract = load_contract(
        &repo_root().join("fixtures/article_mixed_lang/expected/contract.yaml"),
    )
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
