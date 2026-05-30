//! OVP Next L4 — the operational workflow (`RunCycle`).
//!
//! Composes the lower layers into one operation: assemble (L2) → run (L0) →
//! apply (L3) → rebuild derived state (MOC + knowledge index) → report. It owns
//! no domain logic of its own; it wires L1–L3 together. See
//! `docs/stage-operational-workflow.md`.

use std::path::Path;
use std::path::PathBuf;

use ovp_app::{AppWiring, AssemblyError, DomainPipelineSpec, GraphAssembler};
use ovp_core::{ApplyMode, ApplyReport, CoreError, PlanApplier};
use ovp_domain::{
    extract_wikilinks, CanonicalConcept, KnowledgeIndex, KnowledgeIndexBuilder, MocBuilder,
};
use ovp_stores::{scan_backlinks, CanonicalFsStoreApplier, CompositePlanApplier, VaultFsPlanApplier};
use serde::Serialize;

/// What a run-cycle needs: a parsed spec, the runtime wiring (which owns the
/// move-only `ModelClient`), the two store roots, and the apply mode.
pub struct RunCycleInputs {
    pub spec: DomainPipelineSpec,
    pub wiring: AppWiring,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub mode: ApplyMode,
}

/// A one-artifact derived-rebuild summary (the MOC or the knowledge index).
#[derive(Debug, Clone, Serialize)]
pub struct DerivedRebuild {
    pub artifact: String,
    pub applied: u32,
    pub skipped: u32,
    pub failed: u32,
    pub unsupported: u32,
}

impl DerivedRebuild {
    fn from_report(artifact: &str, report: &ApplyReport) -> Self {
        let c = report.counts();
        Self {
            artifact: artifact.to_string(),
            applied: c.applied,
            skipped: c.skipped,
            failed: c.failed,
            unsupported: c.unsupported,
        }
    }
}

/// Everything one run-cycle did. Serializable so the CLI can dump it (`--report`).
#[derive(Debug, Clone, Serialize)]
pub struct RunCycleReport {
    pub run_id: String,
    pub records_seen: u64,
    pub records_forwarded_to_sinks: u64,
    pub records_dropped: u64,
    pub ops_emitted: usize,
    /// The main composite apply (vault notes + evergreen stubs + canonical).
    pub apply: ApplyReport,
    /// MOC rebuild summary, or `None` if derived rebuild was skipped.
    pub moc: Option<DerivedRebuild>,
    /// Knowledge-index rebuild summary, or `None` if skipped.
    pub knowledge_index: Option<DerivedRebuild>,
    /// Set when derived rebuild was skipped: the main apply was not clean
    /// (failed OR unsupported ops), the canonical store could not be
    /// read/parsed, or the vault backlink scan failed. Makes the failure loud.
    pub derived_skipped_reason: Option<String>,
    /// True if this was a `--dry-run` (`ApplyMode::DryRun`): nothing was written.
    /// In dry-run the derived previews reflect the **current on-disk** canonical
    /// store, NOT a speculative "as if the main plan had applied" state — the
    /// main apply's canonical writes did not happen. A preview, not a simulation.
    pub dry_run: bool,
}

impl RunCycleReport {
    /// True iff the full cycle landed cleanly: nothing was skipped due to
    /// failure, and no op anywhere `Failed` or was left `Unsupported`. An
    /// unsupported op means a `WriteOp` no applier handled — the cycle did NOT
    /// fully apply, so it is not a success.
    pub fn succeeded(&self) -> bool {
        self.derived_skipped_reason.is_none()
            && self.apply.counts().failed == 0
            && self.apply.counts().unsupported == 0
            && self.moc.as_ref().is_none_or(|m| m.failed == 0 && m.unsupported == 0)
            && self.knowledge_index.as_ref().is_none_or(|k| k.failed == 0 && k.unsupported == 0)
    }
}

