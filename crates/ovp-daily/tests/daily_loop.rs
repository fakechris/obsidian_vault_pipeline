//! End-to-end daily loop on a temp vault with canned model clients: plan →
//! run → re-plan. Proves the M30/M31 contract: packs land in the vault-local
//! product dir, every attempt is in the durable ledger, every pack write is in
//! `pipeline.jsonl` BEFORE its success record (audit ordering), succeeded
//! sources move to `03-Processed/`, a re-run is a no-op, a failed source stays
//! retryable, and three failures block a source.

use std::path::Path;

use ovp_daily::{
    plan_daily, read_daily_ledger, run_daily, DailyConfig, RunStatus,
};
use ovp_llm::{CallError, ModelClient, ModelReply, ModelRequest, StopReason, Usage};

const BODY: &str = "A chunk is a structurally neutral container. It knows nothing about ownership.";

/// Serves one canned reply per reader-trunk stage, routed by call order within
/// a source (clients are built base → critic → cards).
struct Canned(String);
impl ModelClient for Canned {
    fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
        Ok(ModelReply {
            model: "canned".into(),
            text: self.0.clone(),
            stop_reason: StopReason::EndTurn,
            usage: Usage { input_tokens: 1, output_tokens: 1 },
        })
    }
}

fn units_reply() -> String {
    r#"{"units":[{"kind":"assertion","text":"A chunk is structurally neutral.",
        "evidence_ref":"p001","evidence_quote":"A chunk is a structurally neutral container.",
        "attribution":"author","modality":"asserted","arguments":[]}]}"#
        .to_string()
}

/// The deterministic id of the accepted unit, learned through the public API so
/// the canned card reply can cite it.
fn accepted_unit_id() -> String {
    let src = ovp_domain::SourceDoc::article("T", "https://e/x", None, None, vec![], BODY);
    let ex = ovp_domain::units::extract_units(&units_reply(), &src);
    ex.accepted().next().expect("one accepted unit").id.clone()
}

fn write_vault_with_inbox(root: &Path, files: &[(&str, &str)]) {
    let inbox = root.join("50-Inbox/01-Raw");
    std::fs::create_dir_all(&inbox).unwrap();
    for (name, body) in files {
        let note = format!("---\ntitle: {name} note\nsource: https://e/x\n---\n{body}\n");
        std::fs::write(inbox.join(format!("{name}.md")), note).unwrap();
    }
}

fn factory_for(
    replies: Vec<String>,
) -> impl FnMut() -> Result<Box<dyn ModelClient>, String> {
    let mut i = 0usize;
    move || {
        let text = replies.get(i).cloned().unwrap_or_else(|| "{}".into());
        i += 1;
        Ok(Box::new(Canned(text)) as Box<dyn ModelClient>)
    }
}

fn cfg(root: &Path) -> DailyConfig {
    DailyConfig {
        vault_root: root.to_path_buf(),
        date: "2026-06-09".into(),
        run_id: "daily-test".into(),
        max_sources: 0,
        lifecycle_move: true,
        retry_blocked: false,
    }
}

