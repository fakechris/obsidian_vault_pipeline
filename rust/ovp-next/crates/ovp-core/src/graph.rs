use std::collections::HashMap;

use crate::error::{CoreError, GraphError};
use crate::event::{Event, EventKind, EventLog};
use crate::filter::{
    EffectfulTransform, FilterDecision, Sink, SinkOutput, Source, SourceOutput, Transform,
};
use crate::manifest::PipelineManifest;
use crate::plan::WritePlan;
use crate::record::{Record, RunId};

/// A registered node in the graph. Owns the underlying trait object.
enum Node<B> {
    Source(Box<dyn Source<B>>),
    Transform(Box<dyn Transform<B>>),
    EffectfulTransform(Box<dyn EffectfulTransform<B>>),
    Sink(Box<dyn Sink<B>>),
}

impl<B> Node<B> {
    fn kind_str(&self) -> &'static str {
        match self {
            Node::Source(_) => "source",
            Node::Transform(_) => "transform",
            Node::EffectfulTransform(_) => "effectful_transform",
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
    /// Records a transform/effect node ended in `FilterDecision::Error`. Unlike
    /// a `Drop` (a legitimate refusal), an `Error` is a failure — a non-zero
    /// count means the run did NOT complete cleanly and callers must treat it as
    /// a failure rather than an empty success. (Distinct from `records_dropped`.)
    pub records_errored: u64,
    /// The first `FilterError` seen, for diagnostics. `None` iff
    /// `records_errored == 0`.
    pub first_error: Option<crate::filter::FilterError>,
}

impl RunReport {
    /// True iff no node errored. A clean run still may have legitimately
    /// dropped records (e.g. an empty input) — only `Error` outcomes count here.
    pub fn is_clean(&self) -> bool {
        self.records_errored == 0
    }
}

/// In-memory single-threaded GraphRunner.
///
/// Records flow along **per-edge queues** keyed by `(upstream_name,
/// downstream_name)`. When a node produces output, the runner copies the
/// output into every outgoing edge's queue (broadcast). When a node runs,
/// it drains all of its incoming edges' queues, concatenating in upstream
/// declaration order.
///
/// Determinism: nodes execute in topological order, ties broken by
/// manifest declaration order; records within a node are processed FIFO.
///
/// `B` is the record body type chosen by the application — see
/// `Record<B>` in `record.rs`.
pub struct GraphRunner<B> {
    manifest: PipelineManifest,
    nodes: HashMap<String, Node<B>>,
    run_id: RunId,
}

impl<B: Clone> GraphRunner<B> {
    pub fn new(manifest: PipelineManifest, run_id: RunId) -> Self {
        Self { manifest, nodes: HashMap::new(), run_id }
    }

    pub fn register_source(&mut self, name: impl Into<String>, src: impl Source<B> + 'static) {
        self.nodes.insert(name.into(), Node::Source(Box::new(src)));
    }

    pub fn register_transform(&mut self, name: impl Into<String>, tx: impl Transform<B> + 'static) {
        self.nodes.insert(name.into(), Node::Transform(Box::new(tx)));
    }

    pub fn register_effectful_transform(
        &mut self,
        name: impl Into<String>,
        tx: impl EffectfulTransform<B> + 'static,
    ) {
        self.nodes.insert(name.into(), Node::EffectfulTransform(Box::new(tx)));
    }

