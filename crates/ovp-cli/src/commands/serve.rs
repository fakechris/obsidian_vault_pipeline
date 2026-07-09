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
    if ask_client.is_none() {
        eprintln!(
            "  ask:     NOT CONFIGURED — POST /api/ask answers 503. Build with \
             `--features anthropic` and set ANTHROPIC_API_KEY to enable."
        );
    }
    let config = ServeConfig {
        vault_root: args.vault_root,
        host: args.host,
        port: args.port,
        viz_dir: args.viz_dir,
        ask_client,
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
