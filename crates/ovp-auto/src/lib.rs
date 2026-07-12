//! OVP Next L6 — the automation path (`ovp-auto`).
//!
//! A **one-shot directory sweep**: discover markdown inputs under an inbox root,
//! run the L4 [`RunCycle`] on each, then run the L5 [`Lint`] health gate over
//! the result, and emit one operational [`AutoReport`]. It **calls** L4/L5 — it
//! reimplements none of the assemble/run/apply/rebuild logic, and it builds no
//! wiring itself: the caller supplies a per-input factory that produces the
//! fully-wired [`RunCycleInputs`]. Sync, no async runtime, no watcher daemon in
//! v1. See `docs/stage-rag-automation.md`.

use std::path::{Path, PathBuf};

use ovp_lint::{Lint, LintReport, Severity};
use ovp_run::{RunCycle, RunCycleInputs};
use ovp_stores::walk_markdown;
use serde::Serialize;

/// What a sweep needs: where to find inputs, the two store roots the cycle +
/// lint operate on, and the lint severity gate.
pub struct SweepOptions {
    pub inbox_root: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    /// Lint fails the sweep if any finding is at or above this severity.
    pub lint_threshold: Severity,
}

/// One input's run-cycle result. `reason` is set only on failure (a factory
/// error, a run-cycle error, or a report that did not fully succeed).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CycleOutcome {
    pub input: String,
    pub run_id: String,
    pub succeeded: bool,
    pub reason: Option<String>,
}

/// A discovered input that was NOT run (v1: an empty/whitespace-only markdown
/// file). Logged, never silently dropped.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SkippedInput {
    pub input: String,
    pub reason: String,
}

/// Everything one sweep did. Serializable so the CLI can dump it (`--json`).
#[derive(Debug, Clone, Serialize)]
pub struct AutoReport {
    /// Total markdown files discovered (== `cycles.len() + skipped.len()`).
    pub considered: usize,
    pub cycles: Vec<CycleOutcome>,
    pub skipped: Vec<SkippedInput>,
    /// The single post-sweep health pass over the resulting vault + canonical.
    pub lint: LintReport,
    pub lint_threshold: String,
    pub lint_passed: bool,
}

impl AutoReport {
    pub fn cycles_succeeded(&self) -> usize {
        self.cycles.iter().filter(|c| c.succeeded).count()
    }

    pub fn cycles_failed(&self) -> usize {
        self.cycles.iter().filter(|c| !c.succeeded).count()
    }

    /// The sweep succeeded iff every cycle succeeded AND lint passed the gate.
    /// Skipped inputs are informational and do not fail the sweep.
    pub fn succeeded(&self) -> bool {
        self.lint_passed && self.cycles.iter().all(|c| c.succeeded)
    }
}

/// A sweep failed before a meaningful report could exist. Per-input run-cycle
/// failures are carried in the report as failed `CycleOutcome`s, not here — so
/// this is reserved for "could not even enumerate the work".
#[derive(Debug)]
pub enum AutoError {
    /// The inbox root is missing or could not be walked. Fail-loud: never a
    /// silent "0 files considered".
    Discovery(String),
}

impl std::fmt::Display for AutoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AutoError::Discovery(m) => write!(f, "input discovery: {m}"),
        }
    }
}

impl std::error::Error for AutoError {}

/// The one-shot automation sweep. Mirrors `Lint::check` in shape (a unit struct
/// with one associated entry point).
pub struct AutoRun;

impl AutoRun {
    /// Discover markdown under `opts.inbox_root`, run [`RunCycle`] on each input
    /// (via `make_inputs`), then run [`Lint`] once and assemble the report.
    ///
    /// `make_inputs` is called once per non-empty input file and returns its
    /// fully-wired [`RunCycleInputs`] (spec + wiring + roots + mode). Keeping it
    /// a caller responsibility is what stops `ovp-auto` from duplicating the L4
    /// wiring; the sweep itself owns only discovery, the loop, lint, and the
    /// report.
    pub fn sweep<F>(opts: &SweepOptions, make_inputs: F) -> Result<AutoReport, AutoError>
    where
        F: FnMut(&Path) -> Result<RunCycleInputs, String>,
    {
        Self::sweep_with_progress(opts, make_inputs, |_, _, _| {})
    }

