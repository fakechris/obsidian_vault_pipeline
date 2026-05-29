//! Integration tests for CanonicalFsStoreApplier. Tempdirs only.

use ovp_core::{
    ApplyMode, CanonicalKey, CanonicalUpsertOp, ContentHash, OpId, OpKind, OpResult, PlanApplier,
    RecordId, RunId, VaultCreateOp, VaultPath, WriteOp, WritePlan,
};
use ovp_stores::CanonicalFsStoreApplier;
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

fn upsert(key: &str, payload: &str, before: Option<&str>) -> WriteOp {
    WriteOp::CanonicalUpsert(CanonicalUpsertOp {
        op_id: OpId::new(format!("op-{key}")),
        key: CanonicalKey::new(key),
        before_hash: before.map(ContentHash::new),
        after_hash: ContentHash::new(sha(payload.as_bytes())),
        payload: payload.into(),
        reason: "test".into(),
        originating_record: RecordId::new("r"),
    })
}

fn plan_with(op: WriteOp) -> WritePlan {
    let mut p = WritePlan::new(RunId::new("test"));
    p.push(op);
    p
}

#[test]
fn creates_new_record() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let payload = r#"{"slug":"ai-agent"}"#;
    let report = a.apply(&plan_with(upsert("ai-agent", payload, None)), ApplyMode::Apply);
    assert_eq!(report.counts().applied, 1);
    let written = std::fs::read_to_string(tmp.path().join("ai-agent.json")).unwrap();
    assert_eq!(written, payload);
}

#[test]
fn idempotent_on_same_content() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let payload = r#"{"slug":"x"}"#;
    let plan = plan_with(upsert("x", payload, None));
    assert_eq!(a.apply(&plan, ApplyMode::Apply).counts().applied, 1);
    let report = a.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().applied, 0);
    assert_eq!(report.counts().skipped, 1);
    match &report.outcomes[0].result {
        OpResult::Skipped { reason } => assert!(reason.contains("idempotent")),
        other => panic!("expected Skipped, got {other:?}"),
    }
}

#[test]
fn upsert_replaces_existing_different_content() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    a.apply(&plan_with(upsert("x", r#"{"v":1}"#, None)), ApplyMode::Apply);
    // New payload, no before_hash → upsert replaces.
    let report = a.apply(&plan_with(upsert("x", r#"{"v":2}"#, None)), ApplyMode::Apply);
    assert_eq!(report.counts().applied, 1);
    assert_eq!(std::fs::read_to_string(tmp.path().join("x.json")).unwrap(), r#"{"v":2}"#);
}

#[test]
fn before_hash_mismatch_fails() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    a.apply(&plan_with(upsert("x", r#"{"v":1}"#, None)), ApplyMode::Apply);
    // Claim the current is some other hash → optimistic conflict.
    let report = a.apply(
        &plan_with(upsert("x", r#"{"v":2}"#, Some(&sha(b"WRONG")))),
        ApplyMode::Apply,
    );
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.contains("before_hash mismatch")),
        other => panic!("expected Failed, got {other:?}"),
    }
    // Original preserved.
    assert_eq!(std::fs::read_to_string(tmp.path().join("x.json")).unwrap(), r#"{"v":1}"#);
}

#[test]
fn before_hash_match_updates() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let v1 = r#"{"v":1}"#;
    a.apply(&plan_with(upsert("x", v1, None)), ApplyMode::Apply);
    let report = a.apply(
        &plan_with(upsert("x", r#"{"v":2}"#, Some(&sha(v1.as_bytes())))),
        ApplyMode::Apply,
    );
    assert_eq!(report.counts().applied, 1);
}

#[test]
fn rejects_key_traversal() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let report = a.apply(&plan_with(upsert("../escape", "{}", None)), ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.starts_with("key_escape")),
        other => panic!("expected Failed, got {other:?}"),
    }
}

#[test]
fn rejects_payload_hash_mismatch() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    // Declared after_hash does NOT match the payload → fail before I/O.
    let op = WriteOp::CanonicalUpsert(CanonicalUpsertOp {
        op_id: OpId::new("op-x"),
        key: CanonicalKey::new("x"),
        before_hash: None,
        after_hash: ContentHash::new(sha(b"SOMETHING ELSE")),
        payload: r#"{"slug":"x"}"#.into(),
        reason: "test".into(),
        originating_record: RecordId::new("r"),
    });
    let report = a.apply(&plan_with(op), ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.contains("after_hash mismatch")),
        other => panic!("expected Failed, got {other:?}"),
    }
    assert!(!tmp.path().join("x.json").exists(), "nothing written on hash mismatch");
}

#[test]
fn rejects_nested_key() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    // `a/b` would nest the record where read_all can't see it.
    let report = a.apply(&plan_with(upsert("a/b", r#"{"v":1}"#, None)), ApplyMode::Apply);
    assert_eq!(report.counts().failed, 1);
    match &report.outcomes[0].result {
        OpResult::Failed { reason } => assert!(reason.starts_with("key_nested")),
        other => panic!("expected Failed, got {other:?}"),
    }
    assert!(!tmp.path().join("a").exists(), "no nested directory created");
    // Confirm the store stays self-consistent: read_all sees every key the
    // store accepted (here: none).
    assert!(a.read_all().unwrap().is_empty());
}

#[test]
fn accepted_keys_round_trip_through_read_all() {
    // The store's contract: every key it accepts for WRITE is recovered
    // verbatim by read_all (write-set == read-set). Includes tricky but
    // valid keys: unicode, an interior dot, a leading dot.
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let keys = ["ai-agent", "v1.2", "a.b", "对话即工作", ".hidden"];
    for k in keys {
        let payload = format!(r#"{{"slug":"{k}"}}"#);
        assert_eq!(
            a.apply(&plan_with(upsert(k, &payload, None)), ApplyMode::Apply).counts().applied,
            1,
            "key {k} should be accepted"
        );
    }
    let mut recovered: Vec<String> = a.read_all().unwrap().into_iter().map(|(k, _)| k).collect();
    recovered.sort();
    let mut expected: Vec<String> = keys.iter().map(|s| s.to_string()).collect();
    expected.sort();
    assert_eq!(recovered, expected, "every written key must read back identically");
}

#[test]
fn dry_run_writes_nothing() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let report = a.apply(&plan_with(upsert("x", "{}", None)), ApplyMode::DryRun);
    assert_eq!(report.counts().skipped, 1);
    assert!(!tmp.path().join("x.json").exists());
}

#[test]
fn vault_op_is_unsupported() {
    let tmp = tempfile::tempdir().unwrap();
    let mut a = CanonicalFsStoreApplier::new(tmp.path());
    let plan = plan_with(WriteOp::VaultCreate(VaultCreateOp {
        op_id: OpId::new("v1"),
        path: VaultPath::new("a.md"),
        after_hash: ContentHash::new("h"),
        body: "x".into(),
        reason: "t".into(),
        originating_record: RecordId::new("r"),
    }));
    let report = a.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().unsupported, 1);
    assert_eq!(report.outcomes[0].kind, OpKind::VaultCreate);
}
