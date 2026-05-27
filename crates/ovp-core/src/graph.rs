use std::collections::HashMap;

use crate::error::{CoreError, GraphError};
use crate::event::{Event, EventKind, EventLog, EventTs};
use crate::filter::{
    FilterDecision, Sink, SinkOutput, Source, SourceOutput, Transform,
};
use crate::manifest::PipelineManifest;
use crate::plan::WritePlan;
use crate::record::{Record, RunId};

/// A registered node in the graph. Owns the underlying trait object.
enum Node {
    Source(Box<dyn Source>),
    Transform(Box<dyn Transform>),
    Sink(Box<dyn Sink>),
}

impl Node {
    fn kind_str(&self) -> &'static str {
        match self {
            Node::Source(_) => "source",
            Node::Transform(_) => "transform",
            Node::Sink(_) => "sink",
        }
    }
}

/// Outcome of a single end-to-end run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunReport {
    pub run_id: RunId,
    pub write_plan: WritePlan,
    pub events: Vec<Event>,
    pub records_seen: u64,
    pub records_forwarded_to_sinks: u64,
    pub records_dropped: u64,
}

/// In-memory single-threaded GraphRunner.
///
/// v0.1 model:
/// - Sources are drained to exhaustion before transforms run.
/// - Topology is walked in topological order. Each non-source node
///   processes all records currently sitting in its upstream node's
///   output queue.
/// - Sinks emit WriteOps + extra Events; everything ends up in one
///   WritePlan and one EventLog.
/// - Determinism: nodes run in manifest declaration order on ties;
///   records are processed FIFO within a node.
pub struct GraphRunner {
    manifest: PipelineManifest,
    nodes: HashMap<String, Node>,
    run_id: RunId,
}

impl GraphRunner {
    pub fn new(manifest: PipelineManifest, run_id: RunId) -> Self {
        Self { manifest, nodes: HashMap::new(), run_id }
    }

    pub fn register_source(&mut self, name: impl Into<String>, src: impl Source + 'static) {
        self.nodes.insert(name.into(), Node::Source(Box::new(src)));
    }

    pub fn register_transform(&mut self, name: impl Into<String>, tx: impl Transform + 'static) {
        self.nodes.insert(name.into(), Node::Transform(Box::new(tx)));
    }

    pub fn register_sink(&mut self, name: impl Into<String>, snk: impl Sink + 'static) {
        self.nodes.insert(name.into(), Node::Sink(Box::new(snk)));
    }

