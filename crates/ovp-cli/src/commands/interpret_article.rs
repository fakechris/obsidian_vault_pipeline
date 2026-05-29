use std::path::{Path, PathBuf};

use ovp_core::{GraphRunner, PipelineManifest, RunId};
use ovp_domain::{
    ArticleParser, ArticleVaultPlanSink, ConceptRegistry, ConceptResolver, DomainBody, LLMInvoker,
    MarkdownInboxSource, PromptBuilder, SourceResolver, ARTICLE_PROMPT_ID,
};

/// Default canonical-evergreen seed used when no `--concept-registry`
/// file is supplied. Two entries cover the article_mixed_lang MUST
/// clauses. Real runs point `--concept-registry` at a registry JSON or
/// (future) scan the vault's evergreen dir.
const DEFAULT_CANONICAL_SLUGS: &[&str] = &["ai-agent", "competitive-advantage"];
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};

use crate::CliError;

/// Selects which `ModelClient` impl the CLI wires into `LLMInvoker`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientKind {
    /// `CachedModelClient(NeverCallsClient, ReplayOnly)` — looks up
    /// canned replies from `--cache-dir`; never hits the network.
    /// Used by integration tests and CI. The default.
    Replay,
    /// `CachedModelClient(AnthropicBlockingClient, Record)` — calls the
    /// live API and captures each reply into `--cache-dir` so future
    /// replay runs hit the cassette. Requires building with
    /// `--features anthropic` and `ANTHROPIC_API_KEY` set. On a default
    /// build this errors with guidance rather than silently degrading.
    Live,
}

/// Build the `ModelClient` for the requested mode. Replay never touches
/// the network. Live is the capture path (record-on-miss into the
/// cassette dir); it's only real when the `anthropic` feature is built.
fn build_client(kind: ClientKind, cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    match kind {
        ClientKind::Replay => {
            let cached = CachedModelClient::new(
                NeverCallsClient,
                cache_dir,
                ARTICLE_PROMPT_ID,
                CacheMode::ReplayOnly,
            )
            .map_err(|e| {
                CliError::Io(format!("opening cache dir `{}`: {e}", cache_dir.display()))
            })?;
            Ok(Box::new(cached))
        }
        ClientKind::Live => build_live_client(cache_dir),
    }
}

#[cfg(feature = "anthropic")]
fn build_live_client(cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    use ovp_llm::AnthropicBlockingClient;
    let live = AnthropicBlockingClient::from_env()
        .map_err(|e| CliError::Io(format!("anthropic client: {e}")))?;
    // Record mode: cache-hit replays, cache-miss calls live + persists.
    // Same namespace (ARTICLE_PROMPT_ID) the replay path reads from, so a
    // capture run leaves a cassette future replay runs will find.
    let cached = CachedModelClient::new(live, cache_dir, ARTICLE_PROMPT_ID, CacheMode::Record)
        .map_err(|e| CliError::Io(format!("opening cache dir `{}`: {e}", cache_dir.display())))?;
    Ok(Box::new(cached))
}

#[cfg(not(feature = "anthropic"))]
fn build_live_client(_cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    Err(CliError::Io(
        "--client live requires building with `--features anthropic` and a set \
         ANTHROPIC_API_KEY; the default build is replay-only. Rebuild: \
         `cargo run -p ovp-cli --features anthropic -- interpret-article --client live ...`"
            .into(),
    ))
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
    /// Optional path to a ConceptRegistry JSON. When absent, a small
    /// default seed is used.
    pub concept_registry: Option<PathBuf>,
}

pub fn run(args: InterpretArticleArgs) -> Result<(), CliError> {
    let toml_str = std::fs::read_to_string(&args.manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", args.manifest_path.display()))
    })?;
    let manifest = PipelineManifest::parse(&toml_str).map_err(|e| CliError::Core(e.into()))?;
    let run_id = RunId::new(&args.run_id);

    // Namespace = ARTICLE_PROMPT_ID = "article_interpret/v1". Schema bump
    // changes the const → namespace dir changes → old cassettes don't
    // masquerade as new-schema responses. See invariant docs.
    let client: Box<dyn ModelClient> = build_client(args.client_kind, &args.cache_dir)?;

    let mut runner: GraphRunner<DomainBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source(
        "markdown_inbox",
        MarkdownInboxSource::new("markdown_inbox", run_id.clone(), &args.input_path),
    );
    runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
    runner.register_transform(
        "article_parser",
        ArticleParser::new("article_parser", &args.area, &args.date_stamp),
    );
    // ConceptResolver consumes a ConceptRegistry (not raw CLI constants):
    // loaded from --concept-registry if given, else a default seed.
    let registry = match &args.concept_registry {
        Some(path) => ConceptRegistry::load_from_file(path)
            .map_err(|e| CliError::Io(format!("loading concept registry: {e}")))?,
        None => ConceptRegistry::from_slugs(DEFAULT_CANONICAL_SLUGS),
    };
    runner.register_transform(
        "concept_resolver",
        ConceptResolver::new("concept_resolver", registry),
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
