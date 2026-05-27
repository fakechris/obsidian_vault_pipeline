//! v0.1 acceptance test: fake source → fake transform (drops 1/3) → fake sink.
//!
//! Asserts:
//! 1. Topology executes in manifest-declared order
//! 2. Dropped record produces a FilterDropped event with the correct reason
//! 3. WritePlan contains exactly 2 ops (one per forwarded record)
//! 4. EventLog timestamps are strictly monotonic
//! 5. RunReport is deterministic across runs
//! 6. Fan-out: one transform feeding two sinks delivers records to both

use ovp_core::fakes::{DropZeroes, FakeBody, FakeSource, VaultPlanSink};
use ovp_core::*;

const MANIFEST: &str = r#"
[pipeline]
nodes = ["fake_source", "fake_transform", "fake_sink"]
edges = [
  ["fake_source", "fake_transform"],
  ["fake_transform", "fake_sink"],
]
"#;

fn build_runner(run_id: &str) -> GraphRunner<FakeBody> {
    let manifest = PipelineManifest::parse(MANIFEST).unwrap();
    let run_id = RunId::new(run_id);
    let mut runner = GraphRunner::new(manifest, run_id.clone());
    runner.register_source("fake_source", FakeSource::new("fake_source", run_id.clone()));
    runner.register_transform("fake_transform", DropZeroes::new("fake_transform"));
    runner.register_sink("fake_sink", VaultPlanSink::new("fake_sink"));
    runner
}

#[test]
fn topology_runs_in_declared_order() {
    let report = build_runner("run-1").run().unwrap();
    assert!(matches!(report.events[0].kind, EventKind::RunStarted));
    let pos = |kind_match: fn(&EventKind) -> bool| {
        report.events.iter().position(|e| kind_match(&e.kind)).unwrap()
    };
    let src_idx = pos(|k| matches!(k, EventKind::SourceProduced { .. }));
    let seen_idx = pos(|k| matches!(k, EventKind::RecordSeen { .. }));
    let fwd_idx = pos(|k| matches!(k, EventKind::RecordForwarded { .. }));
    let snk_idx = pos(|k| matches!(k, EventKind::SinkEmitted { .. }));
    assert!(src_idx < seen_idx, "source must produce before transform sees records");
    assert!(fwd_idx < snk_idx, "transform must forward before sink emits");
}

#[test]
fn drop_emits_filter_dropped_event() {
    let report = build_runner("run-2").run().unwrap();
    let drops: Vec<_> = report
        .events
        .iter()
        .filter_map(|e| match &e.kind {
            EventKind::FilterDropped { record_id, reason, .. } => Some((record_id.clone(), reason.clone())),
            _ => None,
        })
        .collect();
    assert_eq!(drops.len(), 1);
    assert_eq!(drops[0].0.as_str(), "r-drop-me");
    assert_eq!(drops[0].1.code.as_str(), "transform.fake.zero_payload");
    assert!(drops[0].1.detail.contains("drop-me"));
}

#[test]
fn write_plan_has_exactly_two_ops() {
    let report = build_runner("run-3").run().unwrap();
    assert_eq!(report.write_plan.len(), 2);
    let originators: Vec<String> = report
        .write_plan
        .ops
        .iter()
        .map(|op| match op {
            WriteOp::VaultCreate(o) => o.originating_record.as_str().to_string(),
            _ => panic!("unexpected op kind"),
        })
        .collect();
    assert_eq!(originators, vec!["r-keep-a", "r-keep-b"]);
    assert_eq!(report.records_dropped, 1);
    assert_eq!(report.records_forwarded_to_sinks, 2);
}

#[test]
fn event_log_is_strictly_monotonic() {
    let report = build_runner("run-4").run().unwrap();
    let mut prev = None;
    for ev in &report.events {
        if let Some(p) = prev {
            assert!(ev.ts > p, "event ts not monotonic: {p:?} -> {:?}", ev.ts);
        }
        prev = Some(ev.ts);
    }
}

#[test]
fn report_is_deterministic_across_runs() {
    let a = build_runner("same-run").run().unwrap();
    let b = build_runner("same-run").run().unwrap();
    assert_eq!(a, b);
}

const FANOUT_MANIFEST: &str = r#"
[pipeline]
nodes = ["fake_source", "fake_transform", "sink_a", "sink_b"]
edges = [
  ["fake_source", "fake_transform"],
  ["fake_transform", "sink_a"],
  ["fake_transform", "sink_b"],
]
"#;

#[test]
fn fan_out_broadcasts_to_both_sinks() {
    // Regression test for the per-edge queue bug found in R1 codex review:
    // one transform → two sinks should deliver records to BOTH sinks, not
    // just the first one in declaration order.
    let manifest = PipelineManifest::parse(FANOUT_MANIFEST).unwrap();
    let run_id = RunId::new("fanout-1");
    let mut runner: GraphRunner<FakeBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source("fake_source", FakeSource::new("fake_source", run_id.clone()));
    runner.register_transform("fake_transform", DropZeroes::new("fake_transform"));
    runner.register_sink("sink_a", VaultPlanSink::new("sink_a"));
    runner.register_sink("sink_b", VaultPlanSink::new("sink_b"));

    let report = runner.run().unwrap();
    // 2 records forwarded (1 dropped) × 2 sinks = 4 records reaching sinks.
    assert_eq!(report.records_forwarded_to_sinks, 4);
    // 2 sinks × 2 ops each = 4 write ops total.
    assert_eq!(report.write_plan.len(), 4);

    let mut emitted_by: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
    for ev in &report.events {
        if let EventKind::SinkEmitted { step_id, ops } = &ev.kind {
            *emitted_by.entry(step_id.as_str().to_string()).or_default() += ops;
        }
    }
    assert_eq!(emitted_by.get("sink_a").copied(), Some(2));
    assert_eq!(emitted_by.get("sink_b").copied(), Some(2));
}
