//! `serve` — start the OVP console HTTP server.

use std::path::PathBuf;

use ovp_server::{run_server, ServeConfig};

use crate::CliError;

pub struct ServeArgs {
    pub vault_root: PathBuf,
    pub host: String,
    pub port: u16,
    pub viz_dir: Option<PathBuf>,
}

pub fn run(args: ServeArgs) -> Result<(), CliError> {
    let config = ServeConfig {
        vault_root: args.vault_root,
        host: args.host,
        port: args.port,
        viz_dir: args.viz_dir,
    };
    run_server(config).map_err(CliError::Io)
}
