//! End-to-end tests for the M7 review harness. Offline: replay-only cassette
//! client, tempdir vault + canonical + pack roots, no network, no API key.
//!
//! The harness itself never builds a client — the caller supplies the wiring —
//! so these tests build a cassette `ModelClient` exactly like the `run-cycle`
//! e2e tests do, and hand `ReviewRun::execute` a factory closure.

use std::path::{Path, PathBuf};

use ovp_app::AppWiring;
use ovp_core::{ApplyMode, RunId};
use ovp_domain::{ConceptRegistry, ARTICLE_PROMPT_ID};
use ovp_lint::Severity;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_review::{ReviewReport, ReviewRun, ReviewRunConfig};

fn repo_root() -> PathBuf {
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap(); // <root>/crates/ovp-review
    Path::new(&md).ancestors().nth(2).unwrap().to_path_buf()
}

fn cassette_wiring(root: &Path, input: &Path, date: &str) -> AppWiring {
    let client: Box<dyn ModelClient> = Box::new(
        CachedModelClient::new(
            NeverCallsClient,
            root.join("crates/ovp-domain/tests/cassettes"),
            ARTICLE_PROMPT_ID,
            CacheMode::ReplayOnly,
        )
        .unwrap(),
    );
    AppWiring::new(RunId::new("review"))
        .with_date_stamp(date)
        .with_area("ai")
        .with_input_path(input)
        .with_client("default_llm", client)
        .with_registry("default", ConceptRegistry::from_slugs(&[]))
}

/// Run a review of `input_rel` through `manifest_rel` against the given roots.
/// `input_rel` / `manifest_rel` are repo-relative; a non-existent path exercises
/// the missing-input / missing-manifest paths.
#[allow(clippy::too_many_arguments)]
fn review(
    out: &Path,
    vault: &Path,
    canon: &Path,
    input_rel: &str,
    manifest_rel: &str,
    date: &str,
    mode: ApplyMode,
    rag_query: Option<&str>,
    expected_dir: Option<PathBuf>,
) -> ReviewReport {
    let root = repo_root();
    let input = root.join(input_rel);
    let config = ReviewRunConfig {
        input_path: input.clone(),
        manifest_path: root.join(manifest_rel),
        vault_root: vault.to_path_buf(),
        canonical_root: canon.to_path_buf(),
        out_dir: out.to_path_buf(),
        run_id: "review".to_string(),
        rag_query: rag_query.map(str::to_string),
        rag_limit: 5,
        expected_dir,
        mode,
    };
    ReviewRun::execute(config, || -> Result<AppWiring, String> {
        Ok(cassette_wiring(&root, &input, date))
    })
    .expect("review pack should always be produced")
}

/// Convenience: review `article_clean` via the article + evergreen manifest.
fn review_article_clean(
    out: &Path,
    vault: &Path,
    canon: &Path,
    rag_query: Option<&str>,
    expected_dir: Option<PathBuf>,
) -> ReviewReport {
    review(
        out,
        vault,
        canon,
        "fixtures/article_clean/input.md",
        "manifests/article_evergreen.pipeline.toml",
        "2026-05-04",
        ApplyMode::Apply,
        rag_query,
        expected_dir,
    )
}

#[test]
fn article_clean_produces_full_review_pack() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review_article_clean(out.path(), vault.path(), canon.path(), None, None);
    assert!(report.review_passed(), "clean run should pass: {:?}", report.failure_reason());
    assert!(report.cycle_succeeded());
    // No expected-dir → no contract verdict to fold into the review.
    assert_eq!(report.contract_clean(), None);

    // Acceptance #1: the core pack files exist. (`generated/primary-note.md` is
    // produced on the article path here; the paper path produces one too — see
    // `paper_path_produces_pack_and_contract_verdict`.)
    for f in [
        "REVIEW.md",
        "input.md",
        "processor-chain.txt",
        "run-report.json",
        "apply-summary.txt",
        "files-written.txt",
        "lint.json",
        "lint.txt",
        "query-stats.json",
        "query-stats.txt",
        "canonical/summary.json",
        "generated/primary-note.md",
    ] {
        assert!(out.path().join(f).exists(), "missing review-pack file: {f}");
    }

    // No RAG query → no RAG artifacts.
    assert!(!out.path().join("rag-context.json").exists(), "RAG output without a query");
    // No expected-dir → no comparison.
    assert!(!out.path().join("comparison").exists(), "comparison without --expected-dir");
}

