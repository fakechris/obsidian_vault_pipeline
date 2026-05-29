//! End-to-end tests for the L4 operational `run-cycle`: a full inbox-file →
//! vault note + evergreen + canonical + MOC + knowledge index cycle, its
//! idempotence, and its fail-closed behavior. Offline (replay-only cassette
//! client); tempdirs only — no real vault, no network.

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_core::{ApplyMode, RunId};
use ovp_domain::{CanonicalConcept, ConceptRegistry, ARTICLE_PROMPT_ID};
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_run::{RunCycle, RunCycleError, RunCycleInputs, RunCycleReport};
use ovp_stores::CanonicalFsStoreApplier;

fn repo_root() -> PathBuf {
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap(); // <root>/crates/ovp-run
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

#[allow(clippy::too_many_arguments)]
fn run(
    root: &Path,
    vault: &Path,
    canon: &Path,
    manifest_rel: &str,
    input_rel: &str,
    date: &str,
    slugs: &[&str],
) -> RunCycleReport {
    let toml = std::fs::read_to_string(root.join(manifest_rel)).unwrap();
    let spec = DomainPipelineSpec::parse(&toml).unwrap();
    let wiring = AppWiring::new(RunId::new("rc"))
        .with_date_stamp(date)
        .with_area("ai")
        .with_input_path(root.join(input_rel))
        .with_client("default_llm", cassette_client(root))
        .with_registry("default", ConceptRegistry::from_slugs(slugs));
    let inputs = RunCycleInputs {
        spec,
        wiring,
        vault_root: vault.to_path_buf(),
        canonical_root: canon.to_path_buf(),
        mode: ApplyMode::Apply,
    };
    RunCycle::new().execute(inputs).unwrap()
}

#[test]
fn run_cycle_article_is_idempotent() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    // First run writes the full cycle.
    let r1 = run(
        &root,
        vault.path(),
        canon.path(),
        "manifests/article_evergreen.pipeline.toml",
        "fixtures/article_clean/input.md",
        "2026-05-04",
        &[],
    );
    assert!(r1.succeeded(), "run1 should succeed: {:?}", r1.derived_skipped_reason);
    assert_eq!(r1.apply.counts().failed, 0);
    assert!(r1.apply.counts().applied > 0, "main apply wrote nothing");
    assert_eq!(r1.moc.as_ref().unwrap().applied, 1, "MOC created");
    assert_eq!(r1.knowledge_index.as_ref().unwrap().applied, 1, "index created");
    assert!(vault.path().join("10-Knowledge/Atlas/MOC-Index.md").exists());
    assert!(vault.path().join("60-Logs/knowledge-index.json").exists());

    // Canonical store parses strictly and has concepts.
    let store = CanonicalFsStoreApplier::new(canon.path());
    let concepts = CanonicalConcept::try_parse_pairs(store.read_all().unwrap()).unwrap();
    assert!(!concepts.is_empty(), "canonical store should hold minted concepts");

    // Second run against the same roots is idempotent: nothing applied anywhere.
    let r2 = run(
        &root,
        vault.path(),
        canon.path(),
        "manifests/article_evergreen.pipeline.toml",
        "fixtures/article_clean/input.md",
        "2026-05-04",
        &[],
    );
    assert!(r2.succeeded());
    assert_eq!(r2.apply.counts().applied, 0, "second main apply must apply nothing");
    assert_eq!(r2.apply.counts().failed, 0);
    assert_eq!(r2.moc.as_ref().unwrap().applied, 0, "MOC unchanged on re-run");
    assert_eq!(r2.knowledge_index.as_ref().unwrap().applied, 0, "index unchanged on re-run");
}

#[test]
fn run_cycle_paper_smoke() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let r = run(
        &root,
        vault.path(),
        canon.path(),
        "manifests/unified.pipeline.toml",
        "fixtures/paper_arxiv/input.md",
        "2026-05-29",
        &[],
    );
    assert!(r.succeeded(), "paper run-cycle should succeed: {:?}", r.derived_skipped_reason);
    assert!(
        vault.path().join("20-Areas/AI-Research/Papers").exists(),
        "paper note directory should exist"
    );
    // Derived artifacts were rebuilt (the unified path mints no evergreens, so
    // the canonical store is empty and these are the empty-state artifacts).
    assert!(r.moc.is_some());
    assert!(r.knowledge_index.is_some());
    let store = CanonicalFsStoreApplier::new(canon.path());
    assert!(CanonicalConcept::try_parse_pairs(store.read_all().unwrap()).is_ok());
}

#[test]
fn run_cycle_bad_manifest_writes_nothing() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let toml = r#"
        [pipeline]
        nodes = ["src", "snk"]
        edges = [["src", "snk"]]
        [assembly.src]
        kind = "source.markdown_inbox"
        [assembly.snk]
        kind = "sink.nonexistent"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("rc"))
        .with_date_stamp("2026-05-04")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let inputs = RunCycleInputs {
        spec,
        wiring,
        vault_root: vault.path().to_path_buf(),
        canonical_root: canon.path().to_path_buf(),
        mode: ApplyMode::Apply,
    };
    let err = RunCycle::new().execute(inputs).expect_err("expected run-cycle to fail");
    assert!(matches!(err, RunCycleError::Assemble(_)), "got {err:?}");

    // Assembly failed before any apply → both roots are untouched.
    assert!(std::fs::read_dir(vault.path()).unwrap().next().is_none(), "vault must be empty");
    assert!(std::fs::read_dir(canon.path()).unwrap().next().is_none(), "canonical must be empty");
}

#[test]
fn run_cycle_corrupt_canonical_skips_derived_loudly() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    // A clean run first: MOC + index + canonical records exist.
    let first = run(
        &root,
        vault.path(),
        canon.path(),
        "manifests/article_evergreen.pipeline.toml",
        "fixtures/article_clean/input.md",
        "2026-05-04",
        &[],
    );
    assert!(first.succeeded());
    let moc_path = vault.path().join("10-Knowledge/Atlas/MOC-Index.md");
    let index_path = vault.path().join("60-Logs/knowledge-index.json");
    let moc_before = std::fs::read_to_string(&moc_path).unwrap();
    let index_before = std::fs::read_to_string(&index_path).unwrap();

    // Corrupt the canonical store out-of-band.
    std::fs::write(canon.path().join("broken.json"), "not valid json").unwrap();

    // Re-run: the main apply is idempotent (no failures), but the strict
    // canonical parse fails → derived rebuild is skipped loudly and the existing
    // MOC/index are NOT overwritten.
    let second = run(
        &root,
        vault.path(),
        canon.path(),
        "manifests/article_evergreen.pipeline.toml",
        "fixtures/article_clean/input.md",
        "2026-05-04",
        &[],
    );
    assert!(!second.succeeded());
    assert_eq!(second.apply.counts().failed, 0, "the main apply itself did not fail");
    let reason = second.derived_skipped_reason.as_deref().unwrap_or("");
    assert!(reason.contains("canonical store unparseable"), "got reason: {reason}");
    assert!(second.moc.is_none(), "MOC rebuild must be skipped");
    assert!(second.knowledge_index.is_none(), "index rebuild must be skipped");

    // The derived artifacts are untouched.
    assert_eq!(std::fs::read_to_string(&moc_path).unwrap(), moc_before, "MOC must not be overwritten");
    assert_eq!(
        std::fs::read_to_string(&index_path).unwrap(),
        index_before,
        "index must not be overwritten"
    );
}
