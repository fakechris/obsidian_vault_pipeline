use std::fmt;

use ovp_core::CoreError;

/// Why assembling a `GraphRunner` from a spec + wiring failed. Every variant
/// names the offending node where one exists, so a bad manifest fails loudly
/// with a pointer to the fix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AssemblyError {
    /// The enriched manifest TOML did not parse.
    Parse(String),
    /// Topology (the `[pipeline]` section) failed `ovp-core` validation
    /// (duplicate node, dangling edge endpoint, empty pipeline, cycle).
    Manifest(CoreError),
    /// `[pipeline].nodes` and the `[assembly.*]` entries disagree.
    SpecMismatch { detail: String },
    /// A node's `kind` is not in the `NodeRegistry`.
    UnknownKind { node_id: String, kind: String },
    /// A required config field for this node's kind is absent (e.g.
    /// `effect.llm_invoker` with no `config.client`).
    MissingConfig { node_id: String, field: &'static str },
    /// The node's config names a wiring entry (a client/registry/input) that
    /// `AppWiring` does not provide.
    MissingWiring { node_id: String, name: String },
    /// The node's category contradicts the topology (a source with an inbound
    /// edge, or a sink with an outbound edge).
    CategoryMismatch { node_id: String, kind: String, detail: String },
}

impl fmt::Display for AssemblyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AssemblyError::Parse(s) => write!(f, "manifest parse error: {s}"),
            AssemblyError::Manifest(e) => write!(f, "topology error: {e}"),
            AssemblyError::SpecMismatch { detail } => write!(f, "spec mismatch: {detail}"),
            AssemblyError::UnknownKind { node_id, kind } => {
                write!(f, "node `{node_id}` has unknown kind `{kind}`")
            }
            AssemblyError::MissingConfig { node_id, field } => {
                write!(f, "node `{node_id}` is missing required config field `{field}`")
            }
            AssemblyError::MissingWiring { node_id, name } => {
                write!(f, "node `{node_id}` needs wiring `{name}`, which AppWiring does not provide")
            }
            AssemblyError::CategoryMismatch { node_id, kind, detail } => {
                write!(f, "node `{node_id}` (kind `{kind}`): {detail}")
            }
        }
    }
}

impl std::error::Error for AssemblyError {}

impl From<CoreError> for AssemblyError {
    fn from(e: CoreError) -> Self {
        AssemblyError::Manifest(e)
    }
}
