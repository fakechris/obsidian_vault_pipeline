//! Derived knowledge-index rebuild from (canonical store + vault).
//! Proves invariant #11 over BOTH derived inputs: concepts come from the
//! canonical store, backlinks from scanning vault notes for `[[slug]]`.
//!
//! Flow: run the article+evergreen pipeline, apply it (composite: vault
//! notes + evergreen stubs + canonical records), then scan vault for
//! backlinks, build the index, and apply it. The article note's "相关概念"
//! section wikilinks every concept, so each concept's backlinks include
//! the article note.

use std::collections::BTreeMap;

use ovp_core::{
    ApplyMode, GraphRunner, PipelineManifest, PlanApplier, RunId, WriteOp,
};
use ovp_domain::*;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_stores::{
    walk_markdown, CanonicalFsStoreApplier, CompositePlanApplier, VaultFsPlanApplier,
};

fn repo_root() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir).ancestors().nth(2).unwrap().to_path_buf()
}

fn run_article_evergreen() -> ovp_core::RunReport {
    let root = repo_root();
    let manifest = PipelineManifest::parse(
        &std::fs::read_to_string(root.join("manifests/article_evergreen.pipeline.toml")).unwrap(),
    )
    .unwrap();
    let run_id = RunId::new("ki-e2e");
    let cached = CachedModelClient::new(
        NeverCallsClient,
        root.join("crates/ovp-domain/tests/cassettes"),
        ARTICLE_PROMPT_ID,
        CacheMode::ReplayOnly,
    )
    .unwrap();
    let client: Box<dyn ModelClient> = Box::new(cached);

    let mut runner: GraphRunner<DomainBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source(
        "markdown_inbox",
        MarkdownInboxSource::new("markdown_inbox", run_id.clone(), root.join("fixtures/article_clean/input.md")),
    );
    runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform("article_parser", ArticleParser::new("article_parser", "ai", "2026-05-04"));
    runner.register_transform("concept_resolver", ConceptResolver::from_slugs("concept_resolver", &[]));
    runner.register_transform(
        "evergreen_concept_writer",
        EvergreenConceptWriter::new("evergreen_concept_writer"),
    );
    runner.register_sink("article_vault_plan", ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()));
    runner.register_sink("evergreen_sink", EvergreenSink::new("evergreen_sink", run_id.clone()));
    runner.run().unwrap()
}

/// Scan a vault for `[[slug]]` backlinks: slug → sorted note paths.
fn scan_backlinks(vault_root: &std::path::Path) -> BTreeMap<String, Vec<String>> {
    let mut map: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (path, content) in walk_markdown(vault_root).unwrap() {
        for slug in extract_wikilinks(&content) {
            map.entry(slug).or_default().push(path.clone());
        }
    }
    map
}

fn read_index(vault: &std::path::Path) -> Option<String> {
    std::fs::read_to_string(vault.join("60-Logs/knowledge-index.json")).ok()
}

#[test]
fn knowledge_index_rebuilt_from_canonical_and_vault() {
    let report = run_article_evergreen();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    // Apply the pipeline output: vault notes + evergreen stubs + canonical.
    let mut applier = CompositePlanApplier::new(vec![
        Box::new(VaultFsPlanApplier::new(vault.path())),
        Box::new(CanonicalFsStoreApplier::new(canon.path())),
    ]);
    assert_eq!(applier.apply(&report.write_plan, ApplyMode::Apply).counts().failed, 0);

    // Rebuild the index from BOTH derived inputs (strict parse).
    let store = CanonicalFsStoreApplier::new(canon.path());
    let concepts = CanonicalConcept::try_parse_pairs(store.read_all().unwrap()).unwrap();
    assert_eq!(concepts.len(), 13);
    let backlinks = scan_backlinks(vault.path());
    let index = KnowledgeIndex::build(&concepts, &backlinks);
    assert_eq!(index.entries.len(), 13);

    // Every concept is wikilinked by the article note's 相关概念 section,
    // so each entry has at least that backlink.
    let article_note = report
        .write_plan
        .ops
        .iter()
        .find_map(|op| match op {
            WriteOp::VaultCreate(o) if o.path.as_str().contains("/Topics/") => {
                Some(o.path.as_str().to_string())
            }
            _ => None,
        })
        .expect("article note in plan");
    for entry in &index.entries {
        assert!(
            entry.backlinks.iter().any(|b| b == &article_note),
            "concept {} should be backlinked by the article note",
            entry.slug
        );
    }

    // Apply the index, verify it lands and parses back.
    let builder = KnowledgeIndexBuilder::new();
    let mut vault_applier = VaultFsPlanApplier::new(vault.path());
    let plan = builder.plan_rebuild(RunId::new("ki"), &index, read_index(vault.path()).as_deref());
    assert_eq!(vault_applier.apply(&plan, ApplyMode::Apply).counts().applied, 1);

    let raw = read_index(vault.path()).expect("index written");
    let parsed: KnowledgeIndex = serde_json::from_str(&raw).expect("index round-trips");
    assert_eq!(parsed.entries.len(), 13);
}

#[test]
fn index_rebuild_is_idempotent() {
    let report = run_article_evergreen();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut applier = CompositePlanApplier::new(vec![
        Box::new(VaultFsPlanApplier::new(vault.path())),
        Box::new(CanonicalFsStoreApplier::new(canon.path())),
    ]);
    applier.apply(&report.write_plan, ApplyMode::Apply);

    let store = CanonicalFsStoreApplier::new(canon.path());
    let concepts = CanonicalConcept::try_parse_pairs(store.read_all().unwrap()).unwrap();
    let backlinks = scan_backlinks(vault.path());
    let index = KnowledgeIndex::build(&concepts, &backlinks);

    let builder = KnowledgeIndexBuilder::new();
    let mut vault_applier = VaultFsPlanApplier::new(vault.path());
    let p1 = builder.plan_rebuild(RunId::new("a"), &index, read_index(vault.path()).as_deref());
    vault_applier.apply(&p1, ApplyMode::Apply);

    // Unchanged inputs → empty rebuild plan.
    let p2 = builder.plan_rebuild(RunId::new("b"), &index, read_index(vault.path()).as_deref());
    assert!(p2.is_empty(), "idempotent: unchanged index → no op");
}

#[test]
fn index_rebuild_fails_loudly_on_corrupt_canonical_record() {
    let report = run_article_evergreen();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut applier = CompositePlanApplier::new(vec![
        Box::new(VaultFsPlanApplier::new(vault.path())),
        Box::new(CanonicalFsStoreApplier::new(canon.path())),
    ]);
    applier.apply(&report.write_plan, ApplyMode::Apply);

    // Corrupt the canonical store out-of-band.
    std::fs::write(canon.path().join("broken.json"), "not valid json").unwrap();

    // A corrupt canonical record must abort the rebuild (naming the key),
    // not silently drop a concept from the knowledge index.
    let store = CanonicalFsStoreApplier::new(canon.path());
    let err = CanonicalConcept::try_parse_pairs(store.read_all().unwrap()).unwrap_err();
    assert_eq!(err.key, "broken");
}
