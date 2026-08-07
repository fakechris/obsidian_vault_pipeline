//! Capture-boundary end-to-end on a temp vault: pinboard sync → intake sweep
//! → raw inbox, with URL/content dedup, needs-content flagging, duplicate
//! parking, audit events, and idempotent re-runs.

use std::collections::HashSet;
use std::path::Path;

use ovp_intake::{
    read_intake_ledger, read_pinboard_ledger, sweep_intake, sync_pinboard, FixturePinboardFetch,
    IntakeConfig, PinboardSyncOptions, FIRST_SYNC_GUARD_MAX_NEW,
};

const LONG_BODY: &str = "A chunk is a structurally neutral container. It knows nothing about \
ownership, provenance, or trust. The reader trunk turns sources into grounded units and cards \
with verbatim quotes, so every claim stays auditable end to end across the whole daily loop.";

fn cfg(root: &Path) -> IntakeConfig {
    IntakeConfig::new(root.to_path_buf(), "2026-06-09".into(), "intake-test".into())
}

fn clip(title: &str, url: &str, body: &str) -> String {
    format!("---\ntitle: \"{title}\"\nsource: \"{url}\"\npublished: 2026-06-01\ncreated: 2026-06-08\ntags:\n  - \"clippings\"\n---\n{body}\n")
}

#[test]
fn sweep_ingests_dedups_flags_and_is_idempotent() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let clippings = root.join("Clippings");
    std::fs::create_dir_all(clippings.join("Twitter")).unwrap();

    // 1 good clipping (nested), 1 thin bookmark, 1 broken frontmatter,
    // 1 URL-duplicate of the good one (different bytes).
    std::fs::write(clippings.join("Twitter/Good Article.md"), clip("Good Article", "https://e.x/good", LONG_BODY)).unwrap();
    std::fs::write(clippings.join("thin.md"), clip("Thin", "https://e.x/thin", "too short")).unwrap();
    std::fs::write(clippings.join("broken.md"), "---\ntitle: [unclosed\n---\nbody\n").unwrap();
    std::fs::write(clippings.join("reclip.md"), clip("Good Article (reclipped)", "https://e.x/good", &format!("{LONG_BODY} extra"))).unwrap();

    let out = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(out.ingested.len(), 1, "{out:?}");
    assert_eq!(out.duplicates.len(), 1);
    assert_eq!(out.needs_content.len(), 1);
    assert_eq!(out.unparseable.len(), 1);

    // Ingested file landed normalized in 01-Raw/<month-of-published>/.
    let to = out.ingested[0].to.as_ref().unwrap();
    assert!(to.starts_with("50-Inbox/01-Raw/2026-06/2026-06-01_Good Article-"), "got {to}");
    assert!(root.join(to).exists());
    assert!(!clippings.join("Twitter/Good Article.md").exists(), "moved, not copied");

    // URL-duplicate parked under duplicates dir; original bytes preserved.
    let dup_to = out.duplicates[0].to.as_ref().unwrap();
    assert!(dup_to.starts_with("50-Inbox/03-Processed/duplicates/2026-06/"), "got {dup_to}");
    assert_eq!(out.duplicates[0].dup_of.as_deref(), Some("url:https://e.x/good"));
    assert!(root.join(dup_to).exists());

    // Thin + broken left in place.
    assert!(clippings.join("thin.md").exists());
    assert!(clippings.join("broken.md").exists());

    // Ledger has all 4 dispositions; pipeline log has exactly the 2 moves.
    let ledger = read_intake_ledger(&root.join(".ovp/intake.jsonl")).unwrap();
    assert_eq!(ledger.len(), 4);
    let log = std::fs::read_to_string(root.join("60-Logs/pipeline.jsonl")).unwrap();
    assert_eq!(log.lines().count(), 2);
    assert!(log.contains("intake_move") && log.contains("intake_duplicate_move"));
    assert!(log.contains("\"event_type\""), "legacy-compatible key");

    // Re-run: nothing new (flagged files skipped quietly, moved files gone) —
    // but the previously-flagged captures surface in `flagged_pending` with
    // their URLs so the enrichment phases can retry them.
    let out2 = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(out2.total_new_records(), 0, "{out2:?}");
    assert_eq!(out2.already_flagged, 2);
    assert_eq!(out2.flagged_pending.len(), 2);
    let thin = out2
        .flagged_pending
        .iter()
        .find(|(from, _)| from.ends_with("thin.md"))
        .expect("thin.md is pending");
    assert_eq!(thin.1.as_deref(), Some("https://e.x/thin"));
    let broken = out2
        .flagged_pending
        .iter()
        .find(|(from, _)| from.ends_with("broken.md"))
        .expect("broken.md is pending");
    assert_eq!(broken.1, None, "unparseable capture carries no URL");
    assert_eq!(read_intake_ledger(&root.join(".ovp/intake.jsonl")).unwrap().len(), 4);

    // Editing the thin file (adding content) re-evaluates it by hash.
    std::fs::write(clippings.join("thin.md"), clip("Thin", "https://e.x/thin", LONG_BODY)).unwrap();
    let out3 = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(out3.ingested.len(), 1);
    assert_eq!(out3.already_flagged, 1, "only broken.md remains flagged");
}

