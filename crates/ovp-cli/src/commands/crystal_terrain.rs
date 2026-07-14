//! `crystal-terrain` — a "knowledge terrain" projection (themescape).
//!
//! Reuses the SAME semantic layer as `crystal-themes` (cached multilingual
//! embeddings + the kNN graph + the community→label assignment in themes.json)
//! and adds ONE thing that view lacks: a 2D position per pack. A deterministic
//! force layout over the kNN graph pulls semantically-similar packs together, so
//! communities fall out as spatial islands. The frontend renders density
//! contours over these points (à la flomo's map) — peaks = dense clusters,
//! labelled by the existing theme names.
//!
//! Output: `.ovp/crystal/terrain.json`, a rebuildable PROJECTION (never a
//! ledger). Needs a WARM embedding cache; packs without a cached vector are
//! skipped (no model download here — parity with `crystal-themes`'s cache path).

use std::collections::BTreeMap;
use std::path::Path;

use ovp_domain::VaultLayout;
use ovp_embed::cache as embed_cache;
use ovp_embed::{EMBED_DIM, EMBED_MODEL_ID};
use serde::Serialize;

use crate::commands::crystal_themes::collect_docs;
use crate::CliError;

/// Deterministic layout seed (reproducible terrain across rebuilds).
const SEED: u64 = 42;

pub struct TerrainArgs {
    pub vault_root: std::path::PathBuf,
}

#[derive(Serialize)]
struct TerrainPoint {
    id: String,
    /// Content sha of the source — links the point to its `/library/:sha` page.
    sha: String,
    title: String,
    /// `YYYY-MM-DD` parsed from the pack case_id, else "".
    date: String,
    theme: String,
    x: f64,
    y: f64,
}

/// A community label, treating the unclassified bucket (negative id) as
/// "Unclassified" rather than a raw "Cluster -1".
fn theme_label(id: i64, labels: &BTreeMap<i64, String>) -> String {
    labels.get(&id).cloned().unwrap_or_else(|| {
        if id < 0 {
            "Unclassified".to_string()
        } else {
            format!("Cluster {id}")
        }
    })
}

#[derive(Serialize)]
struct TerrainTheme {
    id: i64,
    label: String,
    /// Centroid of the theme's points (peak/label anchor).
    cx: f64,
    cy: f64,
    count: usize,
}

#[derive(Serialize)]
struct Terrain {
    schema: &'static str,
    model: String,
    point_count: usize,
    bounds: [f64; 4], // [minx, miny, maxx, maxy]
    themes: Vec<TerrainTheme>,
    points: Vec<TerrainPoint>,
}

/// case_id looks like `<sha8>-<YYYY-MM-DD>_<title…>`; pull the date.
fn date_from_case_id(case_id: &str) -> String {
    // after the first '-': "YYYY-MM-DD_..."
    let after = case_id.split_once('-').map(|(_, a)| a).unwrap_or("");
    let date = after.split('_').next().unwrap_or("");
    // must look like a date
    if date.len() == 10 && date.as_bytes().get(4) == Some(&b'-') {
        date.to_string()
    } else {
        String::new()
    }
}

/// Tiny deterministic PRNG (splitmix64) so the layout is reproducible without a
/// rand dependency.
struct Rng(u64);
impl Rng {
    fn next_f64(&mut self) -> f64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        ((z ^ (z >> 31)) >> 11) as f64 / (1u64 << 53) as f64
    }
    fn unit(&mut self) -> f64 {
        self.next_f64() * 2.0 - 1.0
    }
}

/// Mean (L2-normalized) vector per community — the cluster's semantic center.
fn centroid(members: &[usize], vectors: &[Vec<f32>], dim: usize) -> Vec<f32> {
    let mut c = vec![0.0f32; dim];
    for &m in members {
        for (ci, vi) in c.iter_mut().zip(&vectors[m]) {
            *ci += *vi;
        }
    }
    let norm = c.iter().map(|v| v * v).sum::<f32>().sqrt().max(1e-6);
    for v in &mut c {
        *v /= norm;
    }
    c
}

