//! `mcp` — start the OVP MCP stdio server.

use std::path::{Path, PathBuf};

use ovp_mcp::{AskClientFactory, McpConfig, run_mcp};

use crate::CliError;
use crate::commands::client::{ClientKind, build_client};

pub struct McpArgs {
    pub vault_root: PathBuf,
}

pub fn run(args: McpArgs) -> Result<(), CliError> {
    let ask_client = ask_client_factory(&args.vault_root);
    // stdout is the JSON-RPC channel — operator-facing notes go to stderr.
    match &ask_client {
        None => eprintln!(
            "ovp-mcp: ask NOT CONFIGURED — the `ask` tool will answer with a \
             configuration error. Build with `--features anthropic` and set \
             ANTHROPIC_API_KEY to enable."
        ),
        // Same fail-loud-at-startup contract as `serve`: a present key with
        // an invalid live setting must not surface as a generic error on the
        // first ask.
        Some(factory) => {
            factory()
                .map_err(|e| CliError::Io(format!("ask client configuration invalid: {e}")))?;
        }
    }
    let config = McpConfig {
        vault_root: args.vault_root,
        ask_client,
    };
    run_mcp(config).map_err(CliError::Io)
}

/// Same environment resolution as `serve`'s ask factory: live transport
/// needs the `anthropic` build feature AND a non-empty ANTHROPIC_API_KEY;
/// the client records into the shared ask cassette dir so repeated
/// identical questions replay.
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
    Some(std::sync::Arc::new(move || {
        build_client(ClientKind::Live, &cache_dir).map_err(|e| e.to_string())
    }))
}