#[test]
fn dry_run_plans_without_touching_anything() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    std::fs::create_dir_all(root.join("Clippings")).unwrap();
    std::fs::write(root.join("Clippings/a.md"), clip("A", "https://e.x/a", LONG_BODY)).unwrap();

    let out = sweep_intake(&cfg(root), &HashSet::new(), true).unwrap();
    assert_eq!(out.ingested.len(), 1);
    assert!(out.dry_run);
    assert!(root.join("Clippings/a.md").exists(), "no move on dry run");
    assert!(!root.join(".ovp/intake.jsonl").exists(), "no ledger on dry run");
    assert!(!root.join("60-Logs/pipeline.jsonl").exists(), "no events on dry run");
}

#[test]
fn daily_succeeded_hashes_park_already_processed_content() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    std::fs::create_dir_all(root.join("Clippings")).unwrap();
    let body = clip("Seen", "https://e.x/seen", LONG_BODY);
    std::fs::write(root.join("Clippings/seen.md"), &body).unwrap();

    let mut done = HashSet::new();
    done.insert(ovp_intake::hex_sha256(body.as_bytes()));
    let out = sweep_intake(&cfg(root), &done, false).unwrap();
    assert_eq!(out.duplicates.len(), 1);
    assert!(out.duplicates[0].dup_of.as_ref().unwrap().starts_with("sha256:"));
}

#[test]
fn pinboard_sync_materializes_dedups_and_feeds_sweep() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    let long_note = LONG_BODY;
    std::fs::write(&export, format!(r#"[
      {{"href":"https://rich.example/post","description":"Rich bookmark","extended":"{long_note}","time":"2026-06-02T08:00:00Z","tags":"ai rust"}},
      {{"href":"https://bare.example/link","description":"Bare bookmark","extended":"just a line","time":"2026-06-03T09:00:00Z","tags":""}},
      {{"href":"","description":"no url","extended":"","time":"","tags":""}}
    ]"#)).unwrap();

    // Sync: 2 notes materialized (empty-URL skipped).
    let mut fetch = FixturePinboardFetch::new(&export);
    let out = sync_pinboard(&cfg(root), &mut fetch, false, &Default::default()).unwrap();
    assert_eq!(out.fetched, 3);
    assert_eq!(out.new_notes.len(), 2);
    assert_eq!(out.skipped_empty_url, 1);
    for rec in &out.new_notes {
        assert!(rec.to.starts_with("50-Inbox/02-Pinboard/"), "got {}", rec.to);
        assert!(root.join(&rec.to).exists());
    }

    // Second sync is a no-op.
    let out2 = sync_pinboard(
        &cfg(root),
        &mut FixturePinboardFetch::new(&export),
        false,
        &Default::default(),
    )
    .unwrap();
    assert_eq!(out2.new_notes.len(), 0);
    assert_eq!(out2.skipped_known, 2);
    assert_eq!(read_pinboard_ledger(&root.join(".ovp/pinboard-sync.jsonl")).unwrap().len(), 2);

    // Sweep: the rich bookmark flows to 01-Raw; the bare one is flagged.
    let sweep = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(sweep.ingested.len(), 1, "{sweep:?}");
    assert_eq!(sweep.needs_content.len(), 1);
    let to = sweep.ingested[0].to.as_ref().unwrap();
    assert!(to.starts_with("50-Inbox/01-Raw/2026-06/2026-06-02_Rich bookmark-"), "got {to}");
    assert_eq!(sweep.ingested[0].url.as_deref(), Some("https://rich.example/post"));
}

