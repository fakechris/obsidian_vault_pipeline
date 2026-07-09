//! `serve` — start the OVP console HTTP server.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use ovp_server::{run_server, AskClientFactory, ServeConfig};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct ServeArgs {
    pub vault_root: PathBuf,
    pub host: String,
    pub port: u16,
    pub viz_dir: Option<PathBuf>,
}

pub fn run(args: ServeArgs) -> Result<(), CliError> {
    let ask_client = ask_client_factory(&args.vault_root);
    match &ask_client {
        None => eprintln!(
            "  ask:     NOT CONFIGURED — POST /api/ask answers 503. Build with \
             `--features anthropic` and set ANTHROPIC_API_KEY to enable."
        ),
        // Fail loud at startup, not on the first ask: with a key present but
        // another live setting invalid (ANTHROPIC_BASE_URL, OVP_LLM_MAX_TOKENS,
        // OVP_LLM_TIMEOUT_SECS…) every request would 502 with a generic error
        // (codex review P2). Building one client validates the shared live
        // config the same way `ovp2 ask --client live` does.
        Some(factory) => {
            factory().map_err(|e| {
                CliError::Io(format!("ask client configuration invalid: {e}"))
            })?;
        }
    }
    let config = ServeConfig {
        vault_root: args.vault_root,
        host: args.host,
        port: args.port,
        viz_dir: args.viz_dir,
        ask_client,
        // Server defaults: the ask guard derives from OVP_LLM_TIMEOUT_SECS
        // (the same env the live client reads), the in-flight cap from
        // DEFAULT_MAX_CONCURRENT_ASKS.
        ask_timeout: None,
        max_concurrent_asks: None,
    };
    run_server(config).map_err(CliError::Io)
}

/// LLM client factory for `POST /api/ask` — resolved from the environment
/// the same way `ovp2 ask --client live` does: the live transport needs the
/// `anthropic` build feature AND a non-empty `ANTHROPIC_API_KEY`. Without
/// either the server gets `None` and ask answers 503 "llm not configured".
/// The client records into the same cassette dir the ask command uses, so
/// repeated identical questions replay instead of re-calling the provider.
fn ask_client_factory(vault_root: &Path) -> Option<AskClientFactory> {
    if !cfg!(feature = "anthropic") {
        return None;
    }
    let key_present = std::env::var("ANTHROPIC_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    if !key_present {
        return None;
    }
    let cache_dir = vault_root.join(".ovp/cassettes/ask");
    Some(Arc::new(move || {
        build_client(ClientKind::Live, &cache_dir).map_err(|e| e.to_string())
    }))
}
