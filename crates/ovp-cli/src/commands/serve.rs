//! `serve` — start the OVP console HTTP server.

use std::path::PathBuf;

use ovp_server::{
    providers_ask_client_factory, run_server, LLM_NOT_CONFIGURED, ServeConfig,
};

use crate::CliError;

pub struct ServeArgs {
    pub vault_root: PathBuf,
    pub host: String,
    pub port: u16,
    pub viz_dir: Option<PathBuf>,
}

pub fn run(args: ServeArgs) -> Result<(), CliError> {
    let ask_client = providers_ask_client_factory(args.vault_root.clone());
    match &ask_client {
        None => eprintln!(
            "  ask:     NOT CONFIGURED — build with `--features anthropic`. \
             System → LLM Provider writes `.ovp/providers.toml`."
        ),
        // Fail loud at startup when a key is present but another live setting
        // is invalid (ANTHROPIC_BASE_URL, OVP_LLM_MAX_TOKENS, …). Missing key
        // is fine — operator can fill System → LLM Provider without restart.
        Some(factory) => match factory() {
            Ok(_) => eprintln!(
                "  ask:     ready (re-reads .ovp/providers.toml on each question)"
            ),
            Err(e) if e == LLM_NOT_CONFIGURED => eprintln!(
                "  ask:     waiting for API key — set it under System → LLM Provider \
                 (or ANTHROPIC_API_KEY); no restart needed after save."
            ),
            Err(e) => {
                return Err(CliError::Io(format!("ask client configuration invalid: {e}")));
            }
        },
    }
    let config = ServeConfig {
        vault_root: args.vault_root,
        host: args.host,
        port: args.port,
        viz_dir: args.viz_dir,
        ask_client,
        // The server IS ovp2 — current_exe is the right binary for
        // `schedule run-now` children.
        ovp2_bin: None,
        // Server defaults: the ask guard derives from OVP_LLM_TIMEOUT_SECS
        // (the same env the live client reads), the in-flight cap from
        // DEFAULT_MAX_CONCURRENT_ASKS.
        ask_timeout: None,
        max_concurrent_asks: None,
    };
    run_server(config).map_err(CliError::Io)
}
