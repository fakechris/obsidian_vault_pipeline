use ovp_core::{
    ApplyMode, ApplyReport, OpKind, OpOutcome, OpResult, PlanApplier, WriteOp, WritePlan,
};

/// Routes a `WritePlan`'s ops across several backend appliers that handle
/// disjoint op kinds (e.g. `VaultFsPlanApplier` for vault ops +
/// `CanonicalFsStoreApplier` for canonical ops), so a full plan applies
/// with no `Unsupported` outcomes.
///
/// Ops are applied **one at a time, in the plan's original order**. Each op
/// is routed to the first backend that handles it (the first non-`Unsupported`
/// outcome wins); if every backend reports `Unsupported`, the op is genuinely
/// unhandled and stays `Unsupported`. Backends act on disjoint kinds, so no
/// op is written twice.
///
/// **Ordering matters for correctness, not just tidiness.** A plan emits
/// paired ops — an evergreen's `VaultCreate` (the stub page) immediately
/// followed by its `CanonicalUpsert` (the canonical identity). If the
/// `VaultCreate` fails, the paired `CanonicalUpsert` must NOT be written:
/// otherwise the canonical store would register a concept whose page does
/// not exist. So the first `Failed` op HALTS the run — every later op
/// records `Skipped { reason: "previous op failed" }` and performs no I/O.
/// Re-applying after the cause is fixed recovers (every op is idempotent).
pub struct CompositePlanApplier {
    backends: Vec<Box<dyn PlanApplier>>,
}

impl CompositePlanApplier {
    pub fn new(backends: Vec<Box<dyn PlanApplier>>) -> Self {
        Self { backends }
    }
}

impl PlanApplier for CompositePlanApplier {
    fn apply(&mut self, plan: &WritePlan, mode: ApplyMode) -> ApplyReport {
        let mut report = ApplyReport::new(plan.run_id.clone(), mode);
        let mut halted = false;

        for op in &plan.ops {
            if halted {
                // A prior op failed; do not apply this one. No I/O happens —
                // we never route it to a backend.
                report.push(OpOutcome {
                    op_id: op.op_id().clone(),
                    kind: op_kind(op),
                    result: OpResult::Skipped { reason: "previous op failed".into() },
                });
                continue;
            }

            // Route this single op to the first backend that handles it.
            let single = single_op_plan(plan, op.clone());
            let mut chosen: Option<OpOutcome> = None;
            for backend in self.backends.iter_mut() {
                let mut r = backend.apply(&single, mode);
                let Some(o) = r.outcomes.pop() else { continue };
                if !matches!(o.result, OpResult::Unsupported) {
                    chosen = Some(o);
                    break;
                }
                // Remember the first Unsupported as a fallback, but keep
                // probing later backends for one that actually handles it.
                chosen.get_or_insert(o);
            }
            let outcome = chosen.unwrap_or_else(|| OpOutcome {
                op_id: op.op_id().clone(),
                kind: op_kind(op),
                result: OpResult::Unsupported,
            });

            if matches!(outcome.result, OpResult::Failed { .. }) {
                halted = true;
            }
            report.push(outcome);
        }
        report
    }
}

/// A single-op `WritePlan` sharing the parent plan's run id, so a backend
/// applies exactly one op and we read back exactly one outcome.
fn single_op_plan(plan: &WritePlan, op: WriteOp) -> WritePlan {
    let mut p = WritePlan::new(plan.run_id.clone());
    p.push(op);
    p
}

