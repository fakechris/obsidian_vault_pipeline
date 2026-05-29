//! OVP Next L4 — the operational workflow (`RunCycle`).
//!
//! Composes the lower layers into one operation: assemble (L2) → run (L0) →
//! apply (L3) → rebuild derived state (MOC + knowledge index) → report. It owns
//! no domain logic of its own; it wires L1–L3 together. See
//! `docs/stage-operational-workflow.md`.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;

use ovp_app::{AppWiring, AssemblyError, DomainPipelineSpec, GraphAssembler};
use ovp_core::{ApplyMode, ApplyReport, CoreError, PlanApplier};
use ovp_domain::{
    extract_wikilinks, CanonicalConcept, KnowledgeIndex, KnowledgeIndexBuilder, MocBuilder,
};
use ovp_stores::{walk_markdown, CanonicalFsStoreApplier, CompositePlanApplier, VaultFsPlanApplier};
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
}

impl DerivedRebuild {
    fn from_report(artifact: &str, report: &ApplyReport) -> Self {
        let c = report.counts();
        Self {
            artifact: artifact.to_string(),
            applied: c.applied,
            skipped: c.skipped,
            failed: c.failed,
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
    /// Set when derived rebuild was skipped because the main apply failed or the
    /// canonical store could not be read/parsed. Makes the failure loud.
    pub derived_skipped_reason: Option<String>,
}

impl RunCycleReport {
    /// True iff nothing failed and nothing was skipped due to failure.
    pub fn succeeded(&self) -> bool {
        self.derived_skipped_reason.is_none()
            && self.apply.counts().failed == 0
            && self.moc.as_ref().is_none_or(|m| m.failed == 0)
            && self.knowledge_index.as_ref().is_none_or(|k| k.failed == 0)
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
        };

        // If the main apply failed, do NOT rebuild derived state (fail-closed).
        let failed = out.apply.counts().failed;
        if failed > 0 {
            out.derived_skipped_reason = Some(format!("main apply had {failed} failed op(s)"));
            return Ok(out);
        }

        // 4. Read the canonical store strictly. A corrupt store must not produce
        //    a half-built MOC/index.
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

        // 5. Rebuild the MOC from the canonical store.
        let mut vault_applier = VaultFsPlanApplier::new(vault_root.clone());
        let moc_builder = MocBuilder::new();
        let current_moc = read_vault_file(&vault_root, moc_builder.moc_path().as_str());
        let moc_plan = moc_builder.plan_rebuild(run_id.clone(), &concepts, current_moc.as_deref());
        let moc_apply = vault_applier.apply(&moc_plan, mode);
        out.moc = Some(DerivedRebuild::from_report("moc", &moc_apply));

        // 6. Rebuild the knowledge index from canonical + vault backlinks. The
        //    MOC is excluded from the backlink scan (it links every concept; it
        //    is a derived index, not a real reference).
        let ki_builder = KnowledgeIndexBuilder::new();
        let backlinks = scan_backlinks(&vault_root, moc_builder.moc_path().as_str());
        let index = KnowledgeIndex::build(&concepts, &backlinks);
        let current_index = read_vault_file(&vault_root, ki_builder.index_path().as_str());
        let ki_plan = ki_builder.plan_rebuild(run_id.clone(), &index, current_index.as_deref());
        let ki_apply = vault_applier.apply(&ki_plan, mode);
        out.knowledge_index = Some(DerivedRebuild::from_report("knowledge_index", &ki_apply));

        Ok(out)
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

/// Scan the vault for `[[slug]]` backlinks → `slug → sorted note paths`,
/// excluding `exclude_rel` (the derived MOC, which links every concept and must
/// not count as a backlink source). A missing vault yields no backlinks.
fn scan_backlinks(vault_root: &Path, exclude_rel: &str) -> BTreeMap<String, Vec<String>> {
    let mut map: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (path, content) in walk_markdown(vault_root).unwrap_or_default() {
        if path == exclude_rel {
            continue;
        }
        for slug in extract_wikilinks(&content) {
            map.entry(slug).or_default().push(path.clone());
        }
    }
    map
}
