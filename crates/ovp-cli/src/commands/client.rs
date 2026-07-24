//! Shared `ModelClient` construction for the CLI commands (`interpret-article`,
//! `run-cycle`). Replay is offline + HTTP-free; live is the capture path behind
//! the `anthropic` feature.

use std::path::Path;

use ovp_domain::ARTICLE_PROMPT_ID;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};

use crate::CliError;

/// Selects which `ModelClient` impl the CLI wires into `LLMInvoker`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientKind {
    /// `CachedModelClient(NeverCallsClient, ReplayOnly)` — looks up canned
    /// replies from `--cache-dir`; never hits the network. The default.
    Replay,
    /// `CachedModelClient(AnthropicBlockingClient, Record)` — calls the live API
    /// and captures each reply into `--cache-dir`. Requires `--features
    /// anthropic` and `ANTHROPIC_API_KEY`; errors with guidance otherwise.
    Live,
}

/// Build the `ModelClient` for the requested mode. The per-request
/// `cache_namespace` set by each prompt builder selects the right cassette dir,
/// so this single client serves both article and paper prompts.
pub fn build_client(kind: ClientKind, cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    match kind {
        ClientKind::Replay => {
            let cached =
                CachedModelClient::new(NeverCallsClient, cache_dir, ARTICLE_PROMPT_ID, CacheMode::ReplayOnly)
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
    use ovp_llm::{build_recording_live_client, resolve_api_key, LiveClientConfig};

    let cfg = LiveClientConfig::from_env()
        .map_err(|e| CliError::Io(format!("live provider config: {e}")))?;
    let key = resolve_api_key(|k| std::env::var(k).ok())
        .map_err(|e| CliError::Io(format!("anthropic client: {e}")))?;
    build_recording_live_client(&key, &cfg, cache_dir, ARTICLE_PROMPT_ID)
        .map_err(CliError::Io)
}

#[cfg(not(feature = "anthropic"))]
fn build_live_client(_cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    Err(CliError::Io(
        "--client live requires building with `--features anthropic` and a set \
         ANTHROPIC_API_KEY; the default build is replay-only. Rebuild: \
         `cargo run -p ovp-cli --features anthropic -- ... --client live`"
            .into(),
    ))
}
