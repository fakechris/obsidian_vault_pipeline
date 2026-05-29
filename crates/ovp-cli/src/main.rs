use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

mod commands;

#[derive(Debug)]
pub enum CliError {
    Io(String),
    Core(ovp_core::CoreError),
    Assembly(ovp_app::AssemblyError),
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CliError::Io(s) => write!(f, "io: {s}"),
            CliError::Core(e) => write!(f, "{e}"),
            CliError::Assembly(e) => write!(f, "assembly: {e}"),
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
    /// Apply a serialized WritePlan to a filesystem vault. Reads the
    /// plan JSON, runs VaultFsPlanApplier, prints the report.
    ApplyPlan {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        vault_root: PathBuf,
        /// Walk the plan without writing anything; report-only.
        #[arg(long)]
        dry_run: bool,
        /// Optional path for an ApplyReport JSON dump.
        #[arg(long)]
        report: Option<PathBuf>,
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
        /// Which ModelClient to wire. `replay` (default) reads committed
        /// cassettes, no network. `live` calls Anthropic and captures the
        /// reply into --cache-dir; requires `--features anthropic` +
        /// ANTHROPIC_API_KEY (errors with guidance otherwise).
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// PARA area to stamp on the InterpretedDoc. `ai` for article_clean.
        #[arg(long, default_value = "ai")]
        area: String,
        /// ISO-8601 date stamped onto the InterpretedDoc. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        /// Path to a ConceptRegistry JSON ({canonical:[...],aliases:{...}}).
        /// Absent → a small default seed.
        #[arg(long)]
        concept_registry: Option<PathBuf>,
    },
    /// Run one full operational cycle (L4): assemble + run the manifest, apply
    /// the plan (vault + canonical), then rebuild the MOC and knowledge index.
    /// Idempotent on re-run. Default client is replay-only; no network.
    RunCycle {
        #[arg(long, default_value = "manifests/article_evergreen.pipeline.toml")]
        manifest: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        canonical_root: PathBuf,
        #[arg(long, default_value = "crates/ovp-domain/tests/cassettes")]
        cache_dir: PathBuf,
        #[arg(long)]
        concept_registry: Option<PathBuf>,
        #[arg(long, default_value = "run-cycle")]
        run_id: String,
        /// ISO-8601 date stamped onto interpreted docs. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// Preview only: apply nothing, report what would happen.
        #[arg(long)]
        dry_run: bool,
        /// Optional path to dump the RunCycleReport JSON.
        #[arg(long)]
        report: Option<PathBuf>,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
enum ClientKindArg {
    Replay,
    Live,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = match cli.cmd {
        Cmd::Graph { manifest } => commands::graph::run(manifest),
        Cmd::ApplyPlan { plan, vault_root, dry_run, report } => {
            commands::apply_plan::run(commands::apply_plan::ApplyPlanArgs {
                plan_path: plan,
                vault_root,
                dry_run,
                report_path: report,
            })
        }
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
            concept_registry,
        } => {
            use commands::client::ClientKind;
            use commands::interpret_article::InterpretArticleArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
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
                concept_registry,
            })
        }
        Cmd::RunCycle {
            manifest,
            input,
            vault_root,
            canonical_root,
            cache_dir,
            concept_registry,
            run_id,
            date,
            client,
            dry_run,
            report,
        } => {
            use commands::client::ClientKind;
            use commands::run_cycle::RunCycleArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let date_stamp = date.unwrap_or_else(today_iso);
            commands::run_cycle::run(RunCycleArgs {
                manifest_path: manifest,
                input_path: input,
                vault_root,
                canonical_root,
                cache_dir,
                concept_registry,
                run_id,
                date_stamp,
                client_kind,
                dry_run,
                report_path: report,
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
