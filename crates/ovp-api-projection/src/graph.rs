//! Graph assembly for `/api/graph` — density-scoped subgraphs over the
//! crystal ledger.
//!
//! The console never renders the raw tri-partite graph at scale; the server
//! is where information density is managed (M33):
//! - `overview` (default): claims only, ranked by `importance`, capped by
//!   `limit`, with `related` edges among survivors plus per-community
//!   metadata for hull labels.
//! - `neighborhood`: BFS around one focus node, units and sources included —
//!   the "focus" tier the client expands into on click / deep link.
//! - search subgraphs (`search_subgraph`): hit-flagged claims + 1-hop
//!   related context for the tight search layout.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

use ovp_domain::crystal::{DurableRecord, StrengthClass};
use ovp_domain::truncate_chars;
use ovp_index::{EvidenceModel, IndexModel};
use serde::Serialize;

pub const DEFAULT_OVERVIEW_LIMIT: usize = 2000;
pub const MAX_NEIGHBORHOOD_NODES: usize = 300;
pub const MAX_HOPS: usize = 2;
/// Communities returned in the payload (the client draws at most ~20 hulls).
const MAX_COMMUNITIES: usize = 40;
/// A community label needs this theme coverage to stand alone; below it we
/// join the top-2 themes so the hull label doesn't overclaim.
const DOMINANT_THEME_COVERAGE: f64 = 0.4;
/// Node label truncation: claims and unit quotes are clipped for the graph
/// payload (full text lives behind /api/claim/:id).
const MAX_CLAIM_LABEL_LEN: usize = 80;
const TRUNCATED_CLAIM_LABEL_LEN: usize = 77;
const MAX_QUOTE_LABEL_LEN: usize = 60;
const TRUNCATED_QUOTE_LABEL_LEN: usize = 57;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GraphMode {
    Overview,
    Neighborhood,
}

impl GraphMode {
    fn as_str(&self) -> &'static str {
        match self {
            GraphMode::Overview => "overview",
            GraphMode::Neighborhood => "neighborhood",
        }
    }
}

/// Which entity the overview graph is built around. The two views over the same
/// crystal (`?persp=`): `claim` (the default — the knowledge-statement network)
/// and `source` (documents, linked when they are co-cited by a claim). The
/// portal's Knowledge page toggles between them; both share one URL param with
/// the Terrain view.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Perspective {
    #[default]
    Claim,
    Source,
}

#[derive(Debug, Clone)]
pub struct GraphParams {
    pub mode: GraphMode,
    pub limit: usize,
    pub theme: Option<String>,
    pub focus: Option<String>,
    pub hops: usize,
    /// Overview only: claim- vs source-centric layout. Ignored by
    /// `neighborhood` (which always spans all three node types).
    pub persp: Perspective,
}

impl GraphParams {
    /// Parse `/api/graph` query params. Unknown modes and a missing focus for
    /// `neighborhood` are client errors — fail loud, never guess.
    pub fn from_query(params: &HashMap<String, String>) -> Result<Self, GraphError> {
        let mode = match params.get("mode").map(String::as_str) {
            None | Some("overview") => GraphMode::Overview,
            Some("neighborhood") => GraphMode::Neighborhood,
            Some(other) => return Err(GraphError::bad_request(&format!("unknown mode: {other}"))),
        };
        let limit = params
            .get("limit")
            .and_then(|v| v.parse::<usize>().ok())
            .unwrap_or(DEFAULT_OVERVIEW_LIMIT)
            .max(1);
        let focus = params.get("focus").cloned();
        if mode == GraphMode::Neighborhood && focus.is_none() {
            return Err(GraphError::bad_request(
                "mode=neighborhood requires focus=<node-id>",
            ));
        }
        let hops = params
            .get("hops")
            .and_then(|v| v.parse::<usize>().ok())
            .unwrap_or(MAX_HOPS)
            .clamp(1, MAX_HOPS);
        let persp = match params.get("persp").map(String::as_str) {
            None | Some("claim") => Perspective::Claim,
            Some("source") => Perspective::Source,
            Some(other) => {
                return Err(GraphError::bad_request(&format!("unknown persp: {other}")))
            }
        };
        Ok(GraphParams {
            mode,
            limit,
            theme: params.get("theme").cloned(),
            focus,
            hops,
            persp,
        })
    }
}

#[derive(Debug)]
pub struct GraphError {
    pub status: u16,
    pub message: String,
}

impl GraphError {
    fn bad_request(msg: &str) -> Self {
        GraphError {
            status: 400,
            message: msg.to_string(),
        }
    }

    fn not_found(msg: &str) -> Self {
        GraphError {
            status: 404,
            message: msg.to_string(),
        }
    }
}

fn is_false(v: &bool) -> bool {
    !*v
}

#[derive(Debug, Serialize, Clone)]
pub struct GNode {
    pub id: String,
    #[serde(rename = "type")]
    pub node_type: String,
    /// Search mode only: this node matched the query (vs 1-hop context).
    #[serde(skip_serializing_if = "is_false")]
    pub hit: bool,
    pub label: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub theme: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub strength: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    pub degree: usize,
    /// Community id — claims in the same shared-source component get the
    /// same cluster; units/sources inherit their claim's cluster.
    pub cluster: usize,
    /// 0..1 rank signal driving node size and label LOD on the client.
    /// Claims blend hub-ness, provenance, and strength; sources scale with
    /// citing claims; units are always 0 (they only appear in focus tier).
    pub importance: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provenance: Option<f64>,
    /// Claims only: the index/ledger `claim_id` (the identifier the portal
    /// links with — /knowledge#<claim_id>, /api/claim/:id). The node `id`
    /// keeps the deterministic `claim_key` (the graph identity); the two
    /// differ by construction, so the payload carries both.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct GEdge {
    pub source: String,
    pub target: String,
    #[serde(rename = "type")]
    pub edge_type: String,
    /// For `related` (claim↔claim) edges: how many sources the two claims
    /// share. Drives edge thickness in the client.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub weight: Option<usize>,
}

