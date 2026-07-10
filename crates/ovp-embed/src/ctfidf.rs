//! c-TF-IDF keywords per cluster (BERTopic-style, deterministic).
//!
//! Each cluster is treated as one mega-document; a term's score in cluster
//! `c` is `tf(t, c) · ln(1 + A / f(t))` where `A` is the average token count
//! per cluster and `f(t)` the term's total count across all clusters. The
//! tokenizer is the CALLER's concern (the CLI reuses the CJK-aware
//! `tokenize_for_search` from the lexical search layer), which keeps this
//! crate a leaf.

use std::collections::BTreeMap;

/// Top-`top_n` keywords for every cluster. `clusters[i]` is the token list of
/// cluster `i`'s concatenated documents. Ordering is deterministic: score
/// desc, then term asc.
pub fn keywords(clusters: &[Vec<String>], top_n: usize) -> Vec<Vec<String>> {
    let mut per_cluster: Vec<BTreeMap<&str, f64>> = Vec::with_capacity(clusters.len());
    let mut global: BTreeMap<&str, f64> = BTreeMap::new();
    let mut total_tokens = 0usize;
    for tokens in clusters {
        let mut tf: BTreeMap<&str, f64> = BTreeMap::new();
        for t in tokens {
            *tf.entry(t.as_str()).or_insert(0.0) += 1.0;
            *global.entry(t.as_str()).or_insert(0.0) += 1.0;
        }
        total_tokens += tokens.len();
        per_cluster.push(tf);
    }
    let n_clusters = clusters.len().max(1);
    let avg = total_tokens as f64 / n_clusters as f64;
    per_cluster
        .into_iter()
        .map(|tf| {
            let mut scored: Vec<(f64, &str)> = tf
                .into_iter()
                .map(|(t, count)| (count * (1.0 + avg / global[t]).ln(), t))
                .collect();
            scored.sort_by(|(sa, ta), (sb, tb)| {
                sb.partial_cmp(sa)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then(ta.cmp(tb))
            });
            scored
                .into_iter()
                .take(top_n)
                .map(|(_, t)| t.to_string())
                .collect()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn toks(s: &str) -> Vec<String> {
        s.split_whitespace().map(str::to_string).collect()
    }

    #[test]
    fn distinctive_terms_beat_shared_terms() {
        let clusters = vec![
            toks("agent agent agent memory shared shared"),
            toks("quant quant market market shared shared"),
        ];
        let kw = keywords(&clusters, 2);
        assert_eq!(kw[0], vec!["agent", "memory"]);
        assert_eq!(kw[1], vec!["market", "quant"], "tie broken by term asc");
    }

    #[test]
    fn deterministic_and_bounded() {
        let clusters = vec![toks("a b c"), toks("d e")];
        let kw1 = keywords(&clusters, 10);
        let kw2 = keywords(&clusters, 10);
        assert_eq!(kw1, kw2);
        assert!(kw1[0].len() <= 3);
    }

    #[test]
    fn empty_cluster_yields_no_keywords() {
        let clusters = vec![vec![], toks("x y")];
        let kw = keywords(&clusters, 5);
        assert!(kw[0].is_empty());
        assert_eq!(kw[1].len(), 2);
    }
}
