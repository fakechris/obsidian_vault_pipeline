//! Deterministic weighted Louvain community detection (~150 LoC, no deps).
//!
//! Standard two-phase Louvain over an undirected weighted graph with a
//! resolution parameter. Determinism contract: given the same node count,
//! edge list, resolution, and seed, the output is byte-identical — the only
//! randomness is a seeded Fisher–Yates shuffle of the node visit order
//! (SplitMix64, inlined below; no rand dependency). Callers are expected to
//! present nodes in a canonical order (e.g. sorted by case_id).

use crate::knn::Edge;

/// SplitMix64 — tiny, seedable, deterministic PRNG (public-domain algorithm).
struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in `0..bound` via rejection-free modulo (bias is irrelevant for
    /// a shuffle order; determinism is what matters).
    fn below(&mut self, bound: usize) -> usize {
        (self.next() % bound as u64) as usize
    }
}

fn shuffled(n: usize, rng: &mut SplitMix64) -> Vec<usize> {
    let mut order: Vec<usize> = (0..n).collect();
    for i in (1..n).rev() {
        order.swap(i, rng.below(i + 1));
    }
    order
}

/// One level of the aggregated graph.
struct Level {
    /// Non-self adjacency: `adj[i]` = (neighbor, weight), both directions.
    adj: Vec<Vec<(usize, f64)>>,
    /// Self-loop weight per node (2 × intra weight of the collapsed group).
    self_w: Vec<f64>,
}

impl Level {
    fn degree(&self, i: usize) -> f64 {
        self.self_w[i] + self.adj[i].iter().map(|(_, w)| w).sum::<f64>()
    }
}

/// Run one local-move phase. Returns (community per node, moved_any).
fn local_move(level: &Level, resolution: f64, rng: &mut SplitMix64) -> (Vec<usize>, bool) {
    let n = level.adj.len();
    let two_m: f64 = (0..n).map(|i| level.degree(i)).sum();
    if two_m <= 0.0 {
        return ((0..n).collect(), false);
    }
    let mut comm: Vec<usize> = (0..n).collect();
    let mut tot: Vec<f64> = (0..n).map(|i| level.degree(i)).collect();
    let mut moved_any = false;
    loop {
        let mut moves = 0usize;
        for &i in &shuffled(n, rng) {
            let k_i = level.degree(i);
            // Weight from i to each neighboring community.
            let mut w_to: std::collections::BTreeMap<usize, f64> = std::collections::BTreeMap::new();
            for &(j, w) in &level.adj[i] {
                *w_to.entry(comm[j]).or_insert(0.0) += w;
            }
            let old = comm[i];
            tot[old] -= k_i;
            let gain_of = |c: usize| -> f64 {
                w_to.get(&c).copied().unwrap_or(0.0) - resolution * k_i * tot[c] / two_m
            };
            // Best community among neighbors + staying put. Ties: prefer the
            // current community, then the smallest id (BTreeMap order).
            let mut best_c = old;
            let mut best_gain = gain_of(old);
            for (&c, _) in &w_to {
                let g = gain_of(c);
                if g > best_gain + 1e-12 {
                    best_c = c;
                    best_gain = g;
                }
            }
            tot[best_c] += k_i;
            if best_c != old {
                comm[i] = best_c;
                moves += 1;
                moved_any = true;
            }
        }
        if moves == 0 {
            break;
        }
    }
    (comm, moved_any)
}

/// Collapse a level by its community assignment. Returns the new level plus
/// the dense community renumbering (old community id → new node id).
fn aggregate(level: &Level, comm: &[usize]) -> (Level, Vec<usize>) {
    let n = level.adj.len();
    // Dense renumbering in ascending community-id order (deterministic).
    let mut ids: Vec<usize> = comm.to_vec();
    ids.sort_unstable();
    ids.dedup();
    let renum: std::collections::BTreeMap<usize, usize> =
        ids.iter().enumerate().map(|(new, &old)| (old, new)).collect();
    let m = ids.len();
    let mut self_w = vec![0.0f64; m];
    let mut pair_w: std::collections::BTreeMap<(usize, usize), f64> = std::collections::BTreeMap::new();
    for i in 0..n {
        let ci = renum[&comm[i]];
        self_w[ci] += level.self_w[i];
        for &(j, w) in &level.adj[i] {
            let cj = renum[&comm[j]];
            if ci == cj {
                // Each undirected edge is seen from both endpoints → sums to
                // 2 × intra weight, the self-loop convention degree() expects.
                self_w[ci] += w;
            } else {
                // Cross edges accumulate once per direction; halved below.
                *pair_w.entry((ci.min(cj), ci.max(cj))).or_insert(0.0) += w;
            }
        }
    }
    let mut adj = vec![Vec::new(); m];
    for ((a, b), w2) in pair_w {
        let w = w2 / 2.0; // both directions were summed
        adj[a].push((b, w));
        adj[b].push((a, w));
    }
    let renum_vec: Vec<usize> = comm.iter().map(|c| renum[c]).collect();
    (Level { adj, self_w }, renum_vec)
}

