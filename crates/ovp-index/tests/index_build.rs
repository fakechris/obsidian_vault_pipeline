//! Build the read model over a synthetic-but-realistic product state:
//! ledgers + real on-disk files + a reader pack dir + a crystal store + run
//! reports. Asserts row folding, ghost-row cleanup, search, determinism, and
//! the rebuild story (delete index → rebuild → identical).

use std::path::Path;

use ovp_daily::{
    DAILY_SCHEMA, DailyRunRecord, RunReport, RunStatus as DailyStatus, append_daily_record,
};
use ovp_index::{
    ClaimStatus, Query, QueryKind, SourceStatus, build_index, read_index, run_query, write_index,
};
use ovp_intake::{INTAKE_SCHEMA, IntakeAction, IntakeRecord, append_intake_record, hex_sha256};

fn daily_rec(
    hash: &str,
    path: &str,
    status: DailyStatus,
    pack: Option<&str>,
    reason: Option<&str>,
) -> DailyRunRecord {
    DailyRunRecord {
        schema: DAILY_SCHEMA.into(),
        run_id: "daily-2026-06-09".into(),
        date: "2026-06-09".into(),
        source_path: path.into(),
        source_sha256: hash.into(),
        status,
        pack_dir: pack.map(String::from),
        moved_to: None, // ledger copies never carry moved_to (M31 ordering)
        units: 2,
        cards: 1,
        reason: reason.map(String::from),
    }
}

fn intake_rec(
    hash: &str,
    action: IntakeAction,
    title: &str,
    url: &str,
    from: &str,
    to: Option<&str>,
) -> IntakeRecord {
    IntakeRecord {
        schema: INTAKE_SCHEMA.into(),
        run_id: "daily-2026-06-09".into(),
        date: "2026-06-09".into(),
        action,
        from: from.into(),
        to: to.map(String::from),
        url: Some(url.into()),
        sha256: hash.into(),
        dup_of: None,
        title: Some(title.into()),
        note: None,
    }
}

/// Write `content` at the vault-relative path and return its sha256.
fn place(root: &Path, rel: &str, content: &str) -> String {
    let p = root.join(rel);
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    std::fs::write(&p, content).unwrap();
    hex_sha256(content.as_bytes())
}

