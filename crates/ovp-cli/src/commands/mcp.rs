//! `mcp` — start the OVP MCP stdio server.

use std::path::PathBuf;

use ovp_mcp::{run_mcp, McpConfig};

use crate::CliError;

pub struct McpArgs {
    pub vault_root: PathBuf,
}

pub fn run(args: McpArgs) -> Result<(), CliError> {
    let config = McpConfig { vault_root: args.vault_root };
    run_mcp(config).map_err(CliError::Io)
}
