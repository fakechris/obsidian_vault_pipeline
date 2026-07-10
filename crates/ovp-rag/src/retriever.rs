//! Deterministic, explainable lexical scoring of a query against the corpus.
//!
//! Integer-only arithmetic over lowercased tokens — same inputs always yield the
//! same scores and the same per-field reason breakdown, with no float
//! nondeterminism. This is the v1 retrieval model; an embedding ranker would be
//! a future `RetrievalWeights`-shaped extension, not a replacement.

use serde::Serialize;

use crate::corpus::{ConceptDoc, RagCorpus};

/// Which field of a concept a query term matched. Drives the explanation, not
/// just the score.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum MatchField {
    Title,
    Slug,
    Body,
    Backlink,
}

/// Why one query term contributed to a concept's score. `hits` is how many times
/// the term matched in that field; `contribution` is the points it added.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MatchReason {
    pub field: MatchField,
    pub term: String,
    pub hits: u32,
    pub contribution: u32,
}

/// A concept with its total score and the breakdown that produced it. Empty
/// `reasons` ⇔ `score == 0` (no query term matched).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ScoredConcept {
    pub slug: String,
    pub score: u32,
    pub reasons: Vec<MatchReason>,
}

/// Per-field weights. A token match (the term equals a whole title/slug token)
/// outscores a bare substring match; body matches are weak and capped so a long
/// note can't dominate; a backlinked note is a mild signal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetrievalWeights {
    pub title_token: u32,
    pub title_substring: u32,
    pub slug_token: u32,
    pub slug_substring: u32,
    pub body_hit: u32,
    /// Max body occurrences of a single term that count (anti-keyword-stuffing).
    pub body_hit_cap: u32,
    pub backlink: u32,
}

impl Default for RetrievalWeights {
    fn default() -> Self {
        Self {
            title_token: 10,
            title_substring: 5,
            slug_token: 8,
            slug_substring: 4,
            body_hit: 1,
            body_hit_cap: 3,
            backlink: 2,
        }
    }
}

/// The deterministic retriever. Holds the weights; scoring is a pure function of
/// `(corpus, query, weights)`.
#[derive(Debug, Clone, Default)]
pub struct Retriever {
    weights: RetrievalWeights,
}

impl Retriever {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_weights(weights: RetrievalWeights) -> Self {
        Self { weights }
    }

    pub fn weights(&self) -> &RetrievalWeights {
        &self.weights
    }

    /// Score every concept in the corpus against `query`. Returns one
    /// [`ScoredConcept`] per doc in corpus order (slug-sorted); a doc with no
    /// matching term scores 0 with empty reasons. Filtering/ordering is the
    /// [`Ranker`](crate::Ranker)'s job.
    pub fn score(&self, corpus: &RagCorpus, query: &str) -> Vec<ScoredConcept> {
        let terms = query_terms(query);
        corpus.docs().iter().map(|d| self.score_doc(d, &terms)).collect()
    }

    fn score_doc(&self, doc: &ConceptDoc, terms: &[String]) -> ScoredConcept {
        let title_tokens = tokenize(&doc.title);
        let title_lower = doc.title.to_lowercase();
        let slug_tokens = tokenize(&doc.slug);
        let slug_lower = doc.slug.to_lowercase();
        let body_tokens = doc.body.as_deref().map(tokenize).unwrap_or_default();
        let backlinks_lower: Vec<String> =
            doc.backlinks.iter().map(|b| b.to_lowercase()).collect();

        let mut reasons = Vec::new();
        for term in terms {
            push_field_reason(
                &mut reasons,
                MatchField::Title,
                term,
                field_points(&title_tokens, &title_lower, term, self.weights.title_token, self.weights.title_substring),
            );
            push_field_reason(
                &mut reasons,
                MatchField::Slug,
                term,
                field_points(&slug_tokens, &slug_lower, term, self.weights.slug_token, self.weights.slug_substring),
            );
            let body_hits = body_tokens.iter().filter(|t| *t == term).count() as u32;
            if body_hits > 0 {
                let counted = body_hits.min(self.weights.body_hit_cap);
                reasons.push(MatchReason {
                    field: MatchField::Body,
                    term: term.clone(),
                    hits: body_hits,
                    contribution: counted * self.weights.body_hit,
                });
            }
            let bl_hits = backlinks_lower.iter().filter(|b| b.contains(term.as_str())).count() as u32;
            if bl_hits > 0 {
                reasons.push(MatchReason {
                    field: MatchField::Backlink,
                    term: term.clone(),
                    hits: bl_hits,
                    contribution: self.weights.backlink,
                });
            }
        }
        let score = reasons.iter().map(|r| r.contribution).sum();
        ScoredConcept { slug: doc.slug.clone(), score, reasons }
    }
}

