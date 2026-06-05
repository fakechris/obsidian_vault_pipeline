//! OVP Next L4 — the operational workflow (`RunCycle`).
//!
//! Composes the lower layers into one operation: assemble (L2) → run (L0) →
//! apply (L3) → rebuild derived state (MOC + knowledge index) → report. It owns
//! no domain logic of its own; it wires L1–L3 together. See
//! `docs/stage-operational-workflow.md`.

use std::collections::HashMap;
use std::path::Path;
use std::path::PathBuf;

use ovp_app::{AppWiring, AssemblyError, DomainPipelineSpec, GraphAssembler};
use ovp_core::{ApplyMode, ApplyReport, CoreError, PlanApplier, RunId, RunReport, WriteOp, WritePlan};
use ovp_domain::{
    content_hash, extract_wikilinks, reconcile_evergreen_write, CanonicalConcept, KnowledgeIndex,
    KnowledgeIndexBuilder, MocBuilder, VaultLayout,
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
    /// Records that ended in `FilterDecision::Error` (e.g. an LLM call failed).
    /// Non-zero ⇒ the run did NOT complete cleanly; see `derived_skipped_reason`
    /// for the first error. Distinct from a legitimate `Drop`.
    pub records_errored: u64,
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
        self.records_errored == 0
            && self.derived_skipped_reason.is_none()
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

        // 2b. Same-slug reconcile (M12b): a concept slug already minted by a
        // PRIOR article (or earlier in THIS plan) must enrich its note, not
        // hard-fail this run. A minted evergreen `VaultCreate` whose path
        // already holds a different note is rewritten to a merge `VaultUpdate`;
        // a `CanonicalUpsert` that would overwrite an existing identity with a
        // different one is dropped (first-writer-wins — the original provenance
        // is kept, and the merged note body carries every source). Without this,
        // the second document surfacing a common slug (`rag`, `ai-agent`, ...)
        // would fail the apply and halt the run. See
        // `ovp_domain::reconcile_evergreen_write`. Fail-closed: a canonical read
        // failure here means we can't safely decide first-writer-wins, so apply
        // NOTHING rather than risk a blind overwrite of canonical provenance.
        let plan = match reconcile_same_slug(&report.write_plan, &vault_root, &canonical_root) {
            Ok(p) => p,
            Err(reason) => {
                let mut out =
                    base_report(&run_id, &report, mode, ApplyReport::new(run_id.clone(), mode));
                out.derived_skipped_reason = Some(format!("reconcile: {reason}"));
                return Ok(out);
            }
        };

        // 3. Apply the (reconciled) main plan via the composite (vault + canonical).
        let mut composite = CompositePlanApplier::new(vec![
            Box::new(VaultFsPlanApplier::new(vault_root.clone())),
            Box::new(CanonicalFsStoreApplier::new(canonical_root.clone())),
        ]);
        let apply = composite.apply(&plan, mode);

        let mut out = base_report(&run_id, &report, mode, apply);

        // Fail loud on a failed extraction. ROOT CAUSE: propagate the graph's
        // error count — a node that ended in `FilterDecision::Error` (e.g. the
        // live LLM call failing transport/decode) must NOT pass as an empty
        // success. Skip derived rebuild and report the first error.
        //
        // NOTE on multi-input semantics: this check runs AFTER the main apply.
        // For a single input (the only shape the article/paper pipelines process
        // today) a failed extraction emits zero ops, so nothing was written. If a
        // future run processed several inputs and one errored, the SUCCEEDED
        // inputs' write ops may already be on disk — this is deliberately
        // "partial main apply + loud failure" (re-apply is idempotent), NOT
        // "any record error ⇒ no writes". Derived rebuild is still skipped so no
        // derived state is built atop a partially-applied, error-tainted run.
        if report.records_errored > 0 {
            let detail = report
                .first_error
                .as_ref()
                .map(|e| format!("{}: {}", e.code.as_str(), e.detail))
                .unwrap_or_else(|| "unknown".to_string());
            out.derived_skipped_reason = Some(format!(
                "{} record(s) errored in the pipeline (first: {detail})",
                report.records_errored
            ));
            return Ok(out);
        }

        // SECONDARY BACKSTOP: a non-dry-run that saw input but produced zero
        // write ops generated nothing usable (e.g. a record silently dropped
        // after a degenerate LLM response). Every real article/paper run emits
        // at least its note's `VaultCreate`, so treat 0 ops here as a failure,
        // not an empty success. (Not relied on alone — the error count above is
        // the primary signal.)
        if !out.dry_run && report.records_seen > 0 && out.ops_emitted == 0 {
            out.derived_skipped_reason = Some(
                "pipeline saw input but produced no write ops (likely a failed or empty LLM extraction)"
                    .to_string(),
            );
            return Ok(out);
        }

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

/// Build the base run-cycle report (before any derived rebuild) from the graph
/// report + the main apply outcome. `ops_emitted` is the PIPELINE's emission
/// (`report.write_plan.len()`), not the reconciled plan's, so the fail-loud
/// backstops still see what the pipeline produced.
fn base_report(
    run_id: &RunId,
    report: &RunReport,
    mode: ApplyMode,
    apply: ApplyReport,
) -> RunCycleReport {
    RunCycleReport {
        run_id: run_id.as_str().to_string(),
        records_seen: report.records_seen,
        records_forwarded_to_sinks: report.records_forwarded_to_sinks,
        records_dropped: report.records_dropped,
        records_errored: report.records_errored,
        ops_emitted: report.write_plan.len(),
        apply,
        moc: None,
        knowledge_index: None,
        derived_skipped_reason: None,
        dry_run: matches!(mode, ApplyMode::DryRun),
    }
}

/// Reconcile a freshly-emitted plan against on-disk state **and the ops already
/// folded earlier in this same plan**, so a repeated concept slug enriches
/// instead of failing the apply (M12b). Evergreen `VaultCreate` ops are routed
/// through [`reconcile_evergreen_write`] (MintNew / keep / EnrichExisting
/// `VaultUpdate` / skip); a `CanonicalUpsert` that would re-register an existing
/// identity with a *different* payload is dropped (first-writer-wins, preserving
/// the original provenance). The in-plan state makes this robust even when one
/// run emits several documents that surface the same new slug (a multi-document
/// source), not just the cross-run case. Everything else — the article/paper
/// note `VaultCreate`, brand-new identities, identical re-registers — passes
/// through unchanged, so the same-input idempotent re-run is preserved.
///
/// **Fail-closed:** if the canonical store can't be read, returns `Err` so the
/// caller applies nothing rather than risk a blind overwrite of canonical
/// provenance (a `CanonicalUpsert` carries `before_hash: None`, so the reconcile
/// drop is the *only* guard for an existing identity).
fn reconcile_same_slug(
    plan: &WritePlan,
    vault_root: &Path,
    canonical_root: &Path,
) -> Result<WritePlan, String> {
    let evergreen_prefix = format!("{}/", VaultLayout::new().evergreen_dir());
    // Existing canonical identities, keyed slug → payload content hash. Read
    // strictly (matches the derived-rebuild read): a fresh/absent store is
    // `Ok(empty)`, but a genuine I/O/corruption fault is fail-closed.
    let existing_canon: HashMap<String, String> = CanonicalFsStoreApplier::new(canonical_root)
        .read_all()
        .map_err(|e| format!("reading canonical store: {e}"))?
        .into_iter()
        .map(|(k, payload)| (k, content_hash(payload.as_bytes())))
        .collect();

    // In-plan state: the body each evergreen path will hold after the ops
    // already emitted, and the canonical keys already registered, in THIS plan.
    let mut in_plan_notes: HashMap<String, String> = HashMap::new();
    let mut in_plan_keys: std::collections::HashSet<String> = std::collections::HashSet::new();

    let mut out = WritePlan::new(plan.run_id.clone());
    for op in &plan.ops {
        match op {
            WriteOp::VaultCreate(c) if c.path.as_str().starts_with(&evergreen_prefix) => {
                let path = c.path.as_str();
                // Effective existing = what an earlier op in this plan will
                // write to the path, else what is on disk.
                let existing = in_plan_notes
                    .get(path)
                    .cloned()
                    .or_else(|| read_vault_file(vault_root, path));
                match reconcile_evergreen_write(c, existing.as_deref()) {
                    Some(write) => {
                        let landed = match &write {
                            WriteOp::VaultCreate(o) => o.body.clone(),
                            WriteOp::VaultUpdate(o) => o.body.clone(),
                            _ => existing.clone().unwrap_or_default(),
                        };
                        in_plan_notes.insert(path.to_string(), landed);
                        out.push(write);
                    }
                    None => {
                        // Skipped (nothing new / unknown-format): the path's body
                        // is unchanged, so remember it for later ops in the plan.
                        if let Some(body) = existing {
                            in_plan_notes.insert(path.to_string(), body);
                        }
                    }
                }
            }
            WriteOp::CanonicalUpsert(u) => {
                let key = u.key.as_str();
                let disk_conflict =
                    matches!(existing_canon.get(key), Some(h) if h != u.after_hash.as_str());
                // An earlier op in this plan already registered this identity →
                // first-writer-wins (drop), regardless of payload.
                let in_plan_conflict = in_plan_keys.contains(key);
                if disk_conflict || in_plan_conflict {
                    // drop: preserve the first registration's provenance.
                } else {
                    in_plan_keys.insert(key.to_string());
                    out.push(op.clone());
                }
            }
            other => out.push(other.clone()),
        }
    }
    Ok(out)
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

    fn clean_report() -> RunCycleReport {
        let mut apply = ApplyReport::new(RunId::new("t"), ApplyMode::Apply);
        apply.push(OpOutcome {
            op_id: OpId::new("op"),
            kind: OpKind::VaultCreate,
            result: OpResult::Applied,
        });
        RunCycleReport {
            run_id: "t".into(),
            records_seen: 1,
            records_forwarded_to_sinks: 1,
            records_dropped: 0,
            records_errored: 0,
            ops_emitted: 1,
            apply,
            moc: Some(DerivedRebuild { artifact: "moc".into(), applied: 1, skipped: 0, failed: 0, unsupported: 0 }),
            knowledge_index: Some(DerivedRebuild { artifact: "knowledge_index".into(), applied: 1, skipped: 0, failed: 0, unsupported: 0 }),
            derived_skipped_reason: None,
            dry_run: false,
        }
    }

    #[test]
    fn succeeded_treats_unsupported_as_failure() {
        let report = RunCycleReport {
            run_id: "t".into(),
            records_seen: 0,
            records_forwarded_to_sinks: 0,
            records_dropped: 0,
            records_errored: 0,
            ops_emitted: 1,
            apply: apply_with(OpResult::Unsupported, OpKind::EventAppend),
            moc: None,
            knowledge_index: None,
            derived_skipped_reason: None,
            dry_run: false,
        };
        assert!(!report.succeeded(), "a cycle with an unsupported main op has not succeeded");
    }

    #[test]
    fn succeeded_requires_zero_errored_records() {
        let clean = clean_report();
        assert!(clean.succeeded(), "baseline clean report should succeed");
        // An errored record fails the cycle even when every applied op is clean.
        let errored = RunCycleReport { records_errored: 1, ..clean_report() };
        assert!(!errored.succeeded(), "a cycle with an errored record must not succeed");
    }

    // ---- M12b: reconcile_same_slug ----

    use ovp_core::{CanonicalKey, CanonicalUpsertOp, ContentHash, RecordId, VaultPath};
    use ovp_domain::{CanonicalConcept, EvergreenConcept, EvergreenNote};

    fn ev_create(slug: &str, def: &str, src_url: &str) -> WriteOp {
        let mut c = EvergreenConcept::from_candidate(slug, src_url);
        c.definition = def.into();
        c.source_claims = vec![format!("Claim from {src_url}.")];
        c.source_title = "Doc".into();
        let body = EvergreenNote::from_concept(&c).render();
        WriteOp::VaultCreate(ovp_core::VaultCreateOp {
            op_id: OpId::new(format!("op-ev-{slug}")),
            path: VaultPath::new(format!("10-Knowledge/Evergreen/{slug}.md")),
            after_hash: ContentHash::new(content_hash(body.as_bytes())),
            body,
            reason: "mint".into(),
            originating_record: RecordId::new("r"),
        })
    }

    fn canon_upsert(slug: &str, provenance: &str) -> WriteOp {
        let payload = CanonicalConcept {
            slug: slug.into(),
            title: "T".into(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: provenance.into(),
        }
        .to_payload();
        WriteOp::CanonicalUpsert(CanonicalUpsertOp {
            op_id: OpId::new(format!("op-canon-{slug}")),
            key: CanonicalKey::new(slug),
            before_hash: None,
            after_hash: ContentHash::new(content_hash(payload.as_bytes())),
            payload,
            reason: "register".into(),
            originating_record: RecordId::new("r"),
        })
    }

    #[test]
    fn reconcile_fails_closed_on_unreadable_canonical_store() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        // A non-UTF-8 *.json record makes read_all() err → reconcile must NOT
        // proceed (a blind apply could overwrite canonical provenance).
        std::fs::write(canon.path().join("bad.json"), [0xff, 0xfe, 0x00, 0x9f]).unwrap();
        let mut plan = WritePlan::new(RunId::new("r"));
        plan.push(ev_create("rag", "Def.", "https://a/x"));
        let res = reconcile_same_slug(&plan, vault.path(), canon.path());
        assert!(res.is_err(), "a corrupt canonical store must fail-close the reconcile");
    }

    #[test]
    fn reconcile_folds_two_same_slug_docs_within_one_plan() {
        // Two DIFFERENT documents surfacing the same NEW slug in ONE plan: the
        // first mints, the second must fold to an enrich VaultUpdate (not a
        // second colliding VaultCreate that would fail the apply).
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        let mut plan = WritePlan::new(RunId::new("r"));
        plan.push(ev_create("rag", "Definition A.", "https://a/x"));
        plan.push(canon_upsert("rag", "https://a/x"));
        plan.push(ev_create("rag", "Definition B.", "https://b/y"));
        plan.push(canon_upsert("rag", "https://b/y"));

        let out = reconcile_same_slug(&plan, vault.path(), canon.path()).unwrap();
        let kinds: Vec<&str> = out
            .ops
            .iter()
            .map(|o| match o {
                WriteOp::VaultCreate(_) => "create",
                WriteOp::VaultUpdate(_) => "update",
                WriteOp::CanonicalUpsert(_) => "canon",
                _ => "other",
            })
            .collect();
        // doc A: create + canon ; doc B: update (folded) ; doc B canon dropped.
        assert_eq!(kinds, vec!["create", "canon", "update"], "got {kinds:?}");
        // The enrich VaultUpdate's before_hash matches doc A's body (in-plan).
        let a_body = match &out.ops[0] {
            WriteOp::VaultCreate(o) => o.body.clone(),
            _ => unreachable!(),
        };
        let merged = match &out.ops[2] {
            WriteOp::VaultUpdate(o) => o,
            _ => unreachable!(),
        };
        assert_eq!(merged.before_hash.as_str(), content_hash(a_body.as_bytes()));
        assert!(merged.body.contains("https://a/x") && merged.body.contains("https://b/y"));
    }

    #[test]
    fn reconcile_drops_conflicting_disk_canonical_but_keeps_identical() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        // Seed an existing canonical record for `rag` with provenance P1.
        let mut store = CanonicalFsStoreApplier::new(canon.path());
        let mut seed = WritePlan::new(RunId::new("seed"));
        seed.push(canon_upsert("rag", "https://first/doc"));
        store.apply(&seed, ApplyMode::Apply);

        // A second document re-registers `rag` with a DIFFERENT provenance →
        // dropped (first-writer-wins); an identical re-register → kept.
        let mut plan = WritePlan::new(RunId::new("r"));
        plan.push(canon_upsert("rag", "https://second/doc")); // conflict → drop
        plan.push(canon_upsert("other", "https://x")); // new identity → keep
        let out = reconcile_same_slug(&plan, vault.path(), canon.path()).unwrap();
        let keys: Vec<&str> = out
            .ops
            .iter()
            .filter_map(|o| match o {
                WriteOp::CanonicalUpsert(u) => Some(u.key.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(keys, vec!["other"], "conflicting rag dropped, new other kept: {keys:?}");
    }
}
