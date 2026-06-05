use serde::{Deserialize, Serialize};

/// Stable identifier for a Record within a run. Two records with the
/// same RecordId in the same run refer to the same logical document.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct RecordId(pub String);

impl RecordId {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct RunId(pub String);

impl RunId {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct StepId(pub String);

impl StepId {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Provenance {
    pub step_id: StepId,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecordMeta {
    pub run_id: RunId,
    pub seq: u64,
}

/// A typed envelope around a body. `B` is the domain-specific body type
/// chosen by the consumer crate — ovp-core itself is body-agnostic.
///
/// Domain crates instantiate `Record<DomainBody>`; tests/fakes use
/// `Record<FakeBody>`. The runner is generic and never inspects `B`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record<B> {
    pub id: RecordId,
    pub body: B,
    pub meta: RecordMeta,
    pub provenance: Vec<Provenance>,
}

impl<B> Record<B> {
    pub fn new(id: RecordId, body: B, meta: RecordMeta) -> Self {
        Self { id, body, meta, provenance: Vec::new() }
    }

    pub fn with_step(mut self, step_id: StepId, note: impl Into<String>) -> Self {
        self.provenance.push(Provenance { step_id, note: note.into() });
        self
    }
}