#[test]
fn review_md_has_processor_chain_and_status() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review_article_clean(out.path(), vault.path(), canon.path(), None, None);
    assert!(report.review_passed());

    // Acceptance #2: REVIEW.md names the chain nodes + the verdicts.
    let md = std::fs::read_to_string(out.path().join("REVIEW.md")).unwrap();
    assert!(md.contains("Processor chain"));
    assert!(md.contains("markdown_inbox"), "chain should list the inbox source");
    assert!(md.contains("evergreen_concept_writer"), "chain should list the evergreen writer");
    assert!(md.contains("Cycle (L4):") && md.contains("SUCCEEDED"), "cycle verdict should show success");
    assert!(md.contains("Review:") && md.contains("PASSED"), "review verdict should show pass");
    // The standalone chain file lists kinds + topo order too.
    let chain = std::fs::read_to_string(out.path().join("processor-chain.txt")).unwrap();
    assert!(chain.contains("source.markdown_inbox"), "chain file should show node kinds");
    assert!(chain.contains("topological order"));
}

#[test]
fn clean_run_is_lint_clean() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review_article_clean(out.path(), vault.path(), canon.path(), None, None);
    // Acceptance #3: no error-severity findings for a clean cycle.
    assert!(
        report.lint.passed(Severity::Error),
        "clean run should have no lint errors: {:?}",
        report.lint.findings
    );
}

#[test]
fn query_stats_report_concepts_and_index() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review_article_clean(out.path(), vault.path(), canon.path(), None, None);
    // Acceptance #4: concept count > 0 and the index is present.
    let stats = report.query_stats.expect("query stats should load");
    assert!(stats.concept_count > 0, "expected minted concepts");
    assert!(stats.index_present, "knowledge index should have been rebuilt");
    assert!(report.canonical.concept_count > 0);
    assert!(!report.canonical.slugs.is_empty());
}

#[test]
fn rag_outputs_produced_when_query_supplied() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review_article_clean(
        out.path(),
        vault.path(),
        canon.path(),
        Some("agent native product management"),
        None,
    );
    // Acceptance #5: RAG artifacts exist when --rag-query is supplied.
    assert!(report.rag.is_some(), "rag context should be built");
    assert!(out.path().join("rag-context.json").exists());
    assert!(out.path().join("rag-context.txt").exists());
}

#[test]
fn expected_dir_comparison_is_produced() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let expected = repo_root().join("fixtures/article_clean/expected");

    let report =
        review_article_clean(out.path(), vault.path(), canon.path(), None, Some(expected));
    // Acceptance #6: comparison artifacts exist + the contract engine ran.
    assert!(report.comparison.is_some());
    for f in ["comparison/summary.md", "comparison/frontmatter.diff", "comparison/interpretation.diff"] {
        assert!(out.path().join(f).exists(), "missing comparison artifact: {f}");
    }
    let cmp = report.comparison.clone().unwrap();
    let contract = cmp.contract.expect("contract.yaml present → engine should run");
    assert!(
        contract.must_passed + contract.must_failed > 0,
        "the contract engine should have evaluated MUST clauses"
    );
    // article_clean's frozen contract is MUST-clean, so the review passes.
    assert!(contract.must_clean, "article_clean should be MUST-clean: {:?}", contract.failures);
    assert_eq!(report.contract_clean(), Some(true));
    assert!(report.review_passed());
    // Expected files were discovered (contract.yaml / frontmatter.yaml / interpretation.md);
    // actual_files lists the full produced vault set, not just the primary note.
    assert!(cmp.expected_files.iter().any(|f| f == "contract.yaml"));
    assert!(cmp.actual_files.len() > 1, "actual_files should list all produced vault files");
}

