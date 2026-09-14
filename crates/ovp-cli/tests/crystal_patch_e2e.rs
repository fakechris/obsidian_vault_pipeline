use std::path::Path;
use std::process::Command;

use ovp_domain::crystal::{FinalClass, ProvenanceClass, StoreEvent, StoreOp, StrengthClass};
use ovp_domain::VaultLayout;

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_ovp2"))
}

fn setup_test_vault(root: &Path) {
    let layout = VaultLayout;
    let store = root.join(layout.crystal_store_dir());
    std::fs::create_dir_all(&store).unwrap();

    let rec = ovp_domain::crystal::DurableRecord {
        claim_key: "ck-test-1".into(),
        claim_id: "c01".into(),
        claim: "Knowledge graphs require continuous validation.".into(),
        theme: "knowledge-architecture".into(),
        theme_id: None,
        source_cases: vec!["case-1".into()],
        citations: Vec::new(),
        provenance_score: 0.95,
        provenance_class: ProvenanceClass::Durable,
        strength: StrengthClass::Supported,
        strength_rationale: "solid".into(),
        final_class: FinalClass::Durable,
        run_id: "r1".into(),
        status: ovp_domain::crystal::CrystalStatus::Active,
    };
    let event = StoreEvent {
        op: StoreOp::Write,
        record: rec,
        supersedes: None,
        reason: None,
    };
    std::fs::write(
        store.join("ledger.jsonl"),
        format!("{}\n", serde_json::to_string(&event).unwrap()),
    )
    .unwrap();
}

#[test]
fn test_crystal_patch_lifecycle_e2e() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    setup_test_vault(root);

    // 1. Initial list: should be empty
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "list",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("No active human patches found"));

    // 2. Apply patch
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "apply",
            "--target",
            "c01",
            "--claim",
            "Knowledge graphs require continuous provenance validation and human patch oversight.",
            "--reason",
            "Added human oversight nuance per M37",
            "--author",
            "operator:alice",
        ])
        .output()
        .unwrap();
    assert!(out.status.success(), "apply failed: {:?}", out);
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("✓ Applied human patch"));
    assert!(stdout.contains("c01"));

    // 3. List active patches
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "list",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("c01"));
    assert!(stdout.contains("operator:alice"));

    // 4. Inspect diff
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "diff",
            "--target",
            "c01",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("- Knowledge graphs require continuous validation."));
    assert!(stdout.contains("+ Knowledge graphs require continuous provenance validation and human patch oversight."));

    // 5. Inspect audit trail
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "audit",
            "--target",
            "c01",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("APPLY (active)"));
    assert!(stdout.contains("Added human oversight nuance per M37"));

    // 6. Build index and check overlay
    let out = bin()
        .args([
            "index",
            "--vault-root",
            root.to_str().unwrap(),
            "--date",
            "2026-09-11",
        ])
        .output()
        .unwrap();
    assert!(out.status.success(), "index build failed: {:?}", out);

    let index_file = root.join(".ovp/index/index.json");
    let index_content = std::fs::read_to_string(&index_file).unwrap();
    let index_model: ovp_index::IndexModel = serde_json::from_str(&index_content).unwrap();
    let c01_row = index_model.claims.iter().find(|c| c.claim_id == "c01").unwrap();
    assert_eq!(
        c01_row.claim,
        "Knowledge graphs require continuous provenance validation and human patch oversight."
    );
    assert!(c01_row.patched_by.is_some());

    // 7. Roll back patch
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "rollback",
            "--target",
            "c01",
            "--reason",
            "Reverting test patch to baseline",
            "--author",
            "operator:alice",
        ])
        .output()
        .unwrap();
    assert!(out.status.success(), "rollback failed: {:?}", out);
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("✓ Rolled back patch"));

    // 8. List after rollback: no active patches
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "list",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("No active human patches found"));

    // 9. Audit trail shows both APPLY and ROLLBACK
    let out = bin()
        .args([
            "crystal-patch",
            "--vault-root",
            root.to_str().unwrap(),
            "audit",
            "--target",
            "c01",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("ROLLBACK (rolled_back)"));
    assert!(stdout.contains("Reverting test patch to baseline"));

    // 10. Rebuild index: reverts cleanly to raw ground truth
    let out = bin()
        .args([
            "index",
            "--vault-root",
            root.to_str().unwrap(),
            "--date",
            "2026-09-11",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());

    let index_content = std::fs::read_to_string(&index_file).unwrap();
    let index_model: ovp_index::IndexModel = serde_json::from_str(&index_content).unwrap();
    let c01_row = index_model.claims.iter().find(|c| c.claim_id == "c01").unwrap();
    assert_eq!(
        c01_row.claim,
        "Knowledge graphs require continuous validation."
    );
    assert_eq!(c01_row.patched_by, None);
}
