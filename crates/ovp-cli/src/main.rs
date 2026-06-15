use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

mod commands;

#[derive(Debug)]
pub enum CliError {
    Io(String),
    Core(ovp_core::CoreError),
    Assembly(ovp_app::AssemblyError),
    /// A gate command produced its report but the gate did NOT pass (non-zero
    /// exit so CI / a durable writer can't treat it as success).
    Gate(String),
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CliError::Io(s) => write!(f, "io: {s}"),
            CliError::Core(e) => write!(f, "{e}"),
            CliError::Assembly(e) => write!(f, "assembly: {e}"),
            CliError::Gate(s) => write!(f, "gate: {s}"),
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
    /// Parse a pipeline manifest and print its nodes, edges, and topological order.
    Graph {
        #[arg(long)]
        manifest: PathBuf,
    },
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DIAGNOSTIC — experimental/eval harness; not a product path.
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
    /// DIAGNOSTIC — experimental/eval harness; not a product path.
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
    /// PRODUCT — reader/crystal trunk (the blessed path).
    /// M17 Grounded Reader Trunk: Source → Grounded Units → Critic Repair → Reader
    /// Cards → a human-usable reader pack (collapsible HTML + flat MD, provenance
    /// intact). Fail-loud on truth-layer errors. NOT canonical/evergreen/RAG/Referent.
    /// `--render-only` renders a pack from existing --units-json + --cards-json.
    ReadSource {
        #[arg(long)]
        input: PathBuf,
        #[arg(long, default_value = ".run/reader/case")]
        out: PathBuf,
        #[arg(long, default_value = ".run/reader/cassettes")]
        cache_dir: PathBuf,
        #[arg(long)]
        critic_cache_dir: Option<PathBuf>,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        #[arg(long)]
        render_only: bool,
        #[arg(long)]
        units_json: Option<PathBuf>,
        #[arg(long)]
        cards_json: Option<PathBuf>,
    },
    /// PRODUCT — the blessed daily operator loop (M30/M31): optional pinboard
    /// capture → intake sweep (Clippings/00-Capture/02-Pinboard → 01-Raw with
    /// URL/content dedup) → grounded reader trunk per NEW source → lifecycle
    /// move to 03-Processed → durable run report (`.ovp/reports/`) → read
    /// model + console refresh. Every attempt lands in the append-only
    /// ledger; every write is logged to `60-Logs/pipeline.jsonl` BEFORE its
    /// success record (OVP_RULES). Per-source failures are retried next run;
    /// 3 failures block a source pending review. Exit is non-zero if any
    /// source failed.
    Daily {
        /// The real vault root (e.g. ~/Documents/ovp-vault).
        #[arg(long)]
        vault_root: PathBuf,
        /// Inbox to sweep. Default: `<vault-root>/50-Inbox/01-Raw`.
        #[arg(long)]
        inbox: Option<PathBuf>,
        /// Cassette root for model calls. Default: `<vault-root>/.ovp/cassettes/daily`.
        #[arg(long)]
        cache_dir: Option<PathBuf>,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
        /// ISO-8601 date stamped on records + pack dirs. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        /// Run id stamped on ledger records. Defaults to `daily-<date>`.
        #[arg(long)]
        run_id: Option<String>,
        /// Plan only: print what would be processed; write nothing.
        #[arg(long)]
        dry_run: bool,
        /// Max sources processed in one run (LLM-loop rate limit per
        /// OVP_RULES). 0 = unlimited.
        #[arg(long, default_value_t = 10)]
        max_sources: usize,
        /// Skip the capture/intake sweep phase.
        #[arg(long)]
        no_intake: bool,
        /// Pinboard capture from a JSON export file (offline).
        #[arg(long)]
        pinboard_fixture: Option<PathBuf>,
        /// Pinboard capture from the live API (requires `--features
        /// pinboard-live` build + PINBOARD_TOKEN).
        #[arg(long)]
        pinboard_live: bool,
        /// Leave succeeded sources in 01-Raw instead of moving them to
        /// 03-Processed.
        #[arg(long)]
        no_lifecycle: bool,
        /// Also retry sources blocked by the 3-failure cap.
        #[arg(long)]
        retry_blocked: bool,
        /// Web fetch enrichment from a fixture directory (offline testing).
        #[arg(long)]
        web_fetch_fixture: Option<PathBuf>,
        /// Enrich needs-content sources via live HTTP fetch (requires
        /// `--features web-fetch-live` build).
        #[arg(long)]
        web_fetch_live: bool,
        /// GitHub enrichment from a fixture directory (offline testing).
        #[arg(long)]
        github_fixture: Option<PathBuf>,
        /// Enrich GitHub repo URLs via live API (requires `--features
        /// github-live` build + GITHUB_TOKEN env).
        #[arg(long)]
        github_live: bool,
        /// Skip image download post-processing for reader packs.
        #[arg(long)]
        no_images: bool,
        /// Image download fixture directory (offline testing).
        #[arg(long)]
        image_fixture: Option<PathBuf>,
        /// Download pack images via live HTTP (requires `--features
        /// web-fetch-live` build).
        #[arg(long)]
        image_live: bool,
    },
    /// PRODUCT — run the capture/intake sweep alone (no model calls):
    /// normalize + dedup Clippings/00-Capture/02-Pinboard into 01-Raw, with
    /// duplicates parked, thin files flagged needs-content, and every
    /// disposition appended to `.ovp/intake.jsonl`.
    Intake {
        #[arg(long)]
        vault_root: PathBuf,
        /// ISO-8601 date stamp. Defaults to today.
        #[arg(long)]
        date: Option<String>,
        /// Run id for the ledger records. Defaults to `intake-<date>`.
        #[arg(long)]
        run_id: Option<String>,
        /// Plan only: print dispositions; move/write nothing.
        #[arg(long)]
        dry_run: bool,
    },
    /// PRODUCT — materialize Pinboard bookmarks as notes in
    /// `50-Inbox/02-Pinboard/` (URL-deduped against `.ovp/pinboard-sync.jsonl`
    /// and the intake ledger). Offline via `--fixture <export.json>`; live API
    /// via `--live` (needs a `--features pinboard-live` build + PINBOARD_TOKEN
    /// env var — never stored, never logged).
    PinboardSync {
        #[arg(long)]
        vault_root: PathBuf,
        /// Pinboard JSON export file (posts/all format).
        #[arg(long)]
        fixture: Option<PathBuf>,
        /// Call the live Pinboard API.
        #[arg(long)]
        live: bool,
        #[arg(long)]
        date: Option<String>,
        #[arg(long)]
        run_id: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },
    /// PRODUCT — rebuild the persistent read model
    /// (`.ovp/index/index.json`) from the ledgers, reader packs, crystal
    /// store, and run reports. Always a FULL deterministic rebuild
    /// (rebuilding IS the migration story); the projection is never
    /// authoritative.
    Index {
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        date: Option<String>,
    },
    /// PRODUCT — query the read model: list/search/filter sources, reader
    /// packs, crystal claims, and runs. `ovp-next find --vault-root V chunks`
    /// or `--kind sources --status blocked`. Run `index` (or `daily`) first.
    Find {
        #[arg(long)]
        vault_root: PathBuf,
        /// Case-insensitive substring over titles/URLs/paths/cards/claims.
        term: Option<String>,
        /// Restrict to one kind: sources|packs|claims|runs.
        #[arg(long)]
        kind: Option<String>,
        /// Status filter (queued|processed|failed|blocked|needs_content|
        /// unparseable|duplicate|durable|caveated|…).
        #[arg(long)]
        status: Option<String>,
        /// Date prefix filter (2026 / 2026-06 / 2026-06-09).
        #[arg(long)]
        date: Option<String>,
        /// Emit JSON instead of text.
        #[arg(long)]
        json: bool,
    },
    /// PRODUCT — refresh the bilingual product console
    /// (`.ovp/console/index.html`) from product state: attention feed, runs,
    /// sources, reader packs, crystal claims. Also persists the read model so
    /// console and `find` agree.
    Console {
        #[arg(long)]
        vault_root: PathBuf,
        #[arg(long)]
        date: Option<String>,
    },
    /// PRODUCT — Projection Lanes: view claims by routing lane (durable/review).
    /// Read-only view over the Crystal ledger. Shows which claims are eligible
    /// for vault projection (durable) and which await human review (caveated).
    Project {
        #[arg(long)]
        vault_root: PathBuf,
        /// Filter to a specific lane: `durable` or `review`. Omit to show all.
        #[arg(long)]
        lane: Option<String>,
        /// Show extra detail per claim (provenance score, sources, rationale).
        #[arg(long)]
        verbose: bool,
    },
    /// PRODUCT — reader/crystal trunk (the blessed path).
    /// M22 Crystal pre-write gate: lint a structured-citation synthesis candidate
    /// against the grounded units and score provenance. Mechanical, fail-loud, no
    /// model call, NO durable write. See `docs/stage-m22-crystal-gates.md`.
    CrystalLint {
        /// Candidate JSON: `{ "items": [ { id, claim, citations:[{case_id,unit_id,quote}] } ] }`.
        #[arg(long)]
        candidate: PathBuf,
        /// Directory with one subdir per case holding `units.accepted.json`.
        #[arg(long)]
        packs_dir: PathBuf,
        #[arg(long, default_value = ".run/m22/crystal-lint.json")]
        out: PathBuf,
        /// Optional claim-strength verdicts JSON (the labeled LLM gate's output);
        /// when given, each claim gets a final durable/caveated/reject routing.
        #[arg(long)]
        strength: Option<PathBuf>,
    },
    /// PRODUCT — reader/crystal trunk (the blessed path).
    /// M23 durable Crystal write: run the FULL pre-write gate and, only if
    /// durable-eligible, append `Durable` claims to an append-only store +
    /// render `crystal.md`. Refuses on any gate gap. No graph. For the M31
    /// product surface pass `--store <vault>/.ovp/crystal` (the console and
    /// `find` read ONLY that location); the `.run` default is diagnostic.
    CrystalWrite {
        #[arg(long)]
        candidate: PathBuf,
        #[arg(long)]
        packs_dir: PathBuf,
        /// Claim-strength verdicts (REQUIRED — a durable write is a full pre-write run).
        #[arg(long)]
        strength: PathBuf,
        /// Durable store directory (append-only ledger.jsonl + crystal.md + review.json).
        #[arg(long, default_value = ".run/m23/store")]
        store: PathBuf,
        #[arg(long)]
        run_id: Option<String>,
        /// Crystal view header: title / scope / what-it-is-not-claiming (for crystal.md).
        #[arg(long)]
        title: Option<String>,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        not_claiming: Option<String>,
    },
    /// PRODUCT — reader/crystal trunk (the blessed path).
    /// M25 Crystal Review Workbench: apply human review decisions over caveated
    /// claims into a REVISED structured candidate. The decision authors a
    /// candidate; it does NOT decide durability — the revised candidate must
    /// re-enter the strength gate + crystal-write. Fail-loud on unknown claim ids.
    CrystalReview {
        #[arg(long)]
        candidate: PathBuf,
        /// Reviewer decisions JSON: [{ claim_id, action, revisions, note }].
        #[arg(long)]
        decisions: PathBuf,
        #[arg(long, default_value = ".run/m25/revised-candidate.json")]
        out: PathBuf,
    },
    /// DIAGNOSTIC — experimental/eval harness; not a product path.
    /// M14b (experimental): classify the OBJECTS that M14a.8 accepted Units talk
    /// about into LOCAL ReferentCandidates and write a review pack to `--out`.
    /// Hand-harness — NOT canonicalization, no manifest / GraphAssembler /
    /// RunCycle / vault. `--client live` records cassettes under
    /// `referent_classify/v1`.
    ExtractReferents {
        /// An M14a.8 `units.accepted.json`.
        #[arg(long)]
        units: PathBuf,
        #[arg(long, default_value = ".run/m14b/case")]
        out: PathBuf,
        #[arg(long, default_value = ".run/m14b/cassettes")]
        cache_dir: PathBuf,
        #[arg(long, value_enum, default_value_t = ClientKindArg::Replay)]
        client: ClientKindArg,
    },
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DEMOTED — M7–M13 substrate, off the blessed path (builds + tests, kept for reference).
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
    /// DIAGNOSTIC — experimental/eval harness; not a product path.
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
        Cmd::ReadSource { input, out, cache_dir, critic_cache_dir, client, render_only, units_json, cards_json } => {
            use commands::client::ClientKind;
            use commands::read_source::ReadSourceArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let critic_cache = critic_cache_dir.unwrap_or_else(|| cache_dir.clone());
            commands::read_source::run(ReadSourceArgs {
                input_path: input,
                out_dir: out,
                cache_dir,
                critic_cache_dir: critic_cache,
                client_kind,
                render_only,
                units_json,
                cards_json,
            })
        }
        Cmd::Daily {
            vault_root,
            inbox,
            cache_dir,
            client,
            date,
            run_id,
            dry_run,
            max_sources,
            no_intake,
            pinboard_fixture,
            pinboard_live,
            no_lifecycle,
            retry_blocked,
            web_fetch_fixture,
            web_fetch_live,
            github_fixture,
            github_live,
            no_images,
            image_fixture,
            image_live,
        } => {
            use commands::client::ClientKind;
            use commands::daily::DailyArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            let date = date.unwrap_or_else(today_iso);
            let run_id = run_id.unwrap_or_else(|| format!("daily-{date}"));
            commands::daily::run(DailyArgs {
                vault_root,
                inbox,
                cache_dir,
                client_kind,
                date,
                run_id,
                dry_run,
                max_sources,
                no_intake,
                pinboard_fixture,
                pinboard_live,
                no_lifecycle,
                retry_blocked,
                web_fetch_fixture,
                web_fetch_live,
                github_fixture,
                github_live,
                no_images,
                image_fixture,
                image_live,
            })
        }
        Cmd::Intake { vault_root, date, run_id, dry_run } => {
            let date = date.unwrap_or_else(today_iso);
            let run_id = run_id.unwrap_or_else(|| format!("intake-{date}"));
            commands::intake::run(commands::intake::IntakeArgs { vault_root, date, run_id, dry_run })
        }
        Cmd::PinboardSync { vault_root, fixture, live, date, run_id, dry_run } => {
            let date = date.unwrap_or_else(today_iso);
            let run_id = run_id.unwrap_or_else(|| format!("pinboard-{date}"));
            commands::pinboard_sync::run(commands::pinboard_sync::PinboardSyncArgs {
                vault_root,
                fixture,
                live,
                date,
                run_id,
                dry_run,
            })
        }
        Cmd::Index { vault_root, date } => {
            let date = date.unwrap_or_else(today_iso);
            commands::index_cmd::run_index(commands::index_cmd::IndexArgs { vault_root, date })
        }
        Cmd::Find { vault_root, term, kind, status, date, json } => {
            commands::index_cmd::run_find(commands::index_cmd::FindArgs {
                vault_root,
                term,
                kind,
                status,
                date,
                json,
            })
        }
        Cmd::Console { vault_root, date } => {
            let date = date.unwrap_or_else(today_iso);
            commands::console_cmd::run(commands::console_cmd::ConsoleArgs { vault_root, date })
        }
        Cmd::Project { vault_root, lane, verbose } => {
            use commands::project::{LaneFilter, ProjectArgs};
            let lane = match lane.as_deref() {
                Some("durable") => LaneFilter::Durable,
                Some("review") => LaneFilter::Review,
                _ => LaneFilter::All,
            };
            commands::project::run(ProjectArgs { vault_root, lane, verbose })
        }
        Cmd::CrystalLint { candidate, packs_dir, out, strength } => {
            use commands::crystal_lint::CrystalLintArgs;
            commands::crystal_lint::run(CrystalLintArgs { candidate, packs_dir, out, strength })
        }
        Cmd::CrystalWrite { candidate, packs_dir, strength, store, run_id, title, scope, not_claiming } => {
            use commands::crystal_write::CrystalWriteArgs;
            commands::crystal_write::run(CrystalWriteArgs {
                candidate, packs_dir, strength, store, run_id, title, scope, not_claiming,
            })
        }
        Cmd::CrystalReview { candidate, decisions, out } => {
            use commands::crystal_review::CrystalReviewArgs;
            commands::crystal_review::run(CrystalReviewArgs { candidate, decisions, out })
        }
        Cmd::ExtractReferents { units, out, cache_dir, client } => {
            use commands::client::ClientKind;
            use commands::extract_referents::ExtractReferentsArgs;
            let client_kind = match client {
                ClientKindArg::Replay => ClientKind::Replay,
                ClientKindArg::Live => ClientKind::Live,
            };
            commands::extract_referents::run(ExtractReferentsArgs {
                units_path: units,
                out_dir: out,
                cache_dir,
                client_kind,
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