#[test]
fn failing_contract_fails_review_even_when_cycle_succeeds() {
    // P1 regression: the cycle runs cleanly, but the output violates a frozen
    // MUST clause. The pack is still produced; the cycle reads SUCCEEDED; but
    // the overall review FAILS (and so would the CLI exit code). This is what
    // makes review-run a quality gate, not just an observability dump.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    // A minimal expected-dir whose contract MUST clause can never pass against
    // the real article_clean output (the title is not this).
    let expected = tempfile::tempdir().unwrap();
    std::fs::write(
        expected.path().join("contract.yaml"),
        "version: 1\nterminal_state: interpretation_produced\nmust:\n  - field: title\n    op: equals\n    value: \"THIS IS DELIBERATELY THE WRONG TITLE\"\n",
    )
    .unwrap();

    let report = review_article_clean(
        out.path(),
        vault.path(),
        canon.path(),
        None,
        Some(expected.path().to_path_buf()),
    );

    // The pipeline itself succeeded...
    assert!(report.cycle_succeeded(), "the cycle should run cleanly");
    // ...but the contract MUST failed, so the review does NOT pass.
    assert_eq!(report.contract_clean(), Some(false));
    assert!(!report.review_passed(), "a MUST-failing contract must fail the review");
    let cmp = report.comparison.clone().expect("comparison present");
    assert!(cmp.contract.as_ref().unwrap().must_failed > 0);
    assert!(
        report.failure_reason().unwrap().contains("contract"),
        "failure reason should name the contract: {:?}",
        report.failure_reason()
    );

    // The pack is still produced, and surfaces the split verdict.
    let md = std::fs::read_to_string(out.path().join("REVIEW.md")).unwrap();
    assert!(md.contains("Cycle (L4):") && md.contains("SUCCEEDED"), "cycle still SUCCEEDED");
    assert!(md.contains("Contract:") && md.contains("FAILED"), "contract FAILED shown");
    assert!(md.contains("Review:"), "review verdict shown");
}

#[test]
fn bad_manifest_fails_before_writing_but_still_packs() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mtmp = tempfile::tempdir().unwrap();
    let root = repo_root();
    let input = root.join("fixtures/article_clean/input.md");

    // Valid TOML, sections match — so the spec parses and the chain is captured
    // — but the sink kind doesn't exist, so assembly fails before any write.
    let manifest = mtmp.path().join("bad.pipeline.toml");
    std::fs::write(
        &manifest,
        r#"
            [pipeline]
            nodes = ["src", "snk"]
            edges = [["src", "snk"]]
            [assembly.src]
            kind = "source.markdown_inbox"
            [assembly.snk]
            kind = "sink.nonexistent"
        "#,
    )
    .unwrap();

    let config = ReviewRunConfig {
        input_path: input.clone(),
        manifest_path: manifest,
        vault_root: vault.path().to_path_buf(),
        canonical_root: canon.path().to_path_buf(),
        out_dir: out.path().to_path_buf(),
        run_id: "review".to_string(),
        rag_query: None,
        rag_limit: 5,
        expected_dir: None,
        mode: ApplyMode::Apply,
    };
    let report = ReviewRun::execute(config, || -> Result<AppWiring, String> {
        Ok(cassette_wiring(&root, &input, "2026-05-04"))
    })
    .expect("a pack must still be produced on a bad manifest");

    // Acceptance #7: failure status, nothing written to the stores, pack exists.
    assert!(!report.review_passed(), "a bad manifest must not pass");
    assert!(!report.cycle_succeeded(), "a bad manifest must not succeed");
    assert!(report.run.is_none(), "assembly failed → no run report");
    assert!(report.run_error.is_some(), "the assembly failure should be recorded");
    assert!(report.chain.is_some(), "the spec parsed, so the chain is still captured");
    assert!(report.files.vault.is_empty(), "no vault files: {:?}", report.files.vault);
    assert!(report.files.canonical.is_empty(), "no canonical files: {:?}", report.files.canonical);

    // P2: the harness creates the (empty) store roots so the read-back is well
    // defined, but writes no content into them.
    assert!(vault.path().is_dir() && canon.path().is_dir(), "store roots should exist (empty)");
    assert!(std::fs::read_dir(vault.path()).unwrap().next().is_none(), "vault root must be empty");
    assert!(std::fs::read_dir(canon.path()).unwrap().next().is_none(), "canonical root must be empty");

    assert!(out.path().join("REVIEW.md").exists(), "pack must exist on failure");
    assert!(out.path().join("run-report.json").exists());
    let md = std::fs::read_to_string(out.path().join("REVIEW.md")).unwrap();
    assert!(md.contains("Review:") && md.contains("FAILED"), "review verdict should show the failure");
}

