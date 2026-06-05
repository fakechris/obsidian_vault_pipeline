//! In-tree fake filters used by tests and `ovp-next run --fake`.
//!
//! Available only when the `fakes` feature is enabled. `FakeBody` is the
//! concrete body type the v0.1 demo pipeline uses — domain crates will
//! ship their own body enum.

use serde::{Deserialize, Serialize};

use crate::filter::{
    DropReason, FilterDecision, Sink, SinkOutput, Source, SourceOutput, Transform,
};
use crate::plan::{ContentHash, OpId, VaultCreateOp, VaultPath, WriteOp};
use crate::record::{Record, RecordId, RecordMeta, RunId, StepId};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FakeBody {
    pub label: String,
    pub payload: i64,
}

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

impl Source<FakeBody> for FakeSource {
    fn step_id(&self) -> &StepId { &self.step }
    fn produce(&mut self) -> SourceOutput<FakeBody> {
        if self.emitted {
            return SourceOutput::Exhausted;
        }
        self.emitted = true;
        let mk = |label: &str, payload: i64, seq: u64, run_id: RunId, step: StepId| {
            Record::new(
                RecordId::new(format!("r-{label}")),
                FakeBody { label: label.into(), payload },
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

impl Transform<FakeBody> for DropZeroes {
    fn step_id(&self) -> &StepId { &self.step }
    fn process(&mut self, record: Record<FakeBody>) -> FilterDecision<FakeBody> {
        if record.body.payload == 0 {
            FilterDecision::Drop(DropReason::new(
                "transform.fake.zero_payload",
                format!("payload was 0 for `{}`", record.body.label),
            ))
        } else {
            FilterDecision::Forward(vec![record.with_step(self.step.clone(), "forwarded")])
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

impl Sink<FakeBody> for VaultPlanSink {
    fn step_id(&self) -> &StepId { &self.step }
    fn consume(&mut self, record: Record<FakeBody>) -> SinkOutput {
        let body = format!("# {}\n\npayload: {}\n", record.body.label, record.body.payload);
        let path = VaultPath::new(format!("50-Inbox/{}.md", record.body.label));
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
