//! End-to-end for EvergreenConceptWriter: run the article+evergreen
//! pipeline against article_clean, confirm the WritePlan carries the new
//! CanonicalUpsert + evergreen VaultCreate write surface, then apply it.
//!
//! It proves a concrete CanonicalUpsert producer exists, and (closed
//! loop) applies the full plan via a CompositePlanApplier so vault notes,
//! evergreen stubs, AND canonical records all land with no Unsupported.

use ovp_core::{
    ApplyMode, GraphRunner, OpKind, OpResult, PipelineManifest, PlanApplier, RunId, WriteOp,
};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_stores::{CanonicalFsStoreApplier, CompositePlanApplier, VaultFsPlanApplier};

fn repo_root() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir).ancestors().nth(2).unwrap().to_path_buf()
}

fn run_pipeline() -> ovp_core::RunReport {
    let root = repo_root();
    let manifest_toml =
        std::fs::read_to_string(root.join("manifests/article_evergreen.pipeline.toml")).unwrap();
    let manifest = PipelineManifest::parse(&manifest_toml).unwrap();
    let run_id = RunId::new("evergreen-e2e");

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
    // Empty registry → nothing promoted → every candidate is "new",
    // so EvergreenConceptWriter mints one evergreen per candidate.
    runner.register_transform(
        "concept_resolver",
        ConceptResolver::from_slugs("concept_resolver", &[]),
    );
    runner.register_transform(
        "evergreen_concept_writer",
        EvergreenConceptWriter::new("evergreen_concept_writer"),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()),
    );
    runner.register_sink("evergreen_sink", EvergreenSink::new("evergreen_sink", run_id.clone()));
    runner.run().unwrap()
}

fn counts_by_kind(plan: &ovp_core::WritePlan) -> (usize, usize) {
    // (vault_creates, canonical_upserts)
    let mut creates = 0;
    let mut upserts = 0;
    for op in &plan.ops {
        match op {
            WriteOp::VaultCreate(_) => creates += 1,
            WriteOp::CanonicalUpsert(_) => upserts += 1,
            _ => {}
        }
    }
    (creates, upserts)
}

#[test]
fn pipeline_emits_evergreen_and_canonical_write_surface() {
    let report = run_pipeline();
    let (creates, upserts) = counts_by_kind(&report.write_plan);

    // article_clean's cassette has 13 linked concepts; the default empty
    // registry promotes none, so 13 evergreens are minted.
    assert_eq!(upserts, 13, "one CanonicalUpsert per minted evergreen");
    // 1 article note + 13 evergreen stubs.
    assert_eq!(creates, 14, "article note + 13 evergreen stubs");

    // Every CanonicalUpsert is a real, populated payload (the write
    // surface the canonical store will consume).
    for op in &report.write_plan.ops {
        if let WriteOp::CanonicalUpsert(c) = op {
            assert!(!c.key.as_str().is_empty());
            assert!(c.payload.contains("\"slug\":"));
            assert!(c.payload.contains("\"evergreen_path\":"));
        }
    }
}

#[test]
fn apply_writes_evergreen_files_and_reports_canonical_unsupported() {
    let report = run_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(vault.path());
    let apply = applier.apply(&report.write_plan, ApplyMode::Apply);

    // VaultCreates applied (article note + evergreen stubs); the
    // CanonicalUpserts are Unsupported on VaultFs (no canonical applier
    // yet) — the documented gap the next stage closes. Not a hard failure.
    let counts = apply.counts();
    assert_eq!(counts.applied, 14, "14 VaultCreate applied");
    assert_eq!(counts.unsupported, 13, "13 CanonicalUpsert unsupported on VaultFs");
    assert_eq!(counts.failed, 0);
    assert!(apply.has_unsupported());

    // A representative evergreen stub landed on disk under the layout path.
    let evergreen_dir = vault.path().join("10-Knowledge/Evergreen");
    let count = std::fs::read_dir(&evergreen_dir).unwrap().count();
    assert_eq!(count, 13, "13 evergreen stub files written");

    // Outcomes carry the right OpKinds.
    let canon_outcomes = apply
        .outcomes
        .iter()
        .filter(|o| o.kind == OpKind::CanonicalUpsert)
        .count();
    assert_eq!(canon_outcomes, 13);
    assert!(apply
        .outcomes
        .iter()
        .any(|o| matches!(o.result, OpResult::Unsupported)));
}

#[test]
fn composite_closes_the_loop_no_unsupported() {
    // The canonical store now exists: route the full plan through a
    // composite of (vault + canonical) appliers. Every op is handled by
    // exactly one backend → zero Unsupported.
    let report = run_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut applier = CompositePlanApplier::new(vec![
        Box::new(VaultFsPlanApplier::new(vault.path())),
        Box::new(CanonicalFsStoreApplier::new(canon.path())),
    ]);
    let apply = applier.apply(&report.write_plan, ApplyMode::Apply);
    let counts = apply.counts();
    assert_eq!(counts.unsupported, 0, "composite leaves no Unsupported");
    assert_eq!(counts.failed, 0);
    // 14 vault creates + 13 canonical upserts all applied.
    assert_eq!(counts.applied, 27);

    // Vault side: 13 evergreen stubs + the article note.
    assert_eq!(
        std::fs::read_dir(vault.path().join("10-Knowledge/Evergreen")).unwrap().count(),
        13
    );
    // Canonical side: 13 records, each a valid typed CanonicalConcept.
    let canon_files: Vec<_> = std::fs::read_dir(canon.path()).unwrap().collect();
    assert_eq!(canon_files.len(), 13, "13 canonical records");
    for entry in canon_files {
        let path = entry.unwrap().path();
        let raw = std::fs::read_to_string(&path).unwrap();
        let concept = CanonicalConcept::from_payload(&raw)
            .unwrap_or_else(|e| panic!("canonical record {path:?} not a CanonicalConcept: {e}"));
        assert!(!concept.slug.is_empty());
        assert!(concept.evergreen_path.starts_with("10-Knowledge/Evergreen/"));
    }
}

#[test]
fn applying_twice_is_idempotent_for_evergreen_stubs() {
    let report = run_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(vault.path());

    let first = applier.apply(&report.write_plan, ApplyMode::Apply);
    assert_eq!(first.counts().applied, 14);

    // Stub bodies are provenance-free + deterministic, so a second apply
    // skips all VaultCreates as idempotent.
    let second = applier.apply(&report.write_plan, ApplyMode::Apply);
    assert_eq!(second.counts().applied, 0, "nothing re-written");
    assert_eq!(second.counts().skipped, 14, "all VaultCreates idempotent-skip");
    assert_eq!(second.counts().failed, 0);
}
