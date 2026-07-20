//! `crystal-terrain` — a "knowledge terrain" projection (themescape).
//!
//! Reuses the SAME semantic layer as `crystal-themes` (cached multilingual
//! embeddings + the kNN-graph Louvain communities + the community→label
//! assignment in themes.json) and adds ONE thing that view lacks: a 2D position
//! per pack. Layout is TWO-LEVEL and only the coarse level is semantic. First,
//! community centroids are placed by a deterministic force layout that attracts
//! by cosine similarity, so related THEMES form adjacent islands (between-island
//! distance is meaningful). Then, within an island, each pack sits at a radius
//! set by how tightly it fits the theme centroid (tight → the peak, loose → the
//! rim) at a fixed pseudo-random ANGLE. That angle is NOT a pairwise-kNN
//! embedding, so within-island neighbor distance is not semantic — the islands
//! and their density peaks are the signal, not fine pack-to-pack placement.
//! The frontend renders density contours over these points (à la flomo's map) —
//! peaks = dense theme clusters, labelled by the existing theme names.
//!
//! Output: `.ovp/crystal/terrain.json`, a rebuildable PROJECTION (never a
//! ledger). Needs a WARM embedding cache; packs without a cached vector are
//! skipped (no model download here — parity with `crystal-themes`'s cache path).

use std::collections::BTreeMap;
use std::path::Path;

use ovp_domain::VaultLayout;
use ovp_embed::cache as embed_cache;
use ovp_embed::{EMBED_DIM, EMBED_MODEL_ID};
use ovp_index::PackRow;
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
    /// Content sha256 of the SOURCE file — links the point to its
    /// `/library/:sha` page. Read from the daily ledger (not the embedding
    /// cache key); empty when the pack isn't ledger-tracked, so the client
    /// renders an unlinked point instead of a 404.
    sha: String,
    title: String,
    /// `YYYY-MM-DD` parsed from the pack case_id, else "".
    date: String,
    /// English theme label (fallback); the client localizes via `theme_id`.
    theme: String,
    /// Community id — the STABLE key. Two communities may share a display label,
    /// so the client keys visible-theme identity on this, not the label string.
    theme_id: i64,
    x: f64,
    y: f64,
}

/// A community label, treating the unclassified bucket (negative id) as the
/// caller-supplied localized "unclassified" string rather than a raw
/// "Cluster -1". `labels` is the locale-specific community→label map.
fn theme_label(id: i64, labels: &BTreeMap<i64, String>, unclassified: &str) -> String {
    labels.get(&id).cloned().unwrap_or_else(|| {
        if id < 0 {
            unclassified.to_string()
        } else {
            format!("Cluster {id}")
        }
    })
}

#[derive(Serialize)]
struct TerrainTheme {
    id: i64,
    label: String,
    /// Chinese label — the portal picks `label`/`label_zh` by active locale so
    /// terrain labels + tooltips follow the rest of the UI.
    label_zh: String,
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

/// Pull the `YYYY-MM-DD` out of a reader-pack case_id, wherever it sits: modern
/// ids are `<YYYY-MM-DD>_<title>-<sha8>` (date first) and legacy ones are
/// `<sha8>-<YYYY-MM-DD>_<title>` (date after the hash). Scanning byte windows
/// handles both and is safe against multibyte title characters.
fn date_from_case_id(case_id: &str) -> String {
    let b = case_id.as_bytes();
    if b.len() < 10 {
        return String::new();
    }
    for i in 0..=b.len() - 10 {
        let w = &b[i..i + 10];
        if is_iso_date(w) {
            // The window is all-ASCII by construction → direct String.
            return String::from_utf8_lossy(w).into_owned();
        }
    }
    String::new()
}

/// `YYYY-MM-DD` shape check on a 10-byte window (digits + `-` separators).
fn is_iso_date(b: &[u8]) -> bool {
    b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b[..4].iter().all(u8::is_ascii_digit)
        && b[5..7].iter().all(u8::is_ascii_digit)
        && b[8..10].iter().all(u8::is_ascii_digit)
}

/// Map each reader-pack `case_id` (the `pack_dir` basename) → its SOURCE content
/// sha256, from the index's pack list — the SAME join the portal's
/// `sourcesByCase` uses. This is the sha `/library/:sha` serves; the
/// embedding-cache key `collect_docs` uses to load vectors is a different hash.
/// Packs the index has not recorded, and legacy packs with no source sha, are
/// simply absent → the client renders an unlinked point (never a 404).
fn source_shas_by_case(packs: &[PackRow]) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    for p in packs {
        let Some(sha) = &p.source_sha256 else { continue };
        if let Some(case_id) = Path::new(&p.pack_dir).file_name() {
            map.insert(case_id.to_string_lossy().into_owned(), sha.clone());
        }
    }
    map
}

