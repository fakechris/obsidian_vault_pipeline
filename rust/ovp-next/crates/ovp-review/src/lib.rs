//! OVP Next M7 — the E2E review harness (`ovp-review`).
//!
//! A **read / orchestrate** layer above L4–L6. Given one input markdown file it:
//!   1. runs the existing L4 [`RunCycle`] (the only thing that writes to the
//!      vault / canonical store — this crate adds NO pipeline logic and changes
//!      NO RunCycle semantics),
//!   2. reads the result back through L5 ([`KnowledgeView`] stats, [`Lint`]) and
//!      L6 ([`ovp_rag`]),
//!   3. captures the manifest's processor chain, the run report, an apply
//!      summary, the files written, and a canonical summary,
//!   4. optionally compares against a frozen `--expected-dir`,
//!   5. writes a deterministic, human-inspectable **review pack** to one output
//!      directory.
//!
//! The only vault / canonical **content** writes go through [`RunCycle`]. The
//! harness itself writes just the review pack — and creates the (initially
//! empty) vault / canonical *root directories* so the read-back is well defined
//! even when the cycle wrote nothing; it never writes content into them. Sync;
//! no async, no shell-out, no network (the caller supplies the wiring, which
//! owns a replay-only or live `ModelClient`). A bad manifest, a wiring failure,
//! or a failed cycle is captured in the [`ReviewReport`] and the pack is still
//! produced — the harness's job is to make *whatever happened* inspectable.
//!
//! ## Two verdicts
//! [`ReviewReport::cycle_succeeded`] is the L4 pipeline-execution verdict;
//! [`ReviewReport::review_passed`] is the overall gate — the cycle succeeded
//! AND any `--expected-dir` contract is MUST-clean. The CLI exit code follows
//! `review_passed`, so a clean run that violates its frozen contract still
//! fails the review.
//!
//! ## Non-goals (v1)
//! - It evaluates observability + the contract verdict, not semantic answer
//!   quality.
//! - The contract comparison reconstructs a subject from the produced note's
//!   frontmatter + body; clauses that need the live event log (`event_emitted`)
//!   can't be checked from disk and would surface as failures.
//! - An arbitrary new clipping still needs a committed cassette (replay) or a
//!   live capture run; the harness does not invent model output.

mod chain;
mod compare;
mod pack;

pub use chain::{ChainNode, ProcessorChain};
pub use compare::{ComparisonSummary, ContractOutcome};

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, DomainPipelineSpec};
use ovp_core::ApplyMode;
use ovp_lint::{Lint, LintReport};
use ovp_query::{KnowledgeView, ViewStats};
use ovp_rag::{ContextBuilder, RagContext, RagCorpus, Ranker, Retriever};
use ovp_run::{RunCycle, RunCycleInputs, RunCycleReport};
use serde::Serialize;

/// Everything one review needs. The review pack is written under `out_dir`; the
/// other paths are inputs / roots for the cycle and the read-back.
pub struct ReviewRunConfig {
    pub input_path: PathBuf,
    pub manifest_path: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    /// Where the review pack is written.
    pub out_dir: PathBuf,
    /// Display label for the report; the actual `RunId` is set by the caller's
    /// wiring factory.
    pub run_id: String,
    /// RAG query to retrieve over the result; `None` skips the RAG step.
    pub rag_query: Option<String>,
    /// Max concepts in the RAG context.
    pub rag_limit: usize,
    /// Directory of frozen expected artifacts; `None` skips comparison.
    pub expected_dir: Option<PathBuf>,
    /// `Apply` (default) or `DryRun`.
    pub mode: ApplyMode,
}

/// The only failures that prevent producing *any* review pack (e.g. the output
/// directory can't be created). Everything else is captured in the report.
#[derive(Debug)]
pub enum ReviewError {
    Io(String),
}

impl std::fmt::Display for ReviewError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReviewError::Io(s) => write!(f, "{s}"),
        }
    }
}

impl std::error::Error for ReviewError {}

/// Files present under a store root after the cycle, vault-relative + sorted.
#[derive(Debug, Clone, Default, Serialize)]
pub struct FilesWritten {
    pub vault: Vec<String>,
    pub canonical: Vec<String>,
}

/// A read-back summary of the canonical store (the authority).
#[derive(Debug, Clone, Default, Serialize)]
pub struct CanonicalSummary {
    pub concept_count: usize,
    pub slugs: Vec<String>,
    pub evergreen_paths: Vec<String>,
}

