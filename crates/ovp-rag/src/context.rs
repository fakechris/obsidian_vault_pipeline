//! Builds a bounded context object from ranked results.

use serde::Serialize;

use crate::corpus::RagCorpus;
use crate::retriever::{MatchReason, ScoredConcept};

/// One entry in a [`RagContext`]: the concept, its score + reasons, a bounded
/// note snippet, and a bounded backlink list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SelectedConcept {
    pub slug: String,
    pub title: String,
    pub evergreen_path: String,
    pub score: u32,
    /// First `max_snippet_chars` of the note body, trimmed on a char boundary;
    /// `None` if the concept has no note on disk.
    pub snippet: Option<String>,
    /// Up to `max_backlinks` referencing note paths.
    pub backlinks: Vec<String>,
    pub reasons: Vec<MatchReason>,
}

/// A bounded retrieval context: the query plus the selected concepts. Bounded so
/// it is safe to hand to a downstream LLM prompt later **without** this crate
/// ever calling one.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RagContext {
    pub query: String,
    pub selected: Vec<SelectedConcept>,
}

/// Caps the context: at most `max_concepts` entries, each with a snippet of at
/// most `max_snippet_chars` and at most `max_backlinks` backlinks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContextBuilder {
    pub max_concepts: usize,
    pub max_snippet_chars: usize,
    pub max_backlinks: usize,
}

impl Default for ContextBuilder {
    fn default() -> Self {
        Self { max_concepts: 5, max_snippet_chars: 280, max_backlinks: 5 }
    }
}

impl ContextBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    /// Build the bounded context. `ranked` is expected to already be ordered
    /// (e.g. from [`Ranker::rank`](crate::Ranker::rank)); a ranked entry whose
    /// slug is not in `corpus` is skipped (it cannot happen for a corpus-derived
    /// ranking, but the build never panics).
    pub fn build(&self, corpus: &RagCorpus, ranked: &[ScoredConcept], query: &str) -> RagContext {
        let selected = ranked
            .iter()
            .take(self.max_concepts)
            .filter_map(|s| {
                let doc = corpus.get(&s.slug)?;
                let snippet = doc.body.as_deref().map(|b| snippet(b, self.max_snippet_chars));
                let mut backlinks = doc.backlinks.clone();
                backlinks.truncate(self.max_backlinks);
                Some(SelectedConcept {
                    slug: doc.slug.clone(),
                    title: doc.title.clone(),
                    evergreen_path: doc.evergreen_path.clone(),
                    score: s.score,
                    snippet,
                    backlinks,
                    reasons: s.reasons.clone(),
                })
            })
            .collect();
        RagContext { query: query.to_string(), selected }
    }
}

/// First `max` characters of the trimmed body, cut on a char boundary, with an
/// ellipsis when truncated.
fn snippet(body: &str, max: usize) -> String {
    let trimmed = body.trim();
    if trimmed.chars().count() <= max {
        return trimmed.to_string();
    }
    let end = trimmed.char_indices().nth(max).map(|(i, _)| i).unwrap_or(trimmed.len());
    let mut s = trimmed[..end].trim_end().to_string();
    s.push('…');
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::ConceptDoc;

    fn doc(slug: &str, body: Option<&str>, backlinks: &[&str]) -> ConceptDoc {
        ConceptDoc {
            slug: slug.into(),
            title: slug.to_uppercase(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "u".into(),
            backlinks: backlinks.iter().map(|s| s.to_string()).collect(),
            body: body.map(|b| b.to_string()),
        }
    }

    fn scored(slug: &str, score: u32) -> ScoredConcept {
        ScoredConcept { slug: slug.into(), score, reasons: vec![] }
    }

    #[test]
    fn caps_concepts_snippet_and_backlinks() {
        let long = "word ".repeat(200);
        let corpus = RagCorpus::from_docs(vec![
            doc("a", Some(&long), &["1.md", "2.md", "3.md", "4.md", "5.md", "6.md"]),
            doc("b", Some("short"), &[]),
            doc("c", None, &[]),
        ]);
        let builder = ContextBuilder { max_concepts: 2, max_snippet_chars: 20, max_backlinks: 3 };
        let ctx = builder.build(&corpus, &[scored("a", 9), scored("b", 5), scored("c", 1)], "q");

        assert_eq!(ctx.selected.len(), 2, "max_concepts honored");
        let a = &ctx.selected[0];
        assert!(a.snippet.as_ref().unwrap().chars().count() <= 21, "snippet bounded (+ ellipsis)");
        assert!(a.snippet.as_ref().unwrap().ends_with('…'), "truncated snippet ends with ellipsis");
        assert_eq!(a.backlinks.len(), 3, "max_backlinks honored");
        assert_eq!(ctx.query, "q");
    }

    #[test]
    fn short_body_not_truncated_and_absent_body_is_none() {
        let corpus = RagCorpus::from_docs(vec![doc("a", Some("  hello  "), &[]), doc("b", None, &[])]);
        let ctx = ContextBuilder::new().build(&corpus, &[scored("a", 3), scored("b", 1)], "q");
        assert_eq!(ctx.selected[0].snippet.as_deref(), Some("hello"));
        assert!(ctx.selected[1].snippet.is_none());
    }

    #[test]
    fn unknown_slug_is_skipped_not_panicked() {
        let corpus = RagCorpus::from_docs(vec![doc("a", None, &[])]);
        let ctx = ContextBuilder::new().build(&corpus, &[scored("ghost", 9), scored("a", 1)], "q");
        let slugs: Vec<&str> = ctx.selected.iter().map(|s| s.slug.as_str()).collect();
        assert_eq!(slugs, vec!["a"]);
    }

    #[test]
    fn snippet_cuts_on_char_boundary() {
        // Multibyte chars must not be split mid-codepoint.
        let body = "日本語のテキストをここに置く".repeat(3);
        let out = snippet(&body, 5);
        assert!(out.chars().count() <= 6); // 5 + ellipsis
        assert!(out.ends_with('…'));
    }
}