#[test]
fn daily_loop_processes_moves_dedups_and_logs_in_order() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("alpha", BODY)]);
    let ledger_path = root.join(".ovp/daily-runs.jsonl");

    // First run: one new source, processed through the reader trunk.
    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[], false).unwrap();
    assert_eq!(work.todo.len(), 1);

    let card_reply = format!(
        r#"{{"cards":[{{"title":"Chunks are neutral","content":"A chunk is structurally neutral.","cited_unit_ids":["{}"]}}]}}"#,
        accepted_unit_id()
    );
    let mut factory = factory_for(vec![units_reply(), "{}".into(), card_reply]);
    let report = run_daily(&cfg(root), &work, &mut factory).unwrap();
    assert_eq!(report.processed.len(), 1);
    assert_eq!(report.failed(), 0);
    assert!(report.lifecycle_warnings.is_empty(), "{:?}", report.lifecycle_warnings);

    // Product surface: the pack is vault-local under 40-Resources/Reader/.
    let rec = &report.processed[0];
    assert_eq!(rec.status, RunStatus::Succeeded);
    let pack_dir = root.join(rec.pack_dir.as_ref().expect("pack dir recorded"));
    assert!(rec.pack_dir.as_ref().unwrap().starts_with("40-Resources/Reader/2026-06-09_"));
    for f in ["reader.md", "reader.html", "cards.json", "units.accepted.json", "run-status.json"] {
        assert!(pack_dir.join(f).exists(), "missing {f} in {}", pack_dir.display());
    }

    // Lifecycle: the source moved out of the raw queue, filename kept.
    let moved_to = rec.moved_to.as_ref().expect("lifecycle move recorded");
    assert_eq!(moved_to, "50-Inbox/03-Processed/2026-06/alpha.md");
    assert!(root.join(moved_to).exists());
    assert!(!root.join("50-Inbox/01-Raw/alpha.md").exists(), "moved, not copied");

    // Durable ledger: one succeeded record.
    let ledger = read_daily_ledger(&ledger_path).unwrap();
    assert_eq!(ledger.len(), 1);
    assert_eq!(ledger[0].status, RunStatus::Succeeded);
    assert_eq!(ledger[0].cards, 1);

    // OVP_RULES write log: pack write + processed move, in that order, using
    // the legacy-compatible `event_type` key.
    let log = std::fs::read_to_string(root.join("60-Logs/pipeline.jsonl")).unwrap();
    let lines: Vec<&str> = log.lines().collect();
    assert_eq!(lines.len(), 2);
    assert!(lines[0].contains("reader_pack_write"));
    assert!(lines[0].contains(rec.pack_dir.as_ref().unwrap().as_str()));
    assert!(lines[1].contains("source_processed_move"));
    assert!(lines[0].contains("\"event_type\""));

    // Second run: dedup makes it a no-op — the processed file is out of the
    // inbox AND its hash is in the ledger, so even a copy left behind would
    // skip. No new ledger lines, no model calls.
    let ledger = read_daily_ledger(&ledger_path).unwrap();
    let work2 = plan_daily(&root.join("50-Inbox/01-Raw"), root, &ledger, false).unwrap();
    assert!(work2.todo.is_empty(), "nothing left to do");
    let mut no_calls = || -> Result<Box<dyn ModelClient>, String> {
        panic!("no client may be built on a fully-deduped run")
    };
    let report2 = run_daily(&cfg(root), &work2, &mut no_calls).unwrap();
    assert_eq!(report2.processed.len(), 0);
    assert_eq!(read_daily_ledger(&ledger_path).unwrap().len(), 1, "ledger unchanged");
}

#[test]
fn write_log_event_precedes_success_record() {
    // Audit-ordering invariant (M31): if the write-log append FAILS, no
    // success record may be appended. Force the failure by making the log
    // path unwritable (a directory).
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("alpha", BODY)]);
    std::fs::create_dir_all(root.join("60-Logs/pipeline.jsonl")).unwrap(); // a DIR at the log path

    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[], false).unwrap();
    let card_reply = format!(
        r#"{{"cards":[{{"title":"t","content":"A chunk is structurally neutral.","cited_unit_ids":["{}"]}}]}}"#,
        accepted_unit_id()
    );
    let mut factory = factory_for(vec![units_reply(), "{}".into(), card_reply]);
    let err = run_daily(&cfg(root), &work, &mut factory)
        .expect_err("log append failure must abort, not record success");
    assert!(err.contains("pipeline.jsonl"), "got: {err}");
    assert!(
        !root.join(".ovp/daily-runs.jsonl").exists(),
        "no success record without its write-log event"
    );
}