fn write_pack(root: &Path, dir_name: &str, title: &str, cards: usize) -> String {
    let pack = root.join("40-Resources/Reader").join(dir_name);
    std::fs::create_dir_all(&pack).unwrap();
    std::fs::write(
        pack.join("run-status.json"),
        format!(
            r#"{{"source":"{title}","accepted_units":2,"cards":{cards},"json_repaired":false}}"#
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

struct Fixture {
    processed_sha: String,
    blocked_sha: String,
}

fn build_fixture_vault(root: &Path) -> Fixture {
    let daily_ledger = root.join(".ovp/daily-runs.jsonl");
    let intake_ledger = root.join(".ovp/intake.jsonl");

    // PROCESSED: pack on disk; moved file recorded via the run report.
    let pack_rel = write_pack(root, "2026-06-09_Good Article-aaaa1111", "Good Article", 1);
    let processed_sha = place(
        root,
        "50-Inbox/03-Processed/2026-06/good.md",
        "the processed source bytes",
    );
    append_daily_record(
        &daily_ledger,
        &daily_rec(
            &processed_sha,
            "50-Inbox/01-Raw/2026-06/good.md",
            DailyStatus::Succeeded,
            Some(&pack_rel),
            None,
        ),
    )
    .unwrap();

    // FAILED ×2 (retryable) — file still sits in 01-Raw.
    let failed_sha = place(
        root,
        "50-Inbox/01-Raw/2026-06/flaky.md",
        "flaky source bytes",
    );
    for _ in 0..2 {
        append_daily_record(
            &daily_ledger,
            &daily_rec(
                &failed_sha,
                "50-Inbox/01-Raw/2026-06/flaky.md",
                DailyStatus::Failed,
                None,
                Some("truth-layer error: 0 units"),
            ),
        )
        .unwrap();
    }

    // BLOCKED (3 failures) — file still in 01-Raw.
    let blocked_sha = place(
        root,
        "50-Inbox/01-Raw/2026-06/cursed.md",
        "cursed source bytes",
    );
    for _ in 0..3 {
        append_daily_record(
            &daily_ledger,
            &daily_rec(
                &blocked_sha,
                "50-Inbox/01-Raw/2026-06/cursed.md",
                DailyStatus::Failed,
                None,
                Some("card synthesis did not parse"),
            ),
        )
        .unwrap();
    }

    // QUEUED via intake; NEEDS-CONTENT in place; DUPLICATE parked.
    let queued_sha = place(
        root,
        "50-Inbox/01-Raw/2026-06/queued.md",
        "queued piece bytes",
    );
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            &queued_sha,
            IntakeAction::Ingested,
            "Queued Piece",
            "https://e.x/q",
            "Clippings/Queued Piece.md",
            Some("50-Inbox/01-Raw/2026-06/queued.md"),
        ),
    )
    .unwrap();

    let bare_sha = place(root, "50-Inbox/02-Pinboard/bare.md", "bare bookmark bytes");
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            &bare_sha,
            IntakeAction::NeedsContent,
            "Bare Bookmark",
            "https://e.x/bare",
            "50-Inbox/02-Pinboard/bare.md",
            None,
        ),
    )
    .unwrap();

    let dup_sha = place(
        root,
        "50-Inbox/03-Processed/duplicates/2026-06/reclip.md",
        "duplicate bytes",
    );
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            &dup_sha,
            IntakeAction::Duplicate,
            "Reclip",
            "https://e.x/q",
            "Clippings/reclip.md",
            Some("50-Inbox/03-Processed/duplicates/2026-06/reclip.md"),
        ),
    )
    .unwrap();

    // GHOST: flagged needs-content whose file no longer exists (operator fixed
    // it elsewhere) — must be dropped by the index, not shown forever.
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            "9999ghost",
            IntakeAction::NeedsContent,
            "Ghost",
            "https://e.x/ghost",
            "Clippings/ghost.md",
            None,
        ),
    )
    .unwrap();

    write_crystal_store(root);

    // One run report carrying the processed record WITH its moved_to (the
    // report copy is where the lifecycle destination lives).
    let mut report = RunReport::new("daily-2026-06-09", "2026-06-09");
    report.reader.succeeded = 1;
    report.reader.failed = 2;
    let mut moved = daily_rec(
        &processed_sha,
        "50-Inbox/01-Raw/2026-06/good.md",
        DailyStatus::Succeeded,
        Some(&pack_rel),
        None,
    );
    moved.moved_to = Some("50-Inbox/03-Processed/2026-06/good.md".into());
    report.records = vec![moved];
    ovp_daily::write_run_report(root, &report).unwrap();

    // A manually-dropped raw file no ledger knows.
    place(
        root,
        "50-Inbox/01-Raw/2026-06/manual.md",
        "---\ntitle: Manual Drop\nsource: https://e.x/manual\n---\nbody text here\n",
    );

    Fixture {
        processed_sha,
        blocked_sha,
    }
}

