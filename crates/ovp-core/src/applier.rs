use serde::{Deserialize, Serialize};

use crate::plan::{OpId, WritePlan};
use crate::record::RunId;

/// Applies a `WritePlan` to a real store (filesystem, canonical store,
/// event log, ...). Sync on purpose — like `ModelClient`, impls may
/// hide async machinery behind a blocking call, but the trait stays
/// out of the runner's async story.
///
/// Per invariant #10, this is the *only* type allowed to mutate
/// real stores. Transforms produce records, Sinks produce WriteOps,
/// PlanApplier produces side effects.
pub trait PlanApplier {
    fn apply(&mut self, plan: &WritePlan, mode: ApplyMode) -> ApplyReport;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApplyMode {
    /// Perform the writes. Default for production.
    Apply,
    /// Walk the plan and report what *would* happen without touching
    /// any external state. Every successful-shaped op records as
    /// `Skipped { reason: "dry-run" }`.
    DryRun,
}

/// Outcome of applying a whole plan. Order matches the plan's op order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApplyReport {
    pub run_id: RunId,
    pub mode: String,
    pub outcomes: Vec<OpOutcome>,
}

impl ApplyReport {
    pub fn new(run_id: RunId, mode: ApplyMode) -> Self {
        Self { run_id, mode: mode_str(mode).to_string(), outcomes: Vec::new() }
    }

    pub fn push(&mut self, outcome: OpOutcome) {
        self.outcomes.push(outcome);
    }

    pub fn counts(&self) -> ApplyCounts {
        let mut c = ApplyCounts::default();
        for o in &self.outcomes {
            match &o.result {
                OpResult::Applied => c.applied += 1,
                OpResult::Skipped { .. } => c.skipped += 1,
                OpResult::Failed { .. } => c.failed += 1,
                OpResult::Unsupported => c.unsupported += 1,
            }
        }
        c
    }

    /// True iff no `Failed` outcomes. `Unsupported` does not fail a report —
    /// an op kind this applier doesn't handle is not a hard error, but it
    /// IS something the operator must see (the work silently didn't happen).
    /// Callers that route ops to the wrong applier should consult
    /// [`Self::has_unsupported`] and surface it; see `ovp-cli apply-plan`.
    pub fn all_ok(&self) -> bool {
        !self.outcomes.iter().any(|o| matches!(o.result, OpResult::Failed { .. }))
    }

    /// True if any op was `Unsupported` (this applier doesn't handle that
    /// kind). Distinct from failure: the plan was well-formed, but this
    /// applier skipped ops it can't perform. Operators should never see
    /// this pass silently — once a producer of `CanonicalUpsert` /
    /// `EventAppend` lands, those ops need an applier that handles them.
    pub fn has_unsupported(&self) -> bool {
        self.outcomes.iter().any(|o| matches!(o.result, OpResult::Unsupported))
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ApplyCounts {
    pub applied: u32,
    pub skipped: u32,
    pub failed: u32,
    pub unsupported: u32,
}

/// Per-op outcome record. `kind` is duplicated from the op itself so
/// readers of the report don't need the original plan to interpret it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpOutcome {
    pub op_id: OpId,
    pub kind: OpKind,
    pub result: OpResult,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OpKind {
    VaultCreate,
    VaultUpdate,
    CanonicalUpsert,
    EventAppend,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum OpResult {
    Applied,
    /// Skipped because dry-run, or because the target was already in
    /// the desired state (idempotent application).
    Skipped { reason: String },
    /// Skipped because this PlanApplier doesn't handle this op kind.
    /// `EventAppend` and `CanonicalUpsert` on `VaultFsPlanApplier` v1.
    Unsupported,
    /// Failed: path escape, hash mismatch, IO error, etc.
    Failed { reason: String },
}

fn mode_str(m: ApplyMode) -> &'static str {
    match m {
        ApplyMode::Apply => "apply",
        ApplyMode::DryRun => "dry_run",
    }
}
