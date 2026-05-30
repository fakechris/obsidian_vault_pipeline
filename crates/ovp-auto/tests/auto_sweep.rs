//! End-to-end for the L6 automation sweep: a real per-input wiring factory
//! (replay client, offline) drives actual run-cycles, then lint gates the
//! result. Tempdirs only — no real vault, no network, no API key.

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_auto::{AutoRun, SweepOptions};
use ovp_core::{ApplyMode, RunId};
use ovp_domain::{ConceptRegistry, ARTICLE_PROMPT_ID};
use ovp_lint::Severity;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_run::RunCycleInputs;

fn repo_root() -> PathBuf {
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap(); // <root>/crates/ovp-auto
    Path::new(&md).ancestors().nth(2).unwrap().to_path_buf()
}

/// The per-input factory the CLI would build: parse the manifest, wire a fresh
/// replay client (the model client is move-only, so one per input), bind the
/// registry, set the input path, and hand back `RunCycleInputs`.
fn factory(
    root: PathBuf,
    vault: PathBuf,
    canon: PathBuf,
) -> impl FnMut(&Path) -> Result<RunCycleInputs, String> {
    move |input: &Path| {
        let toml = std::fs::read_to_string(root.join("manifests/article_evergreen.pipeline.toml"))
            .map_err(|e| e.to_string())?;
        let spec = DomainPipelineSpec::parse(&toml).map_err(|e| e.to_string())?;
        let client: Box<dyn ModelClient> = Box::new(
            CachedModelClient::new(
                NeverCallsClient,
                root.join("crates/ovp-domain/tests/cassettes"),
                ARTICLE_PROMPT_ID,
                CacheMode::ReplayOnly,
            )
            .map_err(|e| e.to_string())?,
        );
        let stem = input.file_stem().and_then(|s| s.to_str()).unwrap_or("auto");
        let wiring = AppWiring::new(RunId::new(format!("auto-{stem}")))
            .with_date_stamp("2026-05-04")
            .with_area("ai")
            .with_input_path(input)
            .with_client("default_llm", client)
            .with_registry("default", ConceptRegistry::from_slugs(&[]));
        Ok(RunCycleInputs {
            spec,
            wiring,
            vault_root: vault.clone(),
            canonical_root: canon.clone(),
            mode: ApplyMode::Apply,
        })
    }
}

fn opts(inbox: &Path, vault: &Path, canon: &Path) -> SweepOptions {
    SweepOptions {
        inbox_root: inbox.to_path_buf(),
        vault_root: vault.to_path_buf(),
        canonical_root: canon.to_path_buf(),
        lint_threshold: Severity::Error,
    }
}

#[test]
fn sweep_runs_cycle_per_input_then_lints_clean() {
    let root = repo_root();
    let inbox = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    // Two replayable inputs (same fixture content → same cassette).
    let src = std::fs::read_to_string(root.join("fixtures/article_clean/input.md")).unwrap();
    std::fs::write(inbox.path().join("a.md"), &src).unwrap();
    std::fs::write(inbox.path().join("b.md"), &src).unwrap();

    let report = AutoRun::sweep(
        &opts(inbox.path(), vault.path(), canon.path()),
        factory(root.clone(), vault.path().to_path_buf(), canon.path().to_path_buf()),
    )
    .unwrap();

    assert_eq!(report.considered, 2);
    assert_eq!(report.cycles.len(), 2);
    assert_eq!(report.cycles_succeeded(), 2, "cycles: {:?}", report.cycles);
    assert!(report.skipped.is_empty());
    assert!(report.lint_passed, "clean run-cycle output should pass lint: {:?}", report.lint.findings);
    assert!(report.succeeded());

    // L4 actually wrote the derived artifacts.
    assert!(vault.path().join("10-Knowledge/Atlas/MOC-Index.md").exists());
    assert!(vault.path().join("60-Logs/knowledge-index.json").exists());
}

#[test]
fn second_sweep_is_idempotent_and_still_succeeds() {
    let root = repo_root();
    let inbox = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let src = std::fs::read_to_string(root.join("fixtures/article_clean/input.md")).unwrap();
    std::fs::write(inbox.path().join("a.md"), &src).unwrap();

    let first = AutoRun::sweep(
        &opts(inbox.path(), vault.path(), canon.path()),
        factory(root.clone(), vault.path().to_path_buf(), canon.path().to_path_buf()),
    )
    .unwrap();
    assert!(first.succeeded(), "first sweep: {:?}", first.cycles);

    // Re-sweep the same roots: L4 idempotence means nothing new lands, and the
    // cycle still reports success (skipped == clean).
    let second = AutoRun::sweep(
        &opts(inbox.path(), vault.path(), canon.path()),
        factory(root.clone(), vault.path().to_path_buf(), canon.path().to_path_buf()),
    )
    .unwrap();
    assert!(second.succeeded(), "second sweep: {:?}", second.cycles);
    assert_eq!(second.cycles_succeeded(), 1);
    assert!(second.lint_passed);
}
