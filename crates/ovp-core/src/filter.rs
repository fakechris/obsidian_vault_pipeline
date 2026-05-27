use serde::{Deserialize, Serialize};

use crate::record::{Record, RecordId, StepId};

/// Why a transform refused to forward a Record.
///
/// `code` is stable, short, machine-readable (e.g. `"quality_below_threshold"`).
/// `detail` is human prose for the event log.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DropReason {
    pub code: String,
    pub detail: String,
}

impl DropReason {
    pub fn new(code: impl Into<String>, detail: impl Into<String>) -> Self {
        Self { code: code.into(), detail: detail.into() }
    }
}

/// Why a transform declared itself complete (no more output, ever).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompleteReason {
    pub note: String,
}

impl CompleteReason {
    pub fn new(note: impl Into<String>) -> Self { Self { note: note.into() } }
}

/// Filter-side errors. These are surfaced as `FilterDecision::Error` and
/// recorded in the event log; they do not panic the runner unless escalated
/// by the application.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FilterError {
    pub code: String,
    pub detail: String,
}

impl FilterError {
    pub fn new(code: impl Into<String>, detail: impl Into<String>) -> Self {
        Self { code: code.into(), detail: detail.into() }
    }
}

/// What a transform decided to do with a Record.
///
/// Drops, completions, and errors are first-class — never "yield nothing and
/// hope the runner notices."
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "decision", rename_all = "snake_case")]
pub enum FilterDecision {
    /// Forward zero or more records downstream. The empty Vec is legal but
    /// strongly discouraged — prefer `Drop` with a reason.
    Forward(Vec<Record>),
    /// Refuse to forward this record. Must include a reason.
    Drop(DropReason),
    /// One-record fan-out into multiple downstream records.
    /// Semantically the same as `Forward` with len > 1, but explicit at
    /// the call site for readability.
    FanOut(Vec<Record>),
    /// Transform is done and will not produce more output for the rest
    /// of the run.
    Complete(CompleteReason),
    /// Transform errored on this record.
    Error(FilterError),
}

/// What a source produced this tick.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SourceOutput {
    /// One or more records this tick.
    Records(Vec<Record>),
    /// No record this tick, but the source is still alive.
    Idle,
    /// Source is done forever.
    Exhausted,
    /// Source errored.
    Error(FilterError),
}

/// What a sink emitted for this record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SinkOutput {
    pub plan_ops: Vec<crate::plan::WriteOp>,
    pub extra_events: Vec<crate::event::Event>,
}

impl SinkOutput {
    pub fn empty() -> Self { Self { plan_ops: Vec::new(), extra_events: Vec::new() } }
}

/// A node that produces Records from the outside world.
pub trait Source {
    fn step_id(&self) -> &StepId;
    fn produce(&mut self) -> SourceOutput;
}

/// A pure node: Record in, FilterDecision out. No I/O, no Store access.
pub trait Transform {
    fn step_id(&self) -> &StepId;
    fn process(&mut self, record: Record) -> FilterDecision;
}

/// A node that consumes records and emits WriteOps (no actual I/O).
pub trait Sink {
    fn step_id(&self) -> &StepId;
    fn consume(&mut self, record: Record) -> SinkOutput;
    /// Called once after all records have been processed. Allows the sink
    /// to flush any buffered ops (e.g. an aggregated index update).
    fn finish(&mut self) -> SinkOutput {
        SinkOutput::empty()
    }
    /// Bookkeeping: used by the runner to attribute dropped/errored records
    /// to a downstream sink that never saw them. Default: just the step_id.
    fn would_have_consumed(&self, _record_id: &RecordId) -> bool {
        true
    }
}
