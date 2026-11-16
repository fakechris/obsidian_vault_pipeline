//! Graph layout precomputation for the knowledge portal.
//! Computes 2D coordinates at index-build time so the frontend
//! can skip force simulation and render immediately.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// A node with precomputed position.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LayoutNode {
    pub id: String,
    pub xy: [f64; 2],
    /// Node weight (e.g. backlink count), influences size in rendering.
    #[serde(default)]
    pub weight: f64,
}

/// An edge between two nodes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LayoutEdge {
    pub source: String,
    pub target: String,
    #[serde(default)]
    pub weight: f64,
}

/// Complete graph layout result.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct GraphLayout {
    pub nodes: Vec<LayoutNode>,
    pub edges: Vec<LayoutEdge>,
}

const DEFAULT_WIDTH: f64 = 800.0;
const DEFAULT_HEIGHT: f64 = 600.0;
const DEFAULT_ITERATIONS: usize = 100;

fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

fn hash_unit(id: &str, salt: u8) -> f64 {
    let mut buf = id.as_bytes().to_vec();
    buf.push(salt);
    let h = fnv1a64(&buf);
    (h as f64 / u64::MAX as f64).mul_add(0.94, 0.03)
}

fn initial_position(id: &str, width: f64, height: f64) -> (f64, f64) {
    let x = hash_unit(id, 0) * width;
    let y = hash_unit(id, 1) * height;
    (x, y)
}

/// Compute a simple force-directed layout using Fruchterman–Reingold.
pub fn compute_layout(
    nodes: &BTreeMap<String, f64>,
    edges: &[(String, String, f64)],
    width: f64,
    height: f64,
    iterations: usize,
) -> GraphLayout {
    let n = nodes.len();
    if n == 0 {
        return GraphLayout::default();
    }
    if n == 1 {
        let id = nodes.keys().next().expect("non-empty").clone();
        let weight = nodes[&id];
        return GraphLayout {
            nodes: vec![LayoutNode {
                id,
                xy: [width / 2.0, height / 2.0],
                weight,
            }],
            edges: layout_edges(edges),
        };
    }

    let ids: Vec<String> = nodes.keys().cloned().collect();
    let mut idx_map = BTreeMap::new();
    for (i, id) in ids.iter().enumerate() {
        idx_map.insert(id.as_str(), i);
    }

    let mut pos: Vec<(f64, f64)> = ids
        .iter()
        .map(|id| initial_position(id, width, height))
        .collect();

    let area = width * height;
    let k = (area / n as f64).sqrt();
    let mut temperature = 0.1 * width.min(height);

    let edge_pairs: Vec<(usize, usize, f64)> = edges
        .iter()
        .filter_map(|(s, t, w)| {
            let i = *idx_map.get(s.as_str())?;
            let j = *idx_map.get(t.as_str())?;
            Some((i, j, *w))
        })
        .collect();

    for _ in 0..iterations {
        let mut disp = vec![(0.0f64, 0.0f64); n];
        for i in 0..n {
            for j in (i + 1)..n {
                let dx = pos[i].0 - pos[j].0;
                let dy = pos[i].1 - pos[j].1;
                let d = (dx * dx + dy * dy).sqrt().max(1e-6);
                let force = k * k / d;
                let (ux, uy) = (dx / d, dy / d);
                disp[i].0 += ux * force;
                disp[i].1 += uy * force;
                disp[j].0 -= ux * force;
                disp[j].1 -= uy * force;
            }
        }
        for &(i, j, w) in &edge_pairs {
            let dx = pos[i].0 - pos[j].0;
            let dy = pos[i].1 - pos[j].1;
            let d = (dx * dx + dy * dy).sqrt().max(1e-6);
            let attract = w.max(1.0) * d * d / k;
            let (ux, uy) = (dx / d, dy / d);
            disp[i].0 -= ux * attract;
            disp[i].1 -= uy * attract;
            disp[j].0 += ux * attract;
            disp[j].1 += uy * attract;
        }
        for i in 0..n {
            let (dx, dy) = disp[i];
            let mag = (dx * dx + dy * dy).sqrt().max(1e-9);
            let step = mag.min(temperature);
            pos[i].0 += dx / mag * step;
            pos[i].1 += dy / mag * step;
        }
        temperature *= 0.95;
    }

    normalize_positions(&mut pos, width, height);

    let layout_nodes: Vec<LayoutNode> = ids
        .iter()
        .enumerate()
        .map(|(i, id)| LayoutNode {
            id: id.clone(),
            xy: [pos[i].0, pos[i].1],
            weight: nodes[id],
        })
        .collect();

    GraphLayout {
        nodes: layout_nodes,
        edges: layout_edges(edges),
    }
}

