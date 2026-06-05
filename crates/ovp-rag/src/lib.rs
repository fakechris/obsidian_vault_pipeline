//! OVP Next L6 — the RAG read path (`ovp-rag`).
//!
//! A **read-only** retrieval surface over `ovp-query::KnowledgeView`. It loads a
//! corpus from the L5 read model (canonical concepts + backlinks + evergreen
//! note bodies), scores a query against it deterministically, ranks the results
//! with explanations, and builds a *bounded* context object. It never
//! assembles, runs, applies, or writes anything — retrieval is more than the
//! knowledge index, but it is still a pure read. See
//! `docs/stage-rag-automation.md`.
//!
//! Pipeline: [`RagCorpus`] → [`Retriever`] → [`Ranker`] → [`ContextBuilder`],
//! gated offline by [`Eval`].

mod context;
mod corpus;
mod eval;
mod ranker;
mod retriever;

pub use context::{ContextBuilder, RagContext, SelectedConcept};
pub use corpus::{ConceptDoc, RagCorpus};
pub use eval::{Eval, EvalCase, EvalOutcome, EvalReport};
pub use ranker::Ranker;
pub use retriever::{MatchField, MatchReason, RetrievalWeights, Retriever, ScoredConcept};

use ovp_query::QueryError;

/// Why building a [`RagCorpus`] failed. Fail-loud: a corrupt read model or an
/// *unreadable* (not merely absent) note body is an error, never a silently
/// empty corpus.
#[derive(Debug)]
pub enum RagError {
    /// The L5 read model could not be loaded (corrupt/unreadable canonical store
    /// or knowledge index). Wraps the underlying [`QueryError`].
    Load(QueryError),
    /// An evergreen note exists at its path but could not be read (a non-
    /// `NotFound` I/O error — permission denied, transient failure, …). Distinct
    /// from "absent" (a missing note is fine → no body for that concept).
    Body(String),
}

impl std::fmt::Display for RagError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RagError::Load(e) => write!(f, "loading read model: {e}"),
            RagError::Body(m) => write!(f, "reading note body: {m}"),
        }
    }
}

impl std::error::Error for RagError {}
