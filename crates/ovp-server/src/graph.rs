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
use ovp_index::IndexModel;
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

#[derive(Debug, Clone)]
pub struct GraphParams {
    pub mode: GraphMode,
    pub limit: usize,
    pub theme: Option<String>,
    pub focus: Option<String>,
    pub hops: usize,
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
        Ok(GraphParams {
            mode,
            limit,
            theme: params.get("theme").cloned(),
            focus,
            hops,
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
        GraphMode::Overview => Ok(overview_response(base, params)),
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
            label: if rec.claim.chars().count() > MAX_CLAIM_LABEL_LEN {
                format!("{}…", truncate_chars(&rec.claim, TRUNCATED_CLAIM_LABEL_LEN))
            } else {
                rec.claim.clone()
            },
            theme: Some(rec.theme.clone()),
            strength: Some(format!("{:?}", rec.strength).to_lowercase()),
            url: None,
            degree: 0,
            cluster: 0,
            importance: 0.0,
            hit: false,
            provenance: Some(rec.provenance_score),
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
        if e.edge_type == "cites" {
            if let Some(c) = claim_cluster.get(&e.source) {
                unit_cluster.insert(e.target.clone(), *c);
            }
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

/// Source-centric neighborhood for the portal's KnowledgeGraph component
/// (design §4, `scope=neighborhood&source=<sha>`): the source node, claims
/// citing it, and the sibling sources those claims also draw from. Units are
/// deliberately excluded — the source detail page shows them as text; the
/// graph tells the claim/sibling story. Cards are not modeled in the graph.
///
/// A source known to the index but cited by no claim returns a single-node
/// graph (the page still shows the component with an empty-neighborhood
/// hint); an entirely unknown sha is a 404.
pub fn source_neighborhood(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
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

    if citing.is_empty() && !base.nodes.contains_key(&focus_id) {
        // Not in the crystal graph at all — fall back to the index so a
        // freshly processed (or blocked) source still gets its focus node.
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
        };
        return Ok(GraphResponse {
            mode: GraphMode::Neighborhood.as_str().into(),
            nodes: vec![node],
            edges: Vec::new(),
            communities: Vec::new(),
            total_nodes: base.nodes.len() + 1,
            truncated: false,
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
        let resp = source_neighborhood(&records, Some(&model), "sha1").unwrap();
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
        });
        let resp = source_neighborhood(&records, Some(&model), "freshsha").unwrap();
        assert_eq!(resp.nodes.len(), 1);
        assert_eq!(resp.nodes[0].id, "source:freshsha");
        assert_eq!(resp.nodes[0].label, "Fresh Source");
        assert!(resp.edges.is_empty());
        assert!(!resp.truncated);
    }

    #[test]
    fn source_neighborhood_unknown_sha_is_404() {
        let records = sample_records();
        let model = model_for_cases(&[("case1", "sha1", "Source One")]);
        let err = source_neighborhood(&records, Some(&model), "nope").unwrap_err();
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
