//! Offline retrieval eval: recall@k against fixtures with known expected slugs.
//!
//! No network, no LLM. Given a corpus + cases, it runs the real
//! [`Retriever`]/[`Ranker`] and measures whether each case's expected concepts
//! appear in the top-k. This is the regression gate for the scoring model.

use serde::Serialize;

use crate::corpus::RagCorpus;
use crate::ranker::Ranker;
use crate::retriever::Retriever;

/// One eval case: a query and the slugs that *should* surface in the top-k.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvalCase {
    pub query: String,
    pub expected: Vec<String>,
}

impl EvalCase {
    pub fn new(query: impl Into<String>, expected: &[&str]) -> Self {
        Self { query: query.into(), expected: expected.iter().map(|s| s.to_string()).collect() }
    }
}

/// The result of one case: what was retrieved (top-k slugs) and the recall.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct EvalOutcome {
    pub query: String,
    pub expected: Vec<String>,
    pub retrieved: Vec<String>,
    pub hits: usize,
    pub recall: f64,
}

/// The aggregate eval result over all cases.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct EvalReport {
    pub outcomes: Vec<EvalOutcome>,
    pub mean_recall: f64,
}

impl EvalReport {
    /// True iff the mean recall meets `min_recall` (the gate for CI use).
    pub fn passed(&self, min_recall: f64) -> bool {
        self.mean_recall >= min_recall
    }
}

/// The offline eval harness.
pub struct Eval;

impl Eval {
    /// Run every case through `retriever` + `ranker`, take the top-`k` slugs, and
    /// score recall (`|expected ∩ retrieved| / |expected|`; an empty `expected`
    /// counts as recall 1.0). Deterministic; no I/O beyond the corpus already in
    /// memory.
    pub fn run(
        corpus: &RagCorpus,
        retriever: &Retriever,
        ranker: &Ranker,
        cases: &[EvalCase],
        k: usize,
    ) -> EvalReport {
        let mut outcomes = Vec::with_capacity(cases.len());
        for case in cases {
            let scored = retriever.score(corpus, &case.query);
            let ranked = ranker.rank(scored);
            let retrieved: Vec<String> =
                ranked.iter().take(k).map(|s| s.slug.clone()).collect();
            let hits = case.expected.iter().filter(|e| retrieved.contains(e)).count();
            let recall = if case.expected.is_empty() {
                1.0
            } else {
                hits as f64 / case.expected.len() as f64
            };
            outcomes.push(EvalOutcome {
                query: case.query.clone(),
                expected: case.expected.clone(),
                retrieved,
                hits,
                recall,
            });
        }
        let mean_recall = if outcomes.is_empty() {
            1.0
        } else {
            outcomes.iter().map(|o| o.recall).sum::<f64>() / outcomes.len() as f64
        };
        EvalReport { outcomes, mean_recall }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::{ConceptDoc, RagCorpus};

    fn doc(slug: &str, title: &str) -> ConceptDoc {
        ConceptDoc {
            slug: slug.into(),
            title: title.into(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "u".into(),
            backlinks: vec![],
            body: None,
        }
    }

    fn corpus() -> RagCorpus {
        RagCorpus::from_docs(vec![
            doc("ai-agent", "AI Agent"),
            doc("rag", "Retrieval Augmented Generation"),
            doc("transformer", "Transformer"),
        ])
    }

    #[test]
    fn perfect_recall_on_matching_cases() {
        let report = Eval::run(
            &corpus(),
            &Retriever::new(),
            &Ranker::new(),
            &[
                EvalCase::new("agent", &["ai-agent"]),
                EvalCase::new("retrieval augmented", &["rag"]),
            ],
            3,
        );
        assert!((report.mean_recall - 1.0).abs() < f64::EPSILON);
        assert!(report.passed(1.0));
        assert_eq!(report.outcomes[0].hits, 1);
    }

    #[test]
    fn partial_recall_when_target_missing() {
        let report = Eval::run(
            &corpus(),
            &Retriever::new(),
            &Ranker::new(),
            &[EvalCase::new("kubernetes", &["ai-agent"])],
            3,
        );
        assert_eq!(report.outcomes[0].hits, 0);
        assert!((report.mean_recall - 0.0).abs() < f64::EPSILON);
        assert!(!report.passed(0.5));
    }
}
