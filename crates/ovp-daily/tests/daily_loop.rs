//! End-to-end daily loop on a temp vault with canned model clients: plan →
//! run → re-plan. Proves the M30 contract: packs land in the vault-local
//! product dir, every attempt is in the durable ledger, every pack write is in
//! `pipeline.jsonl`, a re-run is a no-op, and a failed source stays retryable.

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
    }
}

#[test]
fn daily_loop_processes_dedups_and_logs() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("alpha", BODY)]);
    let ledger_path = root.join(".ovp/daily-runs.jsonl");

    // First run: one new source, processed through the reader trunk.
    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[]).unwrap();
    assert_eq!(work.todo.len(), 1);

    let card_reply = format!(
        r#"{{"cards":[{{"title":"Chunks are neutral","content":"A chunk is structurally neutral.","cited_unit_ids":["{}"]}}]}}"#,
        accepted_unit_id()
    );
    let mut factory = factory_for(vec![units_reply(), "{}".into(), card_reply]);
    let report = run_daily(&cfg(root), &work, &mut factory).unwrap();
    assert_eq!(report.processed.len(), 1);
    assert_eq!(report.failed(), 0);

    // Product surface: the pack is vault-local under 40-Resources/Reader/.
    let rec = &report.processed[0];
    assert_eq!(rec.status, RunStatus::Succeeded);
    let pack_dir = root.join(rec.pack_dir.as_ref().expect("pack dir recorded"));
    assert!(rec.pack_dir.as_ref().unwrap().starts_with("40-Resources/Reader/2026-06-09_"));
    for f in ["reader.md", "reader.html", "cards.json", "units.accepted.json", "run-status.json"] {
        assert!(pack_dir.join(f).exists(), "missing {f} in {}", pack_dir.display());
    }

    // Durable ledger: one succeeded record.
    let ledger = read_daily_ledger(&ledger_path).unwrap();
    assert_eq!(ledger.len(), 1);
    assert_eq!(ledger[0].status, RunStatus::Succeeded);
    assert_eq!(ledger[0].cards, 1);

    // OVP_RULES write log: one reader_pack_write event naming the target.
    let log = std::fs::read_to_string(root.join("60-Logs/pipeline.jsonl")).unwrap();
    assert_eq!(log.lines().count(), 1);
    assert!(log.contains("reader_pack_write"));
    assert!(log.contains(rec.pack_dir.as_ref().unwrap().as_str()));

    // Second run: dedup makes it a no-op — no new ledger lines, no model calls.
    let work2 = plan_daily(&root.join("50-Inbox/01-Raw"), root, &ledger).unwrap();
    assert!(work2.todo.is_empty(), "succeeded source must be skipped");
    assert_eq!(work2.skipped.len(), 1);
    let mut no_calls = || -> Result<Box<dyn ModelClient>, String> {
        panic!("no client may be built on a fully-deduped run")
    };
    let report2 = run_daily(&cfg(root), &work2, &mut no_calls).unwrap();
    assert_eq!(report2.processed.len(), 0);
    assert_eq!(report2.skipped, 1);
    assert_eq!(read_daily_ledger(&ledger_path).unwrap().len(), 1, "ledger unchanged");
}

#[test]
fn failed_source_is_recorded_and_stays_retryable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("broken", BODY)]);
    let ledger_path = root.join(".ovp/daily-runs.jsonl");

    // The base model reply is garbage → truth-layer failure.
    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[]).unwrap();
    let mut factory = factory_for(vec!["not json".into(), "{}".into(), "{}".into()]);
    let report = run_daily(&cfg(root), &work, &mut factory).unwrap();
    assert_eq!(report.failed(), 1);

    let ledger = read_daily_ledger(&ledger_path).unwrap();
    assert_eq!(ledger[0].status, RunStatus::Failed);
    assert!(ledger[0].reason.as_ref().unwrap().contains("truth-layer"), "{:?}", ledger[0].reason);
    assert!(ledger[0].pack_dir.is_none());

    // No write-log event for a failed run (nothing product-facing was written).
    assert!(!root.join("60-Logs/pipeline.jsonl").exists());

    // The failed source is still in the next plan (retryable).
    let work2 = plan_daily(&root.join("50-Inbox/01-Raw"), root, &ledger).unwrap();
    assert_eq!(work2.todo.len(), 1);
}

#[test]
fn max_sources_caps_a_run_loudly() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_vault_with_inbox(root, &[("a", BODY), ("b", BODY2), ("c", BODY3)]);

    let work = plan_daily(&root.join("50-Inbox/01-Raw"), root, &[]).unwrap();
    assert_eq!(work.todo.len(), 3);

    let mut cfg = cfg(root);
    cfg.max_sources = 1;
    // The single processed source fails fast (bad reply) — the cap math is the
    // point here, not the pipeline outcome.
    let mut factory = factory_for(vec!["not json".into(), "{}".into(), "{}".into()]);
    let report = run_daily(&cfg, &work, &mut factory).unwrap();
    assert_eq!(report.processed.len(), 1);
    assert_eq!(report.capped, 2);
}

const BODY2: &str = "Benchmark maxxing is for augmenting experts.";
const BODY3: &str = "IdeaBlocks replace prose chunks for retrieval.";