#[derive(Debug, Serialize)]
pub struct Community {
    pub id: usize,
    /// Dominant member theme (top-2 joined when no theme covers ≥40%).
    pub label: String,
    pub size: usize,
    /// Up to 3 member claim ids by importance — hull label anchors.
    pub top_claims: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct GraphResponse {
    pub mode: String,
    pub nodes: Vec<GNode>,
    pub edges: Vec<GEdge>,
    pub communities: Vec<Community>,
    /// Node count of the FULL graph (all types), so the client can say
    /// "showing 2,000 of 45,700".
    pub total_nodes: usize,
    pub truncated: bool,
}

pub fn build_graph(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
    params: &GraphParams,
) -> Result<GraphResponse, GraphError> {
    let mut base = build_base(records, model);
    add_related_edges(&mut base);
    compute_degrees(&mut base);
    assign_clusters(&mut base);
    compute_importance(&mut base, records);

    match params.mode {
        GraphMode::Overview => Ok(match params.persp {
            Perspective::Claim => overview_response(base, params),
            Perspective::Source => source_overview_response(base, params),
        }),
        GraphMode::Neighborhood => neighborhood_response(base, params),
    }
}

/// Everything the mode-specific shaping needs, built once from the ledger.
struct BaseGraph {
    nodes: HashMap<String, GNode>,
    edges: Vec<GEdge>,
    /// claim node id → set of source node ids it draws evidence from.
    /// BTreeMap so related-edge chaining and cluster numbering stay
    /// deterministic run-to-run.
    claim_sources: BTreeMap<String, BTreeSet<String>>,
}

/// Last segment of a vault-relative dir string, tolerant of either
/// separator: an index written on Windows carries `\`, which Unix
/// `Path::file_name` would NOT treat as a separator.
pub(crate) fn last_path_segment(dir: &str) -> Option<&str> {
    dir.rsplit(['/', '\\']).next().filter(|s| !s.is_empty())
}

/// Claim text clipped for the graph payload (full text via /api/claim/:id).
fn claim_label(claim: &str) -> String {
    if claim.chars().count() > MAX_CLAIM_LABEL_LEN {
        format!("{}…", truncate_chars(claim, TRUNCATED_CLAIM_LABEL_LEN))
    } else {
        claim.to_string()
    }
}

fn build_base(records: &[DurableRecord], model: Option<&IndexModel>) -> BaseGraph {
    let source_lookup: HashMap<&str, &ovp_index::SourceRow> = model
        .map(|m| m.sources.iter().map(|s| (s.sha256.as_str(), s)).collect())
        .unwrap_or_default();
    let pack_lookup: HashMap<&str, &ovp_index::PackRow> = model
        .map(|m| {
            m.packs
                .iter()
                .filter_map(|p| Some((last_path_segment(&p.pack_dir)?, p)))
                .collect()
        })
        .unwrap_or_default();

    let mut nodes: HashMap<String, GNode> = HashMap::new();
    let mut edges: Vec<GEdge> = Vec::new();
    let mut claim_sources: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();

    for rec in records {
        let claim_id = format!("claim:{}", rec.claim_key);
        nodes.entry(claim_id.clone()).or_insert_with(|| GNode {
            id: claim_id.clone(),
            node_type: "claim".into(),
            label: claim_label(&rec.claim),
            theme: Some(rec.theme.clone()),
            strength: Some(format!("{:?}", rec.strength).to_lowercase()),
            url: None,
            degree: 0,
            cluster: 0,
            importance: 0.0,
            hit: false,
            provenance: Some(rec.provenance_score),
            claim_id: Some(rec.claim_id.clone()),
        });

        for cit in &rec.citations {
            let unit_id = format!("unit:{}", cit.unit_id);
            nodes.entry(unit_id.clone()).or_insert_with(|| GNode {
                id: unit_id.clone(),
                node_type: "unit".into(),
                label: if cit.quote.chars().count() > MAX_QUOTE_LABEL_LEN {
                    format!("{}…", truncate_chars(&cit.quote, TRUNCATED_QUOTE_LABEL_LEN))
                } else {
                    cit.quote.clone()
                },
                theme: None,
                strength: None,
                url: None,
                degree: 0,
                cluster: 0,
                importance: 0.0,
                hit: false,
                provenance: None,
                claim_id: None,
            });

            edges.push(GEdge {
                source: claim_id.clone(),
                target: unit_id.clone(),
                edge_type: "cites".into(),
                weight: None,
            });

            let source_node_id = if let Some(pack) = pack_lookup.get(cit.case_id.as_str()) {
                let sha = pack.source_sha256.as_deref().unwrap_or(&cit.case_id);
                let sid = format!("source:{}", sha);
                let src = source_lookup.get(sha);
                nodes.entry(sid.clone()).or_insert_with(|| GNode {
                    id: sid.clone(),
                    node_type: "source".into(),
                    label: src
                        .and_then(|s| s.title.clone())
                        .unwrap_or_else(|| pack.title.clone()),
                    theme: None,
                    strength: None,
                    url: src.and_then(|s| s.url.clone()),
                    degree: 0,
                    cluster: 0,
                    importance: 0.0,
                    hit: false,
                    provenance: None,
                    claim_id: None,
                });
                sid
            } else {
                let sid = format!("source:{}", cit.case_id);
                nodes.entry(sid.clone()).or_insert_with(|| GNode {
                    id: sid.clone(),
                    node_type: "source".into(),
                    label: cit.case_id.clone(),
                    theme: None,
                    strength: None,
                    url: None,
                    degree: 0,
                    cluster: 0,
                    importance: 0.0,
                    hit: false,
                    provenance: None,
                    claim_id: None,
                });
                sid
            };

            claim_sources
                .entry(claim_id.clone())
                .or_default()
                .insert(source_node_id.clone());

            edges.push(GEdge {
                source: unit_id,
                target: source_node_id,
                edge_type: "extracted_from".into(),
                weight: None,
            });
        }
    }

    BaseGraph {
        nodes,
        edges,
        claim_sources,
    }
}

/// source node id → claim node ids citing it. Values sorted (deterministic).
fn source_claims_index(base: &BaseGraph) -> BTreeMap<String, Vec<String>> {
    let mut source_claims: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (claim_id, srcs) in &base.claim_sources {
        for s in srcs {
            source_claims
                .entry(s.clone())
                .or_default()
                .push(claim_id.clone());
        }
    }
    source_claims
}

/// `related` edges among the given claim→sources map — the graph's
/// connective tissue. Small sets get exact pairwise edges weighted by the
/// number of shared sources; large sets would blow up O(n²), so we chain
/// each source's claims instead — linear in citations, same connectivity.
///
/// Standalone (not a `BaseGraph` method) so overview can REBUILD edges over
/// the truncated claim set: filtering the full edge list would break chains
/// whose middle claims were dropped and leave the overview fragmented.
fn related_edges(claim_sources: &BTreeMap<String, BTreeSet<String>>) -> Vec<GEdge> {
    let mut edges = Vec::new();
    if claim_sources.len() <= 400 {
        let claim_src_vec: Vec<(&String, &BTreeSet<String>)> = claim_sources.iter().collect();
        for i in 0..claim_src_vec.len() {
            for j in (i + 1)..claim_src_vec.len() {
                let shared = claim_src_vec[i].1.intersection(claim_src_vec[j].1).count();
                if shared > 0 {
                    edges.push(GEdge {
                        source: claim_src_vec[i].0.clone(),
                        target: claim_src_vec[j].0.clone(),
                        edge_type: "related".into(),
                        weight: Some(shared),
                    });
                }
            }
        }
    } else {
        let mut source_claims: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
        for (claim_id, srcs) in claim_sources {
            for s in srcs {
                source_claims.entry(s.as_str()).or_default().push(claim_id);
            }
        }
        let mut seen: HashSet<(String, String)> = HashSet::new();
        for claims in source_claims.values() {
            for w in claims.windows(2) {
                let (a, b) = if w[0] <= w[1] {
                    (w[0].to_string(), w[1].to_string())
                } else {
                    (w[1].to_string(), w[0].to_string())
                };
                if a != b && seen.insert((a.clone(), b.clone())) {
                    edges.push(GEdge {
                        source: a,
                        target: b,
                        edge_type: "related".into(),
                        weight: Some(1),
                    });
                }
            }
        }
    }
    edges
}

fn add_related_edges(base: &mut BaseGraph) {
    let mut edges = related_edges(&base.claim_sources);
    base.edges.append(&mut edges);
}

fn compute_degrees(base: &mut BaseGraph) {
    for i in 0..base.edges.len() {
        let (s, t) = (base.edges[i].source.clone(), base.edges[i].target.clone());
        if let Some(n) = base.nodes.get_mut(&s) {
            n.degree += 1;
        }
        if let Some(n) = base.nodes.get_mut(&t) {
            n.degree += 1;
        }
    }
}

/// Community assignment via weighted label propagation over the claim
/// `related` network — O(edges·iterations). Connected components alone won't
/// do here: a few hub sources merge a dense corpus into one giant blob and
/// the overview hulls become meaningless. Label propagation splits dense
/// components into sub-communities while leaving sparse ones intact.
/// Deterministic: claim ids sorted, ties resolved to the smallest label.
fn assign_clusters(base: &mut BaseGraph) {
    let mut claim_ids: Vec<String> = base
        .nodes
        .values()
        .filter(|n| n.node_type == "claim")
        .map(|n| n.id.clone())
        .collect();
    claim_ids.sort();

    let mut idx: HashMap<&str, usize> = HashMap::new();
    for (i, c) in claim_ids.iter().enumerate() {
        idx.insert(c.as_str(), i);
    }

    let n = claim_ids.len();
    let mut adj: Vec<Vec<(usize, f64)>> = vec![Vec::new(); n];
    for e in &base.edges {
        if e.edge_type != "related" {
            continue;
        }
        if let (Some(&a), Some(&b)) = (idx.get(e.source.as_str()), idx.get(e.target.as_str())) {
            let w = e.weight.unwrap_or(1) as f64;
            adj[a].push((b, w));
            adj[b].push((a, w));
        }
    }

    // Synchronous updates (everyone reads last round's labels) with sticky
    // ties: a node keeps its current label unless a neighbor label strictly
    // outweighs it. In-place updates with a smallest-label tie-break would
    // let one label sweep an entire chain in a single pass and re-create the
    // giant-blob problem. Deterministic: sorted ids, BTreeMap iteration.
    let mut label: Vec<usize> = (0..n).collect();
    for _ in 0..10 {
        let prev = label.clone();
        let mut changed = false;
        for i in 0..n {
            if adj[i].is_empty() {
                continue;
            }
            let mut score: BTreeMap<usize, f64> = BTreeMap::new();
            for &(j, w) in &adj[i] {
                *score.entry(prev[j]).or_default() += w;
            }
            let current_w = score.get(&prev[i]).copied().unwrap_or(0.0);
            let mut best = prev[i];
            let mut best_w = current_w;
            for (&l, &w) in &score {
                if w > best_w {
                    best = l;
                    best_w = w;
                }
            }
            if best != label[i] {
                label[i] = best;
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }

    // Renumber communities 1..k by size desc (ties: smallest member index)
    // so cluster 1 is always the biggest and colors are stable run-to-run.
    let mut members_by_label: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (i, l) in label.iter().enumerate() {
        members_by_label.entry(*l).or_default().push(i);
    }
    let mut ordered: Vec<(usize, Vec<usize>)> = members_by_label.into_iter().collect();
    ordered.sort_by(|a, b| b.1.len().cmp(&a.1.len()).then_with(|| a.0.cmp(&b.0)));

    let mut claim_cluster: HashMap<String, usize> = HashMap::new();
    for (cluster_num, (_, members)) in ordered.into_iter().enumerate() {
        for i in members {
            let c = &claim_ids[i];
            claim_cluster.insert(c.clone(), cluster_num + 1);
            if let Some(node) = base.nodes.get_mut(c) {
                node.cluster = cluster_num + 1;
            }
        }
    }

    // Propagate claim → unit (cites), then unit → source (extracted_from).
    let mut unit_cluster: HashMap<String, usize> = HashMap::new();
    for e in &base.edges {
        if e.edge_type == "cites"
            && let Some(c) = claim_cluster.get(&e.source) {
                unit_cluster.insert(e.target.clone(), *c);
            }
    }
    for (id, c) in &unit_cluster {
        if let Some(n) = base.nodes.get_mut(id) {
            n.cluster = *c;
        }
    }
    let updates: Vec<(String, usize)> = base
        .edges
        .iter()
        .filter(|e| e.edge_type == "extracted_from")
        .filter_map(|e| unit_cluster.get(&e.source).map(|c| (e.target.clone(), *c)))
        .collect();
    for (id, c) in updates {
        if let Some(n) = base.nodes.get_mut(&id) {
            n.cluster = c;
        }
    }
}

fn strength_weight(s: StrengthClass) -> f64 {
    match s {
        StrengthClass::Supported => 1.0,
        StrengthClass::OverSynthesized => 0.5,
        StrengthClass::Overreach => 0.4,
        StrengthClass::OpinionAsFact => 0.3,
    }
}

/// importance = 0.45·norm(ln(1+related_degree)) + 0.35·provenance_score
///            + 0.20·strength_weight — hub-ness in the claim network plus the
/// quality signals already on the record. Sources scale with citing claims.
fn compute_importance(base: &mut BaseGraph, records: &[DurableRecord]) {
    let mut related_degree: HashMap<String, usize> = HashMap::new();
    for e in &base.edges {
        if e.edge_type == "related" {
            *related_degree.entry(e.source.clone()).or_default() += 1;
            *related_degree.entry(e.target.clone()).or_default() += 1;
        }
    }
    let max_related = related_degree.values().copied().max().unwrap_or(0).max(1) as f64;

    let rec_by_id: HashMap<String, &DurableRecord> = records
        .iter()
        .map(|r| (format!("claim:{}", r.claim_key), r))
        .collect();

    let source_claims = source_claims_index(base);
    let max_citing = source_claims
        .values()
        .map(|v| v.len())
        .max()
        .unwrap_or(0)
        .max(1) as f64;

    for node in base.nodes.values_mut() {
        match node.node_type.as_str() {
            "claim" => {
                let deg = *related_degree.get(&node.id).unwrap_or(&0) as f64;
                let hub = (1.0 + deg).ln() / (1.0 + max_related).ln();
                let (prov, strength) = rec_by_id
                    .get(&node.id)
                    .map(|r| {
                        (
                            r.provenance_score.clamp(0.0, 1.0),
                            strength_weight(r.strength),
                        )
                    })
                    .unwrap_or((0.0, 0.0));
                node.importance = 0.45 * hub + 0.35 * prov + 0.20 * strength;
            }
            "source" => {
                let citing = source_claims.get(&node.id).map(|v| v.len()).unwrap_or(0) as f64;
                node.importance = (1.0 + citing).ln() / (1.0 + max_citing).ln();
            }
            _ => node.importance = 0.0,
        }
    }
}

/// Communities among the given claim nodes: dominant-theme label, size, top
/// member claims by importance. Sorted by size desc; ≥2 members; capped.
fn build_communities(claims: &[&GNode]) -> Vec<Community> {
    let mut by_cluster: BTreeMap<usize, Vec<&GNode>> = BTreeMap::new();
    for n in claims {
        if n.cluster > 0 {
            by_cluster.entry(n.cluster).or_default().push(n);
        }
    }

    let mut communities: Vec<Community> = Vec::new();
    for (cluster, mut members) in by_cluster {
        if members.len() < 2 {
            continue;
        }
        members.sort_by(|a, b| {
            b.importance
                .partial_cmp(&a.importance)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.id.cmp(&b.id))
        });

        let mut theme_counts: BTreeMap<&str, usize> = BTreeMap::new();
        for m in &members {
            if let Some(t) = m.theme.as_deref() {
                *theme_counts.entry(t).or_default() += 1;
            }
        }
        let mut themes: Vec<(&str, usize)> = theme_counts.into_iter().collect();
        // Highest count first; ties break lexicographically (deterministic).
        themes.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));

        let label = match themes.as_slice() {
            [] => format!("community {cluster}"),
            [(t, _)] => (*t).to_string(),
            [(t1, c1), (t2, _), ..] => {
                if (*c1 as f64) / (members.len() as f64) < DOMINANT_THEME_COVERAGE {
                    format!("{t1} / {t2}")
                } else {
                    (*t1).to_string()
                }
            }
        };

        communities.push(Community {
            id: cluster,
            label,
            size: members.len(),
            top_claims: members.iter().take(3).map(|m| m.id.clone()).collect(),
        });
    }

