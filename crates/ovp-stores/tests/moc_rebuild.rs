//! Derived MOC rebuild from canonical state. Proves invariant #11: the
//! Atlas MOC index is reconstructible from the canonical store alone.
//!
//! Flow: seed a canonical store (apply CanonicalUpsert ops), read it back
//! (read_all), parse into CanonicalConcepts, build the MOC WritePlan, and
//! apply it to a vault. Then re-run to confirm the rebuild is idempotent
//! (no-op when nothing changed) and reflects additions when it does.

use ovp_core::{
    ApplyMode, CanonicalKey, CanonicalUpsertOp, ContentHash, OpId, PlanApplier, RecordId, RunId,
    WriteOp, WritePlan,
};
use ovp_domain::{CanonicalConcept, MocBuilder};
use ovp_stores::{CanonicalFsStoreApplier, VaultFsPlanApplier};
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

fn seed_concept(store: &mut CanonicalFsStoreApplier, slug: &str, title: &str) {
    let payload = CanonicalConcept {
        slug: slug.into(),
        title: title.into(),
        evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
        provenance_source_url: "https://example.com/src".into(),
    }
    .to_payload();
    let mut plan = WritePlan::new(RunId::new("seed"));
    plan.push(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
        op_id: OpId::new(format!("op-{slug}")),
        key: CanonicalKey::new(slug),
        before_hash: None,
        after_hash: ContentHash::new(sha(payload.as_bytes())),
        payload,
        reason: "seed".into(),
        originating_record: RecordId::new("r"),
    }));
    store.apply(&plan, ApplyMode::Apply);
}

/// Read the current MOC from the vault, if present.
fn current_moc(vault: &std::path::Path, builder: &MocBuilder) -> Option<String> {
    std::fs::read_to_string(vault.join(builder.moc_path().as_str())).ok()
}

#[test]
fn moc_rebuilt_from_canonical_store() {
    let canon = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let mut store = CanonicalFsStoreApplier::new(canon.path());
    seed_concept(&mut store, "ai-agent", "Ai Agent");
    seed_concept(&mut store, "rag", "Rag");

    let builder = MocBuilder::new();
    let mut vault_applier = VaultFsPlanApplier::new(vault.path());

    // Rebuild: read canonical → parse → plan → apply.
    let pairs = store.read_all().unwrap();
    let concepts = CanonicalConcept::parse_pairs(pairs);
    assert_eq!(concepts.len(), 2);
    let plan = builder.plan_rebuild(RunId::new("moc-1"), &concepts, current_moc(vault.path(), &builder).as_deref());
    let report = vault_applier.apply(&plan, ApplyMode::Apply);
    assert_eq!(report.counts().applied, 1, "MOC created");

    let moc = std::fs::read_to_string(vault.path().join("10-Knowledge/Atlas/MOC-Index.md")).unwrap();
    assert!(moc.contains("[[ai-agent]]"));
    assert!(moc.contains("[[rag]]"));
    assert!(moc.contains("concept_count: 2"));
}

#[test]
fn rebuild_is_idempotent_when_unchanged() {
    let canon = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let mut store = CanonicalFsStoreApplier::new(canon.path());
    seed_concept(&mut store, "x", "X");

    let builder = MocBuilder::new();
    let mut vault_applier = VaultFsPlanApplier::new(vault.path());
    let concepts = CanonicalConcept::parse_pairs(store.read_all().unwrap());

    // First rebuild creates the MOC.
    let plan1 = builder.plan_rebuild(RunId::new("a"), &concepts, current_moc(vault.path(), &builder).as_deref());
    vault_applier.apply(&plan1, ApplyMode::Apply);

    // Second rebuild with unchanged canonical state → empty plan.
    let plan2 = builder.plan_rebuild(RunId::new("b"), &concepts, current_moc(vault.path(), &builder).as_deref());
    assert!(plan2.is_empty(), "unchanged → no op");
}

#[test]
fn rebuild_updates_when_concept_added() {
    let canon = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let mut store = CanonicalFsStoreApplier::new(canon.path());
    seed_concept(&mut store, "x", "X");

    let builder = MocBuilder::new();
    let mut vault_applier = VaultFsPlanApplier::new(vault.path());

    // Build v1.
    let c1 = CanonicalConcept::parse_pairs(store.read_all().unwrap());
    let p1 = builder.plan_rebuild(RunId::new("a"), &c1, None);
    vault_applier.apply(&p1, ApplyMode::Apply);

    // Add a concept, rebuild → VaultUpdate against the current MOC hash.
    seed_concept(&mut store, "y", "Y");
    let c2 = CanonicalConcept::parse_pairs(store.read_all().unwrap());
    let p2 = builder.plan_rebuild(RunId::new("b"), &c2, current_moc(vault.path(), &builder).as_deref());
    assert_eq!(p2.len(), 1);
    let report = vault_applier.apply(&p2, ApplyMode::Apply);
    assert_eq!(report.counts().applied, 1, "MOC updated");

    let moc = std::fs::read_to_string(vault.path().join("10-Knowledge/Atlas/MOC-Index.md")).unwrap();
    assert!(moc.contains("[[x]]"));
    assert!(moc.contains("[[y]]"));
    assert!(moc.contains("concept_count: 2"));
}
