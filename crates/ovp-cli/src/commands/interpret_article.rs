use std::path::PathBuf;

use ovp_core::{GraphRunner, PipelineManifest, RunId};
use ovp_domain::{
    ArticleParser, ArticleVaultPlanSink, DomainBody, LLMInvoker, MarkdownInboxSource, PromptBuilder,
};
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};

use crate::CliError;

/// Selects which `ModelClient` impl the CLI wires into `LLMInvoker`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientKind {
    /// `CachedModelClient(NeverCallsClient, ReplayOnly)` — looks up
    /// canned replies from `--cache-dir`; never hits the network.
    /// Used by integration tests and CI.
    Replay,
    /// `CachedModelClient(NeverCallsClient, Record)` — same lookup path
    /// but errors loudly if the cache is missing. Used for sanity
    /// checking that cassettes are present before a CI run.
    RecordWithoutNetwork,
}

pub struct InterpretArticleArgs {
    pub manifest_path: PathBuf,
    pub input_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub run_id: String,
    pub client_kind: ClientKind,
    pub area: String,
    pub date_stamp: String,
}

pub fn run(args: InterpretArticleArgs) -> Result<(), CliError> {
    let toml_str = std::fs::read_to_string(&args.manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", args.manifest_path.display()))
    })?;
    let manifest = PipelineManifest::parse(&toml_str).map_err(|e| CliError::Core(e.into()))?;
    let run_id = RunId::new(&args.run_id);

    let mode = match args.client_kind {
        ClientKind::Replay => CacheMode::ReplayOnly,
        ClientKind::RecordWithoutNetwork => CacheMode::Record,
    };
    let cached = CachedModelClient::new(NeverCallsClient, &args.cache_dir, mode).map_err(|e| {
        CliError::Io(format!("opening cache dir `{}`: {e}", args.cache_dir.display()))
    })?;
    let client: Box<dyn ModelClient> = Box::new(cached);

    let mut runner: GraphRunner<DomainBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source(
        "markdown_inbox",
        MarkdownInboxSource::new("markdown_inbox", run_id.clone(), &args.input_path),
    );
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform(
        "article_parser",
        ArticleParser::new("article_parser", &args.area, &args.date_stamp),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()),
    );

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
