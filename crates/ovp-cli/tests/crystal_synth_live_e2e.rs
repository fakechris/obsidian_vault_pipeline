//! M32 live-repro regression — replay the REAL 34-source `crystal-synth` run
//! (captured 2026-07-01 against MiniMax `MiniMax-M2.7-highspeed`) end-to-end
//! from committed fixtures, zero network.
//!
//! Pins the full pipeline shape on real data: 34 reader packs → 5 keyword
//! clusters → 28 synthesized claims → 3 dropped ungrounded → 22 durable
//! claims + 3 review, idempotent on re-run. The replay client fails loud on
//! any cassette miss, so this also pins the deterministic request-building
//! chain (catalog collection → clustering → slicing → prompt assembly): any
//! change that alters a request key breaks this test and must re-record.
//!
//! Fixture: `tests/fixtures/crystal-synth-live/` — the 34 pilot reader packs
//! (units.accepted.json + reader.md) and the 7 replay cassettes (5 synth
//! clusters + 2 chunked strength calls), curated per AGENTS.md from the run
//! whose verdict closed M32 P0 #2.

use std::path::PathBuf;
use std::process::Command;

fn fixture(sub: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/crystal-synth-live")
        .join(sub)
}

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_ovp2"))
}

#[test]
fn live_repro_34_sources_to_22_durable_and_idempotent() {
    let tmp = tempfile::tempdir().unwrap();
    let work = tmp.path().join("work");
    let store = tmp.path().join("store");

    let run = |label: &str| -> String {
        let out = bin()
            .args(["crystal-synth", "--client", "replay"])
            .arg("--reader-dir")
            .arg(fixture("reader"))
            .arg("--cache-dir")
            .arg(fixture("cassettes"))
            .arg("--work-dir")
            .arg(&work)
            .arg("--store")
            .arg(&store)
            .args(["--title", "M32 live repro"])
            .output()
            .expect("binary runs");
        let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
        let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
        assert!(
            out.status.success(),
            "{label} failed.\nstdout:\n{stdout}\nstderr:\n{stderr}"
        );
        stdout
    };

    // First run: the exact live-run shape.
    let first = run("first run");
    assert!(
        first.contains("collected: 34 case(s) → 5 cluster(s)"),
        "{first}"
    );
    assert!(
        first.contains("synthesized 28 claim(s); dropped_ungrounded=3"),
        "{first}"
    );
    assert!(first.contains("22 newly appended"), "{first}");
    assert!(first.contains("review (NOT durable): 3"), "{first}");

    let ledger = std::fs::read_to_string(store.join("ledger.jsonl")).unwrap();
    assert_eq!(
        ledger.lines().filter(|l| !l.trim().is_empty()).count(),
        22,
        "exactly 22 durable claims written"
    );
    assert!(store.join("crystal.md").exists());
    assert!(store.join("review.json").exists());
    // All 5 clusters stayed within the case cap in the live run — the warning
    // report is written but empty.
    let warnings = std::fs::read_to_string(work.join("warnings.json")).unwrap();
    let w: serde_json::Value = serde_json::from_str(&warnings).unwrap();
    assert_eq!(
        w["fallback_title_cases"].as_array().unwrap().len(),
        0,
        "{w}"
    );
    assert_eq!(
        w["cluster_cap_overflow"].as_array().unwrap().len(),
        0,
        "{w}"
    );

    // Second run into the same store: idempotent by claim_key.
    let second = run("second run");
    assert!(
        second.contains("0 newly appended (22 already active)"),
        "{second}"
    );
    let ledger2 = std::fs::read_to_string(store.join("ledger.jsonl")).unwrap();
    assert_eq!(ledger2.lines().filter(|l| !l.trim().is_empty()).count(), 22);
}
