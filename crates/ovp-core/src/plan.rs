use serde::{Deserialize, Serialize};

use crate::record::{RecordId, RunId, StepId};

/// A hash of a target's "before" or "after" content. v0.1 uses a hex digest
/// string — production will likely be a blake3 or sha256 newtype, but the
/// shape stays the same.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ContentHash(pub String);

impl ContentHash {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

/// A vault-relative path (e.g. `10-Knowledge/Evergreen/Foo.md`).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct VaultPath(pub String);

impl VaultPath {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct CanonicalKey(pub String);

impl CanonicalKey {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpId(pub String);

impl OpId {
    pub fn new(s: impl Into<String>) -> Self { Self(s.into()) }
    pub fn as_str(&self) -> &str { &self.0 }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VaultCreateOp {
    pub op_id: OpId,
    pub path: VaultPath,
    pub after_hash: ContentHash,
    /// v0.1 keeps the body inline as text so the test can assert on it.
    /// Production may move this to a content-addressed store.
    pub body: String,
    pub reason: String,
    pub originating_record: RecordId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VaultUpdateOp {
    pub op_id: OpId,
    pub path: VaultPath,
    pub before_hash: ContentHash,
    pub after_hash: ContentHash,
    pub body: String,
    pub reason: String,
    pub originating_record: RecordId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonicalUpsertOp {
    pub op_id: OpId,
    pub key: CanonicalKey,
    pub before_hash: Option<ContentHash>,
    pub after_hash: ContentHash,
    /// Serialized canonical payload. v0.1 stores it as a string so the
    /// data model stays loose; real domain types fill this in later.
    pub payload: String,
    pub reason: String,
    pub originating_record: RecordId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventAppendOp {
    pub op_id: OpId,
    pub event_kind: String,
    pub payload: String,
    pub originating_record: RecordId,
    pub emitted_by: StepId,
}

/// All side effects must first become a WriteOp.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum WriteOp {
    VaultCreate(VaultCreateOp),
    VaultUpdate(VaultUpdateOp),
    CanonicalUpsert(CanonicalUpsertOp),
    EventAppend(EventAppendOp),
}

impl WriteOp {
    pub fn op_id(&self) -> &OpId {
        match self {
            WriteOp::VaultCreate(o) => &o.op_id,
            WriteOp::VaultUpdate(o) => &o.op_id,
            WriteOp::CanonicalUpsert(o) => &o.op_id,
            WriteOp::EventAppend(o) => &o.op_id,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WritePlan {
    pub run_id: RunId,
    pub ops: Vec<WriteOp>,
}

impl WritePlan {
    pub fn new(run_id: RunId) -> Self {
        Self { run_id, ops: Vec::new() }
    }

    pub fn push(&mut self, op: WriteOp) {
        self.ops.push(op);
    }

    pub fn extend(&mut self, ops: impl IntoIterator<Item = WriteOp>) {
        self.ops.extend(ops);
    }

    pub fn len(&self) -> usize { self.ops.len() }
    pub fn is_empty(&self) -> bool { self.ops.is_empty() }
}