/// Errors that stop a run-cycle before a meaningful report can exist. Soft
/// failures (a failed apply op, an unparseable canonical store) are carried in
/// the `RunCycleReport` instead, so the report stays the single output.
#[derive(Debug)]
pub enum RunCycleError {
    /// Assembly failed; nothing ran and nothing was written.
    Assemble(AssemblyError),
    /// The graph errored at run time; the plan was never applied.
    GraphRun(CoreError),
}

impl std::fmt::Display for RunCycleError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunCycleError::Assemble(e) => write!(f, "assembly failed: {e}"),
            RunCycleError::GraphRun(e) => write!(f, "graph run failed: {e}"),
        }
    }
}

impl std::error::Error for RunCycleError {}

/// The operational run-cycle. Holds the assembler (the compiled-in node set by
/// default) and executes the full ingest → apply → rebuild-derived flow.
pub struct RunCycle {
    assembler: GraphAssembler,
}

impl RunCycle {
    pub fn new() -> Self {
        Self { assembler: GraphAssembler::with_domain_nodes() }
    }

    pub fn with_assembler(assembler: GraphAssembler) -> Self {
        Self { assembler }
    }

    pub fn execute(&self, inputs: RunCycleInputs) -> Result<RunCycleReport, RunCycleError> {
        let RunCycleInputs { spec, wiring, vault_root, canonical_root, mode } = inputs;
        let run_id = wiring.run_id().clone();

        // 1. Assemble. On failure: nothing ran, nothing written.
        let runner = self.assembler.assemble(&spec, wiring).map_err(RunCycleError::Assemble)?;

        // 2. Run the graph. On failure: the plan is never applied.
        let report = runner.run().map_err(RunCycleError::GraphRun)?;

        // 3. Apply the main plan via the composite (vault + canonical).
        let mut composite = CompositePlanApplier::new(vec![
            Box::new(VaultFsPlanApplier::new(vault_root.clone())),
            Box::new(CanonicalFsStoreApplier::new(canonical_root.clone())),
        ]);
        let apply = composite.apply(&report.write_plan, mode);

        let mut out = RunCycleReport {
            run_id: run_id.as_str().to_string(),
            records_seen: report.records_seen,
            records_forwarded_to_sinks: report.records_forwarded_to_sinks,
            records_dropped: report.records_dropped,
            ops_emitted: report.write_plan.len(),
            apply,
            moc: None,
            knowledge_index: None,
            derived_skipped_reason: None,
            dry_run: matches!(mode, ApplyMode::DryRun),
        };

        // If the main apply was not clean — any FAILED or UNSUPPORTED op — do NOT
        // rebuild derived state (fail-closed). An unsupported op means a WriteOp
        // no applier handled; rebuilding the MOC/index on top of a partially-
        // applied plan would be inconsistent.
        if let Some(reason) = main_apply_block(&out.apply) {
            out.derived_skipped_reason = Some(reason);
            return Ok(out);
        }

        // --- Derived rebuild: do ALL prerequisite reads FIRST; only write once
        //     every input is in hand, so a read failure leaves zero partial
        //     derived state. ---

        // 4. Read + parse the canonical store strictly.
        let store = CanonicalFsStoreApplier::new(canonical_root.clone());
        let pairs = match store.read_all() {
            Ok(p) => p,
            Err(e) => {
                out.derived_skipped_reason = Some(format!("reading canonical store: {e}"));
                return Ok(out);
            }
        };
        let concepts = match CanonicalConcept::try_parse_pairs(pairs) {
            Ok(c) => c,
            Err(e) => {
                out.derived_skipped_reason = Some(format!("canonical store unparseable: {e}"));
                return Ok(out);
            }
        };

        // 5. Scan vault backlinks (excluding the derived MOC, which links every
        //    concept and is not a real reference). Fail LOUD on an I/O error —
        //    never silently treat a scan failure as "no backlinks", which would
        //    rebuild the index into an empty reference graph.
        let moc_builder = MocBuilder::new();
        let ki_builder = KnowledgeIndexBuilder::new();
        let backlinks =
            match scan_backlinks(&vault_root, moc_builder.moc_path().as_str(), extract_wikilinks) {
                Ok(b) => b,
                Err(e) => {
                    out.derived_skipped_reason = Some(format!("scanning vault backlinks: {e}"));
                    return Ok(out);
                }
            };

        // 6. Read current derived artifacts + build BOTH rebuild plans. No writes
        //    yet — all derived inputs are validated above before anything lands.
        let current_moc = read_vault_file(&vault_root, moc_builder.moc_path().as_str());
        let moc_plan = moc_builder.plan_rebuild(run_id.clone(), &concepts, current_moc.as_deref());
        let index = KnowledgeIndex::build(&concepts, &backlinks);
        let current_index = read_vault_file(&vault_root, ki_builder.index_path().as_str());
        let ki_plan = ki_builder.plan_rebuild(run_id.clone(), &index, current_index.as_deref());

        // 7. Apply both derived plans.
        let mut vault_applier = VaultFsPlanApplier::new(vault_root.clone());
        let moc_apply = vault_applier.apply(&moc_plan, mode);
        out.moc = Some(DerivedRebuild::from_report("moc", &moc_apply));
        let ki_apply = vault_applier.apply(&ki_plan, mode);
        out.knowledge_index = Some(DerivedRebuild::from_report("knowledge_index", &ki_apply));

        Ok(out)
    }
}