    communities.sort_by(|a, b| b.size.cmp(&a.size).then_with(|| a.id.cmp(&b.id)));
    communities.truncate(MAX_COMMUNITIES);
    communities
}

fn sort_by_importance(nodes: &mut [GNode]) {
    nodes.sort_by(|a, b| {
        b.importance
            .partial_cmp(&a.importance)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
}

fn overview_response(base: BaseGraph, params: &GraphParams) -> GraphResponse {
    let total_nodes = base.nodes.len();

    let mut claims: Vec<GNode> = base
        .nodes
        .values()
        .filter(|n| n.node_type == "claim")
        .filter(|n| match &params.theme {
            Some(t) => n.theme.as_deref() == Some(t.as_str()),
            None => true,
        })
        .cloned()
        .collect();
    sort_by_importance(&mut claims);

    let matching = claims.len();
    claims.truncate(params.limit);
    let kept: HashSet<&str> = claims.iter().map(|n| n.id.as_str()).collect();

    // Rebuild `related` edges over the surviving claims only — filtering the
    // full edge list would break chains at dropped claims and shatter the
    // overview into fragments.
    let kept_sources: BTreeMap<String, BTreeSet<String>> = base
        .claim_sources
        .iter()
        .filter(|(c, _)| kept.contains(c.as_str()))
        .map(|(c, s)| (c.clone(), s.clone()))
        .collect();
    let edges = related_edges(&kept_sources);

    let claim_refs: Vec<&GNode> = claims.iter().collect();
    let communities = build_communities(&claim_refs);

    GraphResponse {
        mode: GraphMode::Overview.as_str().into(),
        nodes: claims,
        edges,
        communities,
        total_nodes,
        truncated: matching > params.limit,
    }
}

/// Source perspective of the overview: nodes are the cited SOURCES (ranked by
/// citing-claim count via `importance`, capped by `limit`), linked when two
/// sources are co-cited by the same claim (weight = shared-claim count). The
/// same crystal, seen document-first instead of statement-first — the portal's
/// `?persp=source` toggle. Communities reuse the propagated cluster ids, labeled
/// by the dominant theme among each cluster's citing claims.
fn source_overview_response(base: BaseGraph, params: &GraphParams) -> GraphResponse {
    let total_nodes = base.nodes.len();
    let source_claims = source_claims_index(&base);

    // Optional theme filter: keep a source only if some claim of that theme
    // cites it (mirrors the claim overview's `theme` filter).
    let theme_ok = |sid: &str| -> bool {
        match &params.theme {
            None => true,
            Some(t) => source_claims
                .get(sid)
                .map(|claims| {
                    claims.iter().any(|c| {
                        base.nodes.get(c).and_then(|n| n.theme.as_deref()) == Some(t.as_str())
                    })
                })
                .unwrap_or(false),
        }
    };

    let mut sources: Vec<GNode> = base
        .nodes
        .values()
        .filter(|n| n.node_type == "source")
        .filter(|n| theme_ok(&n.id))
        .cloned()
        .collect();
    sort_by_importance(&mut sources);
    let matching = sources.len();
    sources.truncate(params.limit);
    let kept: HashSet<&str> = sources.iter().map(|n| n.id.as_str()).collect();

    let edges = shared_claim_edges(&base.claim_sources, &kept);
    let source_refs: Vec<&GNode> = sources.iter().collect();
    let communities = build_source_communities(&source_refs, &source_claims, &base);

    GraphResponse {
        mode: GraphMode::Overview.as_str().into(),
        nodes: sources,
        edges,
        communities,
        total_nodes,
        truncated: matching > params.limit,
    }
}

/// Source↔source edges for the source perspective: two sources are linked when
/// the same claim cites both; weight = number of such shared claims. Only edges
/// among `kept` sources are emitted. Deterministic (sorted pair keys). A claim's
/// cited-source set is small in practice, so per-claim pairwise is cheap.
fn shared_claim_edges(
    claim_sources: &BTreeMap<String, BTreeSet<String>>,
    kept: &HashSet<&str>,
) -> Vec<GEdge> {
    let mut pair_weight: BTreeMap<(String, String), usize> = BTreeMap::new();
    for srcs in claim_sources.values() {
        // BTreeSet iterates sorted, so (i < j) already yields (a <= b).
        let members: Vec<&String> = srcs.iter().filter(|s| kept.contains(s.as_str())).collect();
        for i in 0..members.len() {
            for j in (i + 1)..members.len() {
                *pair_weight
                    .entry((members[i].clone(), members[j].clone()))
                    .or_default() += 1;
            }
        }
    }
    pair_weight
        .into_iter()
        .map(|((a, b), w)| GEdge {
            source: a,
            target: b,
            edge_type: "related".into(),
            weight: Some(w),
        })
        .collect()
}

/// Communities for the source perspective: group kept sources by their
/// propagated cluster id (≥2 members), labeled by the dominant theme among the
/// claims that cite them (same coverage rule as claim communities). `top_claims`
/// carries the top source ids by importance — the field is a generic hull
/// anchor, not claim-specific.
fn build_source_communities(
    sources: &[&GNode],
    source_claims: &BTreeMap<String, Vec<String>>,
    base: &BaseGraph,
) -> Vec<Community> {
    let mut by_cluster: BTreeMap<usize, Vec<&GNode>> = BTreeMap::new();
    for n in sources {
        if n.cluster > 0 {
            by_cluster.entry(n.cluster).or_default().push(n);
        }
    }

    let mut communities: Vec<Community> = Vec::new();
    for (cluster, mut members) in by_cluster {
        if members.len() < 2 {
            continue;
        }
        members.sort_by(|a, b| {
            b.importance
                .partial_cmp(&a.importance)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.id.cmp(&b.id))
        });

        // Tally themes over the citing claims of every member source.
        let mut theme_counts: BTreeMap<&str, usize> = BTreeMap::new();
        for m in &members {
            if let Some(claims) = source_claims.get(&m.id) {
                for c in claims {
                    if let Some(t) = base.nodes.get(c).and_then(|n| n.theme.as_deref()) {
                        *theme_counts.entry(t).or_default() += 1;
                    }
                }
            }
        }
        let total: usize = theme_counts.values().sum();
        let mut themes: Vec<(&str, usize)> = theme_counts.into_iter().collect();
        themes.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));

        let label = match themes.as_slice() {
            [] => format!("community {cluster}"),
            [(t, _)] => (*t).to_string(),
            [(t1, c1), (t2, _), ..] => {
                if total == 0 || (*c1 as f64) / (total as f64) < DOMINANT_THEME_COVERAGE {
                    format!("{t1} / {t2}")
                } else {
                    (*t1).to_string()
                }
            }
        };

        communities.push(Community {
            id: cluster,
            label,
            size: members.len(),
            top_claims: members.iter().take(3).map(|m| m.id.clone()).collect(),
        });
    }

    communities.sort_by(|a, b| b.size.cmp(&a.size).then_with(|| a.id.cmp(&b.id)));
    communities.truncate(MAX_COMMUNITIES);
    communities
}

