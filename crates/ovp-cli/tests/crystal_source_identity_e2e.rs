//! INV-915 over the REAL `ovp2` binary: two reader packs of ONE source must
//! not clear the durable gate's ≥2-sources rule, and `crystal-lint` (the
//! pre-write report) must say the same thing `crystal-write` does.

use std::path::Path;
use std::process::Command;

use ovp_domain::SourceDoc;
use ovp_domain::units::validate;

const BODY_A: &str = "A chunk is a structurally neutral container.";
const BODY_B: &str = "Memory is scarce working memory in agents.";

fn write_pack(reader_root: &Path, case_id: &str, body: &str) -> (String, String) {
    let raw = vec![serde_json::json!({
        "kind": "assertion", "text": "t", "evidence_ref": "p001",
        "evidence_quote": body, "attribution": "author", "modality": "asserted", "arguments": []
    })];
    let ex = validate(
        &raw,
        &SourceDoc::article("T", "https://e/x", None, None, vec![], body),
    );
    let units: Vec<_> = ex.accepted().cloned().collect();
    assert_eq!(units.len(), 1, "fixture unit validates");
    let dir = reader_root.join(case_id);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(
        dir.join("units.accepted.json"),
        serde_json::to_string(&units).unwrap(),
    )
    .unwrap();
    (units[0].id.clone(), units[0].evidence.quote.clone())
}

/// A vault whose two packs are one tweet captured twice (share params differ).
fn fixture(vault: &Path, with_index: bool) -> (std::path::PathBuf, std::path::PathBuf) {
    let reader_root = vault.join("40-Resources/Reader");
    let (u1, q1) = write_pack(&reader_root, "old-layout-pack", BODY_A);
    let (u2, q2) = write_pack(&reader_root, "2026-06-15_new layout pack", BODY_B);
    if with_index {
        let index = serde_json::json!({
            "sources": [
                {"sha256": "s1", "url": "https://x.com/Vtrivedy10/status/2041927488918413589"},
                {"sha256": "s2", "url": "https://x.com/vtrivedy10/status/2041927488918413589?s=46&t=x"}
            ],
            "packs": [
                {"pack_dir": "40-Resources/Reader/old-layout-pack", "source_sha256": "s1"},
                {"pack_dir": "40-Resources/Reader/2026-06-15_new layout pack", "source_sha256": "s2"}
            ]
        });
        std::fs::create_dir_all(vault.join(".ovp/index")).unwrap();
        std::fs::write(vault.join(".ovp/index/index.json"), index.to_string()).unwrap();
    }
    let candidate = serde_json::json!({"items": [{
        "id": "dup-1", "claim": "Chunks are neutral and memory is scarce.", "theme": "x",
        "citations": [
            {"case_id": "old-layout-pack", "unit_id": u1, "quote": q1},
            {"case_id": "2026-06-15_new layout pack", "unit_id": u2, "quote": q2}
        ]
    }]});
    let strength = serde_json::json!([{
        "claim_id": "dup-1", "strength": "supported",
        "evidence_sufficient": true, "rationale": "fixture"
    }]);
    let cand = vault.join("candidate.json");
    let strn = vault.join("strength.json");
    std::fs::write(&cand, candidate.to_string()).unwrap();
    std::fs::write(&strn, strength.to_string()).unwrap();
    (cand, strn)
}

fn ovp2(cache: &Path) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_ovp2"));
    cmd.env("OVP_CACHE_DIR", cache);
    cmd
}

fn run_ok(cmd: &mut Command) -> String {
    let out = cmd.output().expect("binary runs");
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    assert!(
        out.status.success(),
        "expected success.\nstdout:\n{stdout}\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    stdout
}

/// (lint report distinct_sources, number of durable ledger writes)
fn lint_and_write(vault: &Path, cand: &Path, strn: &Path) -> (u64, usize) {
    let cache = vault.join("cache");
    let reader_root = vault.join("40-Resources/Reader");
    let lint_out = vault.join("lint.json");
    run_ok(ovp2(&cache).args([
        "crystal-lint",
        "--candidate",
        cand.to_str().unwrap(),
        "--packs-dir",
        reader_root.to_str().unwrap(),
        "--out",
        lint_out.to_str().unwrap(),
    ]));
    let lint: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&lint_out).unwrap()).unwrap();
    let lint_sources = lint["report"]["claims"][0]["distinct_sources"]
        .as_u64()
        .unwrap_or_else(|| panic!("distinct_sources in lint report: {lint}"));

    run_ok(ovp2(&cache).args([
        "crystal-write",
        "--candidate",
        cand.to_str().unwrap(),
        "--packs-dir",
        reader_root.to_str().unwrap(),
        "--strength",
        strn.to_str().unwrap(),
        "--store",
        vault.join(".ovp/crystal").to_str().unwrap(),
        "--run-id",
        "inv-915",
    ]));
    let durable = std::fs::read_to_string(vault.join(".ovp/crystal/ledger.jsonl"))
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .count();
    (lint_sources, durable)
}

#[test]
fn two_packs_of_one_source_are_not_durable_and_lint_agrees_with_write() {
    let tmp = tempfile::tempdir().unwrap();
    let (cand, strn) = fixture(tmp.path(), true);
    let (lint_sources, durable) = lint_and_write(tmp.path(), &cand, &strn);
    assert_eq!(lint_sources, 1, "crystal-lint counts the source once");
    assert_eq!(
        durable, 0,
        "crystal-write routes it to review, not the ledger"
    );
    let review = std::fs::read_to_string(tmp.path().join(".ovp/crystal/review.json")).unwrap();
    assert!(
        review.contains("dup-1"),
        "routed to the review queue: {review}"
    );
}

#[test]
fn without_a_built_index_the_gate_counts_packs_as_before() {
    let tmp = tempfile::tempdir().unwrap();
    let (cand, strn) = fixture(tmp.path(), false);
    let (lint_sources, durable) = lint_and_write(tmp.path(), &cand, &strn);
    assert_eq!(lint_sources, 2);
    assert_eq!(durable, 1);
}
