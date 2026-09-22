use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap()
}
#[test]
fn decision_plan_dispatches_through_existing_evolve_ab_cli() {
    let dir = tempfile::tempdir().unwrap();
    let out = dir.path().join(".run/paired");
    let result = Command::new(env!("CARGO_BIN_EXE_ovp2"))
        .current_dir(root())
        .args([
            "evolve",
            "ab",
            "--candidate",
            "evolution/candidates/decision-experiment-runtime-v1.json",
            "--out",
        ])
        .arg(&out)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("needs_human_review"));
    let manifest: Value =
        serde_json::from_slice(&std::fs::read(out.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["schema"], "ovp.evolution.paired_decision/v1");
    assert_eq!(manifest["status"], "completed");
    assert_eq!(
        manifest["comparison"]["questions"]
            .as_array()
            .unwrap()
            .len(),
        12
    );
}
