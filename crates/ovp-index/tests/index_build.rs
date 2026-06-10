//! Build the read model over a synthetic-but-realistic product state:
//! ledgers + a real reader pack dir + a crystal store + run reports. Asserts
//! row folding, search, determinism, and the rebuild story (delete index →
//! rebuild → identical).

use std::path::Path;

use ovp_daily::{
    append_daily_record, DailyRunRecord, RunReport, RunStatus as DailyStatus, DAILY_SCHEMA,
};
use ovp_index::{
    build_index, read_index, run_query, write_index, ClaimStatus, Query, QueryKind, SourceStatus,
};
use ovp_intake::{append_intake_record, IntakeAction, IntakeRecord, INTAKE_SCHEMA};

fn daily_rec(hash: &str, status: DailyStatus, pack: Option<&str>, reason: Option<&str>) -> DailyRunRecord {
    DailyRunRecord {
        schema: DAILY_SCHEMA.into(),
        run_id: "daily-2026-06-09".into(),
        date: "2026-06-09".into(),
        source_path: format!("50-Inbox/01-Raw/2026-06/{hash}.md"),
        source_sha256: hash.into(),
        status,
        pack_dir: pack.map(String::from),
        moved_to: matches!(status, DailyStatus::Succeeded)
            .then(|| format!("50-Inbox/03-Processed/2026-06/{hash}.md")),
        units: 2,
        cards: 1,
        reason: reason.map(String::from),
    }
}

fn intake_rec(hash: &str, action: IntakeAction, title: &str, url: &str) -> IntakeRecord {
    IntakeRecord {
        schema: INTAKE_SCHEMA.into(),
        run_id: "daily-2026-06-09".into(),
        date: "2026-06-09".into(),
        action,
        from: format!("Clippings/{title}.md"),
        to: matches!(action, IntakeAction::Ingested)
            .then(|| format!("50-Inbox/01-Raw/2026-06/{title}.md")),
        url: Some(url.into()),
        sha256: hash.into(),
        dup_of: None,
        title: Some(title.into()),
        note: None,
    }
}

fn write_pack(root: &Path, dir_name: &str, title: &str) -> String {
    let pack = root.join("40-Resources/Reader").join(dir_name);
    std::fs::create_dir_all(&pack).unwrap();
    std::fs::write(
        pack.join("run-status.json"),
        format!(
            r#"{{"source":"{title}","accepted_units":2,"cards":1,"json_repaired":false}}"#
        ),
    )
    .unwrap();
    std::fs::write(
        pack.join("cards.json"),
        r#"[{"title":"Chunks are neutral","content":"x","cited_unit_ids":["u-001-aaaa"]}]"#,
    )
    .unwrap();
    format!("40-Resources/Reader/{dir_name}")
}

fn write_crystal_store(root: &Path) {
    let store = root.join(".ovp/crystal");
    std::fs::create_dir_all(&store).unwrap();
    // One active durable claim in the append-only ledger (the exact StoreEvent shape).
    let event = serde_json::json!({
        "op": "write",
        "record": {
            "claim_key": "k1",
            "claim_id": "c01",
            "claim": "Filesystem works as agent memory.",
            "theme": "memory",
            "source_cases": ["case-a", "case-b"],
            "citations": [],
            "provenance_score": 0.9,
            "provenance_class": "durable",
            "strength": "supported",
            "strength_rationale": "ok",
            "final_class": "durable",
            "run_id": "crystal-1",
            "status": "active"
        }
    });
    std::fs::write(store.join("ledger.jsonl"), format!("{event}\n")).unwrap();
    std::fs::write(
        store.join("review.json"),
        serde_json::json!({"review": [{
            "claim_id": "c02",
            "claim": "Context is the moat.",
            "theme": "strategy",
            "final_class": "caveated",
            "strength": "opinion_as_fact",
            "evidence_sufficient": false,
            "rationale": "hedged in source"
        }]})
        .to_string(),
    )
    .unwrap();
}

fn build_fixture_vault(root: &Path) {
    let daily_ledger = root.join(".ovp/daily-runs.jsonl");
    let intake_ledger = root.join(".ovp/intake.jsonl");

    // processed + pack
    let pack_rel = write_pack(root, "2026-06-09_Good Article-aaaa1111", "Good Article");
    append_daily_record(&daily_ledger, &daily_rec("aaaa", DailyStatus::Succeeded, Some(&pack_rel), None)).unwrap();
    // failed twice (retryable)
    append_daily_record(&daily_ledger, &daily_rec("bbbb", DailyStatus::Failed, None, Some("truth-layer error: 0 units"))).unwrap();
    append_daily_record(&daily_ledger, &daily_rec("bbbb", DailyStatus::Failed, None, Some("truth-layer error: 0 units"))).unwrap();
    // blocked (3 failures)
    for _ in 0..3 {
        append_daily_record(&daily_ledger, &daily_rec("cccc", DailyStatus::Failed, None, Some("card synthesis did not parse"))).unwrap();
    }
    // intake: ingested-not-yet-read, needs_content, duplicate
    append_intake_record(&intake_ledger, &intake_rec("dddd", IntakeAction::Ingested, "Queued Piece", "https://e.x/q")).unwrap();
    append_intake_record(&intake_ledger, &intake_rec("eeee", IntakeAction::NeedsContent, "Bare Bookmark", "https://e.x/bare")).unwrap();
    append_intake_record(&intake_ledger, &intake_rec("ffff", IntakeAction::Duplicate, "Reclip", "https://e.x/q")).unwrap();

    write_crystal_store(root);

    // one run report
    let mut report = RunReport::new("daily-2026-06-09", "2026-06-09");
    report.reader.succeeded = 1;
    report.reader.failed = 2;
    ovp_daily::write_run_report(root, &report).unwrap();

    // a manually-dropped raw file no ledger knows
    let raw = root.join("50-Inbox/01-Raw/2026-06");
    std::fs::create_dir_all(&raw).unwrap();
    std::fs::write(raw.join("manual.md"), "---\ntitle: Manual Drop\nsource: https://e.x/manual\n---\nbody text here\n").unwrap();
}

