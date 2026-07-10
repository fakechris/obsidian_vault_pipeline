//! End-to-end acceptance: run the article_clean pipeline, take the
//! resulting WritePlan, apply it to a tempdir vault, read the written
//! file back, parse its frontmatter, and assert the round-trip fields
//! match what the pipeline produced.
//!
//! This is the proof that "WritePlan → real vault files" actually works.

use ovp_core::{
    ApplyMode, GraphRunner, PipelineManifest, PlanApplier, RunId, WriteOp,
};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_stores::VaultFsPlanApplier;
use serde::Deserialize;

fn repo_root() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir)
        .ancestors()
        .nth(2)
        .unwrap()
        .to_path_buf()
}

fn run_article_clean_pipeline() -> ovp_core::RunReport {
    let root = repo_root();
    let manifest_toml =
        std::fs::read_to_string(root.join("manifests/article.pipeline.toml")).unwrap();
    let manifest = PipelineManifest::parse(&manifest_toml).unwrap();
    let run_id = RunId::new("e2e-test");

    let cassette_dir = root.join("crates/ovp-domain/tests/cassettes");
    let cached = CachedModelClient::new(
        NeverCallsClient,
        &cassette_dir,
        ARTICLE_PROMPT_ID,
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
    runner.run().unwrap()
}

#[derive(Deserialize)]
struct ReadBackFm {
    title: String,
    source: String,
    #[serde(default)]
    author: Option<String>,
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

fn parse_frontmatter(body: &str) -> ReadBackFm {
    let trimmed = body.strip_prefix("---\n").expect("body starts with `---`");
    let end = trimmed.find("\n---\n").expect("frontmatter terminated");
    serde_yaml::from_str(&trimmed[..end]).expect("frontmatter parses")
}

#[test]
fn interpret_then_apply_writes_a_real_file() {
    let report = run_article_clean_pipeline();
    assert_eq!(report.write_plan.len(), 1);

    let vault = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(vault.path());
    let apply_report = applier.apply(&report.write_plan, ApplyMode::Apply);
    let counts = apply_report.counts();
    assert_eq!(counts.applied, 1, "report: {apply_report:?}");
    assert_eq!(counts.failed, 0);

    // The file lands at the legacy convention path.
    let expected_path = vault
        .path()
        .join("20-Areas/AI-Research/Topics/2026-05/2026-05-04_A Guide to Agent-native Product Management_深度解读.md");
    assert!(expected_path.exists(), "expected file at {expected_path:?}");

    // Round-trip: read the file back and confirm key fields survived.
    let body = std::fs::read_to_string(&expected_path).unwrap();
    let fm = parse_frontmatter(&body);
    assert_eq!(fm.title, "A Guide to Agent-native Product Management");
    assert_eq!(
        fm.source,
        "https://every.to/guides/ai-product-management-guide"
    );
    assert_eq!(fm.doc_type, "article");
    assert_eq!(fm.area, "ai");
    assert!(fm.author.is_some());
    assert!(!fm.tags.is_empty());
    assert!(fm.canonical_concepts.is_empty(), "v1 article_clean has no canonicals");
    assert!(fm.concept_candidates.len() >= 10);
}

#[test]
fn second_apply_is_idempotent() {
    let report = run_article_clean_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(vault.path());

    let first = applier.apply(&report.write_plan, ApplyMode::Apply);
    assert_eq!(first.counts().applied, 1);

    // Second time around the file is already there with matching content.
    let second = applier.apply(&report.write_plan, ApplyMode::Apply);
    assert_eq!(second.counts().applied, 0);
    assert_eq!(second.counts().skipped, 1);
    assert_eq!(second.counts().failed, 0);
}

#[test]
fn dry_run_after_real_apply_still_reports_idempotent() {
    let report = run_article_clean_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(vault.path());

    applier.apply(&report.write_plan, ApplyMode::Apply);

    let dry = applier.apply(&report.write_plan, ApplyMode::DryRun);
    // The file exists with matching hash → idempotent skip wins over dry-run.
    // (idempotence is checked before mode in apply_create.)
    assert_eq!(dry.counts().skipped, 1);
    match &dry.outcomes[0].result {
        ovp_core::OpResult::Skipped { reason } => {
            assert!(
                reason.contains("idempotent") || reason == "dry-run",
                "unexpected skip reason: {reason}"
            );
        }
        other => panic!("expected Skipped, got {other:?}"),
    }
}

#[test]
fn vault_create_reaches_disk_with_correct_body_hash() {
    use sha2::{Digest, Sha256};
    let report = run_article_clean_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(vault.path());
    applier.apply(&report.write_plan, ApplyMode::Apply);

    let op = match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => o,
        _ => unreachable!(),
    };
    let written = std::fs::read(vault.path().join(op.path.as_str())).unwrap();
    let hash = Sha256::digest(&written);
    let mut hex = String::with_capacity(64);
    use std::fmt::Write;
    for b in hash.iter() {
        write!(hex, "{:02x}", b).unwrap();
    }
    assert_eq!(hex, op.after_hash.as_str(), "on-disk hash must match op.after_hash");
}
