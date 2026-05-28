//! C v1 acceptance test: run the full article pipeline against the
//! `article_clean` fixture and assert against its `contract.yaml`.
//!
//! Offline: uses `CachedModelClient(NeverCallsClient, ReplayOnly)`
//! against the cassette committed under tests/cassettes. No network,
//! no API key.
//!
//! Failure on ANY MUST clause = test fail. SHOULD failures are logged
//! but don't fail the gate. MAY-break clauses are documentation; they
//! auto-pass.

use ovp_core::{GraphRunner, PipelineManifest, RunId, WriteOp};
use ovp_domain::testing::{assert_contract, load_contract};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};

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
    let cached = CachedModelClient::new(NeverCallsClient, &cassette_dir, CacheMode::ReplayOnly)
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
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform(
        "article_parser",
        ArticleParser::new("article_parser", "ai", "2026-05-04"),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()),
    );

    runner.run().expect("pipeline runs")
}

fn extract_interpreted_from_plan(plan: &ovp_core::WritePlan) -> Option<InterpretedDoc> {
    // Reverse-engineer the InterpretedDoc from the rendered body: not
    // possible without a markdown parser. Instead, the test re-runs the
    // pipeline at a lower level to grab the InterpretedDoc directly via
    // a side-channel — but our public Sink trait doesn't expose that.
    //
    // Easier: get the InterpretedDoc by running the parser stages
    // manually below. (See `interp_doc()`.)
    let _ = plan;
    None
}

/// Re-runs the source + first three transforms via the public API to
/// get the InterpretedDoc that the sink would consume. The full
/// pipeline above gives us the WritePlan; this gives us the structured
/// InterpretedDoc for field-level assertion. Yes, it's redundant work;
/// no, we don't want to leak runtime internals from the sink trait.
fn interp_doc() -> InterpretedDoc {
    use ovp_core::{FilterDecision, Source, SourceOutput, Transform};

    let root = repo_root();
    let mut src = MarkdownInboxSource::new(
        "md",
        RunId::new("t"),
        root.join("fixtures/article_clean/input.md"),
    );
    let source_record = match src.produce() {
        SourceOutput::Records(mut rs) => rs.pop().expect("one record"),
        other => panic!("source: {other:?}"),
    };

    let mut pb = PromptBuilder::new("pb");
    let prompt_record = match pb.process(source_record) {
        FilterDecision::Forward(mut rs) => rs.pop().expect("one record"),
        other => panic!("prompt builder: {other:?}"),
    };

    let cassette_dir = root.join("crates/ovp-domain/tests/cassettes");
    let cached = CachedModelClient::new(NeverCallsClient, &cassette_dir, CacheMode::ReplayOnly)
        .expect("open cache");
    let client: Box<dyn ModelClient> = Box::new(cached);
    let mut invoker = LLMInvoker::new("llm", client);

    use ovp_core::EffectfulTransform;
    let model_record = match invoker.process(prompt_record) {
        FilterDecision::Forward(mut rs) => rs.pop().expect("one record"),
        other => panic!("invoker: {other:?}"),
    };

    let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-04");
    match parser.process(model_record) {
        FilterDecision::Forward(mut rs) => {
            let rec = rs.pop().expect("one record");
            match rec.body {
                DomainBody::Interpreted(d) => *d,
                _ => panic!("expected Interpreted variant"),
            }
        }
        other => panic!("parser: {other:?}"),
    }
}

#[test]
fn full_pipeline_runs_against_cassette() {
    let report = run_pipeline();
    assert_eq!(report.records_dropped, 0, "unexpected drops: {:?}", report.events);
    assert_eq!(report.records_forwarded_to_sinks, 1);
    assert_eq!(report.write_plan.len(), 1);

    // Sanity: the produced VaultCreate op lands at the expected path.
    let op = match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => o,
        other => panic!("expected VaultCreate, got {other:?}"),
    };
    assert!(
        op.path.as_str().starts_with("20-Areas/AI-Research/Topics/2026-05/"),
        "unexpected path: {}",
        op.path.as_str()
    );
    let _ = extract_interpreted_from_plan(&report.write_plan); // placeholder helper, unused.
}

#[test]
fn contract_must_clauses_pass() {
    let report = run_pipeline();
    let interp = interp_doc();

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
    let interp = interp_doc();

    let root = repo_root();
    let contract = load_contract(&root.join("fixtures/article_clean/expected/contract.yaml"))
        .expect("contract loads");

    let result =
        assert_contract(&contract, Some(&interp), &report.write_plan, &report.events);

    // SHOULD failures are warnings, not errors — but the v1 cassette
    // is hand-rolled to satisfy every SHOULD. If this ever starts
    // failing, the cassette has drifted.
    if !result.should_failed.is_empty() {
        for f in &result.should_failed {
            eprintln!("SHOULD failed — {}: {}", f.clause, f.detail);
        }
        panic!("{} SHOULD clause(s) failed", result.should_failed.len());
    }
}