/// Louvain community labels for `n_nodes` nodes under `edges`. Singleton
/// communities (including isolated nodes) are labeled `-1` (noise /
/// Unclassified); real communities are renumbered `0..` by descending size,
/// ties broken by the smallest member node index. Deterministic given
/// `(n_nodes, edges, resolution, seed)`.
pub fn louvain_labels(n_nodes: usize, edges: &[Edge], resolution: f64, seed: u64) -> Vec<i64> {
    let mut adj = vec![Vec::new(); n_nodes];
    for e in edges {
        adj[e.a].push((e.b, e.weight));
        adj[e.b].push((e.a, e.weight));
    }
    let mut level = Level {
        adj,
        self_w: vec![0.0; n_nodes],
    };
    let mut rng = SplitMix64(seed);
    // node → community at the ORIGINAL level, refined through aggregations.
    let mut assignment: Vec<usize> = (0..n_nodes).collect();
    loop {
        let (comm, moved) = local_move(&level, resolution, &mut rng);
        if !moved {
            break;
        }
        let (next, renum) = aggregate(&level, &comm);
        // `renum[i]` = the aggregated node that current-level node `i` joins.
        for slot in assignment.iter_mut() {
            *slot = renum[*slot];
        }
        level = next;
    }
    // Community sizes at the original level.
    let mut size: std::collections::BTreeMap<usize, usize> = std::collections::BTreeMap::new();
    let mut first_member: std::collections::BTreeMap<usize, usize> = std::collections::BTreeMap::new();
    for (i, &c) in assignment.iter().enumerate() {
        *size.entry(c).or_insert(0) += 1;
        first_member.entry(c).or_insert(i);
    }
    // Renumber: real communities (size ≥ 2) by size desc, then first member asc.
    let mut real: Vec<(usize, usize, usize)> = size
        .iter()
        .filter(|&(_, &s)| s >= 2)
        .map(|(&c, &s)| (s, first_member[&c], c))
        .collect();
    real.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
    let label_of: std::collections::BTreeMap<usize, i64> = real
        .iter()
        .enumerate()
        .map(|(new, &(_, _, c))| (c, new as i64))
        .collect();
    assignment
        .iter()
        .map(|c| label_of.get(c).copied().unwrap_or(-1))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blob_edges(groups: &[&[usize]]) -> Vec<Edge> {
        // Fully connect each group with weight 1.0.
        let mut edges = Vec::new();
        for g in groups {
            for (x, &a) in g.iter().enumerate() {
                for &b in &g[x + 1..] {
                    edges.push(Edge {
                        a,
                        b,
                        weight: 1.0,
                    });
                }
            }
        }
        edges
    }

    #[test]
    fn two_obvious_blobs_separate() {
        let edges = blob_edges(&[&[0, 1, 2, 3], &[4, 5, 6, 7]]);
        let labels = louvain_labels(8, &edges, 1.5, 42);
        assert_eq!(labels[0], labels[1]);
        assert_eq!(labels[1], labels[2]);
        assert_eq!(labels[2], labels[3]);
        assert_eq!(labels[4], labels[5]);
        assert_ne!(labels[0], labels[4], "blobs must not merge: {labels:?}");
        assert!(labels.iter().all(|&l| l >= 0));
    }

    #[test]
    fn deterministic_across_runs() {
        let edges = blob_edges(&[&[0, 1, 2], &[3, 4, 5], &[6, 7, 8, 9]]);
        let a = louvain_labels(10, &edges, 1.5, 42);
        let b = louvain_labels(10, &edges, 1.5, 42);
        assert_eq!(a, b);
    }

    #[test]
    fn isolated_singleton_is_noise() {
        // Node 4 has no edges at all.
        let edges = blob_edges(&[&[0, 1, 2, 3]]);
        let labels = louvain_labels(5, &edges, 1.5, 42);
        assert_eq!(labels[4], -1, "singleton → noise: {labels:?}");
        assert!(labels[..4].iter().all(|&l| l == labels[0]));
    }

    #[test]
    fn labels_ordered_by_community_size() {
        let edges = blob_edges(&[&[0, 1, 2], &[3, 4, 5, 6, 7]]);
        let labels = louvain_labels(8, &edges, 1.5, 42);
        assert_eq!(labels[3], 0, "bigger blob gets label 0: {labels:?}");
        assert_eq!(labels[0], 1);
    }
}