fn neighborhood_response(
    base: BaseGraph,
    params: &GraphParams,
) -> Result<GraphResponse, GraphError> {
    let focus = params.focus.as_deref().unwrap_or_default();
    if !base.nodes.contains_key(focus) {
        return Err(GraphError::not_found(&format!("node not found: {focus}")));
    }

    let mut adjacency: HashMap<&str, Vec<&str>> = HashMap::new();
    for e in &base.edges {
        adjacency
            .entry(e.source.as_str())
            .or_default()
            .push(e.target.as_str());
        adjacency
            .entry(e.target.as_str())
            .or_default()
            .push(e.source.as_str());
    }

    // BFS layer by layer; within a layer higher-importance nodes win the cap.
    let mut layer_of: HashMap<&str, usize> = HashMap::new();
    layer_of.insert(focus, 0);
    let mut queue: VecDeque<(&str, usize)> = VecDeque::new();
    queue.push_back((focus, 0));
    while let Some((id, layer)) = queue.pop_front() {
        if layer >= params.hops {
            continue;
        }
        if let Some(next) = adjacency.get(id) {
            for n in next {
                if !layer_of.contains_key(n) {
                    layer_of.insert(n, layer + 1);
                    queue.push_back((n, layer + 1));
                }
            }
        }
    }

    let reachable = layer_of.len();
    let mut by_layer: BTreeMap<usize, Vec<&str>> = BTreeMap::new();
    for (id, layer) in &layer_of {
        by_layer.entry(*layer).or_default().push(id);
    }

    // The focus node's own citation chain (its units, and their sources) is
    // the point of the focus tier — reserve it BEFORE the importance-ranked
    // cap. Otherwise a hub claim's high-importance `related` neighbors can
    // fill all slots and evict the selected claim's own evidence.
    let mut kept: HashSet<&str> = HashSet::new();
    kept.insert(focus);
    let mut focus_units: HashSet<&str> = HashSet::new();
    for e in &base.edges {
        if e.edge_type == "cites" && e.source == focus {
            focus_units.insert(e.target.as_str());
        }
    }
    for u in &focus_units {
        if kept.len() >= MAX_NEIGHBORHOOD_NODES {
            break;
        }
        kept.insert(u);
    }
    for e in &base.edges {
        if kept.len() >= MAX_NEIGHBORHOOD_NODES {
            break;
        }
        if e.edge_type == "extracted_from" && focus_units.contains(e.source.as_str()) {
            kept.insert(e.target.as_str());
        }
    }

    'outer: for (_, mut ids) in by_layer {
        ids.sort_by(|a, b| {
            let ia = base.nodes.get(*a).map(|n| n.importance).unwrap_or(0.0);
            let ib = base.nodes.get(*b).map(|n| n.importance).unwrap_or(0.0);
            ib.partial_cmp(&ia)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.cmp(b))
        });
        for id in ids {
            if kept.len() >= MAX_NEIGHBORHOOD_NODES {
                break 'outer;
            }
            kept.insert(id);
        }
    }

    let mut nodes: Vec<GNode> = base
        .nodes
        .values()
        .filter(|n| kept.contains(n.id.as_str()))
        .cloned()
        .collect();
    sort_by_importance(&mut nodes);

    let edges: Vec<GEdge> = base
        .edges
        .iter()
        .filter(|e| kept.contains(e.source.as_str()) && kept.contains(e.target.as_str()))
        .cloned()
        .collect();

    let claim_refs: Vec<&GNode> = nodes.iter().filter(|n| n.node_type == "claim").collect();
    let communities = build_communities(&claim_refs);
    let truncated = reachable > kept.len();
    let total_nodes = base.nodes.len();

    Ok(GraphResponse {
        mode: GraphMode::Neighborhood.as_str().into(),
        nodes,
        edges,
        communities,
        total_nodes,
        truncated,
    })
}

/// The focus source's memory-layer cards from the evidence sidecar, as graph
/// nodes (B5, operator finding 2026-07-09: 72% of sources have no citing
/// claims — without the memory layer their neighborhood rendered a single
/// lonely node). Row ids already carry the `card:` prefix
/// (`card:<pack_dir>:<idx>`), so they double as graph node ids. Matching
/// mirrors /api/source/:sha: rows keyed by the source sha OR its pack dir.
fn memory_card_nodes(
    evidence: Option<&EvidenceModel>,
    model: Option<&IndexModel>,
    sha: &str,
) -> Vec<GNode> {
    let Some(evidence) = evidence else {
        return Vec::new();
    };
    let pack_dir = model.and_then(|m| {
        m.sources
            .iter()
            .find(|s| s.sha256 == sha)
            .and_then(|s| s.pack_dir.as_deref())
    });
    evidence
        .cards
        .iter()
        .filter(|c| {
            c.source_sha256.as_deref() == Some(sha) || pack_dir == Some(c.pack_dir.as_str())
        })
        .map(|c| GNode {
            id: c.id.clone(),
            node_type: "card".into(),
            label: if c.title.chars().count() > MAX_QUOTE_LABEL_LEN {
                format!("{}…", truncate_chars(&c.title, TRUNCATED_QUOTE_LABEL_LEN))
            } else {
                c.title.clone()
            },
            theme: None,
            strength: None,
            url: None,
            degree: 1,
            cluster: 0,
            importance: 0.0,
            hit: false,
            provenance: None,
            claim_id: None,
        })
        .collect()
}

/// Source-centric neighborhood for the portal's KnowledgeGraph component
/// (design §4, `scope=neighborhood&source=<sha>`): the source node, its
/// memory-layer cards (`has_memory` edges), claims citing it, and the
/// sibling sources those claims also draw from. Units are deliberately
/// excluded — the source detail page shows them as text; the graph tells
/// the memory/claim/sibling story.
///
/// Cap accounting: claims (with their sibling sources) take precedence,
/// then cards fill the remaining budget. A source known to the index but
/// cited by no claim returns its focus node plus cards (the page shows the
/// memory layer instead of a single lonely node); an entirely unknown sha
/// is a 404.
pub fn source_neighborhood(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
    evidence: Option<&EvidenceModel>,
    sha: &str,
) -> Result<GraphResponse, GraphError> {
    let mut base = build_base(records, model);
    add_related_edges(&mut base);
    compute_degrees(&mut base);
    assign_clusters(&mut base);
    compute_importance(&mut base, records);

    let focus_id = format!("source:{sha}");

    // Claims citing this source, importance-ranked so a hub source keeps its
    // strongest claims under the node cap.
    let mut citing: Vec<&String> = base
        .claim_sources
        .iter()
        .filter(|(_, srcs)| srcs.contains(&focus_id))
        .map(|(claim, _)| claim)
        .collect();
    citing.sort_by(|a, b| {
        let ia = base.nodes.get(*a).map(|n| n.importance).unwrap_or(0.0);
        let ib = base.nodes.get(*b).map(|n| n.importance).unwrap_or(0.0);
        ib.partial_cmp(&ia)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.cmp(b))
    });

    // Memory-layer cards for the focus source; claims win the cap first,
    // cards fill the remainder (see the fn docs).
    let cards = memory_card_nodes(evidence, model, sha);
    let total_cards = cards.len();

    if citing.is_empty() && !base.nodes.contains_key(&focus_id) {
        // Not in the crystal graph at all — fall back to the index so a
        // freshly processed (or blocked) source still gets its focus node,
        // plus its cards: an uncited source shows its memory layer instead
        // of a single lonely node.
        let Some(src) = model.and_then(|m| m.sources.iter().find(|s| s.sha256 == sha)) else {
            return Err(GraphError::not_found(&format!("source not found: {sha}")));
        };
        let node = GNode {
            id: focus_id.clone(),
            node_type: "source".into(),
            label: src.title.clone().unwrap_or_else(|| sha.to_string()),
            theme: None,
            strength: None,
            url: src.url.clone(),
            degree: 0,
            cluster: 0,
            importance: 1.0,
            hit: false,
            provenance: None,
            claim_id: None,
        };
        let mut nodes = vec![node];
        let budget = MAX_NEIGHBORHOOD_NODES.saturating_sub(nodes.len());
        let truncated = total_cards > budget;
        nodes.extend(cards.into_iter().take(budget));
        let edges: Vec<GEdge> = nodes[1..]
            .iter()
            .map(|card| GEdge {
                source: focus_id.clone(),
                target: card.id.clone(),
                edge_type: "has_memory".into(),
                weight: None,
            })
            .collect();
        return Ok(GraphResponse {
            mode: GraphMode::Neighborhood.as_str().into(),
            nodes,
            edges,
            communities: Vec::new(),
            total_nodes: base.nodes.len() + 1 + total_cards,
            truncated,
        });
    }

    // Keep focus + claims + their sibling sources under the shared cap.
    let mut kept: BTreeSet<&str> = BTreeSet::new();
    kept.insert(focus_id.as_str());
    let mut truncated = false;
    for claim in &citing {
        let srcs = &base.claim_sources[claim.as_str()];
        // +1 for the claim itself; siblings may already be kept.
        let new_sources = srcs.iter().filter(|s| !kept.contains(s.as_str())).count();
        if kept.len() + 1 + new_sources > MAX_NEIGHBORHOOD_NODES {
            truncated = true;
            break;
        }
        kept.insert(claim.as_str());
        for s in srcs {
            kept.insert(s.as_str());
        }
    }

    let mut nodes: Vec<GNode> = base
        .nodes
        .values()
        .filter(|n| kept.contains(n.id.as_str()))
        .cloned()
        .collect();
    sort_by_importance(&mut nodes);

    // Bipartite claim→source edges (`cites`): the units in between are
    // collapsed for this compact view.
    let mut edges: Vec<GEdge> = Vec::new();
    for (claim, srcs) in &base.claim_sources {
        if !kept.contains(claim.as_str()) {
            continue;
        }
        for s in srcs {
            if kept.contains(s.as_str()) {
                edges.push(GEdge {
                    source: claim.clone(),
                    target: s.clone(),
                    edge_type: "cites".into(),
                    weight: None,
                });
            }
        }
    }

    let claim_refs: Vec<&GNode> = nodes.iter().filter(|n| n.node_type == "claim").collect();
    let communities = build_communities(&claim_refs);
    let total_nodes = base.nodes.len() + total_cards;

    // Cards last: claims and their sibling sources already won the cap.
    let budget = MAX_NEIGHBORHOOD_NODES.saturating_sub(nodes.len());
    if total_cards > budget {
        truncated = true;
    }
    for card in cards.into_iter().take(budget) {
        edges.push(GEdge {
            source: focus_id.clone(),
            target: card.id.clone(),
            edge_type: "has_memory".into(),
            weight: None,
        });
        nodes.push(card);
    }

    Ok(GraphResponse {
        mode: GraphMode::Neighborhood.as_str().into(),
        nodes,
        edges,
        communities,
        total_nodes,
        truncated,
    })
}

