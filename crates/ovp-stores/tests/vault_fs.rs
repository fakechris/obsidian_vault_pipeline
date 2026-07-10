//! Integration tests for VaultFsPlanApplier. All I/O happens under a
//! `tempfile::tempdir` — real vaults are never touched.

use ovp_core::{
    ApplyMode, CanonicalKey, CanonicalUpsertOp, ContentHash, EventAppendOp, OpId, OpKind,
    OpResult, PlanApplier, RecordId, RunId, StepId, VaultCreateOp, VaultPath, VaultUpdateOp,
    WriteOp, WritePlan,
};
use ovp_stores::VaultFsPlanApplier;
use sha2::{Digest, Sha256};

fn sha256_hex(bytes: &[u8]) -> String {
    let hash = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    use std::fmt::Write;
    for b in hash.iter() {
        write!(s, "{:02x}", b).expect("infallible");
    }
    s
}

fn plan_with(op: WriteOp) -> WritePlan {
    let mut p = WritePlan::new(RunId::new("test"));
    p.push(op);
    p
}

fn create_op(path: &str, body: &str) -> WriteOp {
    WriteOp::VaultCreate(VaultCreateOp {
        op_id: OpId::new(format!("op-{path}")),
        path: VaultPath::new(path),
        after_hash: ContentHash::new(sha256_hex(body.as_bytes())),
        body: body.into(),
        reason: "test create".into(),
        originating_record: RecordId::new("r"),
    })
}

fn update_op(path: &str, before: &str, after: &str) -> WriteOp {
    WriteOp::VaultUpdate(VaultUpdateOp {
        op_id: OpId::new(format!("update-{path}")),
        path: VaultPath::new(path),
        before_hash: ContentHash::new(sha256_hex(before.as_bytes())),
        after_hash: ContentHash::new(sha256_hex(after.as_bytes())),
        body: after.into(),
        reason: "test update".into(),
        originating_record: RecordId::new("r"),
    })
}

#[test]
fn create_new_file_in_subdir() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let body = "# Hello\nworld\n";
    let plan = plan_with(create_op("notes/a.md", body));
    let report = applier.apply(&plan, ApplyMode::Apply);

    let counts = report.counts();
    assert_eq!(counts.applied, 1);
    assert_eq!(counts.failed, 0);
    let written = std::fs::read_to_string(tmp.path().join("notes/a.md")).unwrap();
    assert_eq!(written, body);
}

#[test]
fn create_idempotent_on_same_hash() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let body = "same content";
    let plan = plan_with(create_op("a.md", body));
    assert_eq!(applier.apply(&plan, ApplyMode::Apply).counts().applied, 1);

    // Second apply: target already has the same content → skip.
    let report = applier.apply(&plan, ApplyMode::Apply);
    let counts = report.counts();
    assert_eq!(counts.applied, 0);
    assert_eq!(counts.skipped, 1);
    assert_eq!(counts.failed, 0);
    match &report.outcomes[0].result {
        OpResult::Skipped { reason } => assert!(reason.contains("idempotent")),
        other => panic!("expected Skipped, got {other:?}"),
    }
}

#[test]
fn create_fails_on_existing_different_content() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(tmp.path().join("a.md"), "preexisting different content").unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(create_op("a.md", "new content"));
    let report = applier.apply(&plan, ApplyMode::Apply);

    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => {
            assert!(reason.contains("target exists with different content"))
        }
        other => panic!("expected Failed, got {other:?}"),
    }
    // Original file untouched.
    assert_eq!(
        std::fs::read_to_string(tmp.path().join("a.md")).unwrap(),
        "preexisting different content"
    );
}

#[test]
fn create_rejects_body_hash_mismatch() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());
    // Declared after_hash does not match the body → fail before I/O.
    let op = WriteOp::VaultCreate(VaultCreateOp {
        op_id: OpId::new("op-x"),
        path: VaultPath::new("a.md"),
        after_hash: ContentHash::new(sha256_hex(b"OTHER")),
        body: "real body".into(),
        reason: "t".into(),
        originating_record: RecordId::new("r"),
    });
    let report = applier.apply(&plan_with(op), ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.contains("after_hash mismatch")),
        other => panic!("expected Failed, got {other:?}"),
    }
    assert!(!tmp.path().join("a.md").exists(), "nothing written on hash mismatch");
}

