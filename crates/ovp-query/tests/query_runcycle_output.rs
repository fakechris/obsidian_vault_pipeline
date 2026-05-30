//! Round-trip acceptance: run a real `run-cycle`, then query the vault +
//! canonical store it produced. Proves the L5 read layer reads real L4 output.
//! Offline (replay-only cassette client); tempdirs only.

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_core::{ApplyMode, RunId};
use ovp_domain::{ConceptRegistry, ARTICLE_PROMPT_ID};
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_query::KnowledgeView;
use ovp_run::{RunCycle, RunCycleInputs};

fn repo_root() -> PathBuf {
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap(); // <root>/crates/ovp-query
    Path::new(&md).ancestors().nth(2).unwrap().to_path_buf()
}

fn cassette_client(root: &Path) -> Box<dyn ModelClient> {
    Box::new(
        CachedModelClient::new(
            NeverCallsClient,
            root.join("crates/ovp-domain/tests/cassettes"),
            ARTICLE_PROMPT_ID,
            CacheMode::ReplayOnly,
        )
        .unwrap(),
    )
}

#[test]
fn query_reads_a_real_run_cycle_output() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    // Produce real L4 output.
    let toml =
        std::fs::read_to_string(root.join("manifests/article_evergreen.pipeline.toml")).unwrap();
    let spec = DomainPipelineSpec::parse(&toml).unwrap();
    let wiring = AppWiring::new(RunId::new("rc"))
        .with_date_stamp("2026-05-04")
        .with_area("ai")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let report = RunCycle::new()
        .execute(RunCycleInputs {
            spec,
            wiring,
            vault_root: vault.path().to_path_buf(),
            canonical_root: canon.path().to_path_buf(),
            mode: ApplyMode::Apply,
        })
        .unwrap();
    assert!(report.succeeded());

    // Query that output through the L5 read layer.
    let view = KnowledgeView::load(vault.path(), canon.path()).unwrap();
    assert!(!view.concepts().is_empty(), "run-cycle minted canonical concepts");
    assert!(view.index().is_some(), "run-cycle wrote a knowledge index");

    // A known concept from the article_clean fixture, with its evergreen path.
    let c = view.get("agent-native-product-management").expect("known concept present");
    assert_eq!(c.evergreen_path, "10-Knowledge/Evergreen/agent-native-product-management.md");

    // The article note backlinks every concept (its 相关概念 section), so each
    // concept has at least the article note as a backlink.
    let backlinks = view.backlinks("agent-native-product-management");
    assert!(
        backlinks.iter().any(|b| b.contains("/Topics/")),
        "the article note should backlink the concept, got {backlinks:?}"
    );

    // search finds it case-insensitively.
    assert!(view.search("AGENT-NATIVE").iter().any(|c| c.slug == "agent-native-product-management"));

    // stats are coherent.
    let stats = view.stats();
    assert_eq!(stats.concept_count, view.concepts().len());
    assert!(stats.index_present);
    assert!(stats.total_backlinks >= stats.concept_count, "each concept has >=1 article backlink");
}