    /// [`sweep`](Self::sweep) with a per-file progress callback.
    ///
    /// `on_progress(i, total, label)` fires once per discovered input, in
    /// discovery order, with a **1-based** index BEFORE that input runs — so a
    /// watched auto-run streams `[i/total] <file>` and the last call reads
    /// `[total/total]`. The sweep itself stays print-free (a leaf automation
    /// crate); the CLI renders the flushed line. `total` counts every
    /// discovered file, including the empty ones the loop then skips, so the
    /// index sequence is contiguous.
    pub fn sweep_with_progress<F, P>(
        opts: &SweepOptions,
        mut make_inputs: F,
        mut on_progress: P,
    ) -> Result<AutoReport, AutoError>
    where
        F: FnMut(&Path) -> Result<RunCycleInputs, String>,
        P: FnMut(usize, usize, &str),
    {
        // Discover. Explicit existence check first so a typo'd inbox is loud
        // (a missing root otherwise walks to an empty list). Any I/O error while
        // walking is also loud — never a silent empty sweep.
        if !opts.inbox_root.exists() {
            return Err(AutoError::Discovery(format!(
                "inbox root does not exist: {}",
                opts.inbox_root.display()
            )));
        }
        let files = walk_markdown(&opts.inbox_root).map_err(|e| {
            AutoError::Discovery(format!("walking inbox `{}`: {e}", opts.inbox_root.display()))
        })?;

        let cycle = RunCycle::new();
        let total = files.len();
        let mut cycles = Vec::new();
        let mut skipped = Vec::new();

        for (i, (rel, content)) in files.iter().enumerate() {
            let abs = opts.inbox_root.join(rel);
            let label = abs.display().to_string();
            on_progress(i + 1, total, &label);

            if content.trim().is_empty() {
                skipped.push(SkippedInput { input: label, reason: "empty markdown file".into() });
                continue;
            }

            cycles.push(run_one(&cycle, &mut make_inputs, &abs, label));
        }

        // One health pass over the resulting state.
        let lint = Lint::check(&opts.vault_root, &opts.canonical_root);
        let lint_passed = lint.passed(opts.lint_threshold);

        Ok(AutoReport {
            considered: files.len(),
            cycles,
            skipped,
            lint,
            lint_threshold: opts.lint_threshold.as_str().to_string(),
            lint_passed,
        })
    }
}