/// Export with `n` bare bookmarks at distinct ascending timestamps.
fn write_flood_export(path: &Path, n: usize) {
    let posts: Vec<serde_json::Value> = (0..n)
        .map(|i| {
            serde_json::json!({
                "href": format!("https://e.x/p{i}"),
                "description": format!("Post {i}"),
                "extended": "",
                "time": format!("2020-01-01T{:02}:{:02}:00Z", i / 60, i % 60),
                "tags": ""
            })
        })
        .collect();
    std::fs::write(path, serde_json::to_string(&posts).unwrap()).unwrap();
}

#[test]
fn pinboard_since_filters_older_and_undated_bookmarks() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    std::fs::write(&export, r#"[
      {"href":"https://e.x/old","description":"Old","extended":"","time":"2026-06-01T10:00:00Z","tags":""},
      {"href":"https://e.x/edge","description":"On the cutoff","extended":"","time":"2026-06-03T00:00:00Z","tags":""},
      {"href":"https://e.x/new","description":"New","extended":"","time":"2026-06-05T10:00:00Z","tags":""},
      {"href":"https://e.x/undated","description":"No timestamp","extended":"","time":"","tags":""}
    ]"#).unwrap();

    let opts = PinboardSyncOptions { since: Some("2026-06-03".into()), ..Default::default() };
    let out = sync_pinboard(&cfg(root), &mut FixturePinboardFetch::new(&export), false, &opts)
        .unwrap();
    // since WITHOUT until pushes the cutoff down to the fetch (5b0bc0ea:
    // live uses fromdt — unfiltered posts/all HTTP 500s on large accounts;
    // the fixture filters identically), so the old + undated posts never
    // arrive: fetched counts only on/after-cutoff posts, and the
    // materialize-side skipped_since stays 0. (since+until backfill windows
    // still fetch the full export and filter materialize-side.)
    assert_eq!(out.fetched, 2);
    assert_eq!(out.skipped_since, 0, "excluded at fetch, not materialize: {out:?}");
    let urls: Vec<&str> = out.new_notes.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(urls, ["https://e.x/edge", "https://e.x/new"], "on/after cutoff, oldest first");
    assert_eq!(read_pinboard_ledger(&root.join(".ovp/pinboard-sync.jsonl")).unwrap().len(), 2);
}

#[test]
fn pinboard_since_rejects_malformed_date() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    write_flood_export(&export, 1);
    let opts = PinboardSyncOptions { since: Some("06/03/2026".into()), ..Default::default() };
    let err = sync_pinboard(&cfg(root), &mut FixturePinboardFetch::new(&export), false, &opts)
        .unwrap_err();
    assert!(err.contains("YYYY-MM-DD"), "{err}");
}

#[test]
fn pinboard_max_takes_newest_and_drains_incrementally() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    write_flood_export(&export, 4); // p0 oldest … p3 newest

    let opts = PinboardSyncOptions { max: Some(2), ..Default::default() };
    let out = sync_pinboard(&cfg(root), &mut FixturePinboardFetch::new(&export), false, &opts)
        .unwrap();
    assert_eq!(out.skipped_over_max, 2);
    let urls: Vec<&str> = out.new_notes.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(urls, ["https://e.x/p2", "https://e.x/p3"], "the 2 NEWEST, processed oldest-first");

    // Second capped run: the newest are now ledger-known, so the next-newest
    // drain through — filters narrow, dedup semantics unchanged.
    let out2 = sync_pinboard(&cfg(root), &mut FixturePinboardFetch::new(&export), false, &opts)
        .unwrap();
    assert_eq!(out2.skipped_known, 2);
    let urls2: Vec<&str> = out2.new_notes.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(urls2, ["https://e.x/p0", "https://e.x/p1"]);
    assert_eq!(read_pinboard_ledger(&root.join(".ovp/pinboard-sync.jsonl")).unwrap().len(), 4);
}

#[test]
fn pinboard_first_sync_guard_aborts_unfiltered_flood_before_any_write() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    write_flood_export(&export, FIRST_SYNC_GUARD_MAX_NEW + 1);

    let err = sync_pinboard(
        &cfg(root),
        &mut FixturePinboardFetch::new(&export),
        false,
        &Default::default(),
    )
    .unwrap_err();
    assert!(err.contains("501 NEW bookmarks"), "states the count: {err}");
    assert!(err.contains("--since") && err.contains("--max") && err.contains("--yes-all"),
        "names every way forward: {err}");

    // ABORT means nothing on disk: no notes, no ledger, no write-log events.
    assert!(!root.join("50-Inbox/02-Pinboard").exists(), "no notes written");
    assert!(!root.join(".ovp/pinboard-sync.jsonl").exists(), "no ledger");
    assert!(!root.join("60-Logs/pipeline.jsonl").exists(), "no events");
}

