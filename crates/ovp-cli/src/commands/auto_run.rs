//! `auto-run` — the L6 automation command. Thin shell over `ovp_auto::AutoRun`:
//! build the per-input wiring factory (the ONE place wiring is constructed — the
//! same way `run-cycle` does it), sweep the inbox, print the operational report,
//! and exit non-zero if any cycle failed or lint failed at the threshold.
//!
//! `ovp-auto` owns discovery + the loop + lint + the report; this command owns
//! only the wiring. Neither reimplements the L4 cycle.

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_auto::{AutoReport, AutoRun, SweepOptions};
use ovp_core::{ApplyMode, RunId};
use ovp_domain::ConceptRegistry;
use ovp_lint::Severity;
use ovp_run::RunCycleInputs;

use crate::commands::client::{build_client, ClientKind};
use crate::commands::defaults::DEFAULT_CANONICAL_SLUGS;
use crate::CliError;

pub struct AutoRunArgs {
    pub inbox_root: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub manifest_path: PathBuf,
    pub cache_dir: PathBuf,
    pub concept_registry: Option<PathBuf>,
    pub run_id: String,
    pub date_stamp: String,
    pub client_kind: ClientKind,
    pub lint_threshold: Severity,
    pub dry_run: bool,
    pub json: bool,
}

pub fn run(args: AutoRunArgs) -> Result<(), CliError> {
    let AutoRunArgs {
        inbox_root,
        vault_root,
        canonical_root,
        manifest_path,
        cache_dir,
        concept_registry,
        run_id,
        date_stamp,
        client_kind,
        lint_threshold,
        dry_run,
        json,
    } = args;

    let toml = std::fs::read_to_string(&manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", manifest_path.display()))
    })?;
    // Fail fast on a bad manifest before sweeping anything. The factory re-parses
    // per input because `DomainPipelineSpec` is not `Clone`.
    DomainPipelineSpec::parse(&toml).map_err(CliError::Assembly)?;

    let registry = match &concept_registry {
        Some(path) => ConceptRegistry::load_from_file(path)
            .map_err(|e| CliError::Io(format!("loading concept registry: {e}")))?,
        None => ConceptRegistry::from_slugs(DEFAULT_CANONICAL_SLUGS),
    };

    let mode = if dry_run { ApplyMode::DryRun } else { ApplyMode::Apply };
    // Per-input cycle roots (cloned per call); `vault_root`/`canonical_root`
    // themselves go to the lint pass via SweepOptions below.
    let vault_for_inputs = vault_root.clone();
    let canon_for_inputs = canonical_root.clone();

    // The per-input factory: a fresh (move-only) client + a cloned registry + a
    // re-parsed spec for each input. This is the only wiring construction;
    // `ovp-auto` calls it and runs the cycle.
    let make_inputs = |input: &Path| -> Result<RunCycleInputs, String> {
        let spec = DomainPipelineSpec::parse(&toml).map_err(|e| e.to_string())?;
        let client = build_client(client_kind, &cache_dir).map_err(|e| e.to_string())?;
        let stem = input.file_stem().and_then(|s| s.to_str()).unwrap_or("input");
        let wiring = AppWiring::new(RunId::new(format!("{run_id}-{stem}")))
            .with_date_stamp(&date_stamp)
            .with_input_path(input)
            .with_client("default_llm", client)
            .with_registry("default", registry.clone());
        Ok(RunCycleInputs {
            spec,
            wiring,
            vault_root: vault_for_inputs.clone(),
            canonical_root: canon_for_inputs.clone(),
            mode,
        })
    };

    let opts = SweepOptions { inbox_root, vault_root, canonical_root, lint_threshold };
    // Per-file flushed progress: the sweep runs the full L4 cycle (LLM calls) on
    // each input and can take minutes per file, so stream `[i/total] <file>`
    // before each one — the sweep crate stays print-free (callback pattern).
    let mut on_progress = |i: usize, total: usize, label: &str| {
        sayln!("  [{i}/{total}] {label}");
    };
    let report = AutoRun::sweep_with_progress(&opts, make_inputs, &mut on_progress)
        .map_err(|e| CliError::Io(format!("auto-run: {e}")))?;

    if json {
        let json = serde_json::to_string_pretty(&report)
            .map_err(|e| CliError::Io(format!("serializing report: {e}")))?;
        println!("{json}");
    } else {
        print_report(&report);
    }

    // Loud failure: any failed cycle or a failed lint gate exits non-zero.
    if !report.succeeded() {
        return Err(CliError::Io(format!(
            "auto-run did not succeed: {} cycle failure(s); lint {}",
            report.cycles_failed(),
            if report.lint_passed { "passed" } else { "failed at threshold" },
        )));
    }
    Ok(())
}

fn print_report(r: &AutoReport) {
    println!("considered:        {}", r.considered);
    println!("cycles succeeded:  {}", r.cycles_succeeded());
    println!("cycles failed:     {}", r.cycles_failed());
    println!("skipped:           {}", r.skipped.len());
    for c in &r.cycles {
        let status = if c.succeeded { "ok" } else { "FAIL" };
        match &c.reason {
            Some(reason) => println!("  [{status}] {}  ({reason})", c.input),
            None => println!("  [{status}] {}", c.input),
        }
    }
    for s in &r.skipped {
        println!("  [skip] {}  ({})", s.input, s.reason);
    }
    println!(
        "lint:              {} ({} error, {} warning, {} info; threshold {})",
        if r.lint_passed { "passed" } else { "FAILED" },
        r.lint.count(Severity::Error),
        r.lint.count(Severity::Warning),
        r.lint.count(Severity::Info),
        r.lint_threshold,
    );
    println!("succeeded:         {}", r.succeeded());
}
