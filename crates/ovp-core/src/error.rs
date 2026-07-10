use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CoreError {
    Manifest(ManifestError),
    Graph(GraphError),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ManifestError {
    Parse(String),
    UnknownNode(String),
    DuplicateNode(String),
    EdgeMissingEndpoint { edge: (String, String), missing: String },
    EmptyPipeline,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GraphError {
    NodeNotRegistered(String),
    MultipleSources,
    NoSource,
    NoSink,
    CycleDetected,
}

impl fmt::Display for CoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CoreError::Manifest(e) => write!(f, "manifest error: {e}"),
            CoreError::Graph(e) => write!(f, "graph error: {e}"),
        }
    }
}

impl fmt::Display for ManifestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ManifestError::Parse(s) => write!(f, "parse: {s}"),
            ManifestError::UnknownNode(n) => write!(f, "unknown node: {n}"),
            ManifestError::DuplicateNode(n) => write!(f, "duplicate node: {n}"),
            ManifestError::EdgeMissingEndpoint { edge, missing } => {
                write!(f, "edge {:?} references missing node `{}`", edge, missing)
            }
            ManifestError::EmptyPipeline => write!(f, "pipeline has no nodes"),
        }
    }
}

impl fmt::Display for GraphError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GraphError::NodeNotRegistered(n) => write!(f, "node `{n}` declared in manifest but not registered"),
            GraphError::MultipleSources => write!(f, "more than one source node"),
            GraphError::NoSource => write!(f, "no source node"),
            GraphError::NoSink => write!(f, "no sink node"),
            GraphError::CycleDetected => write!(f, "pipeline contains a cycle"),
        }
    }
}

impl std::error::Error for CoreError {}
impl std::error::Error for ManifestError {}
impl std::error::Error for GraphError {}

impl From<ManifestError> for CoreError {
    fn from(e: ManifestError) -> Self { CoreError::Manifest(e) }
}
impl From<GraphError> for CoreError {
    fn from(e: GraphError) -> Self { CoreError::Graph(e) }
}
