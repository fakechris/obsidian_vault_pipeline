//! Processor-chain capture: the node ids + kinds + edges + topological order of
//! the manifest the cycle ran. Read straight off the parsed
//! [`DomainPipelineSpec`] (topology from `[pipeline]`, kinds from the
//! `[assembly.*]` overlay) — no re-parsing, no business logic.

use ovp_app::DomainPipelineSpec;
use serde::Serialize;

/// One node in the processor chain: its manifest id and its build `kind`.
#[derive(Debug, Clone, Serialize)]
pub struct ChainNode {
    pub id: String,
    pub kind: String,
}

/// The manifest's processor chain, captured for the review pack.
#[derive(Debug, Clone, Serialize)]
pub struct ProcessorChain {
    pub nodes: Vec<ChainNode>,
    pub edges: Vec<[String; 2]>,
    /// `Some` topological order, or `None` if the topology has a cycle (the
    /// spec parser already rejects most of those, so this is belt-and-braces).
    pub topo_order: Option<Vec<String>>,
}

impl ProcessorChain {
    /// Extract the chain from an already-parsed spec. Node order follows the
    /// manifest's `[pipeline].nodes` declaration order.
    pub fn from_spec(spec: &DomainPipelineSpec) -> Self {
        let topology = spec.topology();
        let nodes = topology
            .nodes()
            .iter()
            .map(|id| ChainNode {
                id: id.clone(),
                kind: spec.node_kind(id).unwrap_or("(unknown)").to_string(),
            })
            .collect();
        let edges = topology.edges().to_vec();
        let topo_order = topology.topo_order().ok();
        Self { nodes, edges, topo_order }
    }

    /// Human-readable rendering for `processor-chain.txt` (mirrors the layout of
    /// the `graph` subcommand).
    pub fn render_text(&self) -> String {
        let mut s = String::new();
        s.push_str(&format!("nodes ({}):\n", self.nodes.len()));
        for n in &self.nodes {
            s.push_str(&format!("  - {}  [{}]\n", n.id, n.kind));
        }
        s.push_str(&format!("\nedges ({}):\n", self.edges.len()));
        for [from, to] in &self.edges {
            s.push_str(&format!("  {from} -> {to}\n"));
        }
        s.push_str("\ntopological order:\n");
        match &self.topo_order {
            Some(order) => {
                for (i, n) in order.iter().enumerate() {
                    s.push_str(&format!("  {:>2}. {n}\n", i + 1));
                }
            }
            None => s.push_str("  (unavailable — topology has a cycle)\n"),
        }
        s
    }
}