/// A caveated claim merged into the theme subgraph from the index model,
/// with the source node ids it cites.
struct ExtraClaim {
    node: GNode,
    sources: BTreeSet<String>,
}

/// Caveated claims for `theme` from the index model — they live in
/// review.json (indexed as ClaimRow), never in the ledger, so the
/// ledger-built base graph cannot see them. Returns synthetic claim nodes
/// (deduped against ledger records by claim_id) plus any cited source nodes
/// the base graph doesn't already contain. Deterministic: sorted by node id.
fn caveated_theme_claims(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
    theme: &str,
    base: &BaseGraph,
) -> (Vec<ExtraClaim>, HashMap<String, GNode>) {
    let Some(model) = model else {
        return (Vec::new(), HashMap::new());
    };
    let ledger_ids: HashSet<&str> = records
        .iter()
        .filter(|r| r.theme == theme)
        .map(|r| r.claim_id.as_str())
        .collect();
    let source_lookup: HashMap<&str, &ovp_index::SourceRow> =
        model.sources.iter().map(|s| (s.sha256.as_str(), s)).collect();
    let pack_lookup: HashMap<&str, &ovp_index::PackRow> = model
        .packs
        .iter()
        .filter_map(|p| Some((last_path_segment(&p.pack_dir)?, p)))
        .collect();

    let mut extras: Vec<ExtraClaim> = Vec::new();
    let mut synthetic: HashMap<String, GNode> = HashMap::new();
    for row in &model.claims {
        if row.status != ovp_index::ClaimStatus::Caveated
            || row.theme.as_deref() != Some(theme)
            || ledger_ids.contains(row.claim_id.as_str())
        {
            continue;
        }
        let node_id = format!("claim:{}", row.claim_id);
        if base.nodes.contains_key(&node_id) {
            continue;
        }
        let mut sources = BTreeSet::new();
        for case in &row.sources {
            // Same node-id rule as build_base: pack → sha when known, else
            // the raw case id (the client's sha-guard treats it as legacy).
            let (sid, label, url) = match pack_lookup.get(case.as_str()) {
                Some(pack) => {
                    let sha = pack.source_sha256.as_deref().unwrap_or(case);
                    let src = source_lookup.get(sha);
                    (
                        format!("source:{sha}"),
                        src.and_then(|s| s.title.clone())
                            .unwrap_or_else(|| pack.title.clone()),
                        src.and_then(|s| s.url.clone()),
                    )
                }
                None => (format!("source:{case}"), case.clone(), None),
            };
            if !base.nodes.contains_key(&sid) {
                synthetic.entry(sid.clone()).or_insert_with(|| GNode {
                    id: sid.clone(),
                    node_type: "source".into(),
                    label,
                    theme: None,
                    strength: None,
                    url,
                    degree: 0,
                    cluster: 0,
                    importance: 0.0,
                    hit: false,
                    provenance: None,
                    claim_id: None,
                });
            }
            sources.insert(sid);
        }
        extras.push(ExtraClaim {
            node: GNode {
                id: node_id.clone(),
                node_type: "claim".into(),
                label: claim_label(&row.claim),
                theme: Some(theme.to_string()),
                strength: row.strength.clone(),
                url: None,
                degree: sources.len(),
                cluster: 0,
                // Caveated claims rank below every durable claim: no
                // provenance/hub signal exists for them in the ledger.
                importance: 0.0,
                hit: false,
                provenance: None,
                claim_id: Some(row.claim_id.clone()),
            },
            sources,
        });
    }
    extras.sort_by(|a, b| a.node.id.cmp(&b.node.id));
    (extras, synthetic)
}

/// Theme-scoped subgraph for the portal's KnowledgeGraph component
/// (design §4, `scope=theme&theme=<t>`): the theme's claims plus the sources
/// they draw evidence from. Durable claims come from the ledger; caveated
/// claims live only in the index (review.json) and are merged in so a
/// caveated-only theme still gets a graph rail. Edges are bipartite
/// claim→source `cites` (units collapsed, same compact view as
/// `source_neighborhood`) plus `related` edges among the theme's ledger
/// claims. A theme neither layer knows is a 404 — fail loud, never render
/// an empty rail for a typo.
pub fn theme_subgraph(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
    theme: &str,
) -> Result<GraphResponse, GraphError> {
    let mut base = build_base(records, model);
    add_related_edges(&mut base);
    compute_degrees(&mut base);
    assign_clusters(&mut base);
    compute_importance(&mut base, records);

    // Ledger claims for the theme, importance-ranked so a huge theme keeps
    // its strongest claims under the shared node cap.
    let mut ledger_claims: Vec<(String, f64)> = base
        .nodes
        .values()
        .filter(|n| n.node_type == "claim" && n.theme.as_deref() == Some(theme))
        .map(|n| (n.id.clone(), n.importance))
        .collect();
    ledger_claims.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.0.cmp(&b.0))
    });

    let (extras, mut synthetic_sources) = caveated_theme_claims(records, model, theme, &base);

    if ledger_claims.is_empty() && extras.is_empty() {
        return Err(GraphError::not_found(&format!("theme not found: {theme}")));
    }

    // Keep claims + their sources under the shared cap — same accounting as
    // source_neighborhood: a claim only enters with its whole citation set.
    // Ledger (durable) claims fill first; caveated extras follow.
    let mut kept: BTreeSet<String> = BTreeSet::new();
    let mut truncated = false;
    let empty = BTreeSet::new();
    for (claim, _) in &ledger_claims {
        let srcs = base.claim_sources.get(claim.as_str()).unwrap_or(&empty);
        let new_sources = srcs.iter().filter(|s| !kept.contains(s.as_str())).count();
        if kept.len() + 1 + new_sources > MAX_NEIGHBORHOOD_NODES {
            truncated = true;
            break;
        }
        kept.insert(claim.clone());
        for s in srcs {
            kept.insert(s.clone());
        }
    }
    let mut kept_extras: Vec<&ExtraClaim> = Vec::new();
    for extra in &extras {
        if truncated {
            break;
        }
        let new_sources = extra
            .sources
            .iter()
            .filter(|s| !kept.contains(s.as_str()))
            .count();
        if kept.len() + 1 + new_sources > MAX_NEIGHBORHOOD_NODES {
            truncated = true;
            break;
        }
        kept.insert(extra.node.id.clone());
        for s in &extra.sources {
            kept.insert(s.clone());
        }
        kept_extras.push(extra);
    }

    let mut nodes: Vec<GNode> = base
        .nodes
        .values()
        .filter(|n| kept.contains(n.id.as_str()))
        .cloned()
        .collect();
    nodes.extend(kept_extras.iter().map(|e| e.node.clone()));
    synthetic_sources.retain(|id, _| kept.contains(id.as_str()));
    nodes.extend(synthetic_sources.into_values());
    sort_by_importance(&mut nodes);

    // Bipartite claim→source `cites` (units collapsed) …
    let mut edges: Vec<GEdge> = Vec::new();
    for (claim, srcs) in &base.claim_sources {
        if !kept.contains(claim.as_str()) {
            continue;
        }
        for s in srcs {
            if kept.contains(s.as_str()) {
                edges.push(GEdge {
                    source: claim.clone(),
                    target: s.clone(),
                    edge_type: "cites".into(),
                    weight: None,
                });
            }
        }
    }
    for extra in &kept_extras {
        for s in &extra.sources {
            if kept.contains(s.as_str()) {
                edges.push(GEdge {
                    source: extra.node.id.clone(),
                    target: s.clone(),
                    edge_type: "cites".into(),
                    weight: None,
                });
            }
        }
    }
    // …plus `related` connectivity among the kept ledger claims, rebuilt
    // over the kept subset so weights stay exact.
    let kept_sources: BTreeMap<String, BTreeSet<String>> = base
        .claim_sources
        .iter()
        .filter(|(c, _)| kept.contains(c.as_str()))
        .map(|(c, s)| (c.clone(), s.clone()))
        .collect();
    edges.extend(related_edges(&kept_sources));

    let claim_refs: Vec<&GNode> = nodes.iter().filter(|n| n.node_type == "claim").collect();
    let communities = build_communities(&claim_refs);
    let total_nodes = base.nodes.len();

    Ok(GraphResponse {
        mode: "theme".into(),
        nodes,
        edges,
        communities,
        total_nodes,
        truncated,
    })
}

pub const MAX_SEARCH_HITS: usize = 40;
const MAX_SEARCH_CONTEXT: usize = 80;