#[test]
fn update_succeeds_when_before_hash_matches() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(tmp.path().join("a.md"), "v1").unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(update_op("a.md", "v1", "v2"));
    let report = applier.apply(&plan, ApplyMode::Apply);

    assert_eq!(report.counts().applied, 1);
    assert_eq!(std::fs::read_to_string(tmp.path().join("a.md")).unwrap(), "v2");
}

#[test]
fn update_rejected_on_hash_mismatch() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(tmp.path().join("a.md"), "drifted").unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(update_op("a.md", "v1", "v2"));
    let report = applier.apply(&plan, ApplyMode::Apply);

    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.contains("before_hash mismatch")),
        other => panic!("expected Failed, got {other:?}"),
    }
    assert_eq!(std::fs::read_to_string(tmp.path().join("a.md")).unwrap(), "drifted");
}

#[test]
fn update_target_missing_fails() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(update_op("missing.md", "v1", "v2"));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.contains("does not exist")),
        other => panic!("expected Failed, got {other:?}"),
    }
}

#[test]
fn rejects_parent_dir_traversal() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(create_op("../escape.md", "x"));
    let report = applier.apply(&plan, ApplyMode::Apply);

    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.starts_with("path_escape")),
        other => panic!("expected Failed, got {other:?}"),
    }
    assert!(!tmp.path().parent().unwrap().join("escape.md").exists());
}

#[test]
fn rejects_nested_parent_traversal() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    // Multiple `..` buried mid-path must still be rejected.
    let plan = plan_with(create_op("notes/../../etc/passwd", "x"));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.starts_with("path_escape")),
        other => panic!("expected Failed, got {other:?}"),
    }
}

#[test]
fn report_has_unsupported_flag() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());
    let plan = plan_with(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
        op_id: OpId::new("c1"),
        key: CanonicalKey::new("foo"),
        before_hash: None,
        after_hash: ContentHash::new("h"),
        payload: "{}".into(),
        reason: "test".into(),
        originating_record: RecordId::new("r"),
    }));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert!(report.has_unsupported());
    assert!(report.all_ok(), "unsupported is not a hard failure");
}

#[test]
fn rejects_absolute_path() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(create_op("/etc/passwd", "x"));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.starts_with("path_absolute")),
        other => panic!("expected Failed, got {other:?}"),
    }
}

#[test]
fn rejects_empty_path() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(create_op("", "x"));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.starts_with("path_empty")),
        other => panic!("expected Failed, got {other:?}"),
    }
}

#[test]
fn dry_run_writes_nothing() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(create_op("dry.md", "body"));
    let report = applier.apply(&plan, ApplyMode::DryRun);

    assert_eq!(report.counts().skipped, 1);
    assert_eq!(report.counts().applied, 0);
    assert!(!tmp.path().join("dry.md").exists());
    match &report.outcomes[0].result {
        OpResult::Skipped { reason } => assert_eq!(reason, "dry-run"),
        other => panic!("expected Skipped(dry-run), got {other:?}"),
    }
}

#[test]
fn canonical_upsert_is_unsupported() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
        op_id: OpId::new("c1"),
        key: CanonicalKey::new("foo"),
        before_hash: None,
        after_hash: ContentHash::new("h"),
        payload: "{}".into(),
        reason: "test".into(),
        originating_record: RecordId::new("r"),
    }));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().unsupported, 1);
    assert_eq!(report.outcomes[0].kind, OpKind::CanonicalUpsert);
    assert_eq!(report.outcomes[0].result, OpResult::Unsupported);
}

#[test]
fn event_append_is_unsupported() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());

    let plan = plan_with(WriteOp::EventAppend(EventAppendOp {
        op_id: OpId::new("e1"),
        event_kind: "some_kind".into(),
        payload: "{}".into(),
        originating_record: RecordId::new("r"),
        emitted_by: StepId::new("step"),
    }));
    let report = applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().unsupported, 1);
    assert_eq!(report.outcomes[0].kind, OpKind::EventAppend);
}

#[test]
fn report_is_serializable_to_json() {
    let tmp = tempfile::tempdir().unwrap();
    let mut applier = VaultFsPlanApplier::new(tmp.path());
    let plan = plan_with(create_op("a.md", "body"));
    let report = applier.apply(&plan, ApplyMode::Apply);
    let json = serde_json::to_string(&report).expect("ApplyReport serializes");
    assert!(json.contains("\"outcome\":\"applied\""));
    assert!(json.contains("\"kind\":\"vault_create\""));
    assert!(json.contains("\"mode\":\"apply\""));
}
