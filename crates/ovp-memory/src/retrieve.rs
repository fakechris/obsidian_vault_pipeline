//! Hybrid retrieval: lexical + soft-semantic (token cosine) fusion with an
//! honest multi-lane coverage ledger.
//!
//! Design:
//! - **Lexical lane** — existing `ovp_index::score::lexical_score` (exact /
//!   token hits). Always available.
//! - **Semantic lane** — bilingual token-set cosine over the same tokenizer
//!   family used by search (character bigrams for CJK). Cheap, no ONNX; helps
//!   paraphrase when terms overlap in different order. True embedding kNN can
//!   later replace/augment this lane via the same RRF fuse without changing
//!   callers.
//! - **Coverage ledger** — reports per-lane NotQueried / Complete / Empty /
//!   Failed / Unavailable so "no hits" ≠ "lane offline".

use std::collections::{BTreeMap, BTreeSet, HashMap};

use ovp_index::score::{lexical_score, tokenize_for_search};
use serde::{Deserialize, Serialize};

/// Five-state lane status — same vocabulary as agent tool Coverage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum LaneState {
    #[default]
    NotQueried,
    Complete,
    Empty,
    Partial,
    Failed,
    Unavailable,
}

/// Honest multi-lane coverage for one retrieval call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct RetrieveCoverage {
    pub lexical: LaneState,
    pub semantic: LaneState,
    /// Dense embedding kNN lane (Unavailable when no vectors provided).
    pub embed: LaneState,
}

#[derive(Debug, Clone)]
pub struct ScoredId {
    pub id: String,
    pub score: f64,
}

/// Reciprocal Rank Fusion over named ranked lists.
///
/// `rrf_k` is the rank constant (common default 60). Lists may use different
/// score units; only rank order matters.
pub fn rrf_fuse(lists: &[Vec<ScoredId>], rrf_k: f64, limit: usize) -> Vec<ScoredId> {
    let mut acc: HashMap<String, f64> = HashMap::new();
    for list in lists {
        for (rank, row) in list.iter().enumerate() {
            let contrib = 1.0 / (rrf_k + (rank as f64) + 1.0);
            *acc.entry(row.id.clone()).or_insert(0.0) += contrib;
        }
    }
    let mut out: Vec<ScoredId> = acc
        .into_iter()
        .map(|(id, score)| ScoredId { id, score })
        .collect();
    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    if limit > 0 && out.len() > limit {
        out.truncate(limit);
    }
    out
}

/// Soft-semantic score: cosine of bilingual token bags (binary presence).
pub fn token_cosine(query: &str, doc: &str) -> f64 {
    let q: BTreeSet<String> = tokenize_for_search(query).into_iter().collect();
    let d: BTreeSet<String> = tokenize_for_search(doc).into_iter().collect();
    if q.is_empty() || d.is_empty() {
        return 0.0;
    }
    let inter = q.intersection(&d).count() as f64;
    let denom = (q.len() as f64).sqrt() * (d.len() as f64).sqrt();
    if denom == 0.0 {
        0.0
    } else {
        inter / denom
    }
}

/// Rank documents by soft-semantic score. `docs` is id → text.
pub fn semantic_rank(query: &str, docs: &BTreeMap<String, String>, min_score: f64) -> Vec<ScoredId> {
    let mut out: Vec<ScoredId> = docs
        .iter()
        .filter_map(|(id, text)| {
            let score = token_cosine(query, text);
            (score >= min_score).then(|| ScoredId {
                id: id.clone(),
                score,
            })
        })
        .collect();
    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    out
}

/// Rank documents by lexical score. `docs` is id → field texts joined.
pub fn lexical_rank(query: &str, docs: &BTreeMap<String, String>) -> Vec<ScoredId> {
    let mut out: Vec<ScoredId> = docs
        .iter()
        .filter_map(|(id, text)| {
            let score = lexical_score(query, &[text.as_str()]);
            (score > 0.0).then(|| ScoredId {
                id: id.clone(),
                score,
            })
        })
        .collect();
    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    out
}

/// Optional third lane: dense embedding kNN (cosine over precomputed vectors).
/// Callers that have a query vector + doc vectors (e.g. from `ovp-embed` cache)
/// pass them here; when absent the embed lane is `Unavailable`.
#[derive(Debug, Clone)]
pub struct EmbedLane {
    pub query: Vec<f32>,
    /// id → L2-normalized (or raw) embedding
    pub docs: BTreeMap<String, Vec<f32>>,
    pub min_cosine: f64,
}

/// Rank by cosine against a query vector.
pub fn embed_knn_rank(lane: &EmbedLane, limit: usize) -> Vec<ScoredId> {
    let q = &lane.query;
    if q.is_empty() {
        return Vec::new();
    }
    let mut out: Vec<ScoredId> = lane
        .docs
        .iter()
        .filter_map(|(id, v)| {
            if v.len() != q.len() || v.is_empty() {
                return None;
            }
            let score = cosine_f32(q, v);
            (score >= lane.min_cosine).then(|| ScoredId {
                id: id.clone(),
                score,
            })
        })
        .collect();
    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    if limit > 0 && out.len() > limit {
        out.truncate(limit);
    }
    out
}