#[test]
fn builds_folds_and_queries_the_full_model() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    build_fixture_vault(root);

    let model = build_index(root, "2026-06-09", Some("daily-2026-06-09")).unwrap();

    // Totals fold every lifecycle state.
    assert_eq!(model.totals.sources, 7, "{:?}", model.totals);
    assert_eq!(model.totals.processed, 1);
    assert_eq!(model.totals.failed, 1);
    assert_eq!(model.totals.blocked, 1);
    assert_eq!(model.totals.queued, 2, "ingested + manual drop");
    assert_eq!(model.totals.needs_content, 1);
    assert_eq!(model.totals.duplicates, 1);
    assert_eq!(model.totals.packs, 1);
    assert_eq!(model.totals.claims_durable, 1);
    assert_eq!(model.totals.claims_caveated, 1);
    assert_eq!(model.totals.runs, 1);

    // Processed row: located at its processed path, titled from the pack.
    let processed = model.sources.iter().find(|s| s.sha256 == "aaaa").unwrap();
    assert_eq!(processed.status, SourceStatus::Processed);
    assert_eq!(processed.title.as_deref(), Some("Good Article"));
    assert!(processed.rel_path.as_deref().unwrap().starts_with("50-Inbox/03-Processed/"));
    assert!(processed.pack_dir.is_some());

    // Blocked row carries the fail count + last reason.
    let blocked = model.sources.iter().find(|s| s.sha256 == "cccc").unwrap();
    assert_eq!(blocked.status, SourceStatus::Blocked);
    assert_eq!(blocked.fail_count, 3);
    assert!(blocked.last_reason.as_deref().unwrap().contains("card synthesis"));

    // Manual drop discovered by the raw scan.
    let manual = model.sources.iter().find(|s| s.title.as_deref() == Some("Manual Drop")).unwrap();
    assert_eq!(manual.status, SourceStatus::Queued);

    // Pack row links back to its source.
    assert_eq!(model.packs[0].source_sha256.as_deref(), Some("aaaa"));
    assert_eq!(model.packs[0].card_titles, vec!["Chunks are neutral"]);

    // Claims from ledger + review.
    let durable = model.claims.iter().find(|c| c.claim_id == "c01").unwrap();
    assert_eq!(durable.status, ClaimStatus::Durable);
    assert_eq!(durable.sources, vec!["case-a", "case-b"]);
    let caveated = model.claims.iter().find(|c| c.claim_id == "c02").unwrap();
    assert_eq!(caveated.status, ClaimStatus::Caveated);
    assert_eq!(caveated.strength.as_deref(), Some("opinion_as_fact"));

    // Search: term over claims; status filter over sources.
    let hits = run_query(&model, &Query { term: Some("moat".into()), ..Default::default() });
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].kind, "claim");

    let hits = run_query(&model, &Query {
        kind: Some(QueryKind::Sources),
        status: Some("blocked".into()),
        ..Default::default()
    });
    assert_eq!(hits.len(), 1);
    assert!(hits[0].line.contains("fails=3"));

    let hits = run_query(&model, &Query { term: Some("chunks are neutral".into()), ..Default::default() });
    assert!(hits.iter().any(|h| h.kind == "pack"), "card titles searchable: {hits:?}");
}

#[test]
fn persisted_index_is_deterministic_and_rebuildable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    build_fixture_vault(root);

    let model = build_index(root, "2026-06-09", None).unwrap();
    let rel = write_index(root, &model).unwrap();
    assert_eq!(rel, ".ovp/index/index.json");
    let first = std::fs::read_to_string(root.join(&rel)).unwrap();

    // The rebuild story: delete the projection, rebuild, byte-identical.
    std::fs::remove_file(root.join(&rel)).unwrap();
    let model2 = build_index(root, "2026-06-09", None).unwrap();
    write_index(root, &model2).unwrap();
    let second = std::fs::read_to_string(root.join(&rel)).unwrap();
    assert_eq!(first, second, "projection must be deterministic");

    let loaded = read_index(root).unwrap();
    assert_eq!(loaded, model2);
}

#[test]
fn empty_vault_builds_an_empty_model() {
    let dir = tempfile::tempdir().unwrap();
    let model = build_index(dir.path(), "2026-06-09", None).unwrap();
    assert_eq!(model.totals.sources, 0);
    assert_eq!(model.totals.packs, 0);
    assert_eq!(model.totals.claims_durable, 0);
    assert_eq!(model.totals.runs, 0);
}
