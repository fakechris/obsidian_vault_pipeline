use serde::{Deserialize, Serialize};

use crate::filter::{CompleteReason, DropReason, FilterError};
use crate::record::{RecordId, RunId, StepId};

/// A monotonic timestamp emitted by the runner. v0.1 uses a simple u64
/// counter for determinism in tests. A real clock can replace this without
/// changing the event model.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct EventTs(pub u64);

impl EventTs {
    pub fn new(n: u64) -> Self { Self(n) }
    pub fn next(self) -> Self { Self(self.0 + 1) }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum EventKind {
    RunStarted,
    RunCompleted { records_seen: u64, ops_emitted: u64 },
    SourceProduced { step_id: StepId, count: u64 },
    SourceExhausted { step_id: StepId },
    RecordSeen { record_id: RecordId, step_id: StepId },
    RecordForwarded { record_id: RecordId, step_id: StepId, fanout: u64 },
    FilterDropped { record_id: RecordId, step_id: StepId, reason: DropReason },
    FilterCompleted { step_id: StepId, reason: CompleteReason },
    FilterErrored { record_id: Option<RecordId>, step_id: StepId, error: FilterError },
    SinkEmitted { step_id: StepId, ops: u64 },
    PlanFinalized { ops: u64 },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Event {
    pub run_id: RunId,
    pub ts: EventTs,
    pub kind: EventKind,
}

impl Event {
    pub fn new(run_id: RunId, ts: EventTs, kind: EventKind) -> Self {
        Self { run_id, ts, kind }
    }
}

/// Append-only event log. The runner owns one of these for the duration
/// of a run; the application can persist it however it likes.
#[derive(Debug, Default)]
pub struct EventLog {
    events: Vec<Event>,
    cursor: EventTs,
}

impl EventLog {
    pub fn new() -> Self {
        Self { events: Vec::new(), cursor: EventTs(0) }
    }

    /// Append an event with the next monotonic timestamp.
    pub fn record(&mut self, run_id: RunId, kind: EventKind) -> EventTs {
        let ts = self.cursor;
        self.cursor = self.cursor.next();
        self.events.push(Event::new(run_id, ts, kind));
        ts
    }

    pub fn events(&self) -> &[Event] { &self.events }
    pub fn len(&self) -> usize { self.events.len() }
    pub fn is_empty(&self) -> bool { self.events.is_empty() }

    pub fn into_events(self) -> Vec<Event> { self.events }
}
