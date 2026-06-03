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
    /// M14a.4 (experimental): copy-only probe — does the model verbatim-copy a
    /// substring from rendered spans? Diagnostic; writes no vault.
    CopyProbe {
        #[arg(long)]
        input: PathBuf,
        #[arg(long, default_value = ".run/m14/copyprobe")]
        out: PathBuf,
        #[arg(long, default_value = ".run/m14/cassettes")]
        cache_dir: PathBuf,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        #[arg(long, default_value_t = 20)]
        max_spans: usize,
    },
    /// M14a (experimental): extract grounded knowledge **Units** from a source
    /// and write a review pack to `--out`. Hand-harness — does NOT go through a
    /// manifest / GraphAssembler / RunCycle, writes no vault. Default client is
    /// replay-only; `--client live` records cassettes under `unit_extract/v1`.
    ExtractUnits {
        #[arg(long)]
        input: PathBuf,
        #[arg(long, default_value = ".run/m14/case")]
        out: PathBuf,
        #[arg(long, default_value = ".run/m14/cassettes")]
        cache_dir: PathBuf,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// M14a.8: after the frozen-v5 base extract, run the independent critic
        /// (`unit_critic/v1`) + bounded TRIM/ADD repair, re-validated once. The
        /// base ALWAYS replays from `--cache-dir` (frozen v5); `--client live`
        /// applies to the CRITIC call only (records under `--critic-cache-dir`).
        #[arg(long)]
        repair: bool,
        /// Where the critic cassette lives. Defaults to `--cache-dir` so the v5
        /// base and the `unit_critic/v1` critic share one cassette root.
        #[arg(long)]
        critic_cache_dir: Option<PathBuf>,
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
    /// Read-only query (L5) over the canonical store + knowledge index.
    /// `list` / `get <slug>` / `search <term>` / `backlinks <slug>` / `stats`.
    Query {
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        canonical_root: PathBuf,
        #[arg(value_enum)]
        kind: QueryKindArg,
        /// Slug (get/backlinks) or substring (search). Ignored for list/stats.
        term: Option<String>,
        /// Emit JSON instead of text.
        #[arg(long)]
        json: bool,
    },
    /// Read-only health checks (L5) over the canonical store + vault + index.
    /// Reports findings; never fixes. Exits non-zero at/above `--max-severity`.
    Lint {
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        canonical_root: PathBuf,
        /// Fail (non-zero exit) if any finding is at or above this severity.
        #[arg(long, value_enum, default_value_t = SeverityArg::Error)]
        max_severity: SeverityArg,
        /// Emit JSON instead of text.
        #[arg(long)]
        json: bool,
    },
    /// Automation sweep (L6): discover markdown under `--inbox-root`, run the L4
    /// run-cycle on each input, then the L5 lint gate; print an operational
    /// report. Exits non-zero if any cycle failed or lint failed at
    /// `--max-severity`. Default client is replay-only; no network.
    AutoRun {
        #[arg(long)]
        inbox_root: PathBuf,
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        canonical_root: PathBuf,
        #[arg(long, default_value = "manifests/article_evergreen.pipeline.toml")]
        manifest: PathBuf,
        #[arg(long, default_value = "crates/ovp-domain/tests/cassettes")]
        cache_dir: PathBuf,
        #[arg(long)]
        concept_registry: Option<PathBuf>,
        #[arg(long, default_value = "auto-run")]
        run_id: String,
        /// ISO-8601 date stamped onto interpreted docs. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// Fail (non-zero exit) if lint reports any finding at or above this.
        #[arg(long, value_enum, default_value_t = SeverityArg::Error)]
        max_severity: SeverityArg,
        /// Preview only: each cycle applies nothing (dry-run). The lint pass
        /// then checks the CURRENT on-disk state, not a post-apply simulation.
        #[arg(long)]
        dry_run: bool,
        /// Emit JSON instead of text.
        #[arg(long)]
        json: bool,
    },
    /// Read-only RAG retrieval (L6) over the canonical store + knowledge index +
    /// evergreen notes. Scores the query, ranks, and prints a bounded context.
    /// Read-only — never assembles, runs, applies, or writes.
    Rag {
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        canonical_root: PathBuf,
        /// The retrieval query.
        #[arg(long)]
        query: String,
        /// Max concepts to return.
        #[arg(long, default_value_t = 5)]
        limit: usize,
        /// Emit JSON instead of text.
        #[arg(long)]
        json: bool,
    },
    /// Run one full cycle (L4) on a single input and produce a deterministic,
    /// human-inspectable review pack: processor chain, run report, apply
    /// summary, files written, canonical summary, L5 query stats + lint, and
    /// (optionally) an L6 RAG context and an `--expected-dir` comparison.
    /// Acts as a quality gate: exits non-zero if the cycle failed OR the output
    /// violates its `--expected-dir` contract MUST clauses (the pack is written
    /// either way). Read / orchestrate only — the only vault/canonical content
    /// writes go through the cycle; this writes just the pack (+ empty store
    /// roots). Default client is replay-only; no network.
    ReviewRun {
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
        #[arg(long, default_value = "review")]
        run_id: String,
        /// ISO-8601 date stamped onto interpreted docs. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// Where to write the review pack.
        #[arg(long, default_value = ".run/review-pack")]
        out: PathBuf,
        /// Optional RAG query to retrieve over the result.
        #[arg(long)]
        rag_query: Option<String>,
        /// Max concepts in the RAG context.
        #[arg(long, default_value_t = 5)]
        rag_limit: usize,
        /// Optional directory of frozen expected artifacts to compare against.
        /// A contract.yaml here is evaluated by the contract engine; a MUST
        /// failure fails the review (non-zero exit) even if the cycle succeeded.
        #[arg(long)]
        expected_dir: Option<PathBuf>,
        /// Preview only: the cycle applies nothing. Read-back / lint /
        /// comparison reflect the CURRENT on-disk state, not a post-apply
        /// simulation.
        #[arg(long)]
        dry_run: bool,
    },
    /// External E2E comparator (M8): run ONE input through both the ovp-next
    /// pipeline (via the review harness) and the external Nowledge Mem HTTP
    /// service, normalize both, and write a deterministic comparison pack
    /// (concept overlap, claim diff, grounding, structure, retrieval). Nowledge
    /// Mem is an external reference system; nothing in the trunk depends on it.
    /// Real-LLM + the network call are explicit, manual operations.
    CompareRun {
        /// Remote URL to ingest on the Nowledge side (the service fetches it).
        #[arg(long)]
        url: Option<String>,
        /// Local markdown — drives the ovp side, and the Nowledge side when no
        /// --url is given. The ovp trunk cannot fetch URLs, so a URL-only run
        /// leaves the ovp side unavailable (loudly noted in the pack).
        #[arg(long)]
        input: Option<PathBuf>,
        #[arg(long, default_value = "http://127.0.0.1:14242")]
        nowledge_base_url: String,
        #[arg(long, default_value_t = 30)]
        nowledge_timeout_secs: u64,
        #[arg(long, default_value = "manifests/article_evergreen.pipeline.toml")]
        manifest: PathBuf,
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        canonical_root: PathBuf,
        #[arg(long, default_value = "crates/ovp-domain/tests/cassettes")]
        cache_dir: PathBuf,
        #[arg(long)]
        concept_registry: Option<PathBuf>,
        #[arg(long, default_value = "compare")]
        case_id: String,
        #[arg(long, default_value = "compare")]
        run_id: String,
        /// ISO-8601 date stamped onto interpreted docs. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        #[arg(long, default_value = ".run/eval/compare")]
        out: PathBuf,
        /// Fixed retrieval query (repeatable). Defaults to a 3-query probe set.
        #[arg(long = "query")]
        queries: Vec<String>,
        #[arg(long, default_value_t = 5)]
        rag_limit: usize,
        #[arg(long, default_value_t = 10)]
        search_limit: usize,
        #[arg(long, default_value = "default")]
        space_id: String,
        /// Token-overlap ratio above which a claim counts as grounded.
        #[arg(long, default_value_t = 0.5)]
        grounding_threshold: f64,
        #[arg(long, default_value_t = 3)]
        poll_interval_secs: u64,
        #[arg(long, default_value_t = 100)]
        poll_max_attempts: u32,
        /// Strict same-input mode: materialize Nowledge's parsed content to a
        /// shared markdown artifact and feed THAT to the ovp side, so both
        /// systems analyze byte-identical text (a URL becomes source metadata).
        #[arg(long)]
        materialize_from_nowledge: bool,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
enum QueryKindArg {
    List,
    Get,
    Search,
    Backlinks,
    Stats,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
enum SeverityArg {
    Info,
    Warning,
    Error,
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
        Cmd::CopyProbe { input, out, cache_dir, client, max_spans } => {
            use commands::client::ClientKind;
            use commands::copy_probe::CopyProbeArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            commands::copy_probe::run(CopyProbeArgs {
                input_path: input,
                out_dir: out,
                cache_dir,
                client_kind,
                max_spans,
            })
        }
        Cmd::ExtractUnits { input, out, cache_dir, client, repair, critic_cache_dir } => {
            use commands::client::ClientKind;
            use commands::extract_units::ExtractUnitsArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let critic_cache = critic_cache_dir.unwrap_or_else(|| cache_dir.clone());
            commands::extract_units::run(ExtractUnitsArgs {
                input_path: input,
                out_dir: out,
                cache_dir,
                client_kind,
                repair,
                critic_cache_dir: critic_cache,
            })
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
        Cmd::Query { vault_root, canonical_root, kind, term, json } => {
            use commands::query::{QueryArgs, QueryKind};
            let kind = match kind {
                QueryKindArg::List => QueryKind::List,
                QueryKindArg::Get => QueryKind::Get,
                QueryKindArg::Search => QueryKind::Search,
                QueryKindArg::Backlinks => QueryKind::Backlinks,
                QueryKindArg::Stats => QueryKind::Stats,
            };
            commands::query::run(QueryArgs { vault_root, canonical_root, kind, term, json })
        }
        Cmd::Lint { vault_root, canonical_root, max_severity, json } => {
            use commands::lint::{LintArgs, SeverityArg as LintSeverity};
            let max_severity = match max_severity {
                SeverityArg::Info => LintSeverity::Info,
                SeverityArg::Warning => LintSeverity::Warning,
                SeverityArg::Error => LintSeverity::Error,
            };
            commands::lint::run(LintArgs { vault_root, canonical_root, max_severity, json })
        }
        Cmd::AutoRun {
            inbox_root,
            vault_root,
            canonical_root,
            manifest,
            cache_dir,
            concept_registry,
            run_id,
            date,
            client,
            max_severity,
            dry_run,
            json,
        } => {
            use commands::auto_run::AutoRunArgs;
            use commands::client::ClientKind;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let lint_threshold = match max_severity {
                SeverityArg::Info => ovp_lint::Severity::Info,
                SeverityArg::Warning => ovp_lint::Severity::Warning,
                SeverityArg::Error => ovp_lint::Severity::Error,
            };
            let date_stamp = date.unwrap_or_else(today_iso);
            commands::auto_run::run(AutoRunArgs {
                inbox_root,
                vault_root,
                canonical_root,
                manifest_path: manifest,
                cache_dir,
                concept_registry,
                run_id,
                date_stamp,
                client_kind,
                lint_threshold,
                dry_run,
                json,
            })
        }
        Cmd::Rag { vault_root, canonical_root, query, limit, json } => {
            use commands::rag::RagArgs;
            commands::rag::run(RagArgs { vault_root, canonical_root, query, limit, json })
        }
        Cmd::ReviewRun {
            manifest,
            input,
            vault_root,
            canonical_root,
            cache_dir,
            concept_registry,
            run_id,
            date,
            client,
            out,
            rag_query,
            rag_limit,
            expected_dir,
            dry_run,
        } => {
            use commands::client::ClientKind;
            use commands::review_run::ReviewRunArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let date_stamp = date.unwrap_or_else(today_iso);
            commands::review_run::run(ReviewRunArgs {
                manifest_path: manifest,
                input_path: input,
                vault_root,
                canonical_root,
                cache_dir,
                concept_registry,
                run_id,
                date_stamp,
                client_kind,
                out_dir: out,
                rag_query,
                rag_limit,
                expected_dir,
                dry_run,
            })
        }
        Cmd::CompareRun {
            url,
            input,
            nowledge_base_url,
            nowledge_timeout_secs,
            manifest,
            vault_root,
            canonical_root,
            cache_dir,
            concept_registry,
            case_id,
            run_id,
            date,
            client,
            out,
            queries,
            rag_limit,
            search_limit,
            space_id,
            grounding_threshold,
            poll_interval_secs,
            poll_max_attempts,
            materialize_from_nowledge,
        } => {
            use commands::client::ClientKind;
            use commands::compare_run::CompareRunArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let date_stamp = date.unwrap_or_else(today_iso);
            commands::compare_run::run(CompareRunArgs {
                case_id,
                url,
                markdown_input: input,
                nowledge_base_url,
                nowledge_timeout_secs,
                manifest_path: manifest,
                vault_root,
                canonical_root,
                cache_dir,
                concept_registry,
                run_id,
                date_stamp,
                client_kind,
                out_dir: out,
                queries,
                rag_limit,
                search_limit,
                space_id,
                grounding_threshold,
                poll_interval_secs,
                poll_max_attempts,
                materialize_from_nowledge,
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
