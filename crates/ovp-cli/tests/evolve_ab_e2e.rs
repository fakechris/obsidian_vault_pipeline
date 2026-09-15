//! Execute the actual CLI twice; no precomputed scorecard is supplied.

use serde_json::{Value, json};
use std::{
    path::{Path, PathBuf},
    process::Command,
};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap()
}
fn setup() -> (tempfile::TempDir, Value) {
    let base = root().join(".run/evolve-ab-tests");
    std::fs::create_dir_all(&base).unwrap();
    let dir = tempfile::tempdir_in(base).unwrap();
    let mut spec: Value = serde_json::from_str(
        &std::fs::read_to_string(root().join("evolution/candidates/evolve-ab-runtime-v1.json"))
            .unwrap(),
    )
    .unwrap();
    spec["eval_plan"]["paired_run"]["fixture_dir"] =
        json!(root().join("fixtures/evolution/retrieval-smoke"));
    (dir, spec)
}
fn run(dir: &Path, spec: &Value, name: &str) -> std::process::Output {
    let candidate = dir.join(format!("{name}.json"));
    std::fs::write(&candidate, serde_json::to_vec(spec).unwrap()).unwrap();
    Command::new(env!("CARGO_BIN_EXE_ovp2"))
        .current_dir(root())
        .args(["evolve", "ab", "--candidate"])
        .arg(candidate)
        .arg("--out")
        .arg(dir.join(name))
        .output()
        .unwrap()
}
fn manifest(dir: &Path, name: &str) -> Value {
    serde_json::from_slice(&std::fs::read(dir.join(name).join("manifest.json")).unwrap()).unwrap()
}
#[test]
fn paired_cli_executes_accepts_rejects_and_preserves_evidence() {
    let (dir, mut spec) = setup();
    let r = run(dir.path(), &spec, "improved");
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    let m = manifest(dir.path(), "improved");
    assert_eq!(m["status"], "completed");
    assert_eq!(m["comparison"]["decision"], "accept");
    assert_eq!(m["arms"].as_array().unwrap().len(), 2);
    assert_eq!(
        m["comparison"]["admission_gate_scope"],
        "not_exercised_read_only_retrieval"
    );
    let ledger = dir.path().join("improved/.ovp/evolution-ledger.jsonl");
    let entry: Value =
        serde_json::from_str(std::fs::read_to_string(ledger).unwrap().trim()).unwrap();
    assert_eq!(entry["decision"], "accept");
    assert!(
        entry["scorecard_summary"]["manifest_sha256"]
            .as_str()
            .unwrap()
            .len()
            == 64
    );
    assert!(
        !run(dir.path(), &spec, "improved").status.success(),
        "evidence may never be overwritten"
    );
    spec["eval_plan"]["paired_run"]["control_query_mode"] = json!("terms");
    spec["eval_plan"]["paired_run"]["candidate_query_mode"] = json!("verbatim");
    assert!(!run(dir.path(), &spec, "regressed").status.success());
    assert_eq!(
        manifest(dir.path(), "regressed")["comparison"]["decision"],
        "reject"
    );
    spec["eval_plan"]["paired_run"]["candidate_query_mode"] = json!("terms");
    assert!(run(dir.path(), &spec, "identical").status.success());
    assert_eq!(
        manifest(dir.path(), "identical")["comparison"]["decision"],
        "needs_human_review"
    );
}
#[test]
fn invalid_spec_and_missing_plan_never_execute() {
    let (dir, mut spec) = setup();
    spec["surface"] = json!("prompt");
    assert!(!run(dir.path(), &spec, "wrong-surface").status.success());
    assert!(!dir.path().join("wrong-surface/control").exists());
    spec["surface"] = json!("runtime");
    spec["eval_plan"]
        .as_object_mut()
        .unwrap()
        .remove("paired_run");
    assert!(!run(dir.path(), &spec, "missing-plan").status.success());
}

#[test]
fn tool_failure_is_invalid_and_cannot_create_an_acceptance_ledger() {
    let (dir, mut spec) = setup();
    let fixture = dir.path().join("broken-fixture");
    for rel in [
        "qrels/q-001.json",
        "qrels/q-002.json",
        "vault/.ovp/index/index.json",
    ] {
        let dest = fixture.join(rel);
        std::fs::create_dir_all(dest.parent().unwrap()).unwrap();
        std::fs::copy(
            root().join("fixtures/evolution/retrieval-smoke").join(rel),
            dest,
        )
        .unwrap();
    }
    // Missing ledger is an actual search_claims failure in VaultTools.
    spec["eval_plan"]["paired_run"]["fixture_dir"] = json!(fixture);
    assert!(!run(dir.path(), &spec, "tool-failure").status.success());
    let m = manifest(dir.path(), "tool-failure");
    assert_eq!(m["status"], "invalid");
    assert!(m["comparison"].is_null());
    assert!(
        !dir.path()
            .join("tool-failure/.ovp/evolution-ledger.jsonl")
            .exists()
    );
}
