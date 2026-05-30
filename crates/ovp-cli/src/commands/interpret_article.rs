use std::path::PathBuf;

use ovp_app::{AppWiring, DomainPipelineSpec, GraphAssembler};
use ovp_core::RunId;
use ovp_domain::ConceptRegistry;
use ovp_llm::ModelClient;

use crate::commands::client::{build_client, ClientKind};
use crate::commands::defaults::DEFAULT_CANONICAL_SLUGS;
use crate::CliError;

pub struct InterpretArticleArgs {
    pub manifest_path: PathBuf,
    pub input_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub run_id: String,
    pub client_kind: ClientKind,
    pub area: String,
    pub date_stamp: String,
    /// Optional path to a ConceptRegistry JSON. When absent, a small
    /// default seed is used.
    pub concept_registry: Option<PathBuf>,
}

pub fn run(args: InterpretArticleArgs) -> Result<(), CliError> {
    let toml_str = std::fs::read_to_string(&args.manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", args.manifest_path.display()))
    })?;
    let spec = DomainPipelineSpec::parse(&toml_str).map_err(CliError::Assembly)?;
    let run_id = RunId::new(&args.run_id);

    // Namespace = ARTICLE_PROMPT_ID = "article_interpret/v1". Schema bump
    // changes the const → namespace dir changes → old cassettes don't
    // masquerade as new-schema responses. See invariant docs.
    let client: Box<dyn ModelClient> = build_client(args.client_kind, &args.cache_dir)?;

    // ConceptResolver consumes a ConceptRegistry (not raw CLI constants):
    // loaded from --concept-registry if given, else a default seed.
    let registry = match &args.concept_registry {
        Some(path) => ConceptRegistry::load_from_file(path)
            .map_err(|e| CliError::Io(format!("loading concept registry: {e}")))?,
        None => ConceptRegistry::from_slugs(DEFAULT_CANONICAL_SLUGS),
    };

    // Topology + node kinds come from the manifest; the live ModelClient,
    // ConceptRegistry, run id, dates, and input path come from AppWiring,
    // bound by the names the manifest's [assembly] config references.
    let wiring = AppWiring::new(run_id)
        .with_date_stamp(&args.date_stamp)
        .with_area(&args.area)
        .with_input_path(&args.input_path)
        .with_client("default_llm", client)
        .with_registry("default", registry);

    let runner = GraphAssembler::with_domain_nodes()
        .assemble(&spec, wiring)
        .map_err(CliError::Assembly)?;

    let report = runner.run().map_err(CliError::Core)?;

    let plans_dir = args.out_dir.join("plans");
    let events_dir = args.out_dir.join("events");
    std::fs::create_dir_all(&plans_dir)
        .map_err(|e| CliError::Io(format!("create {}: {e}", plans_dir.display())))?;
    std::fs::create_dir_all(&events_dir)
        .map_err(|e| CliError::Io(format!("create {}: {e}", events_dir.display())))?;

    let plan_path = plans_dir.join(format!("{}.json", report.run_id.as_str()));
    let events_path = events_dir.join(format!("{}.jsonl", report.run_id.as_str()));

    let plan_json = serde_json::to_string_pretty(&report.write_plan)
        .map_err(|e| CliError::Io(format!("serializing plan: {e}")))?;
    std::fs::write(&plan_path, plan_json)
        .map_err(|e| CliError::Io(format!("write {}: {e}", plan_path.display())))?;

    let mut events_jsonl = String::new();
    for ev in &report.events {
        let line = serde_json::to_string(ev)
            .map_err(|e| CliError::Io(format!("serializing event: {e}")))?;
        events_jsonl.push_str(&line);
        events_jsonl.push('\n');
    }
    std::fs::write(&events_path, events_jsonl)
        .map_err(|e| CliError::Io(format!("write {}: {e}", events_path.display())))?;

    println!("run_id:            {}", report.run_id.as_str());
    println!("records_seen:      {}", report.records_seen);
    println!("records_forwarded: {}", report.records_forwarded_to_sinks);
    println!("records_dropped:   {}", report.records_dropped);
    println!("write_plan ops:    {}", report.write_plan.len());
    println!("events:            {}", report.events.len());
    println!();
    println!("wrote {}", plan_path.display());
    println!("wrote {}", events_path.display());

    Ok(())
}
