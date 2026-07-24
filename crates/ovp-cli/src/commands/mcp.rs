//! `mcp` — start the OVP MCP stdio server.

use std::path::PathBuf;

use ovp_server::{providers_ask_client_factory, LLM_NOT_CONFIGURED};
use ovp_mcp::{McpConfig, run_mcp};

use crate::CliError;

pub struct McpArgs {
    pub vault_root: PathBuf,
}

pub fn run(args: McpArgs) -> Result<(), CliError> {
    // Same providers-aware factory as `serve` / desktop: re-reads
    // `.ovp/providers.toml` each ask; no set_var.
    let ask_client = providers_ask_client_factory(args.vault_root.clone());
    // stdout is the JSON-RPC channel — operator-facing notes go to stderr.
    match &ask_client {
        None => eprintln!(
            "ovp-mcp: ask NOT CONFIGURED — build with `--features anthropic`. \
             System → LLM Provider (or ANTHROPIC_API_KEY) enables the tool."
        ),
        Some(factory) => match factory() {
            Ok(_) => {}
            Err(e) if e == LLM_NOT_CONFIGURED => eprintln!(
                "ovp-mcp: ask waiting for API key — set System → LLM Provider \
                 or ANTHROPIC_API_KEY (no restart needed after save)."
            ),
            Err(e) => {
                return Err(CliError::Io(format!("ask client configuration invalid: {e}")));
            }
        },
    }
    let config = McpConfig {
        vault_root: args.vault_root,
        ask_client,
    };
    run_mcp(config).map_err(CliError::Io)
}
