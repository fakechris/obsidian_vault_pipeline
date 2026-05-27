use serde::{Deserialize, Serialize};

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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    pub id: RecordId,
    pub body: RecordBody,
    pub meta: RecordMeta,
    pub provenance: Vec<Provenance>,
}

impl Record {
    pub fn new(id: RecordId, body: RecordBody, meta: RecordMeta) -> Self {
        Self { id, body, meta, provenance: Vec::new() }
    }

    pub fn with_step(mut self, step_id: StepId, note: impl Into<String>) -> Self {
        self.provenance.push(Provenance { step_id, note: note.into() });
        self
    }
}

/// Sealed body of a Record.
///
/// v0.1 only contains a `Fake` variant for runner validation.
/// Real domain variants (SourceDoc, InterpretedDoc, CandidateNote,
/// CanonicalNote, Query) come in R3+ and live in a separate `ovp-domain` crate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RecordBody {
    Fake(FakeBody),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FakeBody {
    pub label: String,
    pub payload: i64,
}
