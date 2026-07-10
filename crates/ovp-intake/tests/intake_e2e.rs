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

    // Re-run: nothing new (flagged files skipped quietly, moved files gone).
    let out2 = sweep_intake(&cfg(root), &HashSet::new(), false).unwrap();
    assert_eq!(out2.total_new_records(), 0, "{out2:?}");
    assert_eq!(out2.already_flagged, 2);
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
    assert_eq!(out.fetched, 4);
    assert_eq!(out.skipped_since, 2, "old + undated excluded: {out:?}");
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