/// Weighted Fruchterman–Reingold over the FEW community centroids: every pair
/// attracts by cosine similarity (similar themes end up adjacent) and repels so
/// they spread into separated islands. Tiny N → many iterations are free.
fn layout_communities(centroids: &[Vec<f32>]) -> Vec<(f64, f64)> {
    let n = centroids.len();
    if n == 0 {
        return vec![];
    }
    if n == 1 {
        return vec![(0.0, 0.0)];
    }
    let mut rng = Rng(SEED);
    // Seed on a circle so they start separated, then relax.
    let mut pos: Vec<(f64, f64)> = (0..n)
        .map(|i| {
            let a = std::f64::consts::TAU * i as f64 / n as f64;
            (a.cos() + 0.01 * rng.unit(), a.sin() + 0.01 * rng.unit())
        })
        .collect();
    let k = 2.0; // target spacing between islands
    let mut temp = 1.0;
    for _ in 0..800 {
        let mut disp = vec![(0.0f64, 0.0f64); n];
        for i in 0..n {
            for j in (i + 1)..n {
                let dx = pos[i].0 - pos[j].0;
                let dy = pos[i].1 - pos[j].1;
                let d = (dx * dx + dy * dy).sqrt().max(1e-6);
                let (ux, uy) = (dx / d, dy / d);
                let repel = k * k / d;
                // Attraction scaled by cosine similarity of the two centroids.
                let sim = ovp_embed::knn::cosine(&centroids[i], &centroids[j]).max(0.0);
                let attract = sim * d * d / k;
                let f = repel - attract;
                disp[i].0 += ux * f;
                disp[i].1 += uy * f;
                disp[j].0 -= ux * f;
                disp[j].1 -= uy * f;
            }
        }
        for i in 0..n {
            let (dx, dy) = disp[i];
            let d = (dx * dx + dy * dy).sqrt().max(1e-9);
            let step = d.min(temp);
            pos[i].0 += dx / d * step;
            pos[i].1 += dy / d * step;
        }
        temp *= 0.995;
    }
    pos
}

/// Rescale positions into a stable [0,100] box (bounds reported for the client).
fn normalize(pos: &mut [(f64, f64)]) -> [f64; 4] {
    if pos.is_empty() {
        return [0.0, 0.0, 100.0, 100.0];
    }
    let (mut minx, mut miny, mut maxx, mut maxy) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
    for &(x, y) in pos.iter() {
        minx = minx.min(x);
        miny = miny.min(y);
        maxx = maxx.max(x);
        maxy = maxy.max(y);
    }
    let sx = if maxx > minx { 100.0 / (maxx - minx) } else { 1.0 };
    let sy = if maxy > miny { 100.0 / (maxy - miny) } else { 1.0 };
    let s = sx.min(sy);
    for p in pos.iter_mut() {
        p.0 = (p.0 - minx) * s;
        p.1 = (p.1 - miny) * s;
    }
    [0.0, 0.0, (maxx - minx) * s, (maxy - miny) * s]
}