#[test]
fn builds_folds_cleans_and_queries_the_full_model() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let fx = build_fixture_vault(root);

    let model = build_index(root, "2026-06-09", Some("daily-2026-06-09")).unwrap();

    // Totals fold every lifecycle state; the ghost row is gone.
    assert_eq!(model.totals.sources, 7, "{:?}", model.totals);
    assert_eq!(model.totals.processed, 1);
    assert_eq!(model.totals.failed, 1);
    assert_eq!(model.totals.blocked, 1);
    assert_eq!(model.totals.queued, 2, "ingested + manual drop");
    assert_eq!(model.totals.needs_content, 1, "ghost needs_content dropped");
    assert_eq!(model.totals.duplicates, 1);
    assert!(
        !model.sources.iter().any(|s| s.sha256 == "9999ghost"),
        "ghost row cleaned"
    );
    assert_eq!(model.totals.packs, 1);
    assert_eq!(model.totals.claims_durable, 1);
    assert_eq!(model.totals.claims_caveated, 1);
    assert_eq!(model.totals.runs, 1);

    // Processed row: located at its post-move path (from the run report),
    // titled from the pack.
    let processed = model
        .sources
        .iter()
        .find(|s| s.sha256 == fx.processed_sha)
        .unwrap();
    assert_eq!(processed.status, SourceStatus::Processed);
    assert_eq!(processed.title.as_deref(), Some("Good Article"));
    assert_eq!(
        processed.rel_path.as_deref(),
        Some("50-Inbox/03-Processed/2026-06/good.md")
    );
    assert!(processed.pack_dir.is_some());

    // Blocked row carries the fail count + last reason.
    let blocked = model
        .sources
        .iter()
        .find(|s| s.sha256 == fx.blocked_sha)
        .unwrap();
    assert_eq!(blocked.status, SourceStatus::Blocked);
    assert_eq!(blocked.fail_count, 3);
    assert!(
        blocked
            .last_reason
            .as_deref()
            .unwrap()
            .contains("card synthesis")
    );

    // Manual drop discovered by the raw scan.
    let manual = model
        .sources
        .iter()
        .find(|s| s.title.as_deref() == Some("Manual Drop"))
        .unwrap();
    assert_eq!(manual.status, SourceStatus::Queued);

    // Pack row links back to its source.
    assert_eq!(
        model.packs[0].source_sha256.as_deref(),
        Some(fx.processed_sha.as_str())
    );
    assert_eq!(model.packs[0].card_titles, vec!["Chunks are neutral"]);

    // Claims from ledger + review.
    let durable = model.claims.iter().find(|c| c.claim_id == "c01").unwrap();
    assert_eq!(durable.status, ClaimStatus::Durable);
    assert_eq!(durable.sources, vec!["case-a", "case-b"]);
    let caveated = model.claims.iter().find(|c| c.claim_id == "c02").unwrap();
    assert_eq!(caveated.status, ClaimStatus::Caveated);
    assert_eq!(caveated.strength.as_deref(), Some("opinion_as_fact"));

    // Search: term over claims; status filter over sources.
    let hits = run_query(
        &model,
        &Query {
            term: Some("moat".into()),
            ..Default::default()
        },
    );
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].kind, "claim");

    let hits = run_query(
        &model,
        &Query {
            kind: Some(QueryKind::Sources),
            status: Some("blocked".into()),
            ..Default::default()
        },
    );
    assert_eq!(hits.len(), 1);
    assert!(hits[0].line.contains("fails=3"));

    let hits = run_query(
        &model,
        &Query {
            term: Some("chunks are neutral".into()),
            ..Default::default()
        },
    );
    assert!(
        hits.iter().any(|h| h.kind == "pack"),
        "card titles searchable: {hits:?}"
    );
}

#[test]
fn duplicate_record_never_masks_a_queued_source() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let intake_ledger = root.join(".ovp/intake.jsonl");

    // Canonical copy ingested and still queued in 01-Raw…
    let sha = place(root, "50-Inbox/01-Raw/2026-06/piece.md", "the piece bytes");
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            &sha,
            IntakeAction::Ingested,
            "Piece",
            "https://e.x/p",
            "Clippings/Piece.md",
            Some("50-Inbox/01-Raw/2026-06/piece.md"),
        ),
    )
    .unwrap();
    // …then an identical re-clip gets parked as a duplicate.
    place(
        root,
        "50-Inbox/03-Processed/duplicates/2026-06/piece.md",
        "the piece bytes",
    );
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            &sha,
            IntakeAction::Duplicate,
            "Piece",
            "https://e.x/p",
            "Clippings/Piece again.md",
            Some("50-Inbox/03-Processed/duplicates/2026-06/piece.md"),
        ),
    )
    .unwrap();

    let model = build_index(root, "2026-06-09", None).unwrap();
    let row = model.sources.iter().find(|s| s.sha256 == sha).unwrap();
    assert_eq!(
        row.status,
        SourceStatus::Queued,
        "queued copy must win over the parked dup"
    );
    assert_eq!(model.totals.queued, 1);
    assert_eq!(model.totals.duplicates, 0);
}

#[test]
fn failed_attempt_pack_dirs_are_not_product_packs() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    // A card-failure attempt leaves a pack dir with 0 cards — audit, not product.
    write_pack(root, "2026-06-09_Broken-bbbb2222", "Broken", 0);
    let model = build_index(root, "2026-06-09", None).unwrap();
    assert_eq!(model.totals.packs, 0, "0-card audit dirs are not packs");
}

