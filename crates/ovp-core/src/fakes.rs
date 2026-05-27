//! In-tree fake filters used by tests and `ovp-next run --fake`.
//!
//! Available only when the `fakes` feature is enabled. The set is
//! deliberately small — these exist to exercise the runner, not to
//! be a public extension point.

use crate::filter::{
    DropReason, FilterDecision, Sink, SinkOutput, Source, SourceOutput, Transform,
};
use crate::plan::{ContentHash, OpId, VaultCreateOp, VaultPath, WriteOp};
use crate::record::{
    FakeBody, Record, RecordBody, RecordId, RecordMeta, RunId, StepId,
};

pub struct FakeSource {
    step: StepId,
    run_id: RunId,
    emitted: bool,
}

impl FakeSource {
    pub fn new(step: impl Into<String>, run_id: RunId) -> Self {
        Self { step: StepId::new(step.into()), run_id, emitted: false }
    }
}

impl Source for FakeSource {
    fn step_id(&self) -> &StepId { &self.step }
    fn produce(&mut self) -> SourceOutput {
        if self.emitted {
            return SourceOutput::Exhausted;
        }
        self.emitted = true;
        let mk = |label: &str, payload: i64, seq: u64, run_id: RunId, step: StepId| {
            Record::new(
                RecordId::new(format!("r-{label}")),
                RecordBody::Fake(FakeBody { label: label.into(), payload }),
                RecordMeta { run_id, seq },
            )
            .with_step(step, "produced")
        };
        SourceOutput::Records(vec![
            mk("keep-a", 10, 0, self.run_id.clone(), self.step.clone()),
            mk("drop-me", 0, 1, self.run_id.clone(), self.step.clone()),
            mk("keep-b", 20, 2, self.run_id.clone(), self.step.clone()),
        ])
    }
}

pub struct DropZeroes {
    step: StepId,
}

impl DropZeroes {
    pub fn new(step: impl Into<String>) -> Self {
        Self { step: StepId::new(step.into()) }
    }
}

impl Transform for DropZeroes {
    fn step_id(&self) -> &StepId { &self.step }
    fn process(&mut self, record: Record) -> FilterDecision {
        match &record.body {
            RecordBody::Fake(b) if b.payload == 0 => FilterDecision::Drop(DropReason::new(
                "fake_zero",
                format!("payload was 0 for `{}`", b.label),
            )),
            RecordBody::Fake(_) => FilterDecision::Forward(vec![
                record.with_step(self.step.clone(), "forwarded"),
            ]),
        }
    }
}

pub struct VaultPlanSink {
    step: StepId,
}

impl VaultPlanSink {
    pub fn new(step: impl Into<String>) -> Self {
        Self { step: StepId::new(step.into()) }
    }
}

impl Sink for VaultPlanSink {
    fn step_id(&self) -> &StepId { &self.step }
    fn consume(&mut self, record: Record) -> SinkOutput {
        let (body, path) = match &record.body {
            RecordBody::Fake(b) => (
                format!("# {}\n\npayload: {}\n", b.label, b.payload),
                VaultPath::new(format!("50-Inbox/{}.md", b.label)),
            ),
        };
        let op = WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new(format!("op-{}", record.id.as_str())),
            path,
            after_hash: ContentHash::new(format!("h-{}", record.id.as_str())),
            body,
            reason: "fake sink".into(),
            originating_record: record.id.clone(),
        });
        SinkOutput { plan_ops: vec![op], extra_events: vec![] }
    }
}