pub fn run(args: TerrainArgs) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let reader_root = args.vault_root.join(layout.reader_root());
    let store = args.vault_root.join(layout.crystal_store_dir());
    let themes_path = store.join("themes.json");
    let cache_dir = args.vault_root.join(".ovp/cache/embeddings");

    let themes = ovp_domain::crystal::themes::ThemesFile::load(&themes_path)
        .map_err(|e| CliError::Io(format!("crystal-terrain: reading themes.json: {e}")))?
        .ok_or_else(|| {
            CliError::Io(format!(
                "crystal-terrain: no themes.json at {} — run `ovp2 crystal-themes` first",
                themes_path.display()
            ))
        })?;
    let community_label: BTreeMap<i64, String> = themes
        .communities
        .iter()
        .map(|c| (c.id, c.label.clone()))
        .collect();

    // Keep only packs that are themed AND have a cached vector (no model here).
    let docs = collect_docs(&reader_root)?;
    let mut vectors: Vec<Vec<f32>> = Vec::new();
    let mut meta: Vec<(String, String, String, String, i64)> = Vec::new(); // id,sha,title,date,community
    for d in &docs {
        let Some(&community) = themes.packs.get(&d.case_id) else {
            continue;
        };
        let Some(vec) = embed_cache::load(&cache_dir, &d.sha, EMBED_MODEL_ID, EMBED_DIM) else {
            continue;
        };
        vectors.push(vec);
        meta.push((
            d.case_id.clone(),
            d.sha.clone(),
            d.title.clone(),
            date_from_case_id(&d.case_id),
            community,
        ));
    }
    if vectors.is_empty() {
        println!(
            "crystal-terrain: no themed packs with cached embeddings under {} — nothing to map.",
            reader_root.display()
        );
        return Ok(());
    }

    println!(
        "crystal-terrain: placing {} pack(s) across their theme islands …",
        vectors.len()
    );
    // Group member indices by community; compute per-community centroid.
    let mut by_comm: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    for (i, (_, _, _, _, c)) in meta.iter().enumerate() {
        by_comm.entry(*c).or_default().push(i);
    }
    let comm_ids: Vec<i64> = by_comm.keys().copied().collect();
    let centroids: Vec<Vec<f32>> = comm_ids
        .iter()
        .map(|c| centroid(&by_comm[c], &vectors, EMBED_DIM))
        .collect();
    // Spread the communities into islands (similar themes adjacent).
    let comm_pos = layout_communities(&centroids);

    // Place each member around its island center: radius from how tightly it
    // fits the theme (cosine to centroid — tight → near the peak, loose → rim),
    // angle deterministic per id. Dense centers = the contour peaks.
    let mut pos = vec![(0.0f64, 0.0f64); vectors.len()];
    for (ci, &cid) in comm_ids.iter().enumerate() {
        let members = &by_comm[&cid];
        let (cx, cy) = comm_pos[ci];
        let sims: Vec<f64> = members
            .iter()
            .map(|&m| ovp_embed::knn::cosine(&vectors[m], &centroids[ci]).clamp(-1.0, 1.0))
            .collect();
        let (mut lo, mut hi) = (f64::MAX, f64::MIN);
        for &s in &sims {
            lo = lo.min(s);
            hi = hi.max(s);
        }
        let span = (hi - lo).max(1e-6);
        // Island radius grows with member count (bigger themes = bigger hills).
        let radius = 0.28 * (members.len() as f64).sqrt();
        let mut rng = Rng(SEED ^ (cid as u64).wrapping_mul(0x9E3779B97F4A7C15));
        for (mi, &m) in members.iter().enumerate() {
            let tight = (sims[mi] - lo) / span; // 1 = closest to centroid
            let r = radius * (1.0 - tight).sqrt(); // peak-concentrated
            let theta = std::f64::consts::TAU * rng.next_f64();
            pos[m] = (cx + r * theta.cos(), cy + r * theta.sin());
        }
    }
    let bounds = normalize(&mut pos);

    let points: Vec<TerrainPoint> = meta
        .iter()
        .zip(pos.iter())
        .map(|((id, sha, title, date, community), (x, y))| TerrainPoint {
            id: id.clone(),
            sha: sha.clone(),
            title: title.clone(),
            date: date.clone(),
            theme: theme_label(*community, &community_label),
            x: *x,
            y: *y,
        })
        .collect();
    // Theme label anchor = the normalized island center.
    let mut themes_out: Vec<TerrainTheme> = comm_ids
        .iter()
        .enumerate()
        .map(|(ci, &id)| {
            // Recompute the island center in normalized space from its members.
            let members = &by_comm[&id];
            let (sx, sy) = members
                .iter()
                .fold((0.0, 0.0), |(ax, ay), &m| (ax + pos[m].0, ay + pos[m].1));
            let n = members.len().max(1) as f64;
            let _ = ci;
            TerrainTheme {
                id,
                label: theme_label(id, &community_label),
                cx: sx / n,
                cy: sy / n,
                count: members.len(),
            }
        })
        .collect();
    themes_out.sort_by(|a, b| b.count.cmp(&a.count));

    let terrain = Terrain {
        schema: "ovp.crystal.terrain/v1",
        model: EMBED_MODEL_ID.to_string(),
        point_count: points.len(),
        bounds,
        themes: themes_out,
        points,
    };
    write_terrain(&store, &terrain)?;
    println!(
        "crystal-terrain: {} point(s), {} theme(s) → {}",
        terrain.point_count,
        terrain.themes.len(),
        store.join("terrain.json").display()
    );
    Ok(())
}

fn write_terrain(store: &Path, terrain: &Terrain) -> Result<(), CliError> {
    std::fs::create_dir_all(store)
        .map_err(|e| CliError::Io(format!("crystal-terrain: mkdir {}: {e}", store.display())))?;
    let body = serde_json::to_string(terrain)
        .map_err(|e| CliError::Io(format!("crystal-terrain: serialize: {e}")))?;
    let path = store.join("terrain.json");
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, body)
        .map_err(|e| CliError::Io(format!("crystal-terrain: write {}: {e}", tmp.display())))?;
    std::fs::rename(&tmp, &path)
        .map_err(|e| CliError::Io(format!("crystal-terrain: publish {}: {e}", path.display())))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn date_parse() {
        assert_eq!(
            date_from_case_id("00044cfd-2026-05-07_Claude_Code_x"),
            "2026-05-07"
        );
        assert_eq!(date_from_case_id("nodate_title"), "");
    }

    #[test]
    fn communities_spread_into_separated_islands() {
        // Three orthogonal centroids (maximally dissimilar) must not overlap.
        let centroids = vec![
            vec![1.0f32, 0.0, 0.0],
            vec![0.0f32, 1.0, 0.0],
            vec![0.0f32, 0.0, 1.0],
        ];
        let pos = layout_communities(&centroids);
        for i in 0..3 {
            for j in (i + 1)..3 {
                let d = ((pos[i].0 - pos[j].0).powi(2) + (pos[i].1 - pos[j].1).powi(2)).sqrt();
                assert!(d > 0.5, "islands {i},{j} too close: {d}");
            }
        }
    }

    #[test]
    fn centroid_is_normalized_mean() {
        let vectors = vec![vec![1.0f32, 0.0], vec![0.0f32, 1.0]];
        let c = centroid(&[0, 1], &vectors, 2);
        let norm = (c[0] * c[0] + c[1] * c[1]).sqrt();
        assert!((norm - 1.0).abs() < 1e-5, "centroid must be unit-norm: {norm}");
    }
}