/// The in-memory result of a review run. Drives `REVIEW.md`, is the test
/// surface, and is serializable for callers that want a machine summary.
#[derive(Debug, Serialize)]
pub struct ReviewReport {
    pub input_path: String,
    pub manifest_path: String,
    pub run_id: String,
    pub out_dir: String,
    /// The manifest's processor chain; `None` if the manifest didn't parse.
    pub chain: Option<ProcessorChain>,
    pub chain_error: Option<String>,
    /// The L4 cycle report; `None` if the cycle didn't run (bad manifest or
    /// wiring) or errored before a report existed (assembly / graph failure).
    pub run: Option<RunCycleReport>,
    pub run_error: Option<String>,
    pub files: FilesWritten,
    pub canonical: CanonicalSummary,
    pub query_stats: Option<ViewStats>,
    pub query_error: Option<String>,
    /// Always present: `Lint::check` returns a report even on a load error.
    pub lint: LintReport,
    /// Present only when `--rag-query` was supplied and the corpus loaded.
    pub rag: Option<RagContext>,
    pub rag_error: Option<String>,
    /// Vault-relative path of the discovered primary note, if any.
    pub primary_note: Option<String>,
    /// Present only when `--expected-dir` was supplied.
    pub comparison: Option<ComparisonSummary>,
}

impl ReviewReport {
    /// The L4 pipeline-execution verdict: the cycle ran and fully landed
    /// (assemble → run → apply → rebuild). Independent of any `--expected-dir`
    /// contract comparison — a cycle can succeed while the output fails its
    /// contract.
    pub fn cycle_succeeded(&self) -> bool {
        self.run.as_ref().is_some_and(RunCycleReport::succeeded)
    }

    /// The contract verdict from an `--expected-dir` comparison:
    /// - `Some(true)`  — a contract was evaluated and every MUST clause passed;
    /// - `Some(false)` — a contract was evaluated and ≥1 MUST clause failed;
    /// - `None`        — no comparison, or no `contract.yaml` / no produced note
    ///   to check (nothing to compare against).
    pub fn contract_clean(&self) -> Option<bool> {
        self.comparison.as_ref().and_then(|c| c.contract.as_ref()).map(|c| c.must_clean)
    }

    /// The overall review verdict — the headline the CLI exit code follows:
    /// the cycle succeeded AND, when a contract was evaluated, its MUST clauses
    /// are clean. With no `--expected-dir` (or no contract / no produced note)
    /// there is nothing to compare, so the review passes on the cycle alone.
    /// This is what makes `review-run` a quality *gate*, not just an
    /// observability dump: an output that violates its frozen contract fails
    /// the review even though the pipeline ran cleanly.
    pub fn review_passed(&self) -> bool {
        self.cycle_succeeded() && self.contract_clean().unwrap_or(true)
    }

    /// The single reason the review did not pass, if it didn't. Prefers the
    /// cycle failure (it precedes any comparison); otherwise reports the
    /// contract MUST failures.
    pub fn failure_reason(&self) -> Option<String> {
        if self.review_passed() {
            return None;
        }
        if !self.cycle_succeeded() {
            return Some(
                self.run_error
                    .clone()
                    .or_else(|| self.chain_error.clone())
                    .or_else(|| self.run.as_ref().and_then(|r| r.derived_skipped_reason.clone()))
                    .unwrap_or_else(|| "run-cycle did not run or did not succeed".to_string()),
            );
        }
        // Cycle landed cleanly, so the failure is the contract comparison.
        let failed = self
            .comparison
            .as_ref()
            .and_then(|c| c.contract.as_ref())
            .map(|c| c.must_failed)
            .unwrap_or(0);
        Some(format!("contract comparison failed: {failed} MUST clause(s) failed"))
    }
}

/// The review harness entry point.
pub struct ReviewRun;

