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
    /// Interpret a single article from disk through the v1 article pipeline.
    /// Default client is replay-only against `--cache-dir`; no network.
    InterpretArticle {
        #[arg(long, default_value = "manifests/article.pipeline.toml")]
        manifest: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long, default_value = ".run/article")]
        out: PathBuf,
        #[arg(long, default_value = "crates/ovp-domain/tests/cassettes")]
        cache_dir: PathBuf,
        #[arg(long, default_value = "demo-article")]
        run_id: String,
        /// Which ModelClient to wire. `replay` (default) reads from
        /// --cache-dir and errors on miss. `record-without-network` is
        /// the same but in record mode (still no network — fails loudly
        /// if a cassette is missing; useful for CI cassette-presence checks).
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// PARA area to stamp on the InterpretedDoc. `ai` for article_clean.
        #[arg(long, default_value = "ai")]
        area: String,
        /// ISO-8601 date stamped onto the InterpretedDoc. Defaults to today.
        #[arg(long)]
        date: Option<String>,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
enum ClientKindArg {
    Replay,
    RecordWithoutNetwork,
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
        Cmd::InterpretArticle {
            manifest,
            input,
            out,
            cache_dir,
            run_id,
            client,
            area,
            date,
        } => {
            use commands::interpret_article::{ClientKind, InterpretArticleArgs};
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::RecordWithoutNetwork => ClientKind::RecordWithoutNetwork,
            };
            let date_stamp = date.unwrap_or_else(today_iso);
            commands::interpret_article::run(InterpretArticleArgs {
                manifest_path: manifest,
                input_path: input,
                out_dir: out,
                cache_dir,
                run_id,
                client_kind,
                area,
                date_stamp,
            })
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

fn today_iso() -> String {
    // Minimal ISO-8601 date generator without a chrono dep. Reads the
    // system clock as a Unix timestamp + a tiny month-length table.
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (secs / 86_400) as i64;
    let (y, m, d) = days_to_ymd(days);
    format!("{y:04}-{m:02}-{d:02}")
}

fn days_to_ymd(mut days: i64) -> (i32, u32, u32) {
    // Days since 1970-01-01.
    let mut year: i32 = 1970;
    loop {
        let dy = if is_leap(year) { 366 } else { 365 };
        if days < dy {
            break;
        }
        days -= dy;
        year += 1;
    }
    let months: [i64; 12] = [
        31,
        if is_leap(year) { 29 } else { 28 },
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    ];
    let mut month: u32 = 1;
    for m in months.iter() {
        if days < *m {
            return (year, month, (days + 1) as u32);
        }
        days -= *m;
        month += 1;
    }
    (year, 12, 31)
}

fn is_leap(y: i32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0)
}