#[test]
fn same_day_rerun_reports_sort_after_their_base() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let report = RunReport::new("daily-2026-06-09", "2026-06-09");
    let first = ovp_daily::write_run_report(root, &report).unwrap();
    let second = ovp_daily::write_run_report(root, &report).unwrap();
    assert_ne!(first, second, "collision-suffixed");

    let model = build_index(root, "2026-06-09", None).unwrap();
    assert_eq!(model.runs.len(), 2);
    assert_eq!(model.runs[0].report_file, first, "base report first");
    assert_eq!(model.runs[1].report_file, second, "rerun is the LATEST run");
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
fn corpus_pack_hash_prefix_joins_and_synthesizes_a_source() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();

    // Corpus source: lives in 03-Processed, known to NO ledger. The pack dir
    // carries the first 8 hex chars of the file's sha256 as its prefix.
    let sha = place(
        root,
        "50-Inbox/03-Processed/2026-05/2026-05-07_Corpus_Article.md",
        "corpus article body bytes",
    );
    let joined_pack = write_pack(
        root,
        &format!("{}-2026-05-07_Corpus Article", &sha[..8]),
        "Corpus Article",
        1,
    );
    // Corpus pack whose prefix matches no file on disk → must stay unjoined.
    write_pack(root, "deadbeef-2026-05-08_No Source", "No Source", 1);

    let model = build_index(root, "2026-06-09", None).unwrap();

    let pack = model
        .packs
        .iter()
        .find(|p| p.pack_dir == joined_pack)
        .unwrap();
    assert_eq!(pack.source_sha256.as_deref(), Some(sha.as_str()));

    let src = model.sources.iter().find(|s| s.sha256 == sha).unwrap();
    assert_eq!(src.status, SourceStatus::Processed);
    assert_eq!(src.title.as_deref(), Some("Corpus Article"));
    assert_eq!(
        src.rel_path.as_deref(),
        Some("50-Inbox/03-Processed/2026-05/2026-05-07_Corpus_Article.md")
    );
    assert_eq!(src.date.as_deref(), Some("2026-05-07"));
    assert_eq!(src.pack_dir.as_deref(), Some(joined_pack.as_str()));
    assert_eq!(src.last_run_id, None, "backfill rows carry no run id");

    let orphan = model
        .packs
        .iter()
        .find(|p| p.pack_dir.contains("deadbeef"))
        .unwrap();
    assert_eq!(orphan.source_sha256, None, "prefix without a file stays unjoined");
    assert_eq!(model.totals.sources, 1, "no source invented for the orphan");
}

#[test]
fn corpus_pack_joins_an_existing_ledger_row_without_duplicating_it() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let fx = build_fixture_vault(root);

    // A corpus-named alias pack whose prefix hashes to the ledger-processed
    // source: it must join the EXISTING row, not synthesize a second one,
    // and the ledger-joined pack must stay exactly as the ledger said.
    let alias_pack = write_pack(
        root,
        &format!("{}-2026-06-09_Alias Pack", &fx.processed_sha[..8]),
        "Alias Pack",
        1,
    );

    let model = build_index(root, "2026-06-09", Some("daily-2026-06-09")).unwrap();

    assert_eq!(model.totals.sources, 7, "no synthesized duplicate row");
    let alias = model
        .packs
        .iter()
        .find(|p| p.pack_dir == alias_pack)
        .unwrap();
    assert_eq!(alias.source_sha256.as_deref(), Some(fx.processed_sha.as_str()));

    // The ledger-joined pack is unchanged by the backfill pass.
    let ledgered = model
        .packs
        .iter()
        .find(|p| p.pack_dir.ends_with("2026-06-09_Good Article-aaaa1111"))
        .unwrap();
    assert_eq!(
        ledgered.source_sha256.as_deref(),
        Some(fx.processed_sha.as_str())
    );
    let processed = model
        .sources
        .iter()
        .find(|s| s.sha256 == fx.processed_sha)
        .unwrap();
    assert_eq!(
        processed.last_run_id.as_deref(),
        Some("daily-2026-06-09"),
        "ledger row untouched"
    );
}