#[test]
fn failed_source_is_recorded_stays_retryable_then_blocks() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("broken", BODY)]);
    let ledger_path = root.join(".ovp/daily-runs.jsonl");
    let inbox = root.join("50-Inbox/01-Raw");

    // Three runs with a garbage base reply → 3 failed records.
    for i in 0..3 {
        let ledger = read_daily_ledger(&ledger_path).unwrap();
        let work = plan_daily(&inbox, root, &ledger, false).unwrap();
        assert_eq!(work.todo.len(), 1, "run {i}: failed source still planned");
        let mut factory = factory_for(vec!["not json".into(), "{}".into(), "{}".into()]);
        let report = run_daily(&cfg(root), &work, &mut factory).unwrap();
        assert_eq!(report.failed(), 1);
    }

    let ledger = read_daily_ledger(&ledger_path).unwrap();
    assert_eq!(ledger.len(), 3);
    assert!(ledger[0].reason.as_ref().unwrap().contains("truth-layer"));
    assert!(ledger[0].pack_dir.is_none());
    assert!(!root.join("60-Logs/pipeline.jsonl").exists(), "no write-log events for failures");
    assert!(inbox.join("broken.md").exists(), "failed source stays in the raw queue");

    // Fourth plan: blocked (not retried, not skipped-silently).
    let work = plan_daily(&inbox, root, &ledger, false).unwrap();
    assert!(work.todo.is_empty());
    assert_eq!(work.blocked.len(), 1);
    assert_eq!(work.blocked[0].prior_failures, 3);

    // Operator override retries it.
    let work = plan_daily(&inbox, root, &ledger, true).unwrap();
    assert_eq!(work.todo.len(), 1);
}

#[test]
fn max_sources_caps_a_run_loudly() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("a", BODY), ("b", BODY2), ("c", BODY3)]);

    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[], false).unwrap();
    assert_eq!(work.todo.len(), 3);

    let mut c = cfg(root);
    c.max_sources = 1;
    // The single processed source fails fast (bad reply) — the cap math is the
    // point here, not the pipeline outcome.
    let mut factory = factory_for(vec!["not json".into(), "{}".into(), "{}".into()]);
    let report = run_daily(&c, &work, &mut factory).unwrap();
    assert_eq!(report.processed.len(), 1);
    assert_eq!(report.capped, 2);
}

#[test]
fn lifecycle_move_failure_is_a_warning_with_the_record_already_durable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("alpha", BODY)]);
    // Make the processed dir IMPOSSIBLE to create: a file where the month dir's
    // parent must go.
    std::fs::write(root.join("50-Inbox/03-Processed"), "a file, not a dir").unwrap();

    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[], false).unwrap();
    let card_reply = format!(
        r#"{{"cards":[{{"title":"t","content":"A chunk is structurally neutral.","cited_unit_ids":["{}"]}}]}}"#,
        accepted_unit_id()
    );
    let mut factory = factory_for(vec![units_reply(), "{}".into(), card_reply]);
    let report = run_daily(&cfg(root), &work, &mut factory).unwrap();

    // The run is a SUCCESS — the pack is the product; the move is a warning.
    assert_eq!(report.failed(), 0);
    assert_eq!(report.lifecycle_warnings.len(), 1, "{:?}", report.lifecycle_warnings);
    assert!(report.lifecycle_warnings[0].contains("lifecycle move failed"));
    assert_eq!(report.processed[0].moved_to, None);
    assert!(root.join("50-Inbox/01-Raw/alpha.md").exists(), "source left in place");

    // The ledger record was durable BEFORE the move attempt.
    let ledger = read_daily_ledger(&root.join(".ovp/daily-runs.jsonl")).unwrap();
    assert_eq!(ledger.len(), 1);
    assert_eq!(ledger[0].status, RunStatus::Succeeded);

    // And the next plan dedup-skips the leftover file.
    let work2 = plan_daily(&root.join("50-Inbox/01-Raw"), root, &ledger, false).unwrap();
    assert!(work2.todo.is_empty());
    assert_eq!(work2.skipped.len(), 1);
}

#[test]
fn lifecycle_move_can_be_disabled() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("alpha", BODY)]);

    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[], false).unwrap();
    let card_reply = format!(
        r#"{{"cards":[{{"title":"t","content":"A chunk is structurally neutral.","cited_unit_ids":["{}"]}}]}}"#,
        accepted_unit_id()
    );
    let mut factory = factory_for(vec![units_reply(), "{}".into(), card_reply]);
    let mut c = cfg(root);
    c.lifecycle_move = false;
    let report = run_daily(&c, &work, &mut factory).unwrap();
    assert_eq!(report.failed(), 0);
    assert_eq!(report.processed[0].moved_to, None);
    assert!(root.join("50-Inbox/01-Raw/alpha.md").exists(), "no move when disabled");
}

const BODY2: &str = "Benchmark maxxing is for augmenting experts.";
const BODY3: &str = "IdeaBlocks replace prose chunks for retrieval.";