impl ReviewRun {
    /// Run one review. `make_wiring` builds the (move-only-client-owning)
    /// [`AppWiring`]; it is called once, and only if the manifest parsed — so a
    /// bad manifest never builds a client and never touches the stores. Mirrors
    /// [`ovp_auto`]'s per-input factory split: this crate owns no wiring.
    pub fn execute<F>(config: ReviewRunConfig, make_wiring: F) -> Result<ReviewReport, ReviewError>
    where
        F: FnOnce() -> Result<AppWiring, String>,
    {
        // 1. Prepare the pack dir + the store roots, so the read-back (query /
        //    lint / walk) is well defined even when the cycle wrote nothing
        //    (e.g. a bad manifest, or a fresh dry-run). This creates empty
        //    directories only — all vault / canonical *content* is written
        //    solely by `RunCycle` below.
        ensure_dir(&config.out_dir)?;
        ensure_dir(&config.vault_root)?;
        ensure_dir(&config.canonical_root)?;

        // 2. Parse the spec → processor chain. A bad manifest is recorded, not
        //    fatal.
        let (chain, chain_error, spec) = match std::fs::read_to_string(&config.manifest_path) {
            Ok(toml) => match DomainPipelineSpec::parse(&toml) {
                Ok(spec) => (Some(ProcessorChain::from_spec(&spec)), None, Some(spec)),
                Err(e) => (None, Some(format!("manifest parse failed: {e}")), None),
            },
            Err(e) => (
                None,
                Some(format!("reading manifest `{}`: {e}", config.manifest_path.display())),
                None,
            ),
        };

        // 3. Run the L4 cycle — only if the spec parsed AND the wiring builds.
        //    Both failure modes are captured; the pack is still produced. This
        //    is the ONLY code path that writes to vault / canonical, and only
        //    through `RunCycle`.
        let (run, run_error) = match spec {
            Some(spec) => match make_wiring() {
                Ok(wiring) => {
                    let inputs = RunCycleInputs {
                        spec,
                        wiring,
                        vault_root: config.vault_root.clone(),
                        canonical_root: config.canonical_root.clone(),
                        mode: config.mode,
                    };
                    match RunCycle::new().execute(inputs) {
                        Ok(report) => (Some(report), None),
                        Err(e) => (None, Some(e.to_string())),
                    }
                }
                Err(e) => (None, Some(format!("building wiring: {e}"))),
            },
            // Spec didn't parse: `chain_error` already explains it.
            None => (None, None),
        };

        // 4. Read the result back (read-only; safe on an empty / failed run).
        let (canonical, query_stats, query_error) =
            match KnowledgeView::load(&config.vault_root, &config.canonical_root) {
                Ok(view) => (canonical_summary(&view), Some(view.stats()), None),
                Err(e) => (CanonicalSummary::default(), None, Some(e.to_string())),
            };

        let lint = Lint::check(&config.vault_root, &config.canonical_root);

        let (rag, rag_error) = match &config.rag_query {
            Some(query) => match run_rag(&config, query) {
                Ok(ctx) => (Some(ctx), None),
                Err(e) => (None, Some(e)),
            },
            None => (None, None),
        };

        // 5. Enumerate files + discover the primary note.
        let files = FilesWritten {
            vault: walk_files(&config.vault_root),
            canonical: walk_files(&config.canonical_root),
        };
        let primary_note = discover_primary_note(&files.vault);

        // 6. Comparison (if requested). Keep the full result for the pack; store
        //    only the serializable summary in the report.
        let comparison = config.expected_dir.as_ref().map(|dir| {
            compare::run(dir, &config.vault_root, primary_note.as_deref(), &files.vault)
        });

        // 7. Assemble the report, then write the pack.
        let report = ReviewReport {
            input_path: config.input_path.display().to_string(),
            manifest_path: config.manifest_path.display().to_string(),
            run_id: config.run_id.clone(),
            out_dir: config.out_dir.display().to_string(),
            chain,
            chain_error,
            run,
            run_error,
            files,
            canonical,
            query_stats,
            query_error,
            lint,
            rag,
            rag_error,
            primary_note,
            comparison: comparison.as_ref().map(|c| c.summary.clone()),
        };

        pack::write(&config, &report, comparison.as_ref())?;

        Ok(report)
    }
}

fn run_rag(config: &ReviewRunConfig, query: &str) -> Result<RagContext, String> {
    let corpus = RagCorpus::load(&config.vault_root, &config.canonical_root)
        .map_err(|e| format!("rag load: {e}"))?;
    let scored = Retriever::new().score(&corpus, query);
    let ranked = Ranker::with_limit(config.rag_limit).rank(scored);
    let ctx = ContextBuilder { max_concepts: config.rag_limit, ..ContextBuilder::default() }
        .build(&corpus, &ranked, query);
    Ok(ctx)
}

fn canonical_summary(view: &KnowledgeView) -> CanonicalSummary {
    let concepts = view.concepts();
    CanonicalSummary {
        concept_count: concepts.len(),
        slugs: concepts.iter().map(|c| c.slug.clone()).collect(),
        evergreen_paths: concepts.iter().map(|c| c.evergreen_path.clone()).collect(),
    }
}

/// Discover the produced interpretation note: under `20-Areas/`, an article
/// note ends `_深度解读.md`; otherwise fall back to the first `.md` under
/// `20-Areas/` (covers the paper path, which files elsewhere under it).
fn discover_primary_note(vault_files: &[String]) -> Option<String> {
    let candidates: Vec<&String> = vault_files
        .iter()
        .filter(|p| p.starts_with("20-Areas/") && p.ends_with(".md"))
        .collect();
    candidates
        .iter()
        .find(|p| p.ends_with("_深度解读.md"))
        .or_else(|| candidates.first())
        .map(|p| (*p).clone())
}

/// All files under `root`, vault-relative, sorted, `/`-separated. Missing root
/// → empty (the caller already ensured the dir exists). Symlinks to directories
/// are followed and an unreadable subdirectory is skipped — neither arises for
/// the harness's own fresh roots; this is a diagnostic listing, not a security
/// boundary.
fn walk_files(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    walk_into(root, root, &mut out);
    out.sort();
    out
}

fn walk_into(root: &Path, dir: &Path, out: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            walk_into(root, &path, out);
        } else if let Ok(rel) = path.strip_prefix(root) {
            out.push(rel.to_string_lossy().replace('\\', "/"));
        }
    }
}

pub(crate) fn ensure_dir(dir: &Path) -> Result<(), ReviewError> {
    std::fs::create_dir_all(dir)
        .map_err(|e| ReviewError::Io(format!("creating `{}`: {e}", dir.display())))
}

pub(crate) fn write_file(path: &Path, contents: &str) -> Result<(), ReviewError> {
    if let Some(parent) = path.parent() {
        ensure_dir(parent)?;
    }
    std::fs::write(path, contents)
        .map_err(|e| ReviewError::Io(format!("writing `{}`: {e}", path.display())))
}