#[test]
fn pinboard_first_sync_guard_yes_all_overrides_then_stays_quiet() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    write_flood_export(&export, FIRST_SYNC_GUARD_MAX_NEW + 1);

    let opts = PinboardSyncOptions { yes_all: true, ..Default::default() };
    let out = sync_pinboard(&cfg(root), &mut FixturePinboardFetch::new(&export), false, &opts)
        .unwrap();
    assert_eq!(out.new_notes.len(), FIRST_SYNC_GUARD_MAX_NEW + 1);

    // Rerun WITHOUT the override: everything is ledger-known, 0 new → the
    // guard counts NEW bookmarks only, so no abort.
    let out2 = sync_pinboard(
        &cfg(root),
        &mut FixturePinboardFetch::new(&export),
        false,
        &Default::default(),
    )
    .unwrap();
    assert_eq!(out2.new_notes.len(), 0);
    assert_eq!(out2.skipped_known, FIRST_SYNC_GUARD_MAX_NEW + 1);
}

#[test]
fn pinboard_first_sync_guard_exempts_dry_run_but_reports_it() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let export = root.join("export.json");
    write_flood_export(&export, FIRST_SYNC_GUARD_MAX_NEW + 1);

    let out = sync_pinboard(
        &cfg(root),
        &mut FixturePinboardFetch::new(&export),
        true,
        &Default::default(),
    )
    .unwrap();
    assert!(out.guard_would_abort, "dry run reports the would-be abort");
    assert_eq!(out.new_notes.len(), FIRST_SYNC_GUARD_MAX_NEW + 1, "count is reported");
    assert!(!root.join("50-Inbox/02-Pinboard").exists(), "dry run writes nothing");
    assert!(!root.join(".ovp/pinboard-sync.jsonl").exists());
}

/// `ovp/skip` is a TERMINAL disposition, and `ovp/force` overrides the size
/// gate. Both are operator intent, which no page heuristic can recover: a
/// measurement over the real 1448-source corpus found that structural signals
/// flagged a 73k-char knowledge-base writeup and a 46k-char CUDA article
/// alongside the three genuine navigation pages, so the operator says so.
#[test]
fn reserved_tags_skip_terminally_and_force_past_the_size_gate() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let clippings = root.join("Clippings");
    std::fs::create_dir_all(&clippings).unwrap();

    let tagged = |title: &str, url: &str, body: &str, tag: &str| {
        format!(
            "---\ntitle: \"{title}\"\nsource: \"{url}\"\npublished: 2026-06-01\n\
             created: 2026-06-08\ntags:\n  - \"clippings\"\n  - \"{tag}\"\n---\n{body}\n"
        )
    };

    // A brand homepage the operator keeps as a quick entry point. Body is LONG,
    // so no automatic gate would have caught it — only the tag does.
    std::fs::write(
        clippings.join("Brand Home.md"),
        tagged("Brand Home", "https://e.x/brand", LONG_BODY, "ovp/skip"),
    )
    .unwrap();
    // A deliberately terse note the operator wants read anyway (below the
    // 200-char gate, which would otherwise park it as needs-content).
    std::fs::write(
        clippings.join("Short But Wanted.md"),
        tagged("Short But Wanted", "https://e.x/short", "Two sentences. That is all.", "ovp/force"),
    )
    .unwrap();

    let sweep = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(sweep.skipped.len(), 1, "the tagged homepage is skipped");
    assert_eq!(sweep.skipped[0].title.as_deref(), Some("Brand Home"));
    assert_eq!(sweep.needs_content.len(), 0, "force beat the size gate");
    assert_eq!(sweep.ingested.len(), 1, "only the forced note was ingested");
    assert_eq!(sweep.ingested[0].title.as_deref(), Some("Short But Wanted"));

    // Re-sweep: the skipped capture is recognised by hash and NOT re-recorded.
    // Critically it must not land in `flagged_pending` either — that list is
    // what enrichment re-fetches, and re-fetching a skipped page every run is
    // exactly the waste this tag exists to stop (six times a day at `every 4h`).
    let again = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(again.skipped.len(), 0, "no duplicate skip record");
    assert_eq!(again.already_flagged, 1);
    assert!(
        again.flagged_pending.is_empty(),
        "a skipped capture must never be handed back to enrichment: {:?}",
        again.flagged_pending
    );

    // The ledger carries the reason, so the disposition is auditable.
    let ledger = read_intake_ledger(&root.join(".ovp/intake.jsonl")).unwrap();
    let skip_rec = ledger
        .iter()
        .find(|r| r.title.as_deref() == Some("Brand Home"))
        .expect("skip record persisted");
    assert!(
        skip_rec.note.as_deref().unwrap_or("").contains("ovp/skip"),
        "note names the tag: {:?}",
        skip_rec.note
    );
}