    pub fn register_sink(&mut self, name: impl Into<String>, snk: impl Sink<B> + 'static) {
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

        // 3. Build adjacency. `downstream[me]` is the ordered list of nodes me feeds.
        //    `upstream[me]` is the ordered list of nodes feeding me.
        let mut downstream: HashMap<String, Vec<String>> = self
            .manifest
            .nodes()
            .iter()
            .map(|n| (n.clone(), Vec::new()))
            .collect();
        let mut upstream: HashMap<String, Vec<String>> = self
            .manifest
            .nodes()
            .iter()
            .map(|n| (n.clone(), Vec::new()))
            .collect();
        for [from, to] in self.manifest.edges() {
            downstream.entry(from.clone()).or_default().push(to.clone());
            upstream.entry(to.clone()).or_default().push(from.clone());
        }

        // 4. Sanity checks: at least one source, at least one sink.
        let mut source_count = 0usize;
        let mut sink_count = 0usize;
        for n in &topo {
            match &self.nodes[n] {
                Node::Source(_) => source_count += 1,
                Node::Sink(_) => sink_count += 1,
                Node::Transform(_) | Node::EffectfulTransform(_) => {}
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
        // Per-edge queues: keyed by (from, to). A node's output is
        // *broadcast* (cloned) into each outgoing edge queue.
        let mut edge_queue: HashMap<(String, String), Vec<Record<B>>> = HashMap::new();
        let mut records_seen: u64 = 0;
        let mut records_forwarded: u64 = 0;
        let mut records_dropped: u64 = 0;
        let mut records_errored: u64 = 0;
        let mut first_error: Option<crate::filter::FilterError> = None;

        for name in &topo {
            // Re-take node ownership so we can mutate it while consulting
            // sibling state (the edge queues live alongside).
            let node = self.nodes.remove(name).expect("checked above");
            let outs = downstream.get(name).cloned().unwrap_or_default();
            match node {
                Node::Source(mut src) => {
                    let mut produced: Vec<Record<B>> = Vec::new();
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
                            SourceOutput::Exhausted => {
                                log.record(
                                    run_id.clone(),
                                    EventKind::SourceExhausted { step_id: src.step_id().clone() },
                                );
                                break;
                            }
                            SourceOutput::Error(e) => {
                                records_errored += 1;
                                if first_error.is_none() {
                                    first_error = Some(e.clone());
                                }
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
                    broadcast(name, &outs, produced, &mut edge_queue);
                    self.nodes.insert(name.clone(), Node::Source(src));
                }
                Node::Transform(mut tx) => {
                    let inputs = gather_inputs(name, &upstream, &mut edge_queue);
                    let stage = run_transform_stage(
                        name,
                        TxRef::Plain(&mut *tx),
                        inputs,
                        &outs,
                        &run_id,
                        &mut log,
                        &mut edge_queue,
                    );
                    records_seen += stage.seen;
                    records_dropped += stage.dropped;
                    records_errored += stage.errored;
                    if first_error.is_none() {
                        first_error = stage.first_error;
                    }
                    self.nodes.insert(name.clone(), Node::Transform(tx));
                }
                Node::EffectfulTransform(mut tx) => {
                    let inputs = gather_inputs(name, &upstream, &mut edge_queue);
                    let stage = run_transform_stage(
                        name,
                        TxRef::Effectful(&mut *tx),
                        inputs,
                        &outs,
                        &run_id,
                        &mut log,
                        &mut edge_queue,
                    );
                    records_seen += stage.seen;
                    records_dropped += stage.dropped;
                    records_errored += stage.errored;
                    if first_error.is_none() {
                        first_error = stage.first_error;
                    }
                    self.nodes.insert(name.clone(), Node::EffectfulTransform(tx));
                }
                Node::Sink(mut snk) => {
                    let inputs = gather_inputs(name, &upstream, &mut edge_queue);
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
            records_errored,
            first_error,
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

/// Broadcast a node's output into every outgoing edge's queue.
/// If the node has zero downstream edges (terminal sink), the records are
/// effectively dropped — that case is only legal for `Sink` nodes, and the
/// runner relies on the `Sink` having already consumed them.
fn broadcast<B: Clone>(
    me: &str,
    downstream: &[String],
    output: Vec<Record<B>>,
    edge_queue: &mut HashMap<(String, String), Vec<Record<B>>>,
) {
    if downstream.is_empty() || output.is_empty() {
        return;
    }
    // The last downstream takes the original Vec; earlier ones get clones.
    // This keeps one allocation in the linear case (1 downstream → 0 clones).
    let mut iter = downstream.iter();
    let last = iter.next_back().expect("non-empty");
    for d in iter {
        let q = edge_queue.entry((me.to_string(), d.clone())).or_default();
        q.extend(output.iter().cloned());
    }
    let q = edge_queue.entry((me.to_string(), last.clone())).or_default();
    q.extend(output);
}

/// Drain all incoming edge queues for `me`, in upstream-declaration order.
fn gather_inputs<B>(
    me: &str,
    upstream: &HashMap<String, Vec<String>>,
    edge_queue: &mut HashMap<(String, String), Vec<Record<B>>>,
) -> Vec<Record<B>> {
    let mut out = Vec::new();
    if let Some(ups) = upstream.get(me) {
        for u in ups {
            if let Some(q) = edge_queue.get_mut(&(u.clone(), me.to_string())) {
                out.append(q);
            }
        }
    }
    out
}

/// Internal-only wrapper so the runner can dispatch through Transform or
/// EffectfulTransform with identical code. The trait split is preserved at
/// registration time (and via CI grep); at execution time they're the same.
enum TxRef<'a, B> {
    Plain(&'a mut dyn Transform<B>),
    Effectful(&'a mut dyn EffectfulTransform<B>),
}

impl<'a, B> TxRef<'a, B> {
    fn step_id(&self) -> &crate::record::StepId {
        match self {
            TxRef::Plain(t) => t.step_id(),
            TxRef::Effectful(t) => t.step_id(),
        }
    }
    fn process(&mut self, record: Record<B>) -> FilterDecision<B> {
        match self {
            TxRef::Plain(t) => t.process(record),
            TxRef::Effectful(t) => t.process(record),
        }
    }
}

/// Per-stage tally returned by [`run_transform_stage`].
struct StageCounts {
    seen: u64,
    dropped: u64,
    errored: u64,
    first_error: Option<crate::filter::FilterError>,
}

/// Drive a sequence of records through a (possibly effectful) transform.
/// Broadcasts outputs into downstream edge queues and logs every per-record
/// decision. Returns the seen/dropped/errored tally; an `Error` decision is
/// counted distinctly from a `Drop` so callers can fail the run.
fn run_transform_stage<B: Clone>(
    me: &str,
    mut tx: TxRef<'_, B>,
    inputs: Vec<Record<B>>,
    outs: &[String],
    run_id: &RunId,
    log: &mut EventLog,
    edge_queue: &mut HashMap<(String, String), Vec<Record<B>>>,
) -> StageCounts {
    let mut outputs: Vec<Record<B>> = Vec::new();
    let mut completed = false;
    let mut seen: u64 = 0;
    let mut dropped: u64 = 0;
    let mut errored: u64 = 0;
    let mut first_error: Option<crate::filter::FilterError> = None;

    for rec in inputs {
        if completed {
            log.record(
                run_id.clone(),
                EventKind::FilterDropped {
                    record_id: rec.id.clone(),
                    step_id: tx.step_id().clone(),
                    reason: crate::filter::DropReason::new(
                        "runner.transform.post_complete",
                        "record arrived after transform declared complete",
                    ),
                },
            );
            dropped += 1;
            continue;
        }
        seen += 1;
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
            FilterDecision::ForwardWithEvents { records: rs, events } => {
                // Emit observation events FIRST so RecordForwarded
                // arrives strictly after any side-band annotations on
                // the same record. Tests rely on this ordering.
                for ev in events {
                    log.record(run_id.clone(), ev);
                }
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
                dropped += 1;
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
                errored += 1;
                if first_error.is_none() {
                    first_error = Some(err.clone());
                }
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
    broadcast(me, outs, outputs, edge_queue);
    StageCounts { seen, dropped, errored, first_error }
}
