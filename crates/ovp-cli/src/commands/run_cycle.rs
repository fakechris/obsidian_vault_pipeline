//! `run-cycle` — the L4 operational command. Thin shell over `ovp_run::RunCycle`:
//! parse the manifest, build the client + registry + wiring + paths, execute the
//! cycle, print the report, optionally dump it as JSON, and exit non-zero if it
//! did not succeed.

use std::path::PathBuf;

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_core::{ApplyMode, RunId};
use ovp_domain::ConceptRegistry;
use ovp_run::{RunCycle, RunCycleInputs, RunCycleReport};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

/// Default canonical-evergreen seed when no `--concept-registry` is supplied.
const DEFAULT_CANONICAL_SLUGS: &[&str] = &["ai-agent", "competitive-advantage"];

pub struct RunCycleArgs {
    pub manifest_path: PathBuf,
    pub input_path: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub cache_dir: PathBuf,
    pub concept_registry: Option<PathBuf>,
    pub run_id: String,
    pub date_stamp: String,
    pub client_kind: ClientKind,
    pub dry_run: bool,
    /// Optional path to dump the `RunCycleReport` JSON.
    pub report_path: Option<PathBuf>,
}

pub fn run(args: RunCycleArgs) -> Result<(), CliError> {
    let toml_str = std::fs::read_to_string(&args.manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", args.manifest_path.display()))
    })?;
    let spec = DomainPipelineSpec::parse(&toml_str).map_err(CliError::Assembly)?;

    let client = build_client(args.client_kind, &args.cache_dir)?;
    let registry = match &args.concept_registry {
        Some(path) => ConceptRegistry::load_from_file(path)
            .map_err(|e| CliError::Io(format!("loading concept registry: {e}")))?,
        None => ConceptRegistry::from_slugs(DEFAULT_CANONICAL_SLUGS),
    };

    // `area` defaults to "ai" (AppWiring's default); papers file under
    // AI-Research regardless, and run-cycle does not expose --area.
    let wiring = AppWiring::new(RunId::new(&args.run_id))
        .with_date_stamp(&args.date_stamp)
        .with_input_path(&args.input_path)
        .with_client("default_llm", client)
        .with_registry("default", registry);

    let mode = if args.dry_run { ApplyMode::DryRun } else { ApplyMode::Apply };
    let inputs = RunCycleInputs {
        spec,
        wiring,
        vault_root: args.vault_root,
        canonical_root: args.canonical_root,
        mode,
    };

    let report = RunCycle::new()
        .execute(inputs)
        .map_err(|e| CliError::Io(format!("run-cycle: {e}")))?;

    print_report(&report);

    if let Some(path) = &args.report_path {
        let json = serde_json::to_string_pretty(&report)
            .map_err(|e| CliError::Io(format!("serializing report: {e}")))?;
        std::fs::write(path, json)
            .map_err(|e| CliError::Io(format!("write {}: {e}", path.display())))?;
        println!("wrote {}", path.display());
    }

    // Loud failure: a run that didn't fully succeed exits non-zero.
    if !report.succeeded() {
        let reason = report
            .derived_skipped_reason
            .clone()
            .unwrap_or_else(|| "one or more ops failed".to_string());
        return Err(CliError::Io(format!("run-cycle did not succeed: {reason}")));
    }

    Ok(())
}

fn print_report(r: &RunCycleReport) {
    println!("run_id:            {}", r.run_id);
    println!("records_seen:      {}", r.records_seen);
    println!("records_forwarded: {}", r.records_forwarded_to_sinks);
    println!("records_dropped:   {}", r.records_dropped);
    println!("plan ops:          {}", r.ops_emitted);
    let a = r.apply.counts();
    println!("main apply:        applied={} skipped={} failed={}", a.applied, a.skipped, a.failed);
    match &r.moc {
        Some(m) => println!("moc:               applied={} skipped={} failed={}", m.applied, m.skipped, m.failed),
        None => println!("moc:               (skipped)"),
    }
    match &r.knowledge_index {
        Some(k) => println!("knowledge_index:   applied={} skipped={} failed={}", k.applied, k.skipped, k.failed),
        None => println!("knowledge_index:   (skipped)"),
    }
    if let Some(reason) = &r.derived_skipped_reason {
        println!("derived skipped:   {reason}");
    }
    println!("succeeded:         {}", r.succeeded());
}