fn cosine_f32(a: &[f32], b: &[f32]) -> f64 {
    let mut dot = 0.0f64;
    let mut na = 0.0f64;
    let mut nb = 0.0f64;
    for (x, y) in a.iter().zip(b.iter()) {
        let xf = f64::from(*x);
        let yf = f64::from(*y);
        dot += xf * yf;
        na += xf * xf;
        nb += yf * yf;
    }
    let denom = na.sqrt() * nb.sqrt();
    if denom == 0.0 {
        0.0
    } else {
        dot / denom
    }
}

/// Run hybrid retrieval over an id→text map. Returns fused ranking + coverage.
pub fn hybrid_retrieve(
    query: &str,
    docs: &BTreeMap<String, String>,
    limit: usize,
) -> (Vec<ScoredId>, RetrieveCoverage) {
    hybrid_retrieve_with_embed(query, docs, limit, None)
}

/// Hybrid retrieve with an optional dense embed lane.
pub fn hybrid_retrieve_with_embed(
    query: &str,
    docs: &BTreeMap<String, String>,
    limit: usize,
    embed: Option<&EmbedLane>,
) -> (Vec<ScoredId>, RetrieveCoverage) {
    let mut coverage = RetrieveCoverage::default();
    if query.trim().is_empty() || docs.is_empty() {
        coverage.lexical = LaneState::Empty;
        coverage.semantic = LaneState::Empty;
        coverage.embed = if embed.is_some() {
            LaneState::Empty
        } else {
            LaneState::Unavailable
        };
        return (Vec::new(), coverage);
    }

    let lex = lexical_rank(query, docs);
    coverage.lexical = if lex.is_empty() {
        LaneState::Empty
    } else {
        LaneState::Complete
    };

    let sem = semantic_rank(query, docs, 0.12);
    coverage.semantic = if sem.is_empty() {
        LaneState::Empty
    } else {
        LaneState::Complete
    };

    let mut lists = vec![lex, sem];
    match embed {
        None => coverage.embed = LaneState::Unavailable,
        Some(lane) if lane.query.is_empty() || lane.docs.is_empty() => {
            coverage.embed = LaneState::Unavailable;
        }
        Some(lane) => {
            let emb = embed_knn_rank(lane, limit.max(32));
            coverage.embed = if emb.is_empty() {
                LaneState::Empty
            } else {
                LaneState::Complete
            };
            lists.push(emb);
        }
    }

    let fused = rrf_fuse(&lists, 60.0, limit);
    (fused, coverage)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rrf_prefers_items_in_both_lists() {
        let a = vec![
            ScoredId {
                id: "x".into(),
                score: 10.0,
            },
            ScoredId {
                id: "y".into(),
                score: 5.0,
            },
        ];
        let b = vec![
            ScoredId {
                id: "y".into(),
                score: 0.9,
            },
            ScoredId {
                id: "z".into(),
                score: 0.8,
            },
        ];
        let fused = rrf_fuse(&[a, b], 60.0, 10);
        assert_eq!(fused[0].id, "y", "agreement across lanes wins: {fused:?}");
    }

    #[test]
    fn hybrid_finds_paraphrase_via_semantic_lane() {
        let mut docs = BTreeMap::new();
        docs.insert(
            "c1".into(),
            "Grounded claims require verbatim source quotes for every citation".into(),
        );
        docs.insert("c2".into(), "WebRTC uses UDP datagrams for media".into());
        // Query shares key terms in different order — soft-semantic should hit.
        let (hits, cov) = hybrid_retrieve(
            "verbatim quotes grounding claims citations",
            &docs,
            5,
        );
        assert_eq!(cov.lexical, LaneState::Complete);
        assert_eq!(cov.semantic, LaneState::Complete);
        assert!(
            hits.iter().any(|h| h.id == "c1"),
            "expected c1 in hits: {hits:?}"
        );
    }

    #[test]
    fn empty_query_marks_lanes_empty() {
        let mut docs = BTreeMap::new();
        docs.insert("a".into(), "hello".into());
        let (hits, cov) = hybrid_retrieve("", &docs, 5);
        assert!(hits.is_empty());
        assert_eq!(cov.lexical, LaneState::Empty);
        assert_eq!(cov.embed, LaneState::Unavailable);
    }

    #[test]
    fn embed_lane_fuses_into_rrf() {
        let mut docs = BTreeMap::new();
        docs.insert("near".into(), "alpha beta gamma".into());
        docs.insert("far".into(), "zzzz unrelated".into());
        let mut emb_docs = BTreeMap::new();
        emb_docs.insert("near".into(), vec![1.0f32, 0.0]);
        emb_docs.insert("far".into(), vec![0.0f32, 1.0]);
        let lane = EmbedLane {
            query: vec![1.0, 0.0],
            docs: emb_docs,
            min_cosine: 0.1,
        };
        let (hits, cov) =
            hybrid_retrieve_with_embed("alpha beta", &docs, 5, Some(&lane));
        assert_eq!(cov.embed, LaneState::Complete);
        assert_eq!(hits[0].id, "near");
    }
}


