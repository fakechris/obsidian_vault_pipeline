//! `review-run` — the M7 E2E review command. It builds the same client,
//! registry, and wiring as `run-cycle` (the ONLY wiring construction here),
//! then hands a wiring factory plus config to `ovp_review::ReviewRun`, which
//! runs the L4 cycle and writes a deterministic review pack. Read / orchestrate
//! only: the only vault / canonical content writes go through `RunCycle`; this
//! command writes just the review pack (and creates the empty store-root dirs).
//! The wiring factory is called once, and only if the manifest parses, so a bad
//! manifest never builds a client or touches the stores.
//!
//! Exit code follows the overall *review* verdict, not just the cycle: a clean
//! run whose output violates its `--expected-dir` contract MUST clauses still
//! exits non-zero (the pack is written either way).

use std::path::PathBuf;

use ovp_app::AppWiring;
use ovp_core::{ApplyMode, RunId};
use ovp_domain::ConceptRegistry;
use ovp_review::{ReviewRun, ReviewRunConfig};

use crate::commands::client::{build_client, ClientKind};
use crate::commands::defaults::DEFAULT_CANONICAL_SLUGS;
use crate::CliError;

pub struct ReviewRunArgs {
    pub manifest_path: PathBuf,
    pub input_path: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub cache_dir: PathBuf,
    pub concept_registry: Option<PathBuf>,
    pub run_id: String,
    pub date_stamp: String,
    pub client_kind: ClientKind,
    pub out_dir: PathBuf,
    pub rag_query: Option<String>,
    pub rag_limit: usize,
    pub expected_dir: Option<PathBuf>,
    /// Preview only: the cycle applies nothing. Read-back / lint / comparison
    /// then reflect the CURRENT on-disk state, not a post-apply simulation.
    pub dry_run: bool,
}

pub fn run(args: ReviewRunArgs) -> Result<(), CliError> {
    let ReviewRunArgs {
        manifest_path,
        input_path,
        vault_root,
        canonical_root,
        cache_dir,
        concept_registry,
        run_id,
        date_stamp,
        client_kind,
        out_dir,
        rag_query,
        rag_limit,
        expected_dir,
        dry_run,
    } = args;

    // The wiring factory: a fresh (move-only) client + registry + paths, the
    // same way `run-cycle` builds them. `ovp-review` calls this at most once.
    let factory_run_id = run_id.clone();
    let factory_input = input_path.clone();
    let make_wiring = move || -> Result<AppWiring, String> {
        let client = build_client(client_kind, &cache_dir).map_err(|e| e.to_string())?;
        let registry = match &concept_registry {
            Some(path) => ConceptRegistry::load_from_file(path)
                .map_err(|e| format!("loading concept registry: {e}"))?,
            None => ConceptRegistry::from_slugs(DEFAULT_CANONICAL_SLUGS),
        };
        Ok(AppWiring::new(RunId::new(&factory_run_id))
            .with_date_stamp(&date_stamp)
            .with_input_path(&factory_input)
            .with_client("default_llm", client)
            .with_registry("default", registry))
    };

    let config = ReviewRunConfig {
        input_path,
        manifest_path,
        vault_root,
        canonical_root,
        out_dir: out_dir.clone(),
        run_id,
        rag_query,
        rag_limit,
        expected_dir,
        mode: if dry_run { ApplyMode::DryRun } else { ApplyMode::Apply },
    };

    let report = ReviewRun::execute(config, make_wiring)
        .map_err(|e| CliError::Io(format!("review-run: {e}")))?;

    // Human pointer. The pack is always written, pass or fail. Three verdicts:
    // the L4 cycle, the expected-dir contract, and the overall review gate.
    println!("review pack: {}", out_dir.display());
    println!("cycle:       {}", if report.cycle_succeeded() { "succeeded" } else { "FAILED" });
    match report.contract_clean() {
        Some(true) => println!("contract:    CLEAN"),
        Some(false) => println!("contract:    FAILED"),
        None => println!("contract:    (no expected-dir contract)"),
    }
    println!("review:      {}", if report.review_passed() { "PASSED" } else { "FAILED" });
    if let Some(note) = &report.primary_note {
        println!("primary note: {note}");
    }
    println!("concepts:    {}", report.canonical.concept_count);

    // Loud failure: a review that did not pass exits non-zero — whether the
    // cycle failed OR the output violated its expected contract. The pack still
    // exists for inspection either way.
    if !report.review_passed() {
        let reason = report.failure_reason().unwrap_or_else(|| "unknown".to_string());
        return Err(CliError::Io(format!("review-run: review did not pass: {reason}")));
    }
    Ok(())
}