fn layout_edges(edges: &[(String, String, f64)]) -> Vec<LayoutEdge> {
    edges
        .iter()
        .map(|(s, t, w)| LayoutEdge {
            source: s.clone(),
            target: t.clone(),
            weight: *w,
        })
        .collect()
}

fn normalize_positions(pos: &mut [(f64, f64)], width: f64, height: f64) {
    if pos.is_empty() {
        return;
    }
    let (mut minx, mut miny, mut maxx, mut maxy) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
    for &(x, y) in pos.iter() {
        minx = minx.min(x);
        miny = miny.min(y);
        maxx = maxx.max(x);
        maxy = maxy.max(y);
    }
    let spanx = (maxx - minx).max(1e-9);
    let spany = (maxy - miny).max(1e-9);
    let pad = 0.05;
    let inner_w = width * (1.0 - 2.0 * pad);
    let inner_h = height * (1.0 - 2.0 * pad);
    for p in pos.iter_mut() {
        p.0 = pad * width + (p.0 - minx) / spanx * inner_w;
        p.1 = pad * height + (p.1 - miny) / spany * inner_h;
    }
}

pub fn compute_layout_default(
    nodes: &BTreeMap<String, f64>,
    edges: &[(String, String, f64)],
) -> GraphLayout {
    compute_layout(
        nodes,
        edges,
        DEFAULT_WIDTH,
        DEFAULT_HEIGHT,
        DEFAULT_ITERATIONS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_graph() {
        let layout = compute_layout_default(&BTreeMap::new(), &[]);
        assert!(layout.nodes.is_empty());
    }

    #[test]
    fn single_node_centered() {
        let mut nodes = BTreeMap::new();
        nodes.insert("only".into(), 3.0);
        let layout = compute_layout_default(&nodes, &[]);
        assert_eq!(layout.nodes[0].xy, [400.0, 300.0]);
    }

    #[test]
    fn coordinates_in_bounds_and_separated() {
        let mut nodes = BTreeMap::new();
        for slug in ["a", "b", "c", "d"] {
            nodes.insert(slug.into(), 1.0);
        }
        let edges = vec![
            ("a".into(), "b".into(), 1.0),
            ("b".into(), "c".into(), 1.0),
            ("c".into(), "d".into(), 1.0),
        ];
        let layout = compute_layout_default(&nodes, &edges);
        let min_dist = 12.0;
        for i in 0..layout.nodes.len() {
            let [x, y] = layout.nodes[i].xy;
            assert!((0.0..=800.0).contains(&x));
            assert!((0.0..=600.0).contains(&y));
            for j in (i + 1)..layout.nodes.len() {
                let [x2, y2] = layout.nodes[j].xy;
                let d = ((x - x2).powi(2) + (y - y2).powi(2)).sqrt();
                assert!(d > min_dist, "overlap d={d}");
            }
        }
    }

    #[test]
    fn layout_is_deterministic() {
        let mut nodes = BTreeMap::new();
        nodes.insert("x".into(), 2.0);
        nodes.insert("y".into(), 5.0);
        nodes.insert("z".into(), 1.0);
        let edges = vec![("x".into(), "y".into(), 2.0), ("y".into(), "z".into(), 1.0)];
        assert_eq!(
            compute_layout_default(&nodes, &edges),
            compute_layout_default(&nodes, &edges)
        );
    }
}