    pub fn run(mut self) -> Result<RunReport, CoreError> {
        // 1. Validate every manifest node is registered.
        for n in self.manifest.nodes() {
            if !self.nodes.contains_key(n) {
                return Err(GraphError::NodeNotRegistered(n.clone()).into());
            }
        }

        // 2. Resolve topo order.
        let topo = self.manifest.topo_order()?;

        // 3. Build upstream adjacency: who feeds each node?
        let mut upstream: HashMap<String, Vec<String>> = self
            .manifest
            .nodes()
            .iter()
            .map(|n| (n.clone(), Vec::new()))
            .collect();
        for [from, to] in self.manifest.edges() {
            upstream.entry(to.clone()).or_default().push(from.clone());
        }

        // 4. Sanity checks: at least one source, at least one sink.
        let mut source_count = 0usize;
        let mut sink_count = 0usize;
        for n in &topo {
            match &self.nodes[n] {
                Node::Source(_) => source_count += 1,
                Node::Sink(_) => sink_count += 1,
                Node::Transform(_) => {}
            }
        }
        if source_count == 0 {
            return Err(GraphError::NoSource.into());
        }
        if sink_count == 0 {
            return Err(GraphError::NoSink.into());
        }

        // 5. Run.
        let run_id = self.run_id.clone();
        let mut log = EventLog::new();
        log.record(run_id.clone(), EventKind::RunStarted);

        let mut write_plan = WritePlan::new(run_id.clone());
        let mut node_output: HashMap<String, Vec<Record>> = HashMap::new();
        let mut records_seen: u64 = 0;
        let mut records_forwarded: u64 = 0;
        let mut records_dropped: u64 = 0;

        for name in &topo {
            // Re-take node ownership so we can mutate it while consulting
            // sibling state (upstream queues live in node_output).
            let node = self.nodes.remove(name).expect("checked above");
            match node {
                Node::Source(mut src) => {
                    let mut produced: Vec<Record> = Vec::new();
                    loop {
                        match src.produce() {
                            SourceOutput::Records(rs) => {
                                let n = rs.len() as u64;
                                produced.extend(rs);
                                log.record(
                                    run_id.clone(),
                                    EventKind::SourceProduced { step_id: src.step_id().clone(), count: n },
                                );
                            }
                            SourceOutput::Idle => break,
                            SourceOutput::Exhausted => {
                                log.record(
                                    run_id.clone(),
                                    EventKind::SourceExhausted { step_id: src.step_id().clone() },
                                );
                                break;
                            }
                            SourceOutput::Error(e) => {
                                log.record(
                                    run_id.clone(),
                                    EventKind::FilterErrored {
                                        record_id: None,
                                        step_id: src.step_id().clone(),
                                        error: e,
                                    },
                                );
                                break;
                            }
                        }
                    }
                    node_output.insert(name.clone(), produced);
                    self.nodes.insert(name.clone(), Node::Source(src));
                }
                Node::Transform(mut tx) => {
                    let inputs = gather_inputs(name, &upstream, &mut node_output);
                    let mut outputs: Vec<Record> = Vec::new();
                    let mut completed = false;
                    for rec in inputs {
                        if completed {
                            // Transform declared itself done; remaining records
                            // are dropped silently (this is intentional — completion
                            // ends the transform's life for the rest of the run).
                            continue;
                        }
                        records_seen += 1;
                        log.record(
                            run_id.clone(),
                            EventKind::RecordSeen {
                                record_id: rec.id.clone(),
                                step_id: tx.step_id().clone(),
                            },
                        );
                        let rid = rec.id.clone();
                        match tx.process(rec) {
                            FilterDecision::Forward(rs) | FilterDecision::FanOut(rs) => {
                                log.record(
                                    run_id.clone(),
                                    EventKind::RecordForwarded {
                                        record_id: rid,
                                        step_id: tx.step_id().clone(),
                                        fanout: rs.len() as u64,
                                    },
                                );
                                outputs.extend(rs);
                            }
                            FilterDecision::Drop(reason) => {
                                records_dropped += 1;
                                log.record(
                                    run_id.clone(),
                                    EventKind::FilterDropped {
                                        record_id: rid,
                                        step_id: tx.step_id().clone(),
                                        reason,
                                    },
                                );
                            }
                            FilterDecision::Complete(reason) => {
                                completed = true;
                                log.record(
                                    run_id.clone(),
                                    EventKind::FilterCompleted {
                                        step_id: tx.step_id().clone(),
                                        reason,
                                    },
                                );
                            }
                            FilterDecision::Error(err) => {
                                log.record(
                                    run_id.clone(),
                                    EventKind::FilterErrored {
                                        record_id: Some(rid),
                                        step_id: tx.step_id().clone(),
                                        error: err,
                                    },
                                );
                            }
                        }
                    }
                    node_output.insert(name.clone(), outputs);
                    self.nodes.insert(name.clone(), Node::Transform(tx));
                }
                Node::Sink(mut snk) => {
                    let inputs = gather_inputs(name, &upstream, &mut node_output);
                    let mut total_ops: u64 = 0;
                    for rec in inputs {
                        records_forwarded += 1;
                        let SinkOutput { plan_ops, extra_events } = snk.consume(rec);
                        total_ops += plan_ops.len() as u64;
                        write_plan.extend(plan_ops);
                        for ev in extra_events {
                            // Re-stamp the timestamp so the log stays monotonic.
                            log.record(run_id.clone(), ev.kind);
                        }
                    }
                    let SinkOutput { plan_ops, extra_events } = snk.finish();
                    total_ops += plan_ops.len() as u64;
                    write_plan.extend(plan_ops);
                    for ev in extra_events {
                        log.record(run_id.clone(), ev.kind);
                    }
                    log.record(
                        run_id.clone(),
                        EventKind::SinkEmitted { step_id: snk.step_id().clone(), ops: total_ops },
                    );
                    self.nodes.insert(name.clone(), Node::Sink(snk));
                }
            }
        }

        log.record(
            run_id.clone(),
            EventKind::PlanFinalized { ops: write_plan.len() as u64 },
        );
        log.record(
            run_id.clone(),
            EventKind::RunCompleted {
                records_seen,
                ops_emitted: write_plan.len() as u64,
            },
        );

        Ok(RunReport {
            run_id,
            write_plan,
            events: log.into_events(),
            records_seen,
            records_forwarded_to_sinks: records_forwarded,
            records_dropped,
        })
    }

    pub fn manifest(&self) -> &PipelineManifest { &self.manifest }
    pub fn registered_kinds(&self) -> Vec<(String, &'static str)> {
        let mut v: Vec<(String, &'static str)> = self
            .nodes
            .iter()
            .map(|(k, v)| (k.clone(), v.kind_str()))
            .collect();
        v.sort_by(|a, b| a.0.cmp(&b.0));
        v
    }
}

fn gather_inputs(
    me: &str,
    upstream: &HashMap<String, Vec<String>>,
    node_output: &mut HashMap<String, Vec<Record>>,
) -> Vec<Record> {
    let mut out = Vec::new();
    if let Some(ups) = upstream.get(me) {
        for u in ups {
            if let Some(q) = node_output.get_mut(u) {
                out.append(q);
            }
        }
    }
    out
}

// Suppress unused EventTs lint via re-export — we deliberately keep EventTs in
// the public API for downstream consumers even though the runner uses EventLog.
#[allow(dead_code)]
fn _evtts_used(_: EventTs) {}