/// If the main apply was not clean, return the reason derived rebuild must be
/// skipped. Both `Failed` and `Unsupported` block it: a full operational cycle
/// requires every emitted `WriteOp` to be applied by some backend.
fn main_apply_block(apply: &ApplyReport) -> Option<String> {
    let c = apply.counts();
    if c.failed > 0 || c.unsupported > 0 {
        Some(format!(
            "main apply not clean: {} failed, {} unsupported op(s)",
            c.failed, c.unsupported
        ))
    } else {
        None
    }
}

impl Default for RunCycle {
    fn default() -> Self {
        Self::new()
    }
}

/// Read a vault-relative file's current content, if present (for rebuild diffs).
fn read_vault_file(vault_root: &Path, rel: &str) -> Option<String> {
    std::fs::read_to_string(vault_root.join(rel)).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_core::{OpId, OpKind, OpOutcome, OpResult, RunId};

    fn apply_with(result: OpResult, kind: OpKind) -> ApplyReport {
        let mut r = ApplyReport::new(RunId::new("t"), ApplyMode::Apply);
        r.push(OpOutcome { op_id: OpId::new("op"), kind, result });
        r
    }

    #[test]
    fn main_apply_block_flags_failed_and_unsupported() {
        let failed = apply_with(OpResult::Failed { reason: "x".into() }, OpKind::VaultCreate);
        assert!(main_apply_block(&failed).is_some(), "failed op must block derived rebuild");

        let unsupported = apply_with(OpResult::Unsupported, OpKind::EventAppend);
        assert!(
            main_apply_block(&unsupported).is_some(),
            "unsupported op must block derived rebuild (no applier handled it)"
        );

        let mut clean = ApplyReport::new(RunId::new("t"), ApplyMode::Apply);
        clean.push(OpOutcome {
            op_id: OpId::new("op"),
            kind: OpKind::VaultCreate,
            result: OpResult::Applied,
        });
        clean.push(OpOutcome {
            op_id: OpId::new("op2"),
            kind: OpKind::CanonicalUpsert,
            result: OpResult::Skipped { reason: "idempotent".into() },
        });
        assert!(main_apply_block(&clean).is_none(), "applied/skipped is clean");
    }

    #[test]
    fn succeeded_treats_unsupported_as_failure() {
        let report = RunCycleReport {
            run_id: "t".into(),
            records_seen: 0,
            records_forwarded_to_sinks: 0,
            records_dropped: 0,
            ops_emitted: 1,
            apply: apply_with(OpResult::Unsupported, OpKind::EventAppend),
            moc: None,
            knowledge_index: None,
            derived_skipped_reason: None,
            dry_run: false,
        };
        assert!(!report.succeeded(), "a cycle with an unsupported main op has not succeeded");
    }
}
