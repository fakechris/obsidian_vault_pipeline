//! Health-check tests for `ovp-lint`. A clean `run-cycle` output lints clean at
//! the error gate; targeted corruptions surface specific findings. Offline;
//! tempdirs only.

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_core::{ApplyMode, RunId};
use ovp_domain::{ConceptRegistry, ARTICLE_PROMPT_ID};
use ovp_lint::{Lint, Severity};
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_run::{RunCycle, RunCycleInputs};

fn repo_root() -> PathBuf {
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap(); // <root>/crates/ovp-lint
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

/// Run a real run-cycle into the given roots so we have a coherent vault to lint.
fn seed_run_cycle(root: &Path, vault: &Path, canon: &Path) {
    let toml =
        std::fs::read_to_string(root.join("manifests/article_evergreen.pipeline.toml")).unwrap();
    let spec = DomainPipelineSpec::parse(&toml).unwrap();
    let wiring = AppWiring::new(RunId::new("rc"))
        .with_date_stamp("2026-05-04")
        .with_area("ai")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let report = RunCycle::new()
        .execute(RunCycleInputs {
            spec,
            wiring,
            vault_root: vault.to_path_buf(),
            canonical_root: canon.to_path_buf(),
            mode: ApplyMode::Apply,
        })
        .unwrap();
    assert!(report.succeeded());
}

fn codes(report: &ovp_lint::LintReport) -> Vec<&str> {
    report.findings.iter().map(|f| f.code.as_str()).collect()
}

#[test]
fn clean_run_cycle_output_passes_error_gate() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    seed_run_cycle(&root, vault.path(), canon.path());

    let report = Lint::check(vault.path(), canon.path());
    assert!(
        report.passed(Severity::Error),
        "a clean run-cycle output must have no error findings, got: {:?}",
        report.findings
    );
    // No missing notes, no stale/absent index or MOC, no broken wikilinks.
    let cs = codes(&report);
    assert!(!cs.contains(&"evergreen.missing_note"), "{cs:?}");
    assert!(!cs.contains(&"index.stale"), "{cs:?}");
    assert!(!cs.contains(&"index.absent"), "{cs:?}");
    assert!(!cs.contains(&"moc.stale"), "{cs:?}");
    assert!(!cs.contains(&"wikilink.broken"), "{cs:?}");
}

#[test]
fn missing_evergreen_note_is_an_error() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    seed_run_cycle(&root, vault.path(), canon.path());

    // Delete one evergreen note out from under the canonical record.
    let victim = vault.path().join("10-Knowledge/Evergreen/agent-native-product-management.md");
    assert!(victim.exists());
    std::fs::remove_file(&victim).unwrap();

    let report = Lint::check(vault.path(), canon.path());
    assert!(!report.passed(Severity::Error), "a missing note must fail the error gate");
    let missing: Vec<_> = report
        .findings
        .iter()
        .filter(|f| f.code == "evergreen.missing_note")
        .collect();
    assert_eq!(missing.len(), 1);
    assert_eq!(missing[0].location.as_deref(), Some("agent-native-product-management"));
    assert_eq!(missing[0].severity, Severity::Error);
}

#[test]
fn stale_index_is_flagged() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    seed_run_cycle(&root, vault.path(), canon.path());

    // Drift the persisted index without rebuilding it.
    let index_path = vault.path().join("60-Logs/knowledge-index.json");
    let mut raw = std::fs::read_to_string(&index_path).unwrap();
    raw.push('\n'); // any byte change makes it differ from a fresh rebuild
    std::fs::write(&index_path, raw).unwrap();

    let report = Lint::check(vault.path(), canon.path());
    assert!(codes(&report).contains(&"index.stale"), "got: {:?}", report.findings);
}

#[test]
fn broken_wikilink_is_flagged() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    seed_run_cycle(&root, vault.path(), canon.path());

    // Add a note with a wikilink to a nonexistent concept/note.
    let note = vault.path().join("20-Areas/AI-Research/Topics/2026-05/extra.md");
    std::fs::write(&note, "See [[totally-nonexistent-concept]] for details.\n").unwrap();

    let report = Lint::check(vault.path(), canon.path());
    let broken: Vec<_> =
        report.findings.iter().filter(|f| f.code == "wikilink.broken").collect();
    assert_eq!(broken.len(), 1, "got: {:?}", report.findings);
    assert!(broken[0].detail.contains("totally-nonexistent-concept"));
}

#[test]
fn corrupt_canonical_surfaces_as_finding_not_panic() {
    let root = repo_root();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    seed_run_cycle(&root, vault.path(), canon.path());
    std::fs::write(canon.path().join("broken.json"), "not json").unwrap();

    // Lint must not abort: the load failure becomes a single error finding.
    let report = Lint::check(vault.path(), canon.path());
    assert!(!report.passed(Severity::Error));
    assert_eq!(report.findings.len(), 1);
    assert_eq!(report.findings[0].code, "canonical.unparseable");
}