/// A `(hits, contribution)` for a token-or-substring field match, or `None`.
/// A whole-token match outscores a bare substring; only the stronger is taken.
fn field_points(
    tokens: &[String],
    lower: &str,
    term: &str,
    token_weight: u32,
    substring_weight: u32,
) -> Option<(u32, u32)> {
    if tokens.iter().any(|t| t == term) {
        Some((1, token_weight))
    } else if lower.contains(term) {
        Some((1, substring_weight))
    } else {
        None
    }
}

fn push_field_reason(
    reasons: &mut Vec<MatchReason>,
    field: MatchField,
    term: &str,
    points: Option<(u32, u32)>,
) {
    if let Some((hits, contribution)) = points {
        reasons.push(MatchReason { field, term: term.to_string(), hits, contribution });
    }
}

/// Lowercase the query and split into unique, deterministically-ordered terms.
fn query_terms(query: &str) -> Vec<String> {
    let mut terms = tokenize(query);
    terms.sort();
    terms.dedup();
    terms
}

/// Split a string into lowercased alphanumeric tokens (so `ai-agent` and
/// `Ai Agent` both yield `["ai", "agent"]`).
fn tokenize(s: &str) -> Vec<String> {
    s.split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_lowercase())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(slug: &str, title: &str, body: Option<&str>, backlinks: &[&str]) -> ConceptDoc {
        ConceptDoc {
            slug: slug.into(),
            title: title.into(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "u".into(),
            backlinks: backlinks.iter().map(|s| s.to_string()).collect(),
            body: body.map(|b| b.to_string()),
        }
    }

    #[test]
    fn tokenize_splits_on_non_alphanumeric_and_lowercases() {
        assert_eq!(tokenize("Ai-Agent  Architecture!"), vec!["ai", "agent", "architecture"]);
        assert_eq!(query_terms("agent Agent  agent"), vec!["agent"]); // dedup + sort
    }

    #[test]
    fn title_token_beats_substring() {
        let r = Retriever::new();
        let corpus = RagCorpus::from_docs(vec![
            doc("ai-agent", "AI Agent", None, &[]),
            doc("agentic-ai", "Agentic AI", None, &[]),
        ]);
        let scored = r.score(&corpus, "agent");
        let agent = scored.iter().find(|s| s.slug == "ai-agent").unwrap();
        let agentic = scored.iter().find(|s| s.slug == "agentic-ai").unwrap();
        // "agent" is a whole token of "AI Agent" (title_token=10 + slug_token=8),
        // but only a substring of "agentic-ai" (title_substring=5 + slug_substring=4).
        assert!(agent.score > agentic.score, "{} vs {}", agent.score, agentic.score);
    }

    #[test]
    fn reasons_explain_each_field() {
        let r = Retriever::new();
        let corpus = RagCorpus::from_docs(vec![doc(
            "rag",
            "Retrieval Augmented Generation",
            Some("RAG combines retrieval and generation. retrieval retrieval."),
            &["20-Areas/rag-notes.md"],
        )]);
        let scored = r.score(&corpus, "retrieval");
        let s = &scored[0];
        let fields: Vec<MatchField> = s.reasons.iter().map(|x| x.field).collect();
        assert!(fields.contains(&MatchField::Title), "title token match expected");
        assert!(fields.contains(&MatchField::Body), "body match expected");
        // Body term "retrieval" occurs 3× → hits=3, capped contribution = 3*1.
        let body = s.reasons.iter().find(|x| x.field == MatchField::Body).unwrap();
        assert_eq!(body.hits, 3);
        assert_eq!(body.contribution, 3);
    }

    #[test]
    fn body_cap_limits_keyword_stuffing() {
        let r = Retriever::new();
        let stuffed = "x ".repeat(50);
        let corpus = RagCorpus::from_docs(vec![doc("x", "Z", Some(&stuffed), &[])]);
        let scored = r.score(&corpus, "x");
        let body = scored[0].reasons.iter().find(|r| r.field == MatchField::Body).unwrap();
        assert_eq!(body.hits, 50);
        assert_eq!(body.contribution, 3, "capped at body_hit_cap * body_hit");
    }

    #[test]
    fn no_match_scores_zero_with_no_reasons() {
        let r = Retriever::new();
        let corpus = RagCorpus::from_docs(vec![doc("ai-agent", "AI Agent", Some("nothing here"), &[])]);
        let scored = r.score(&corpus, "kubernetes");
        assert_eq!(scored[0].score, 0);
        assert!(scored[0].reasons.is_empty());
    }

    #[test]
    fn backlink_substring_contributes() {
        let r = Retriever::new();
        let corpus = RagCorpus::from_docs(vec![doc("ai-agent", "Z", None, &["20-Areas/agent-survey.md"])]);
        let scored = r.score(&corpus, "agent");
        let bl = scored[0].reasons.iter().find(|x| x.field == MatchField::Backlink).unwrap();
        assert_eq!(bl.contribution, 2);
    }
}
