//! Deterministic ordering + top-k selection over scored concepts.

use crate::retriever::ScoredConcept;

/// Orders scored results and keeps the best `limit`. Drops anything below
/// `min_score` (default 1 → zero-score docs are not "results"), then sorts by
/// `(score desc, slug asc)` — the slug tie-break makes ordering a total,
/// reproducible function of the inputs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ranker {
    pub min_score: u32,
    pub limit: usize,
}

impl Default for Ranker {
    fn default() -> Self {
        Self { min_score: 1, limit: 10 }
    }
}

impl Ranker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_limit(limit: usize) -> Self {
        Self { limit, ..Self::default() }
    }

    /// Filter, order, and truncate. Consumes the scored list and returns the
    /// ranked top-k.
    pub fn rank(&self, scored: Vec<ScoredConcept>) -> Vec<ScoredConcept> {
        let floor = self.min_score.max(1);
        let mut kept: Vec<ScoredConcept> =
            scored.into_iter().filter(|s| s.score >= floor).collect();
        kept.sort_by(|a, b| b.score.cmp(&a.score).then_with(|| a.slug.cmp(&b.slug)));
        kept.truncate(self.limit);
        kept
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scored(slug: &str, score: u32) -> ScoredConcept {
        ScoredConcept { slug: slug.into(), score, reasons: vec![] }
    }

    #[test]
    fn drops_zero_orders_desc_and_breaks_ties_by_slug() {
        let ranker = Ranker::new();
        let out = ranker.rank(vec![
            scored("zebra", 5),
            scored("apple", 5), // tie with zebra → apple first (slug asc)
            scored("none", 0),  // dropped
            scored("top", 9),
        ]);
        let slugs: Vec<&str> = out.iter().map(|s| s.slug.as_str()).collect();
        assert_eq!(slugs, vec!["top", "apple", "zebra"]);
    }

    #[test]
    fn limit_truncates_after_ordering() {
        let ranker = Ranker::with_limit(2);
        let out = ranker.rank(vec![scored("a", 1), scored("b", 3), scored("c", 2)]);
        let slugs: Vec<&str> = out.iter().map(|s| s.slug.as_str()).collect();
        assert_eq!(slugs, vec!["b", "c"]);
    }

    #[test]
    fn empty_input_is_empty_output() {
        assert!(Ranker::new().rank(vec![]).is_empty());
    }
}