#[test]
fn corpus_pack_promotes_a_raw_scan_queued_row() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();

    // Corpus source still sitting in 01-Raw: the raw sweep makes a QUEUED
    // row for it before the backfill runs. The prefix join must PROMOTE that
    // row (processed + pack link) — not skip it, and not duplicate it.
    let sha = place(
        root,
        "50-Inbox/01-Raw/2026-05/corpus.md",
        "---\ntitle: Raw Frontmatter Title\nsource: https://e.x/corpus\n---\ncorpus body\n",
    );
    let pack = write_pack(
        root,
        &format!("{}-2026-05-07_Corpus", &sha[..8]),
        "Pack Title",
        1,
    );

    let model = build_index(root, "2026-06-09", None).unwrap();

    assert_eq!(model.totals.sources, 1, "promoted, not duplicated");
    assert_eq!(model.totals.processed, 1);
    assert_eq!(model.totals.queued, 0, "no pack-linked source stuck queued");
    let row = model.sources.iter().find(|s| s.sha256 == sha).unwrap();
    assert_eq!(row.status, SourceStatus::Processed);
    assert_eq!(row.pack_dir.as_deref(), Some(pack.as_str()));
    // Frontmatter metadata from the raw scan beats pack metadata.
    assert_eq!(row.title.as_deref(), Some("Raw Frontmatter Title"));
    assert_eq!(row.url.as_deref(), Some("https://e.x/corpus"));
    assert_eq!(
        row.rel_path.as_deref(),
        Some("50-Inbox/01-Raw/2026-05/corpus.md")
    );
    // Gaps fill from the pack dir.
    assert_eq!(row.date.as_deref(), Some("2026-05-07"));
    assert_eq!(row.last_run_id, None, "still no ledger record");
    let joined = model.packs.iter().find(|p| p.pack_dir == pack).unwrap();
    assert_eq!(joined.source_sha256.as_deref(), Some(sha.as_str()));
}

#[test]
fn corpus_pack_never_promotes_a_ledger_queued_row() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let intake_ledger = root.join(".ovp/intake.jsonl");

    // Same shape, but the INTAKE ledger owns this row (queued, with a run
    // id): the pack may join, the lifecycle must not move — the ledgers are
    // the authority, only the daily run promotes ledgered sources.
    let sha = place(
        root,
        "50-Inbox/01-Raw/2026-05/ledgered.md",
        "ledgered corpus bytes",
    );
    append_intake_record(
        &intake_ledger,
        &intake_rec(
            &sha,
            IntakeAction::Ingested,
            "Ledgered Piece",
            "https://e.x/l",
            "Clippings/Ledgered Piece.md",
            Some("50-Inbox/01-Raw/2026-05/ledgered.md"),
        ),
    )
    .unwrap();
    let pack = write_pack(
        root,
        &format!("{}-2026-05-07_Ledgered", &sha[..8]),
        "Ledgered Piece",
        1,
    );

    let model = build_index(root, "2026-06-09", None).unwrap();

    let row = model.sources.iter().find(|s| s.sha256 == sha).unwrap();
    assert_eq!(row.status, SourceStatus::Queued, "ledger rows stay untouched");
    assert_eq!(row.pack_dir, None);
    assert_eq!(row.last_run_id.as_deref(), Some("daily-2026-06-09"));
    let joined = model.packs.iter().find(|p| p.pack_dir == pack).unwrap();
    assert_eq!(joined.source_sha256.as_deref(), Some(sha.as_str()));
}

#[test]
fn corpus_backfill_skips_files_over_the_size_cap() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();

    // 3 MB of markdown — over the 2 MB hashing cap, so never a candidate.
    let big = "x".repeat(3 * 1024 * 1024);
    let sha = place(root, "50-Inbox/03-Processed/2026-05/huge.md", &big);
    write_pack(root, &format!("{}-2026-05-07_Huge", &sha[..8]), "Huge", 1);

    let model = build_index(root, "2026-06-09", None).unwrap();
    let pack = model.packs.iter().find(|p| p.pack_dir.contains("_Huge")).unwrap();
    assert_eq!(pack.source_sha256, None, "oversized files are not hashed");
    assert_eq!(model.totals.sources, 0);
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