#[test]
fn paper_path_produces_pack_and_contract_verdict() {
    // The paper path (unified manifest + paper fixture) is structurally
    // different from the article path: it files under 20-Areas/AI-Research/
    // Papers/ and mints no evergreens. The harness + the path-agnostic contract
    // subject must still produce a coherent pack + verdict.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let expected = repo_root().join("fixtures/paper_arxiv/expected");

    let report = review(
        out.path(),
        vault.path(),
        canon.path(),
        "fixtures/paper_arxiv/input.md",
        "manifests/unified.pipeline.toml",
        "2026-05-29",
        ApplyMode::Apply,
        None,
        Some(expected),
    );
    assert!(report.review_passed(), "paper review should pass: {:?}", report.failure_reason());
    assert!(report.cycle_succeeded(), "paper cycle should succeed");

    let note = report.primary_note.expect("paper note should be discovered");
    assert!(note.contains("20-Areas/AI-Research/Papers/"), "unexpected paper note path: {note}");
    assert!(out.path().join("generated/primary-note.md").exists());

    // The contract engine ran against the paper note via the path-agnostic
    // subject — and crucially the `source_kind: paper` clause is NOT a failure
    // (the regression the hardcoded "article" would have caused).
    let cmp = report.comparison.expect("comparison should be produced");
    let contract = cmp.contract.expect("paper contract should be evaluated");
    assert!(contract.must_passed + contract.must_failed > 0, "MUST clauses should be evaluated");
    assert!(
        !contract.failures.iter().any(|f| f.contains("source_kind")),
        "source_kind clause must pass for a paper note: {:?}",
        contract.failures
    );
    // The path-agnostic subject reads arxiv_id / authors too, so the paper
    // contract is fully MUST-clean (the regression the article-only subject
    // would have caused).
    assert!(contract.must_clean, "paper contract should be MUST-clean: {:?}", contract.failures);
}

#[test]
fn dry_run_produces_pack_without_writing() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review(
        out.path(),
        vault.path(),
        canon.path(),
        "fixtures/article_clean/input.md",
        "manifests/article_evergreen.pipeline.toml",
        "2026-05-04",
        ApplyMode::DryRun,
        None,
        None,
    );
    // A clean dry-run reports success but writes nothing to the stores.
    assert!(report.review_passed(), "a clean dry-run passes");
    assert!(report.cycle_succeeded());
    assert!(report.files.vault.is_empty(), "dry-run wrote vault files: {:?}", report.files.vault);
    assert!(report.files.canonical.is_empty(), "dry-run wrote canonical files");
    let summary = std::fs::read_to_string(out.path().join("apply-summary.txt")).unwrap();
    assert!(summary.contains("dry-run"), "apply-summary should note dry-run: {summary}");
    assert!(out.path().join("REVIEW.md").exists());
}

#[test]
fn missing_input_still_produces_pack() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let _report = review(
        out.path(),
        vault.path(),
        canon.path(),
        "fixtures/__does_not_exist__.md",
        "manifests/article_evergreen.pipeline.toml",
        "2026-05-04",
        ApplyMode::Apply,
        None,
        None,
    );
    // The pack is still produced; input.md records that the input was unreadable.
    assert!(out.path().join("REVIEW.md").exists());
    let input_copy = std::fs::read_to_string(out.path().join("input.md")).unwrap();
    assert!(input_copy.contains("not readable"), "input.md should note the missing input: {input_copy}");
}

#[test]
fn missing_manifest_still_produces_failure_pack() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();

    let report = review(
        out.path(),
        vault.path(),
        canon.path(),
        "fixtures/article_clean/input.md",
        "manifests/__does_not_exist__.toml",
        "2026-05-04",
        ApplyMode::Apply,
        None,
        None,
    );
    // A missing manifest is caught before the chain or cycle: failure status,
    // nothing written, but a pack is still produced.
    assert!(!report.review_passed());
    assert!(!report.cycle_succeeded());
    assert!(report.chain.is_none(), "no chain when the manifest can't be read");
    assert!(
        report.chain_error.as_deref().unwrap_or("").contains("reading manifest"),
        "chain_error should explain the missing manifest: {:?}",
        report.chain_error
    );
    assert!(report.files.vault.is_empty(), "nothing should be written: {:?}", report.files.vault);
    let chain_txt = std::fs::read_to_string(out.path().join("processor-chain.txt")).unwrap();
    assert!(chain_txt.contains("unavailable"));
    assert!(out.path().join("REVIEW.md").exists());
}
