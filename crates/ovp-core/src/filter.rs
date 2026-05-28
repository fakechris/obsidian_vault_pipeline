use serde::{Deserialize, Serialize};

use crate::record::{Record, StepId};

/// Validated drop/error code: a dotted namespace string like
/// `transform.article.low_quality` or `source.inbox.unreadable`.
///
/// The newtype prevents free-form strings creeping in. Plugins and domain
/// crates extend the code space by namespacing under their own prefix —
/// we deliberately don't seal this into an enum.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ReasonCode(String);

impl ReasonCode {
    /// Construct a reason code. Requires at least one `.` separator and
    /// non-empty segments — e.g. `transform.article.low_quality`. Panics
    /// on invalid input because reason codes are always literal in source.
    pub fn new(s: &str) -> Self {
        Self::try_new(s).unwrap_or_else(|e| panic!("invalid ReasonCode `{s}`: {e}"))
    }

    pub fn try_new(s: &str) -> Result<Self, &'static str> {
        if s.is_empty() {
            return Err("empty code");
        }
        if !s.contains('.') {
            return Err("missing namespace separator `.`");
        }
        if s.split('.').any(|seg| seg.is_empty()) {
            return Err("empty segment between dots");
        }
        if !s.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '.' || c == '_') {
            return Err("only [a-z0-9._] allowed");
        }
        Ok(Self(s.to_string()))
    }

    pub fn as_str(&self) -> &str { &self.0 }
}

/// Why a transform refused to forward a Record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DropReason {
    pub code: ReasonCode,
    pub detail: String,
}

impl DropReason {
    pub fn new(code: &str, detail: impl Into<String>) -> Self {
        Self { code: ReasonCode::new(code), detail: detail.into() }
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FilterError {
    pub code: ReasonCode,
    pub detail: String,
}

impl FilterError {
    pub fn new(code: &str, detail: impl Into<String>) -> Self {
        Self { code: ReasonCode::new(code), detail: detail.into() }
    }
}

/// What a transform decided to do with a Record. All five outcomes are
/// first-class; the runner is required to log each one to the event log.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "decision", rename_all = "snake_case")]
pub enum FilterDecision<B> {
    Forward(Vec<Record<B>>),
    Drop(DropReason),
    FanOut(Vec<Record<B>>),
    Complete(CompleteReason),
    Error(FilterError),
}

/// What a source produced this tick.
///
/// v0.1 keeps this synchronous: either a source has records, or it's
/// exhausted, or it errored. There is no "idle this tick" state —
/// streaming/polling sources will reach this layer via an async adapter
/// in a later crate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SourceOutput<B> {
    Records(Vec<Record<B>>),
    Exhausted,
    Error(FilterError),
}

/// What a sink emitted for this record. WriteOps and extra events are
/// not generic — they live downstream of the typed pipeline.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SinkOutput {
    pub plan_ops: Vec<crate::plan::WriteOp>,
    pub extra_events: Vec<crate::event::Event>,
}

impl SinkOutput {
    pub fn empty() -> Self { Self { plan_ops: Vec::new(), extra_events: Vec::new() } }
}

/// Produces Records from the outside world.
pub trait Source<B> {
    fn step_id(&self) -> &StepId;
    fn produce(&mut self) -> SourceOutput<B>;
}

/// Pure transform: Record in, FilterDecision out. No I/O, no Store access,
/// no spawned processes, no held effect clients. Same input → same output.
///
/// If the node needs to call a network service, a database, the LLM, or
/// any other effectful client, use [`EffectfulTransform`] instead. The
/// runner treats both identically; the trait split is a type-system
/// signal of intent that CI greps for.
pub trait Transform<B> {
    fn step_id(&self) -> &StepId;
    fn process(&mut self, record: Record<B>) -> FilterDecision<B>;
}

/// Sync facade over an injected effect client. Holds something like a
/// `Box<dyn ModelClient>`, `Box<dyn Store>`, or `Box<dyn Fetcher>` and
/// calls it from `process()`. The pipeline sees a sync method; whether
/// the underlying client is blocking or `Handle::block_on(async)` is
/// the client's business.
///
/// Replayable in tests when the client is a fixture or cached impl.
/// When the executor becomes async-aware (post-v1), EffectfulTransforms
/// are candidates for "suspend at this node, drive the effect outside
/// the pipeline, resume with the response" — not Transforms.
///
/// Trait shape is intentionally identical to [`Transform`] — only the
/// trait identity differs. The split exists for architectural intent +
/// CI enforcement, not for runtime behavior.
pub trait EffectfulTransform<B> {
    fn step_id(&self) -> &StepId;
    fn process(&mut self, record: Record<B>) -> FilterDecision<B>;
}

/// Consumes records, emits WriteOps. Real side effects belong to PlanApplier,
/// not Sink — Sink only describes what *should* happen.
pub trait Sink<B> {
    fn step_id(&self) -> &StepId;
    fn consume(&mut self, record: Record<B>) -> SinkOutput;
    fn finish(&mut self) -> SinkOutput {
        SinkOutput::empty()
    }
}