// -- legacy URL dedup (pre-intake copies) ------------------------------------

#[test]
fn sweep_dedups_against_pre_intake_processed_sources() {
    // The intake ledger only reaches back to its go-live; a source processed
    // BEFORE intake existed has no Ingested record. Re-clipping its URL must
    // still park as a duplicate — the processed tree is the dedup authority
    // the ledger can't be.
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let processed = root.join("50-Inbox/03-Processed/2026-04");
    std::fs::create_dir_all(&processed).unwrap();
    std::fs::write(
        processed.join("2026-04-05_Legacy Article.md"),
        clip("Legacy Article", "https://e.x/legacy", LONG_BODY),
    )
    .unwrap();

    let clippings = root.join("Clippings");
    std::fs::create_dir_all(&clippings).unwrap();
    std::fs::write(
        clippings.join("reclip.md"),
        clip("Legacy Article (reclipped)", "https://e.x/legacy", &format!("{LONG_BODY} v2")),
    )
    .unwrap();

    let out = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(out.ingested.len(), 0, "{out:?}");
    assert_eq!(out.duplicates.len(), 1);
    assert_eq!(out.duplicates[0].dup_of.as_deref(), Some("url:https://e.x/legacy"));
    // The legacy original stays untouched.
    assert!(processed.join("2026-04-05_Legacy Article.md").exists());
}

#[test]
fn park_legacy_url_duplicates_keeps_oldest_and_is_idempotent() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let april = root.join("50-Inbox/03-Processed/2026-04");
    let may = root.join("50-Inbox/03-Processed/2026-05");
    std::fs::create_dir_all(&april).unwrap();
    std::fs::create_dir_all(&may).unwrap();
    // Same URL captured twice (different bytes), plus an unrelated unique.
    std::fs::write(
        april.join("2026-04-05_Twice.md"),
        clip("Twice", "https://e.x/twice", LONG_BODY),
    )
    .unwrap();
    std::fs::write(
        may.join("2026-05-07_Twice.md"),
        clip("Twice (reclip)", "https://e.x/twice", &format!("{LONG_BODY} v2")),
    )
    .unwrap();
    std::fs::write(
        may.join("2026-05-08_Unique.md"),
        clip("Unique", "https://e.x/unique", LONG_BODY),
    )
    .unwrap();

    // Dry-run: plans the park, moves nothing.
    let plan = ovp_intake::park_legacy_url_duplicates(&cfg(root), true).unwrap();
    assert_eq!(plan.len(), 1);
    assert_eq!(plan[0].url, "https://e.x/twice");
    assert!(plan[0].kept.contains("2026-04-05_Twice"), "oldest kept: {}", plan[0].kept);
    assert_eq!(plan[0].parked.len(), 1);
    assert!(may.join("2026-05-07_Twice.md").exists(), "dry-run must not move");
    assert!(read_intake_ledger(&root.join(".ovp/intake.jsonl")).unwrap().is_empty());

    // Apply: newer copy parked with a normal Duplicate ledger record.
    let done = ovp_intake::park_legacy_url_duplicates(&cfg(root), false).unwrap();
    assert_eq!(done.len(), 1);
    let to = done[0].parked[0].to.as_ref().unwrap();
    assert!(to.starts_with("50-Inbox/03-Processed/duplicates/2026-06/"), "got {to}");
    assert!(root.join(to).exists());
    assert!(!may.join("2026-05-07_Twice.md").exists());
    assert!(april.join("2026-04-05_Twice.md").exists());
    assert!(may.join("2026-05-08_Unique.md").exists(), "unique untouched");
    let ledger = read_intake_ledger(&root.join(".ovp/intake.jsonl")).unwrap();
    assert_eq!(ledger.len(), 1);
    assert_eq!(ledger[0].dup_of.as_deref(), Some("url:https://e.x/twice"));

    // Second run: nothing left to do (parked copies are excluded from the walk).
    let again = ovp_intake::park_legacy_url_duplicates(&cfg(root), false).unwrap();
    assert!(again.is_empty(), "{again:?}");
}
