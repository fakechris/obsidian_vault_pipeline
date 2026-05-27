//! OVP Next core: Record, Filter, GraphRunner, WritePlan, EventLog.
//!
//! See `docs/architecture.md` and `docs/invariants.md` at the repo root.

pub mod error;
pub mod event;
pub mod filter;
pub mod graph;
pub mod manifest;
pub mod plan;
pub mod record;

#[cfg(feature = "fakes")]
pub mod fakes;

pub use error::{CoreError, GraphError, ManifestError};
pub use event::{Event, EventKind, EventLog, EventTs};
pub use filter::{
    CompleteReason, DropReason, FilterDecision, FilterError, Sink, SinkOutput, Source,
    SourceOutput, Transform,
};
pub use plan::{
    CanonicalKey, CanonicalUpsertOp, ContentHash, EventAppendOp, OpId, VaultCreateOp, VaultPath,
    VaultUpdateOp, WriteOp, WritePlan,
};
pub use graph::{GraphRunner, RunReport};
pub use manifest::{PipelineBody, PipelineManifest};
pub use record::{FakeBody, Provenance, Record, RecordBody, RecordId, RecordMeta, RunId, StepId};
