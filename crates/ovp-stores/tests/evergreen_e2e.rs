//! End-to-end for EvergreenConceptWriter: run the article+evergreen
//! pipeline against article_clean, confirm the WritePlan carries the new
//! CanonicalUpsert + evergreen VaultCreate write surface, then apply it.
//!
//! It proves a concrete CanonicalUpsert producer exists, and (closed
//! loop) applies the full plan via a CompositePlanApplier so vault notes,
//! evergreen stubs, AND canonical records all land with no Unsupported.

use ovp_core::{
    ApplyMode, GraphRunner, OpKind, OpResult, PipelineManifest, PlanApplier, Record, RecordId,
    RecordMeta, RunId, Sink, VaultCreateOp, WriteOp, WritePlan,
};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_stores::{CanonicalFsStoreApplier, CompositePlanApplier, VaultFsPlanApplier};

/// Build the evergreen `VaultCreate` a grounded concept renders to (the first
/// op the sink emits), as if minted from a specific source document.
fn grounded_create(slug: &str, definition: &str, source_url: &str) -> VaultCreateOp {
    let mut c = EvergreenConcept::from_candidate(slug, source_url);
    c.definition = definition.into();
    c.source_claims = vec![format!("Claim from {source_url}.")];
    c.source_title = "Doc".into();
    let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("r"));
    let out = sink.consume(Record::new(
        RecordId::new(format!("evg-{slug}")),
        DomainBody::EvergreenConcept(Box::new(c)),
        RecordMeta { run_id: RunId::new("r"), seq: 0 },
    ));
    match out.plan_ops.into_iter().find(|o| matches!(o, WriteOp::VaultCreate(_))) {
        Some(WriteOp::VaultCreate(o)) => o,
        _ => unreachable!("sink emits a VaultCreate"),
    }
}

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

    // Rich bodies are deterministic from the (cassette-fixed) interpretation,
    // so a second apply still skips all VaultCreates as idempotent.
    let second = applier.apply(&report.write_plan, ApplyMode::Apply);
    assert_eq!(second.counts().applied, 0, "nothing re-written");
    assert_eq!(second.counts().skipped, 14, "all VaultCreates idempotent-skip");
    assert_eq!(second.counts().failed, 0);
}

#[test]
fn minted_evergreen_notes_are_grounded_not_stub() {
    // M12a: notes minted from a real interpreted article carry a definition +
    // source-backed claims + a source link, not the bare stub placeholder.
    let report = run_pipeline();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut applier = CompositePlanApplier::new(vec![
        Box::new(VaultFsPlanApplier::new(vault.path())),
        Box::new(CanonicalFsStoreApplier::new(canon.path())),
    ]);
    let apply = applier.apply(&report.write_plan, ApplyMode::Apply);
    assert_eq!(apply.counts().failed, 0);

    let evergreen_dir = vault.path().join("10-Knowledge/Evergreen");
    let mut files = 0;
    let mut with_claims = 0;
    for entry in std::fs::read_dir(&evergreen_dir).unwrap() {
        let body = std::fs::read_to_string(entry.unwrap().path()).unwrap();
        files += 1;
        assert!(
            !body.contains("Stub evergreen. Expand"),
            "minted note must not be a stub:\n{body}"
        );
        // Every minted note is grounded: a definition and a source link.
        assert!(body.contains("status: minted"), "note marked minted:\n{body}");
        assert!(body.contains("## Source"), "note links its source:\n{body}");
        if body.contains("## Source-backed claims") {
            with_claims += 1;
        }
    }
    assert_eq!(files, 13, "13 minted evergreen notes");
    assert!(with_claims >= 1, "at least one note carries source-backed claims");
}

#[test]
fn raw_applier_rejects_a_conflicting_vaultcreate() {
    // Low-level safety net: the vault applier never silently overwrites. Two
    // DISTINCT grounded notes for the same slug have different hashes, so a
    // second raw VaultCreate to the same path FAILS (and the composite halts).
    // In a run-cycle this never reaches the applier — the M12b reconcile
    // (see `run_cycle_enriches_a_preexisting_same_slug_note`) converts the
    // conflicting VaultCreate into a merge VaultUpdate first. This test pins the
    // applier's fail-loud behavior as the backstop.
    let vault = tempfile::tempdir().unwrap();
    let mut applier =
        CompositePlanApplier::new(vec![Box::new(VaultFsPlanApplier::new(vault.path()))]);

    let mut plan = WritePlan::new(RunId::new("two-docs"));
    plan.push(WriteOp::VaultCreate(grounded_create("rag", "Definition A.", "https://a/x")));
    plan.push(WriteOp::VaultCreate(grounded_create("rag", "Definition B.", "https://b/y")));
    plan.push(WriteOp::VaultCreate(grounded_create("vector-db", "A vector db.", "https://c/z")));

    let counts = applier.apply(&plan, ApplyMode::Apply).counts();
    assert_eq!(counts.applied, 1, "only the first grounded note lands");
    assert_eq!(counts.failed, 1, "second distinct same-slug body fails loud (no overwrite)");
    assert!(counts.skipped >= 1, "composite halts after the failure; later ops are skipped");
}

#[test]
fn reconcile_enriches_same_slug_across_documents() {
    // M12b: route each document's evergreen VaultCreate through the reconcile,
    // then apply. Document A mints; document B (same slug, different grounding)
    // enriches via VaultUpdate; re-running B is a no-op.
    let vault = tempfile::tempdir().unwrap();

    let apply_one = |op: WriteOp| {
        let mut applier = VaultFsPlanApplier::new(vault.path());
        let mut plan = WritePlan::new(RunId::new("doc"));
        plan.push(op);
        applier.apply(&plan, ApplyMode::Apply)
    };
    let note_on_disk = || {
        std::fs::read_to_string(vault.path().join("10-Knowledge/Evergreen/rag.md")).unwrap()
    };

    // Document A: no note yet → MintNew.
    let a = grounded_create("rag", "Definition from A.", "https://a/x");
    let wa = reconcile_evergreen_write(&a, None).expect("mint new");
    assert!(matches!(wa, WriteOp::VaultCreate(_)));
    assert_eq!(apply_one(wa).counts().applied, 1);

    // Document B: same slug, different grounding → EnrichExisting (VaultUpdate).
    let b = grounded_create("rag", "Definition from B.", "https://b/y");
    let wb = reconcile_evergreen_write(&b, Some(&note_on_disk())).expect("enrich");
    assert!(matches!(wb, WriteOp::VaultUpdate(_)), "second doc enriches, not fails");
    assert_eq!(apply_one(wb).counts().applied, 1, "the enrich update applies cleanly");

    let merged = note_on_disk();
    assert!(merged.contains("https://a/x") && merged.contains("https://b/y"), "both sources present");
    assert!(
        merged.contains("Claim from https://a/x.") && merged.contains("Claim from https://b/y."),
        "both documents' claims present"
    );
    assert!(merged.contains("Definition from A."), "keeps the first definition");

    // Re-running document B is idempotent: nothing new to add → skip (no write).
    let again = reconcile_evergreen_write(&b, Some(&note_on_disk()));
    assert!(again.is_none(), "re-enriching with the same document is a no-op");
}