/// Load the case_id → source-sha map from the vault's index. A missing or
/// unreadable index yields an empty map (all points render unlinked, no 404s).
fn load_source_shas(vault_root: &Path) -> BTreeMap<String, String> {
    ovp_index::read_index(vault_root)
        .map(|idx| source_shas_by_case(&idx.packs))
        .unwrap_or_default()
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
    let spanx = maxx - minx;
    let spany = maxy - miny;
    // Single pack (or a collinear layout) has zero span on an axis. Scaling by
    // that would pin every coordinate to 0, which the frontend maps to the map
    // EDGE (-SIZE/2). Center degenerate axes at 50 and report a nonzero bound so
    // the lone hill sits in the middle, not the corner.
    let span = spanx.max(spany);
    if span <= 1e-9 {
        for p in pos.iter_mut() {
            *p = (50.0, 50.0);
        }
        return [0.0, 0.0, 100.0, 100.0];
    }
    let s = 100.0 / span;
    for p in pos.iter_mut() {
        p.0 = if spanx > 1e-9 { (p.0 - minx) * s } else { 50.0 };
        p.1 = if spany > 1e-9 { (p.1 - miny) * s } else { 50.0 };
    }
    let w = if spanx > 1e-9 { spanx * s } else { 100.0 };
    let h = if spany > 1e-9 { spany * s } else { 100.0 };
    [0.0, 0.0, w, h]
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
    let community_label_zh: BTreeMap<i64, String> = themes
        .communities
        .iter()
        .map(|c| (c.id, c.label_zh.clone()))
        .collect();

    // Link sha (for /library/:sha) is the source CONTENT sha, joined from the
    // index by case_id — NOT `d.sha`, which is the embedding-cache key used to
    // load the vector.
    let source_shas = load_source_shas(&args.vault_root);

    // Keep only packs that are themed AND have a cached vector (no model here).
    let docs = collect_docs(&reader_root)?;
    let mut vectors: Vec<Vec<f32>> = Vec::new();
    let mut meta: Vec<(String, String, String, String, i64)> = Vec::new(); // id,link_sha,title,date,community
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
            source_shas.get(&d.case_id).cloned().unwrap_or_default(),
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
    // Group member indices by REAL community (id ≥ 0). Negative ids are the
    // themes contract's noise/singleton bucket — NOT one community. Clustering
    // them would build a synthetic "Unclassified" centroid that, on a sparse
    // corpus, becomes the tallest peak. Collect them separately and scatter them
    // as low-density background so they never form an island.
    let mut by_comm: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    let mut noise: Vec<usize> = Vec::new();
    for (i, (_, _, _, _, c)) in meta.iter().enumerate() {
        if *c < 0 {
            noise.push(i);
        } else {
            by_comm.entry(*c).or_default().push(i);
        }
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
        let range = hi - lo;
        // Every member equally fits the centroid (any 2-member island, or ties):
        // range is 0, so a normalized `tight` would be 0 for all and fling every
        // point to the max radius — a ring with the label between two hills, not
        // one peak. Treat them as equally tight (near the peak) with slight
        // jitter instead.
        let degenerate = range < 1e-6;
        // Island radius grows with member count (bigger themes = bigger hills).
        let radius = 0.28 * (members.len() as f64).sqrt();
        let mut rng = Rng(SEED ^ (cid as u64).wrapping_mul(0x9E3779B97F4A7C15));
        for (mi, &m) in members.iter().enumerate() {
            let r = if degenerate {
                radius * 0.15 * rng.next_f64() // clustered at the peak, lightly spread
            } else {
                let tight = (sims[mi] - lo) / range; // 1 = closest to centroid
                radius * (1.0 - tight).sqrt() // peak-concentrated
            };
            let theta = std::f64::consts::TAU * rng.next_f64();
            pos[m] = (cx + r * theta.cos(), cy + r * theta.sin());
        }
    }
    // Scatter noise packs uniformly across the islands' bounding box (a unit box
    // if there are no real communities). Uniform spread = flat KDE = no peak.
    if !noise.is_empty() {
        let real: Vec<usize> = by_comm.values().flatten().copied().collect();
        let (mut nx0, mut ny0, mut nx1, mut ny1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
        for &m in &real {
            nx0 = nx0.min(pos[m].0);
            ny0 = ny0.min(pos[m].1);
            nx1 = nx1.max(pos[m].0);
            ny1 = ny1.max(pos[m].1);
        }
        if real.is_empty() {
            (nx0, ny0, nx1, ny1) = (-1.0, -1.0, 1.0, 1.0);
        }
        let mut rng = Rng(SEED ^ 0x5EED_1FED_C0FF_EE00);
        for &m in &noise {
            pos[m] = (
                nx0 + rng.next_f64() * (nx1 - nx0),
                ny0 + rng.next_f64() * (ny1 - ny0),
            );
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
            theme: theme_label(*community, &community_label, "Unclassified"),
            theme_id: *community,
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
                label: theme_label(id, &community_label, "Unclassified"),
                label_zh: theme_label(id, &community_label_zh, "未分类"),
                cx: sx / n,
                cy: sy / n,
                count: members.len(),
            }
        })
        .collect();
    themes_out.sort_by_key(|theme| std::cmp::Reverse(theme.count));

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
        // Legacy: <sha8>-<date>_<title>
        assert_eq!(
            date_from_case_id("00044cfd-2026-05-07_Claude_Code_x"),
            "2026-05-07"
        );
        // Modern (VaultLayout::reader_pack_dir): <date>_<title>-<sha8>
        assert_eq!(
            date_from_case_id("2026-05-07_Claude_Code_x-00044cfd"),
            "2026-05-07"
        );
        // Multibyte title must not panic and still finds the date.
        assert_eq!(
            date_from_case_id("2026-05-07_知识地图-00044cfd"),
            "2026-05-07"
        );
        assert_eq!(date_from_case_id("nodate_title"), "");
        assert_eq!(date_from_case_id("short"), "");
    }

    fn pack(pack_dir: &str, sha: Option<&str>) -> PackRow {
        PackRow {
            pack_dir: pack_dir.into(),
            title: "t".into(),
            date: None,
            units: 0,
            cards: 0,
            json_repaired: false,
            card_titles: vec![],
            source_sha256: sha.map(String::from),
        }
    }

    #[test]
    fn source_shas_keyed_by_case_id_basename() {
        let packs = vec![
            // Nested pack_dir → key is the basename (the case_id).
            pack("40-Resources/Reader/2026-05-07_a-1111", Some("sha_a")),
            // Bare case_id also works.
            pack("2026-05-08_b-2222", Some("sha_b")),
            // Legacy pack with no source sha → absent (renders unlinked).
            pack("40-Resources/Reader/2026-05-09_c-3333", None),
        ];
        let map = source_shas_by_case(&packs);
        assert_eq!(map.get("2026-05-07_a-1111").map(String::as_str), Some("sha_a"));
        assert_eq!(map.get("2026-05-08_b-2222").map(String::as_str), Some("sha_b"));
        assert!(!map.contains_key("2026-05-09_c-3333"));
        assert_eq!(map.len(), 2);
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
    fn normalize_centers_degenerate_layouts() {
        // Single point → centered, nonzero bound (not pinned to the edge).
        let mut one = [(7.0, 7.0)];
        let b = normalize(&mut one);
        assert_eq!(one[0], (50.0, 50.0));
        assert!(b[2] > 0.0 && b[3] > 0.0, "bound must be nonzero: {b:?}");
        // Collinear on x (zero y-span) → y centered, x spread across the box.
        let mut row = [(0.0, 5.0), (10.0, 5.0)];
        let bb = normalize(&mut row);
        assert_eq!(row[0].1, 50.0);
        assert_eq!(row[1].1, 50.0);
        assert!((row[0].0 - 0.0).abs() < 1e-9 && (row[1].0 - 100.0).abs() < 1e-9);
        assert!(bb[3] > 0.0, "degenerate y-bound must be nonzero: {bb:?}");
    }

    #[test]
    fn centroid_is_normalized_mean() {
        let vectors = vec![vec![1.0f32, 0.0], vec![0.0f32, 1.0]];
        let c = centroid(&[0, 1], &vectors, 2);
        let norm = (c[0] * c[0] + c[1] * c[1]).sqrt();
        assert!((norm - 1.0).abs() < 1e-5, "centroid must be unit-norm: {norm}");
    }
}
