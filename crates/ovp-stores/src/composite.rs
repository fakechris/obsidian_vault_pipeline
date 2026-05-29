use ovp_core::{ApplyMode, ApplyReport, OpResult, PlanApplier, WritePlan};

/// Routes a `WritePlan`'s ops across several backend appliers that handle
/// disjoint op kinds (e.g. `VaultFsPlanApplier` for vault ops +
/// `CanonicalFsStoreApplier` for canonical ops), so a full plan applies
/// with no `Unsupported` outcomes.
///
/// Each child applier sees the whole plan and acts only on the kinds it
/// supports (reporting the rest `Unsupported` with no side effect, by
/// construction). The composite then merges per-op outcomes: for each op,
/// the single non-`Unsupported` outcome wins; if every backend reports
/// `Unsupported`, the op is genuinely unhandled and stays `Unsupported`.
///
/// Correct because backends act on disjoint kinds — no op is written
/// twice. If two backends both claim an op (a misconfiguration), the
/// first non-`Unsupported` outcome is taken.
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
        // Each backend iterates plan.ops in order, so reports are aligned
        // index-for-index with plan.ops.
        let reports: Vec<ApplyReport> =
            self.backends.iter_mut().map(|b| b.apply(plan, mode)).collect();

        let mut merged = ApplyReport::new(plan.run_id.clone(), mode);
        for i in 0..plan.ops.len() {
            // Prefer the first backend that actually handled this op.
            let chosen = reports
                .iter()
                .filter_map(|r| r.outcomes.get(i))
                .find(|o| !matches!(o.result, OpResult::Unsupported))
                .or_else(|| reports.first().and_then(|r| r.outcomes.get(i)));
            if let Some(o) = chosen {
                merged.push(o.clone());
            }
        }
        merged
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
