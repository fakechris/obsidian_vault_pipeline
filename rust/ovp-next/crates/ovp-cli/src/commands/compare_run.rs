//! `compare-run` — the M8 External E2E Comparator command. Builds the ovp-side
//! wiring (the same client + registry as `run-cycle`/`review-run` — the ONLY
//! wiring construction here) and a live Nowledge Mem HTTP client, then hands
//! both to `ovp_eval::CompareRun`, which runs both systems end-to-end and writes
//! a comparison pack. Nowledge Mem is an EXTERNAL reference system reached only
//! over HTTP; nothing in the trunk depends on it.
//!
//! Real-LLM (`--client live`) and the network call to Nowledge are explicit,
//! manual operations — this command is not part of normal CI.

use std::path::PathBuf;
use std::time::Duration;

use ovp_app::AppWiring;
use ovp_core::RunId;
use ovp_domain::ConceptRegistry;
use ovp_eval::{CompareConfig, CompareRun, LiveNowledgeClient};

use crate::commands::client::{build_client, ClientKind};
use crate::commands::defaults::DEFAULT_CANONICAL_SLUGS;
use crate::CliError;

pub struct CompareRunArgs {
    pub case_id: String,
    pub url: Option<String>,
    pub markdown_input: Option<PathBuf>,
    pub nowledge_base_url: String,
    pub nowledge_timeout_secs: u64,
    pub manifest_path: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub cache_dir: PathBuf,
    pub concept_registry: Option<PathBuf>,
    pub run_id: String,
    pub date_stamp: String,
    pub client_kind: ClientKind,
    pub out_dir: PathBuf,
    pub queries: Vec<String>,
    pub rag_limit: usize,
    pub search_limit: usize,
    pub space_id: String,
    pub grounding_threshold: f64,
    pub poll_interval_secs: u64,
    pub poll_max_attempts: u32,
    pub materialize_from_nowledge: bool,
}

/// The default fixed query set when none is supplied: the three probes the M8
/// spec calls for — main claims, reusable concepts, connections to prior knowledge.
fn default_queries() -> Vec<String> {
    vec![
        "What are the main claims of this article?".to_string(),
        "What reusable concepts does it introduce?".to_string(),
        "How does it connect to existing knowledge?".to_string(),
    ]
}

pub fn run(args: CompareRunArgs) -> Result<(), CliError> {
    // The ovp-side wiring factory: a fresh (move-only) client + registry, bound
    // to the input path the comparator resolves (a local --input, or a
    // materialized-from-Nowledge artifact). The comparator calls it at most once
    // and only when a usable ovp input exists.
    let factory_run_id = args.run_id.clone();
    let factory_date = args.date_stamp.clone();
    let factory_cache = args.cache_dir.clone();
    let factory_registry = args.concept_registry.clone();
    let client_kind = args.client_kind;
    let make_wiring = move |input: &std::path::Path| -> Result<AppWiring, String> {
        let client = build_client(client_kind, &factory_cache).map_err(|e| e.to_string())?;
        let registry = match &factory_registry {
            Some(path) => ConceptRegistry::load_from_file(path)
                .map_err(|e| format!("loading concept registry: {e}"))?,
            None => ConceptRegistry::from_slugs(DEFAULT_CANONICAL_SLUGS),
        };
        Ok(AppWiring::new(RunId::new(&factory_run_id))
            .with_date_stamp(&factory_date)
            .with_input_path(input)
            .with_client("default_llm", client)
            .with_registry("default", registry))
    };

    let nowledge = LiveNowledgeClient::new(
        &args.nowledge_base_url,
        Duration::from_secs(args.nowledge_timeout_secs),
    )
    .map_err(|e| CliError::Io(format!("building nowledge client: {e}")))?;

    let queries = if args.queries.is_empty() { default_queries() } else { args.queries.clone() };

    let config = CompareConfig {
        case_id: args.case_id,
        out_dir: args.out_dir.clone(),
        url: args.url,
        markdown_input: args.markdown_input,
        manifest_path: args.manifest_path,
        vault_root: args.vault_root,
        canonical_root: args.canonical_root,
        run_id: args.run_id,
        queries,
        rag_limit: args.rag_limit,
        space_id: args.space_id,
        search_limit: args.search_limit,
        poll_interval: Duration::from_secs(args.poll_interval_secs),
        poll_max_attempts: args.poll_max_attempts,
        grounding_threshold: args.grounding_threshold,
        materialize_from_nowledge: args.materialize_from_nowledge,
    };

    let report = CompareRun::execute(config, make_wiring, &nowledge)
        .map_err(|e| CliError::Io(format!("compare-run: {e}")))?;

    println!("comparison pack: {}", report.out_dir.display());
    println!("ovp-next:        {}", if report.ovp_available { "available" } else { "UNAVAILABLE" });
    println!("nowledge-mem:    {}", if report.nowledge_available { "available" } else { "UNAVAILABLE" });
    println!("findings:        {}", report.comparison.findings.len());
    for f in &report.comparison.findings {
        println!("  - {f}");
    }

    // This is an evaluation tool, not a gate: a partial pack is a useful result.
    // Fail only if NOTHING usable was produced (both sides unavailable).
    if !report.ovp_available && !report.nowledge_available {
        return Err(CliError::Io(
            "compare-run: both sides failed; see the pack for details".to_string(),
        ));
    }
    Ok(())
}
