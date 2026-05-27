use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

mod commands;

#[derive(Debug)]
pub enum CliError {
    Io(String),
    Core(ovp_core::CoreError),
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CliError::Io(s) => write!(f, "io: {s}"),
            CliError::Core(e) => write!(f, "{e}"),
        }
    }
}

#[derive(Parser, Debug)]
#[command(name = "ovp-next", version, about = "OVP Next — clean-core Rust pipeline")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand, Debug)]
enum Cmd {
    /// Parse a pipeline manifest and print its nodes, edges, and topological order.
    Graph {
        #[arg(long)]
        manifest: PathBuf,
    },
    /// Execute a pipeline with the v0.1 in-tree fake filters; dump plan + events to disk.
    Run {
        #[arg(long)]
        manifest: PathBuf,
        /// Required: opt-in flag so this command is never confused with a real run.
        #[arg(long)]
        fake: bool,
        /// Identifier stamped onto every event + the plan file name.
        #[arg(long, default_value = "demo")]
        run_id: String,
        /// Directory where `plans/` and `events/` will be written.
        #[arg(long, default_value = ".run")]
        out: PathBuf,
    },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = match cli.cmd {
        Cmd::Graph { manifest } => commands::graph::run(manifest),
        Cmd::Run { manifest, fake, run_id, out } => {
            if !fake {
                eprintln!("ovp-next: v0.1 only supports --fake runs. Pass --fake to proceed.");
                return ExitCode::from(2);
            }
            commands::run::run(manifest, run_id, out)
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}