/// Build inputs for one file and run the cycle, mapping every failure mode to a
/// loud `CycleOutcome` reason (never a silent skip).
fn run_one<F>(cycle: &RunCycle, make_inputs: &mut F, input: &Path, label: String) -> CycleOutcome
where
    F: FnMut(&Path) -> Result<RunCycleInputs, String>,
{
    let inputs = match make_inputs(input) {
        Ok(i) => i,
        Err(e) => {
            return CycleOutcome {
                input: label,
                run_id: String::new(),
                succeeded: false,
                reason: Some(format!("preparing inputs: {e}")),
            };
        }
    };
    match cycle.execute(inputs) {
        Ok(report) => {
            let succeeded = report.succeeded();
            let reason = if succeeded {
                None
            } else {
                Some(
                    report
                        .derived_skipped_reason
                        .clone()
                        .unwrap_or_else(|| "one or more ops failed".into()),
                )
            };
            CycleOutcome { input: label, run_id: report.run_id, succeeded, reason }
        }
        Err(e) => CycleOutcome {
            input: label,
            run_id: String::new(),
            succeeded: false,
            reason: Some(format!("run-cycle error: {e}")),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A factory that must never be invoked (used by tests where no cycle runs).
    fn unused_factory(_: &Path) -> Result<RunCycleInputs, String> {
        panic!("make_inputs should not be called");
    }

    fn opts(inbox: &Path, vault: &Path, canon: &Path, threshold: Severity) -> SweepOptions {
        SweepOptions {
            inbox_root: inbox.to_path_buf(),
            vault_root: vault.to_path_buf(),
            canonical_root: canon.to_path_buf(),
            lint_threshold: threshold,
        }
    }

    #[test]
    fn missing_inbox_is_loud_not_empty() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        let err = AutoRun::sweep(
            &opts(Path::new("/no/such/inbox"), vault.path(), canon.path(), Severity::Error),
            unused_factory,
        )
        .unwrap_err();
        assert!(matches!(err, AutoError::Discovery(_)), "got {err:?}");
    }

    #[test]
    fn empty_markdown_is_skipped_not_failed() {
        let inbox = tempfile::tempdir().unwrap();
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        std::fs::write(inbox.path().join("blank.md"), "   \n\t\n").unwrap();

        let report =
            AutoRun::sweep(&opts(inbox.path(), vault.path(), canon.path(), Severity::Error), unused_factory)
                .unwrap();

        assert_eq!(report.considered, 1);
        assert_eq!(report.skipped.len(), 1);
        assert_eq!(report.skipped[0].reason, "empty markdown file");
        assert!(report.cycles.is_empty(), "an empty file is skipped, not run");
    }

    #[test]
    fn lint_failure_fails_the_sweep() {
        // An empty inbox (so no cycle runs) over a corrupt canonical store: the
        // post-sweep lint surfaces a `canonical.unparseable` error, so the gate
        // does not pass and the whole sweep is not a success.
        let inbox = tempfile::tempdir().unwrap();
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        std::fs::write(canon.path().join("broken.json"), "not json").unwrap();

        let report =
            AutoRun::sweep(&opts(inbox.path(), vault.path(), canon.path(), Severity::Error), unused_factory)
                .unwrap();

        assert!(report.cycles.is_empty());
        assert!(!report.lint_passed, "corrupt canonical → lint error → gate fails");
        assert!(!report.succeeded(), "a failing lint gate fails the sweep");
        assert!(report.lint.findings.iter().any(|f| f.code == "canonical.unparseable"));
    }

    #[test]
    fn progress_callback_fires_per_input_in_order() {
        // Three discovered files → three callbacks with 1-based contiguous
        // indices, all reporting the same total, in discovery order. Content is
        // whitespace-only so no cycle runs (make_inputs stays unused) — the
        // progress hook must still fire for every discovered file, including
        // skipped ones.
        let inbox = tempfile::tempdir().unwrap();
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        std::fs::write(inbox.path().join("a.md"), " ").unwrap();
        std::fs::write(inbox.path().join("b.md"), " ").unwrap();
        std::fs::write(inbox.path().join("c.md"), " ").unwrap();

        let mut calls: Vec<(usize, usize, String)> = Vec::new();
        let report = AutoRun::sweep_with_progress(
            &opts(inbox.path(), vault.path(), canon.path(), Severity::Error),
            unused_factory,
            |i, total, label| calls.push((i, total, label.to_string())),
        )
        .unwrap();

        assert_eq!(report.considered, 3);
        assert_eq!(calls.len(), 3, "one callback per discovered file");
        // 1-based, contiguous, constant total.
        assert_eq!(calls.iter().map(|(i, _, _)| *i).collect::<Vec<_>>(), vec![1, 2, 3]);
        assert!(calls.iter().all(|(_, t, _)| *t == 3), "total is constant");
        // Order is discovery order (walk_markdown is sorted): a, b, c.
        assert!(calls[0].2.ends_with("a.md"));
        assert!(calls[1].2.ends_with("b.md"));
        assert!(calls[2].2.ends_with("c.md"));
    }

    #[test]
    fn succeeded_requires_clean_cycles_and_passing_lint() {
        let base = AutoReport {
            considered: 1,
            cycles: vec![CycleOutcome {
                input: "a.md".into(),
                run_id: "r".into(),
                succeeded: true,
                reason: None,
            }],
            skipped: vec![],
            lint: LintReport { findings: vec![] },
            lint_threshold: "error".into(),
            lint_passed: true,
        };
        assert!(base.succeeded());
        assert_eq!(base.cycles_succeeded(), 1);
        assert_eq!(base.cycles_failed(), 0);

        let failed_cycle = AutoReport {
            cycles: vec![CycleOutcome {
                input: "a.md".into(),
                run_id: String::new(),
                succeeded: false,
                reason: Some("boom".into()),
            }],
            ..base.clone()
        };
        assert!(!failed_cycle.succeeded(), "a failed cycle fails the sweep");

        let failed_lint = AutoReport { lint_passed: false, ..base };
        assert!(!failed_lint.succeeded(), "a failed lint gate fails the sweep");
    }
}