fn op_kind(op: &WriteOp) -> OpKind {
    match op {
        WriteOp::VaultCreate(_) => OpKind::VaultCreate,
        WriteOp::VaultUpdate(_) => OpKind::VaultUpdate,
        WriteOp::CanonicalUpsert(_) => OpKind::CanonicalUpsert,
        WriteOp::EventAppend(_) => OpKind::EventAppend,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{CanonicalFsStoreApplier, VaultFsPlanApplier};
    use ovp_core::{
        CanonicalKey, CanonicalUpsertOp, ContentHash, OpId, OpKind, OpResult, RecordId, RunId,
        VaultCreateOp, VaultPath, WriteOp, WritePlan,
    };
    use sha2::{Digest, Sha256};

    fn sha(b: &[u8]) -> String {
        let h = Sha256::digest(b);
        let mut s = String::new();
        use std::fmt::Write;
        for x in h.iter() {
            write!(s, "{:02x}", x).unwrap();
        }
        s
    }

    #[test]
    fn routes_vault_and_canonical_with_no_unsupported() {
        let tmp = tempfile::tempdir().unwrap();
        let vault_root = tmp.path().join("vault");
        let canon_root = tmp.path().join("canonical");

        let body = "# note\n";
        let payload = r#"{"slug":"x"}"#;
        let mut plan = WritePlan::new(RunId::new("r"));
        plan.push(WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new("v1"),
            path: VaultPath::new("notes/a.md"),
            after_hash: ContentHash::new(sha(body.as_bytes())),
            body: body.into(),
            reason: "t".into(),
            originating_record: RecordId::new("r"),
        }));
        plan.push(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
            op_id: OpId::new("c1"),
            key: CanonicalKey::new("x"),
            before_hash: None,
            after_hash: ContentHash::new(sha(payload.as_bytes())),
            payload: payload.into(),
            reason: "t".into(),
            originating_record: RecordId::new("r"),
        }));

        let mut applier = CompositePlanApplier::new(vec![
            Box::new(VaultFsPlanApplier::new(&vault_root)),
            Box::new(CanonicalFsStoreApplier::new(&canon_root)),
        ]);
        let report = applier.apply(&plan, ApplyMode::Apply);

        let counts = report.counts();
        assert_eq!(counts.applied, 2, "both ops applied by their backend");
        assert_eq!(counts.unsupported, 0, "composite leaves no Unsupported");
        assert_eq!(counts.failed, 0);
        // Each op routed to the right kind.
        assert_eq!(report.outcomes[0].kind, OpKind::VaultCreate);
        assert_eq!(report.outcomes[1].kind, OpKind::CanonicalUpsert);
        assert!(matches!(report.outcomes[0].result, OpResult::Applied));
        assert!(matches!(report.outcomes[1].result, OpResult::Applied));
        // Files landed in their respective roots.
        assert!(vault_root.join("notes/a.md").exists());
        assert!(canon_root.join("x.json").exists());
    }

    #[test]
    fn vault_failure_halts_paired_canonical_upsert() {
        // The pipeline emits an evergreen's VaultCreate (stub page)
        // immediately followed by its CanonicalUpsert (identity). If the
        // VaultCreate fails, the CanonicalUpsert MUST NOT run — otherwise the
        // canonical store registers a concept whose page does not exist.
        let tmp = tempfile::tempdir().unwrap();
        let vault_root = tmp.path().join("vault");
        let canon_root = tmp.path().join("canonical");

        // Pre-create the evergreen note with DIFFERENT content so the
        // VaultCreate fails (target exists, hash mismatch).
        let note_rel = "10-Knowledge/Evergreen/x.md";
        let note_abs = vault_root.join(note_rel);
        std::fs::create_dir_all(note_abs.parent().unwrap()).unwrap();
        std::fs::write(&note_abs, "pre-existing different content").unwrap();

        let body = "# evergreen x\n";
        let payload = r#"{"slug":"x"}"#;
        let mut plan = WritePlan::new(RunId::new("r"));
        plan.push(WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new("v1"),
            path: VaultPath::new(note_rel),
            after_hash: ContentHash::new(sha(body.as_bytes())),
            body: body.into(),
            reason: "mint evergreen".into(),
            originating_record: RecordId::new("evg-x"),
        }));
        plan.push(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
            op_id: OpId::new("c1"),
            key: CanonicalKey::new("x"),
            before_hash: None,
            after_hash: ContentHash::new(sha(payload.as_bytes())),
            payload: payload.into(),
            reason: "register canonical".into(),
            originating_record: RecordId::new("evg-x"),
        }));

        let mut applier = CompositePlanApplier::new(vec![
            Box::new(VaultFsPlanApplier::new(&vault_root)),
            Box::new(CanonicalFsStoreApplier::new(&canon_root)),
        ]);
        let report = applier.apply(&plan, ApplyMode::Apply);

        // op0 failed; op1 skipped because the prior op failed.
        assert!(
            matches!(report.outcomes[0].result, OpResult::Failed { .. }),
            "vault create should fail: {:?}",
            report.outcomes[0].result
        );
        assert_eq!(report.outcomes[1].kind, OpKind::CanonicalUpsert);
        match &report.outcomes[1].result {
            OpResult::Skipped { reason } => assert_eq!(reason, "previous op failed"),
            other => panic!("expected Skipped(previous op failed), got {other:?}"),
        }
        // The canonical record must NOT have been written.
        assert!(
            !canon_root.join("x.json").exists(),
            "canonical upsert must not run after its paired vault op failed"
        );
    }

    #[test]
    fn genuinely_unhandled_op_stays_unsupported() {
        // A canonical-only composite handed a vault op: no backend handles
        // it → Unsupported survives.
        let tmp = tempfile::tempdir().unwrap();
        let mut plan = WritePlan::new(RunId::new("r"));
        plan.push(WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new("v1"),
            path: VaultPath::new("a.md"),
            after_hash: ContentHash::new("h"),
            body: "x".into(),
            reason: "t".into(),
            originating_record: RecordId::new("r"),
        }));
        let mut applier = CompositePlanApplier::new(vec![Box::new(
            CanonicalFsStoreApplier::new(tmp.path()),
        )]);
        let report = applier.apply(&plan, ApplyMode::Apply);
        assert_eq!(report.counts().unsupported, 1);
    }
}