/// Search-mode subgraph: claims matching `query` (case-insensitive over
/// claim text, theme, and claim key), flagged `hit`, plus up to
/// MAX_SEARCH_CONTEXT 1-hop `related` context claims — the ≤40-node tight
/// layout scenario. Matching runs over the ledger directly (the index's
/// query hits are display strings without structured ids).
pub fn search_subgraph(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
    query: &str,
) -> GraphResponse {
    let mut base = build_base(records, model);
    add_related_edges(&mut base);
    compute_degrees(&mut base);
    assign_clusters(&mut base);
    compute_importance(&mut base, records);

    let needle = query.to_lowercase();
    let mut matching: Vec<(&DurableRecord, f64)> = records
        .iter()
        .filter(|r| {
            r.claim.to_lowercase().contains(&needle)
                || r.theme.to_lowercase().contains(&needle)
                || r.claim_key.to_lowercase().contains(&needle)
        })
        .map(|r| {
            let id = format!("claim:{}", r.claim_key);
            let imp = base.nodes.get(&id).map(|n| n.importance).unwrap_or(0.0);
            (r, imp)
        })
        .collect();
    matching.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.0.claim_key.cmp(&b.0.claim_key))
    });
    let total_matches = matching.len();
    matching.truncate(MAX_SEARCH_HITS);

    let hit_ids: HashSet<String> = matching
        .iter()
        .map(|(r, _)| format!("claim:{}", r.claim_key))
        .collect();

    // 1-hop related context around the hits, importance-ranked, capped.
    let mut context_ids: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = hit_ids.clone();
    for e in &base.edges {
        if e.edge_type != "related" {
            continue;
        }
        let other = if hit_ids.contains(e.source.as_str()) {
            &e.target
        } else if hit_ids.contains(e.target.as_str()) {
            &e.source
        } else {
            continue;
        };
        if seen.insert(other.clone()) {
            context_ids.push(other.clone());
        }
    }
    context_ids.sort_by(|a, b| {
        let ia = base.nodes.get(a).map(|n| n.importance).unwrap_or(0.0);
        let ib = base.nodes.get(b).map(|n| n.importance).unwrap_or(0.0);
        ib.partial_cmp(&ia)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.cmp(b))
    });
    context_ids.truncate(MAX_SEARCH_CONTEXT);

    let mut nodes: Vec<GNode> = hit_ids
        .iter()
        .chain(context_ids.iter())
        .filter_map(|id| base.nodes.get(id).cloned())
        .map(|mut n| {
            n.hit = hit_ids.contains(n.id.as_str());
            n
        })
        .collect();
    sort_by_importance(&mut nodes);

    let kept: HashSet<&str> = nodes.iter().map(|n| n.id.as_str()).collect();
    let edges: Vec<GEdge> = base
        .edges
        .iter()
        .filter(|e| {
            e.edge_type == "related"
                && kept.contains(e.source.as_str())
                && kept.contains(e.target.as_str())
        })
        .cloned()
        .collect();

    let claim_refs: Vec<&GNode> = nodes.iter().collect();
    let communities = build_communities(&claim_refs);
    let total_nodes = base.nodes.len();

    GraphResponse {
        mode: "search".into(),
        nodes,
        edges,
        communities,
        total_nodes,
        truncated: total_matches > MAX_SEARCH_HITS,
    }
}

