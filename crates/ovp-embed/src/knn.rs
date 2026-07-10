//! Non-mutual kNN similarity graph over L2-normalized embedding vectors.
//!
//! Brute-force O(n²·d) — at vault scale (n ≈ 1000, d = 384) this is a few
//! hundred million multiply-adds, milliseconds-to-seconds territory, and it
//! keeps the recipe dependency-free and exactly reproducible.

/// One undirected weighted edge (`a < b`, weight = cosine similarity).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Edge {
    pub a: usize,
    pub b: usize,
    pub weight: f64,
}

/// Cosine similarity of two L2-normalized vectors (= dot product).
pub fn cosine(a: &[f32], b: &[f32]) -> f64 {
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| f64::from(*x) * f64::from(*y))
        .sum()
}

/// Build the NON-mutual kNN graph: each node contributes a directed edge to
/// its top-`k` neighbors with cosine ≥ `threshold`; the undirected union is
/// returned (an edge exists if EITHER endpoint picked the other — this is the
/// spike-validated variant; mutual-kNN starves the graph).
///
/// Deterministic: neighbor ties are broken by ascending index, and edges are
/// emitted in sorted `(a, b)` order.
pub fn knn_edges(vectors: &[Vec<f32>], k: usize, threshold: f64) -> Vec<Edge> {
    let n = vectors.len();
    let mut picked: std::collections::BTreeMap<(usize, usize), f64> = std::collections::BTreeMap::new();
    for i in 0..n {
        // (sim, j) for all candidates above the threshold.
        let mut sims: Vec<(f64, usize)> = (0..n)
            .filter(|&j| j != i)
            .map(|j| (cosine(&vectors[i], &vectors[j]), j))
            .filter(|(s, _)| *s >= threshold)
            .collect();
        // Sort by sim desc, then index asc — a total, deterministic order.
        sims.sort_by(|(sa, ja), (sb, jb)| {
            sb.partial_cmp(sa)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(ja.cmp(jb))
        });
        for &(s, j) in sims.iter().take(k) {
            let key = (i.min(j), i.max(j));
            picked.entry(key).or_insert(s);
        }
    }
    picked
        .into_iter()
        .map(|((a, b), weight)| Edge { a, b, weight })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cosine_of_normalized_vectors_is_dot() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0];
        assert_eq!(cosine(&a, &b), 0.0);
        assert!((cosine(&a, &a) - 1.0).abs() < 1e-9);
    }

    #[test]
    fn knn_union_is_non_mutual() {
        // c is closest to a and b; with k=1, a picks b, b picks a, c picks a —
        // the union must still contain (a, c) even though a never picked c.
        let a = vec![1.0, 0.0];
        let b = vec![0.98, 0.198_997_49]; // ~a
        let c = vec![0.9, 0.435_889_89]; // closer to a than to b? both above threshold
        let edges = knn_edges(&[a, b, c], 1, 0.5);
        let pairs: Vec<(usize, usize)> = edges.iter().map(|e| (e.a, e.b)).collect();
        assert!(pairs.contains(&(0, 1)), "mutual top pick kept: {pairs:?}");
        assert!(
            pairs.iter().any(|&(x, y)| y == 2 || x == 2),
            "non-mutual edge from c kept: {pairs:?}"
        );
    }

    #[test]
    fn threshold_prunes_edges() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0]; // orthogonal — below any positive threshold
        let edges = knn_edges(&[a, b], 5, 0.5);
        assert!(edges.is_empty());
    }
}