/// Theme histogram over active records — feeds the theme filter dropdown.
pub fn theme_counts(records: &[DurableRecord]) -> Vec<(String, usize)> {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for r in records {
        *counts.entry(r.theme.as_str()).or_default() += 1;
    }
    let mut v: Vec<(String, usize)> = counts
        .into_iter()
        .map(|(t, c)| (t.to_string(), c))
        .collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    v
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{CrystalStatus, DurableCitation, FinalClass, ProvenanceClass};

    fn rec(
        key: &str,
        theme: &str,
        strength: StrengthClass,
        prov: f64,
        cites: &[(&str, &str)],
    ) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: format!("id-{key}"),
            claim: format!("claim text for {key}"),
            theme: theme.into(),
            source_cases: cites.iter().map(|(c, _)| (*c).to_string()).collect(),
            citations: cites
                .iter()
                .map(|(c, u)| DurableCitation {
                    case_id: (*c).into(),
                    unit_id: (*u).into(),
                    quote: format!("quote {u}"),
                    resolved_line: None,
                })
                .collect(),
            provenance_score: prov,
            provenance_class: ProvenanceClass::Durable,
            strength,
            strength_rationale: "test".into(),
            final_class: FinalClass::Durable,
            run_id: "r1".into(),
            status: CrystalStatus::Active,
        }
    }

    fn params(mode: GraphMode) -> GraphParams {
        GraphParams {
            mode,
            limit: DEFAULT_OVERVIEW_LIMIT,
            theme: None,
            focus: None,
            hops: MAX_HOPS,
            persp: Perspective::Claim,
        }
    }

    /// Three claims on a shared source + one isolated claim.
    fn sample_records() -> Vec<DurableRecord> {
        vec![
            rec(
                "a",
                "alpha",
                StrengthClass::Supported,
                0.9,
                &[("case1", "u1"), ("case2", "u2")],
            ),
            rec(
                "b",
                "alpha",
                StrengthClass::Supported,
                0.8,
                &[("case1", "u3")],
            ),
            rec(
                "c",
                "beta",
                StrengthClass::Overreach,
                0.6,
                &[("case1", "u4")],
            ),
            rec(
                "d",
                "gamma",
                StrengthClass::Supported,
                0.7,
                &[("case9", "u9")],
            ),
        ]
    }

    #[test]
    fn overview_returns_claims_only_ranked_by_importance() {
        let records = sample_records();
        let resp = build_graph(&records, None, &params(GraphMode::Overview)).unwrap();
        assert!(resp.nodes.iter().all(|n| n.node_type == "claim"));
        assert_eq!(resp.nodes.len(), 4);
        // claim:a — most shared sources + best provenance — ranks first.
        assert_eq!(resp.nodes[0].id, "claim:a");
        for w in resp.nodes.windows(2) {
            assert!(w[0].importance >= w[1].importance);
        }
        // Full graph is 4 claims + 5 units + 3 sources.
        assert_eq!(resp.total_nodes, 12);
        assert!(!resp.truncated);
        assert!(resp.edges.iter().all(|e| e.edge_type == "related"));
    }

    #[test]
    fn overview_limit_truncates_and_prunes_edges() {
        let records = sample_records();
        let mut p = params(GraphMode::Overview);
        p.limit = 2;
        let resp = build_graph(&records, None, &p).unwrap();
        assert_eq!(resp.nodes.len(), 2);
        assert!(resp.truncated);
        let kept: Vec<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        for e in &resp.edges {
            assert!(kept.contains(&e.source.as_str()));
            assert!(kept.contains(&e.target.as_str()));
        }
    }

    #[test]
    fn from_query_parses_persp() {
        let empty = std::collections::HashMap::new();
        assert_eq!(GraphParams::from_query(&empty).unwrap().persp, Perspective::Claim);
        let mut q = std::collections::HashMap::new();
        q.insert("persp".to_string(), "source".to_string());
        assert_eq!(GraphParams::from_query(&q).unwrap().persp, Perspective::Source);
        q.insert("persp".to_string(), "bogus".to_string());
        assert!(GraphParams::from_query(&q).is_err());
    }

    #[test]
    fn source_overview_returns_sources_linked_by_shared_claims() {
        let records = sample_records();
        let mut p = params(GraphMode::Overview);
        p.persp = Perspective::Source;
        let resp = build_graph(&records, None, &p).unwrap();

        // Nodes are all cited sources, ranked by citing-claim count.
        assert!(resp.nodes.iter().all(|n| n.node_type == "source"));
        assert_eq!(resp.nodes.len(), 3);
        assert_eq!(resp.nodes[0].id, "source:case1", "case1 (cited by a,b,c) ranks first");
        for w in resp.nodes.windows(2) {
            assert!(w[0].importance >= w[1].importance);
        }
        assert_eq!(resp.total_nodes, 12, "total counts the full tri-partite graph");
        assert!(!resp.truncated);

        // Only claim `a` cites two sources → exactly one shared-claim edge.
        assert_eq!(resp.edges.len(), 1);
        let e = &resp.edges[0];
        assert_eq!(e.edge_type, "related");
        assert_eq!(e.weight, Some(1));
        let mut pair = [e.source.as_str(), e.target.as_str()];
        pair.sort();
        assert_eq!(pair, ["source:case1", "source:case2"]);

        // One community (case1 + case2 share a cluster), labeled by the dominant
        // theme among their citing claims (alpha 3 : beta 1).
        assert_eq!(resp.communities.len(), 1);
        assert_eq!(resp.communities[0].size, 2);
        assert_eq!(resp.communities[0].label, "alpha");
    }

    #[test]
    fn source_overview_limit_truncates_and_drops_now_dangling_edges() {
        let records = sample_records();
        let mut p = params(GraphMode::Overview);
        p.persp = Perspective::Source;
        p.limit = 1;
        let resp = build_graph(&records, None, &p).unwrap();
        assert_eq!(resp.nodes.len(), 1);
        assert!(resp.truncated);
        // One kept source can't co-occur with another → no edges survive.
        assert!(resp.edges.is_empty());
    }

    #[test]
    fn overview_theme_filter_is_server_side() {
        let records = sample_records();
        let mut p = params(GraphMode::Overview);
        p.theme = Some("alpha".into());
        let resp = build_graph(&records, None, &p).unwrap();
        assert_eq!(resp.nodes.len(), 2);
        assert!(
            resp.nodes
                .iter()
                .all(|n| n.theme.as_deref() == Some("alpha"))
        );
    }

    #[test]
    fn strength_weight_orders_equal_hub_claims() {
        // Same source (same hub degree), same provenance — only strength
        // differs, so the supported claim must outrank the opinion.
        let records = vec![
            rec(
                "weak",
                "t",
                StrengthClass::OpinionAsFact,
                0.7,
                &[("case1", "u1")],
            ),
            rec(
                "strong",
                "t",
                StrengthClass::Supported,
                0.7,
                &[("case1", "u2")],
            ),
        ];
        let resp = build_graph(&records, None, &params(GraphMode::Overview)).unwrap();
        assert_eq!(resp.nodes[0].id, "claim:strong");
    }

    #[test]
    fn neighborhood_expands_by_hops_and_always_keeps_focus_chain() {
        let records = sample_records();
        let mut p = params(GraphMode::Neighborhood);
        p.focus = Some("claim:a".into());
        p.hops = 1;
        let resp = build_graph(&records, None, &p).unwrap();
        let ids: Vec<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        // The focus claim's OWN citation chain is always reserved, even at
        // 1 hop — it's the point of the focus tier.
        assert!(ids.contains(&"claim:a"));
        assert!(ids.contains(&"unit:u1"));
        assert!(ids.contains(&"source:case1"));
        // But 1 hop does NOT expand other claims' units (2 hops away via
        // the related edge to b).
        assert!(ids.contains(&"claim:b"));
        assert!(!ids.contains(&"unit:u3"));

        p.hops = 2;
        let resp = build_graph(&records, None, &p).unwrap();
        let ids: Vec<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        assert!(ids.contains(&"unit:u3"));
    }

    #[test]
    fn neighborhood_missing_focus_is_client_error() {
        let records = sample_records();
        let mut p = params(GraphMode::Neighborhood);
        p.focus = Some("claim:nope".into());
        let err = build_graph(&records, None, &p).unwrap_err();
        assert_eq!(err.status, 404);
    }

    #[test]
    fn neighborhood_respects_node_cap() {
        // A hub source cited by many claims: 2 hops reaches everything.
        let mut records = Vec::new();
        for i in 0..500 {
            records.push(rec(
                &format!("k{i:03}"),
                "t",
                StrengthClass::Supported,
                0.8,
                &[("hub", &format!("u{i:03}"))],
            ));
        }
        // Focus on the hub source: 1 hop reaches all 500 units, 2 hops all
        // 500 claims — far past the cap.
        let mut p = params(GraphMode::Neighborhood);
        p.focus = Some("source:hub".into());
        let resp = build_graph(&records, None, &p).unwrap();
        assert!(resp.nodes.len() <= MAX_NEIGHBORHOOD_NODES);
        assert!(resp.truncated);
        // The focus itself always survives the cap (layer 0 fills first).
        assert!(resp.nodes.iter().any(|n| n.id == "source:hub"));
    }

    #[test]
    fn neighborhood_reserves_focus_citation_chain_under_cap() {
        // 350 claims all share one source → pairwise related edges, so the
        // focus claim's layer 1 holds 349 high-importance claims. Without
        // the reservation its own (importance-0) unit gets evicted by the
        // importance-ranked cap.
        let mut records = Vec::new();
        for i in 0..350 {
            records.push(rec(
                &format!("k{i:03}"),
                "t",
                StrengthClass::Supported,
                0.9,
                &[("hub", &format!("u{i:03}"))],
            ));
        }
        let mut p = params(GraphMode::Neighborhood);
        p.focus = Some("claim:k000".into());
        let resp = build_graph(&records, None, &p).unwrap();
        assert!(resp.nodes.len() <= MAX_NEIGHBORHOOD_NODES);
        assert!(resp.truncated);
        let ids: HashSet<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        assert!(ids.contains("claim:k000"));
        assert!(
            ids.contains("unit:u000"),
            "focus claim's own unit must survive the cap"
        );
        assert!(
            ids.contains("source:hub"),
            "focus claim's source must survive the cap"
        );
    }

    #[test]
    fn community_label_is_dominant_theme() {
        let records = sample_records();
        let resp = build_graph(&records, None, &params(GraphMode::Overview)).unwrap();
        // a, b, c share case1 → one community; d is isolated (size 1 → skipped).
        assert_eq!(resp.communities.len(), 1);
        let c = &resp.communities[0];
        assert_eq!(c.size, 3);
        // alpha covers 2/3 ≥ 40% → single-theme label.
        assert_eq!(c.label, "alpha");
        assert_eq!(c.top_claims[0], "claim:a");
    }

    #[test]
    fn community_label_joins_top2_when_no_dominant_theme() {
        // 6 members, no theme reaching 40%: 2×t1, 2×t2, 1×t3, 1×t4.
        let records = vec![
            rec("a", "t1", StrengthClass::Supported, 0.8, &[("case1", "u1")]),
            rec("b", "t1", StrengthClass::Supported, 0.8, &[("case1", "u2")]),
            rec("c", "t2", StrengthClass::Supported, 0.8, &[("case1", "u3")]),
            rec("d", "t2", StrengthClass::Supported, 0.8, &[("case1", "u4")]),
            rec("e", "t3", StrengthClass::Supported, 0.8, &[("case1", "u5")]),
            rec("f", "t4", StrengthClass::Supported, 0.8, &[("case1", "u6")]),
        ];
        let resp = build_graph(&records, None, &params(GraphMode::Overview)).unwrap();
        assert_eq!(resp.communities[0].label, "t1 / t2");
    }

    /// Index model mapping `(case_id, sha, title)` triples to sources+packs
    /// so build_base resolves `source:<sha>` node ids.
    fn model_for_cases(cases: &[(&str, &str, &str)]) -> IndexModel {
        use ovp_index::{OpsState, PackRow, SourceRow, SourceStatus, Totals};
        IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-07-09".into(),
            built_at: None,
            run_id: None,
            totals: Totals::default(),
            sources: cases
                .iter()
                .map(|(case, sha, title)| SourceRow {
                    sha256: (*sha).into(),
                    status: SourceStatus::Processed,
                    title: Some((*title).into()),
                    url: None,
                    rel_path: None,
                    date: None,
                    last_run_id: None,
                    pack_dir: Some(format!("40-Resources/Reader/{case}")),
                    fail_count: 0,
                    last_reason: None,
                    tags: Vec::new(),
                    tags_inferred: Vec::new(),
                })
                .collect(),
            packs: cases
                .iter()
                .map(|(case, sha, title)| PackRow {
                    pack_dir: format!("40-Resources/Reader/{case}"),
                    title: (*title).into(),
                    date: None,
                    units: 0,
                    cards: 0,
                    json_repaired: false,
                    card_titles: vec![],
                    source_sha256: Some((*sha).into()),
                })
                .collect(),
            claims: vec![],
            runs: vec![],
            ops: OpsState::default(),
        }
    }

    #[test]
    fn source_neighborhood_returns_citing_claims_and_sibling_sources() {
        let records = sample_records();
        let model = model_for_cases(&[
            ("case1", "sha1", "Source One"),
            ("case2", "sha2", "Source Two"),
            ("case9", "sha9", "Source Nine"),
        ]);
        let resp = source_neighborhood(&records, Some(&model), None, "sha1").unwrap();
        assert_eq!(resp.mode, "neighborhood");

        let ids: HashSet<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        // Focus source, its citing claims a/b/c, and the sibling source of
        // claim a (case2 → sha2).
        assert!(ids.contains("source:sha1"));
        assert!(ids.contains("claim:a"));
        assert!(ids.contains("claim:b"));
        assert!(ids.contains("claim:c"));
        assert!(ids.contains("source:sha2"));
        // Unrelated claim d and its source never enter the neighborhood.
        assert!(!ids.contains("claim:d"));
        assert!(!ids.contains("source:case9"));
        assert!(!ids.contains("source:sha9"));
        // No unit nodes in this compact view.
        assert!(resp.nodes.iter().all(|n| n.node_type != "unit"));

        // Edges are bipartite claim→source `cites`, endpoints all kept.
        assert!(!resp.edges.is_empty());
        for e in &resp.edges {
            assert_eq!(e.edge_type, "cites");
            assert!(e.source.starts_with("claim:"));
            assert!(e.target.starts_with("source:"));
            assert!(ids.contains(e.source.as_str()));
            assert!(ids.contains(e.target.as_str()));
        }
        assert!(
            resp.edges
                .iter()
                .any(|e| e.source == "claim:a" && e.target == "source:sha2"),
            "sibling edge missing"
        );
    }

    #[test]
    fn source_neighborhood_uncited_source_is_single_node() {
        let records = sample_records();
        let mut model = model_for_cases(&[("case1", "sha1", "Source One")]);
        // A source the index knows but no crystal claim cites.
        model.sources.push(ovp_index::SourceRow {
            sha256: "freshsha".into(),
            status: ovp_index::SourceStatus::Processed,
            title: Some("Fresh Source".into()),
            url: None,
            rel_path: None,
            date: None,
            last_run_id: None,
            pack_dir: None,
            fail_count: 0,
            last_reason: None,
            tags: Vec::new(),
            tags_inferred: Vec::new(),
        });
        let resp = source_neighborhood(&records, Some(&model), None, "freshsha").unwrap();
        assert_eq!(resp.nodes.len(), 1);
        assert_eq!(resp.nodes[0].id, "source:freshsha");
        assert_eq!(resp.nodes[0].label, "Fresh Source");
        assert!(resp.edges.is_empty());
        assert!(!resp.truncated);
    }

    /// Evidence sidecar with `n` cards for the given case, keyed by sha.
    fn evidence_for(case: &str, sha: &str, n: usize) -> EvidenceModel {
        use ovp_index::evidence::CardEvidenceRow;
        EvidenceModel {
            schema: "ovp.index.evidence/v1".into(),
            date: "2026-07-09".into(),
            cards: (0..n)
                .map(|i| CardEvidenceRow {
                    id: format!("card:40-Resources/Reader/{case}:{i}"),
                    pack_dir: format!("40-Resources/Reader/{case}"),
                    source_sha256: Some(sha.into()),
                    source_title: "Source".into(),
                    title: format!("Card {i}"),
                    content: format!("Body of card {i}."),
                    unit_type: None,
                    cited_unit_ids: vec![],
                })
                .collect(),
            units: vec![],
            warnings: vec![],
        }
    }

    #[test]
    fn source_neighborhood_uncited_source_shows_its_cards() {
        // The operator finding (2026-07-09): 72% of sources have no citing
        // claims — the memory layer must render, not a single lonely node.
        let records = sample_records();
        let mut model = model_for_cases(&[("case1", "sha1", "Source One")]);
        model.sources.push(ovp_index::SourceRow {
            sha256: "freshsha".into(),
            status: ovp_index::SourceStatus::Processed,
            title: Some("Fresh Source".into()),
            url: None,
            rel_path: None,
            date: None,
            last_run_id: None,
            pack_dir: Some("40-Resources/Reader/fresh".into()),
            fail_count: 0,
            last_reason: None,
            tags: Vec::new(),
            tags_inferred: Vec::new(),
        });
        let evidence = evidence_for("fresh", "freshsha", 2);
        let resp =
            source_neighborhood(&records, Some(&model), Some(&evidence), "freshsha").unwrap();

        assert!(resp.nodes.len() > 1, "must not be a single lonely node");
        assert_eq!(resp.nodes.len(), 3); // focus + 2 cards
        assert_eq!(resp.nodes[0].id, "source:freshsha");
        let cards: Vec<&GNode> = resp
            .nodes
            .iter()
            .filter(|n| n.node_type == "card")
            .collect();
        assert_eq!(cards.len(), 2);
        assert_eq!(cards[0].label, "Card 0");
        // source→card has_memory edges, one per card.
        assert_eq!(resp.edges.len(), 2);
        for e in &resp.edges {
            assert_eq!(e.edge_type, "has_memory");
            assert_eq!(e.source, "source:freshsha");
            assert!(e.target.starts_with("card:"));
        }
        assert!(!resp.truncated);

        // Cards from another source never leak into this neighborhood.
        let other = evidence_for("case1", "sha1", 1);
        let resp =
            source_neighborhood(&records, Some(&model), Some(&other), "freshsha").unwrap();
        assert_eq!(resp.nodes.len(), 1);
    }

    #[test]
    fn source_neighborhood_cited_source_keeps_claims_and_adds_cards() {
        let records = sample_records();
        let model = model_for_cases(&[
            ("case1", "sha1", "Source One"),
            ("case2", "sha2", "Source Two"),
            ("case9", "sha9", "Source Nine"),
        ]);
        let evidence = evidence_for("case1", "sha1", 2);
        let resp = source_neighborhood(&records, Some(&model), Some(&evidence), "sha1").unwrap();

        let ids: HashSet<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        // The claim/sibling story is unchanged…
        assert!(ids.contains("source:sha1"));
        assert!(ids.contains("claim:a"));
        assert!(ids.contains("claim:b"));
        assert!(ids.contains("claim:c"));
        assert!(ids.contains("source:sha2"));
        assert!(!ids.contains("claim:d"));
        // …plus the focus source's cards with has_memory edges.
        assert!(ids.contains("card:40-Resources/Reader/case1:0"));
        assert!(ids.contains("card:40-Resources/Reader/case1:1"));
        let mem_edges: Vec<&GEdge> = resp
            .edges
            .iter()
            .filter(|e| e.edge_type == "has_memory")
            .collect();
        assert_eq!(mem_edges.len(), 2);
        assert!(mem_edges.iter().all(|e| e.source == "source:sha1"));
        // Sibling sources do NOT pull their own cards in.
        assert!(
            resp.nodes
                .iter()
                .filter(|n| n.node_type == "card")
                .count()
                == 2
        );
    }

    #[test]
    fn source_neighborhood_cap_prefers_claims_over_cards() {
        // 400 claims on one hub source fill the cap before any card fits.
        let mut records = Vec::new();
        for i in 0..400 {
            records.push(rec(
                &format!("k{i:03}"),
                "t",
                StrengthClass::Supported,
                0.8,
                &[("hub", &format!("u{i:03}"))],
            ));
        }
        let model = model_for_cases(&[("hub", "hubsha", "Hub Source")]);
        let evidence = evidence_for("hub", "hubsha", 3);
        let resp = source_neighborhood(&records, Some(&model), Some(&evidence), "hubsha").unwrap();
        assert!(resp.nodes.len() <= MAX_NEIGHBORHOOD_NODES);
        assert!(resp.truncated);
        // Claims won the budget; the cards were dropped, not the claims.
        assert!(resp.nodes.iter().any(|n| n.node_type == "claim"));
        assert!(resp.nodes.iter().all(|n| n.node_type != "card"));
    }

    #[test]
    fn theme_subgraph_filters_to_theme_claims_and_their_sources() {
        let records = sample_records();
        let model = model_for_cases(&[
            ("case1", "sha1", "Source One"),
            ("case2", "sha2", "Source Two"),
            ("case9", "sha9", "Source Nine"),
        ]);
        let resp = theme_subgraph(&records, Some(&model), "alpha").unwrap();
        assert_eq!(resp.mode, "theme");

        let ids: HashSet<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        // Theme alpha = claims a + b, drawing on sources sha1 (case1) and
        // sha2 (case2, via claim a).
        assert!(ids.contains("claim:a"));
        assert!(ids.contains("claim:b"));
        assert!(ids.contains("source:sha1"));
        assert!(ids.contains("source:sha2"));
        // Other themes' claims and their sources never enter the subgraph.
        assert!(!ids.contains("claim:c"));
        assert!(!ids.contains("claim:d"));
        assert!(!ids.contains("source:sha9"));
        // Units are collapsed in this compact view.
        assert!(resp.nodes.iter().all(|n| n.node_type != "unit"));

        // Bipartite cites edges plus related connectivity among theme claims.
        for e in &resp.edges {
            assert!(ids.contains(e.source.as_str()));
            assert!(ids.contains(e.target.as_str()));
        }
        assert!(
            resp.edges.iter().any(|e| e.edge_type == "cites"
                && e.source == "claim:a"
                && e.target == "source:sha1")
        );
        assert!(
            resp.edges.iter().any(|e| e.edge_type == "related"
                && e.weight == Some(1)
                && ((e.source == "claim:a" && e.target == "claim:b")
                    || (e.source == "claim:b" && e.target == "claim:a"))),
            "related edge between the theme's claims missing"
        );
        assert!(!resp.truncated);
    }

    #[test]
    fn theme_subgraph_unknown_theme_is_404() {
        let records = sample_records();
        let err = theme_subgraph(&records, None, "no-such-theme").unwrap_err();
        assert_eq!(err.status, 404);
    }

    #[test]
    fn claim_nodes_carry_index_claim_id_for_portal_links() {
        // Graph identity is the ledger claim_key ("a"); portal links resolve
        // the index claim_id ("id-a") — the fixture makes them differ on
        // purpose (codex review P2: double-click deep links broke wherever
        // the two diverged).
        let records = sample_records();
        let resp = theme_subgraph(&records, None, "alpha").unwrap();
        let claim = resp
            .nodes
            .iter()
            .find(|n| n.id == "claim:a")
            .expect("claim:a in theme graph");
        assert_eq!(claim.claim_id.as_deref(), Some("id-a"));
        let source = resp
            .nodes
            .iter()
            .find(|n| n.node_type == "source")
            .expect("a source node");
        assert!(source.claim_id.is_none());
    }

    #[test]
    fn caveated_only_theme_gets_a_graph_not_404() {
        // Themes that exist only as caveated review.json claims have a
        // working theme wall + detail page; the graph rail must not 404
        // (codex review P2). Unknown themes still fail loud.
        let records = sample_records(); // no "gamma" in the ledger
        let mut model = model_for_cases(&[("case1", "sha1", "Source One")]);
        model.claims.push(ovp_index::ClaimRow {
            claim_id: "cav-1".into(),
            claim: "caveated-only claim".into(),
            theme: Some("gamma".into()),
            status: ovp_index::ClaimStatus::Caveated,
            sources: vec!["case1".into()],
            strength: Some("weak".into()),
            run_id: None,
            lane: None,
        });
        let resp = theme_subgraph(&records, Some(&model), "gamma").unwrap();
        let ids: Vec<&str> = resp.nodes.iter().map(|n| n.id.as_str()).collect();
        assert!(ids.contains(&"claim:cav-1"), "caveated claim node present");
        assert!(ids.contains(&"source:sha1"), "cited source resolved via pack");
        let claim = resp.nodes.iter().find(|n| n.id == "claim:cav-1").unwrap();
        assert_eq!(claim.claim_id.as_deref(), Some("cav-1"));
        assert!(
            resp.edges
                .iter()
                .any(|e| e.source == "claim:cav-1" && e.target == "source:sha1"),
            "cites edge from caveated claim to its source"
        );
        assert!(theme_subgraph(&records, Some(&model), "still-unknown").is_err());
    }

    #[test]
    fn theme_subgraph_respects_node_cap() {
        // One theme, 400 claims on a shared hub source — past the cap.
        let mut records = Vec::new();
        for i in 0..400 {
            records.push(rec(
                &format!("k{i:03}"),
                "big",
                StrengthClass::Supported,
                0.8,
                &[("hub", &format!("u{i:03}"))],
            ));
        }
        let resp = theme_subgraph(&records, None, "big").unwrap();
        assert!(resp.nodes.len() <= MAX_NEIGHBORHOOD_NODES);
        assert!(resp.truncated);
        assert!(resp.nodes.iter().any(|n| n.id == "source:hub"));
    }

    #[test]
    fn source_neighborhood_unknown_sha_is_404() {
        let records = sample_records();
        let model = model_for_cases(&[("case1", "sha1", "Source One")]);
        let err = source_neighborhood(&records, Some(&model), None, "nope").unwrap_err();
        assert_eq!(err.status, 404);
    }

    #[test]
    fn params_reject_unknown_mode_and_missing_focus() {
        let mut q = HashMap::new();
        q.insert("mode".to_string(), "3d".to_string());
        assert_eq!(GraphParams::from_query(&q).unwrap_err().status, 400);

        let mut q = HashMap::new();
        q.insert("mode".to_string(), "neighborhood".to_string());
        assert_eq!(GraphParams::from_query(&q).unwrap_err().status, 400);
    }

    #[test]
    fn search_subgraph_flags_hits_and_pulls_related_context() {
        let records = sample_records();
        let resp = search_subgraph(&records, None, "for a");
        let hits: Vec<&GNode> = resp.nodes.iter().filter(|n| n.hit).collect();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].id, "claim:a");
        assert_eq!(resp.mode, "search");
        // b and c share case1 with a → related context, not hits.
        assert!(resp.nodes.iter().any(|n| n.id == "claim:b" && !n.hit));
        // d shares nothing with a — not in the subgraph.
        assert!(!resp.nodes.iter().any(|n| n.id == "claim:d"));
        assert!(resp.edges.iter().all(|e| e.edge_type == "related"));
    }

    #[test]
    fn search_subgraph_no_matches_is_empty() {
        let records = sample_records();
        let resp = search_subgraph(&records, None, "zzz-no-such-term");
        assert!(resp.nodes.is_empty());
        assert!(resp.edges.is_empty());
    }

    #[test]
    fn theme_counts_ordered_by_count_then_name() {
        let records = sample_records();
        let t = theme_counts(&records);
        assert_eq!(t[0], ("alpha".to_string(), 2));
        assert_eq!(t.len(), 3);
    }

    #[test]
    fn last_path_segment_handles_both_separators() {
        assert_eq!(last_path_segment("a/b/case-01"), Some("case-01"));
        assert_eq!(
            last_path_segment(r"40-Resources\Reader\case-01"),
            Some("case-01")
        );
        assert_eq!(last_path_segment("case-01"), Some("case-01"));
        assert_eq!(last_path_segment("a/b/"), None);
        assert_eq!(last_path_segment(""), None);
    }
}
