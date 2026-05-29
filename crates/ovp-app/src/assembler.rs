use std::collections::{HashMap, HashSet};

use ovp_core::{GraphError, GraphRunner};
use ovp_domain::DomainBody;

use crate::error::AssemblyError;
use crate::node_kind::{kinds, NodeCategory, NodeKind};
use crate::registry::{ConfigField, NodeBuildArgs, NodeRegistry};
use crate::spec::DomainPipelineSpec;
use crate::wiring::AppWiring;

/// Builds a runnable `GraphRunner<DomainBody>` from a `DomainPipelineSpec` and
/// the app's `AppWiring`, using a `NodeRegistry` of compiled-in factories.
///
/// ALL validation runs before any node is built, so assembly fails loudly with
/// a typed error and never leaves a half-wired runner. The checks, in order:
/// unknown kind / category-vs-edges, per-kind config, required runtime wiring,
/// and graph shape (an acyclic, connected source→sink pipeline).
pub struct GraphAssembler {
    registry: NodeRegistry,
}

impl GraphAssembler {
    pub fn new(registry: NodeRegistry) -> Self {
        Self { registry }
    }

    /// The standard assembler over the compiled-in domain node set.
    pub fn with_domain_nodes() -> Self {
        Self::new(NodeRegistry::with_domain_nodes())
    }

    pub fn assemble(
        &self,
        spec: &DomainPipelineSpec,
        mut wiring: AppWiring,
    ) -> Result<GraphRunner<DomainBody>, AssemblyError> {
        // --- validate everything up front (no side effects, no half-build) ---
        self.validate_categories(spec)?;
        self.validate_config(spec)?;
        self.validate_runtime_wiring(spec, &wiring)?;
        self.validate_graph_shape(spec)?;

        // --- build: topology drives the runner; node order matches manifest ---
        let mut runner = GraphRunner::new(spec.topology().clone(), wiring.run_id().clone());
        for node_id in spec.topology().nodes() {
            let na = spec.assembly().get(node_id).expect("validated by DomainPipelineSpec::parse");
            let kind = NodeKind::new(na.kind.clone());
            let entry = self.registry.get(&kind).ok_or_else(|| AssemblyError::UnknownKind {
                node_id: node_id.clone(),
                kind: na.kind.clone(),
            })?;
            let mut args = NodeBuildArgs {
                runner: &mut runner,
                node_id: node_id.as_str(),
                config: &na.config,
                wiring: &mut wiring,
            };
            (entry.factory)(&mut args)?;
        }

        Ok(runner)
    }

    /// Unknown kind, and category-vs-edge sanity (source ⇒ no inbound,
    /// sink ⇒ no outbound).
    fn validate_categories(&self, spec: &DomainPipelineSpec) -> Result<(), AssemblyError> {
        let edges = spec.topology().edges();
        let has_inbound: HashSet<&str> = edges.iter().map(|[_, to]| to.as_str()).collect();
        let has_outbound: HashSet<&str> = edges.iter().map(|[from, _]| from.as_str()).collect();

        for node_id in spec.topology().nodes() {
            let na = spec.assembly().get(node_id).expect("validated");
            let kind = NodeKind::new(na.kind.clone());
            let entry = self.registry.get(&kind).ok_or_else(|| AssemblyError::UnknownKind {
                node_id: node_id.clone(),
                kind: na.kind.clone(),
            })?;
            match entry.category {
                NodeCategory::Source if has_inbound.contains(node_id.as_str()) => {
                    return Err(AssemblyError::CategoryMismatch {
                        node_id: node_id.clone(),
                        kind: na.kind.clone(),
                        detail: "source node must not have an inbound edge".into(),
                    });
                }
                NodeCategory::Sink if has_outbound.contains(node_id.as_str()) => {
                    return Err(AssemblyError::CategoryMismatch {
                        node_id: node_id.clone(),
                        kind: na.kind.clone(),
                        detail: "sink node must not have an outbound edge".into(),
                    });
                }
                _ => {}
            }
        }
        Ok(())
    }

    /// Per-kind config: reject a config field the kind does not accept (a typo
    /// that would otherwise be silently ignored), and a missing required field.
    fn validate_config(&self, spec: &DomainPipelineSpec) -> Result<(), AssemblyError> {
        for node_id in spec.topology().nodes() {
            let na = spec.assembly().get(node_id).expect("validated");
            let kind = NodeKind::new(na.kind.clone());
            let entry = self.registry.get(&kind).ok_or_else(|| AssemblyError::UnknownKind {
                node_id: node_id.clone(),
                kind: na.kind.clone(),
            })?;
            for &field in ConfigField::ALL {
                if field.is_set(&na.config) && !entry.config.allowed.contains(&field) {
                    return Err(AssemblyError::UnexpectedConfig {
                        node_id: node_id.clone(),
                        kind: na.kind.clone(),
                        field: field.name(),
                    });
                }
            }
            for &field in entry.config.required {
                if !field.is_set(&na.config) {
                    return Err(AssemblyError::MissingConfig {
                        node_id: node_id.clone(),
                        field: field.name(),
                    });
                }
            }
        }
        Ok(())
    }

    /// Required runtime wiring that a static manifest can't carry. Today: any
    /// graph containing an article/paper parser needs a non-empty,
    /// `YYYY-MM-DD` `date_stamp` (it stamps the note path + frontmatter).
    fn validate_runtime_wiring(
        &self,
        spec: &DomainPipelineSpec,
        wiring: &AppWiring,
    ) -> Result<(), AssemblyError> {
        for node_id in spec.topology().nodes() {
            let kind = spec.assembly().get(node_id).expect("validated").kind.as_str();
            if kind == kinds::ARTICLE_PARSER || kind == kinds::PAPER_PARSER {
                let date = wiring.date_stamp();
                if date.is_empty() {
                    return Err(AssemblyError::MissingWiring {
                        node_id: node_id.clone(),
                        name: "date_stamp".into(),
                    });
                }
                if !is_iso_date(date) {
                    return Err(AssemblyError::InvalidWiring {
                        node_id: node_id.clone(),
                        name: "date_stamp".into(),
                        detail: format!("expected YYYY-MM-DD, got `{date}`"),
                    });
                }
            }
        }
        Ok(())
    }

    /// The graph must be an **acyclic, connected source→sink pipeline**: no
    /// cycles, at least one source and one sink, and every node both reachable
    /// from some source AND able to reach some sink. This catches cycles and
    /// self-loops, sources with no outbound edge, sinks with no inbound edge,
    /// transforms missing an edge, and floating/dead-end nodes — all of which
    /// would otherwise assemble and then either fail at `run()` or silently drop
    /// records (or emit an empty plan) at runtime.
    fn validate_graph_shape(&self, spec: &DomainPipelineSpec) -> Result<(), AssemblyError> {
        let nodes = spec.topology().nodes();
        let edges = spec.topology().edges();

        // Acyclicity: a cycle (or self-loop) is not a source→sink pipeline and
        // would otherwise only surface as `CycleDetected` at run() — catch it at
        // assembly. `topo_order` is the same check the runner uses.
        spec.topology().topo_order().map_err(|e| AssemblyError::Manifest(e.into()))?;

        let mut sources: Vec<&str> = Vec::new();
        let mut sinks: Vec<&str> = Vec::new();
        for node_id in nodes {
            let na = spec.assembly().get(node_id).expect("validated");
            let kind = NodeKind::new(na.kind.clone());
            let entry = self.registry.get(&kind).ok_or_else(|| AssemblyError::UnknownKind {
                node_id: node_id.clone(),
                kind: na.kind.clone(),
            })?;
            match entry.category {
                NodeCategory::Source => sources.push(node_id.as_str()),
                NodeCategory::Sink => sinks.push(node_id.as_str()),
                _ => {}
            }
        }
        if sources.is_empty() {
            return Err(AssemblyError::Manifest(GraphError::NoSource.into()));
        }
        if sinks.is_empty() {
            return Err(AssemblyError::Manifest(GraphError::NoSink.into()));
        }

        let mut fwd: HashMap<&str, Vec<&str>> = HashMap::new();
        let mut rev: HashMap<&str, Vec<&str>> = HashMap::new();
        for [from, to] in edges {
            fwd.entry(from.as_str()).or_default().push(to.as_str());
            rev.entry(to.as_str()).or_default().push(from.as_str());
        }
        let from_sources = reachable(&fwd, &sources);
        let to_sinks = reachable(&rev, &sinks);

        for node_id in nodes {
            let n = node_id.as_str();
            if !from_sources.contains(n) {
                return Err(AssemblyError::DisconnectedGraph {
                    node_id: node_id.clone(),
                    detail: "not reachable from any source".into(),
                });
            }
            if !to_sinks.contains(n) {
                return Err(AssemblyError::DisconnectedGraph {
                    node_id: node_id.clone(),
                    detail: "cannot reach any sink (dead end)".into(),
                });
            }
        }
        Ok(())
    }
}

/// DFS reachability from `seeds` over adjacency `adj`. Includes the seeds.
fn reachable<'a>(adj: &HashMap<&'a str, Vec<&'a str>>, seeds: &[&'a str]) -> HashSet<&'a str> {
    let mut seen: HashSet<&str> = HashSet::new();
    let mut stack: Vec<&str> = seeds.to_vec();
    while let Some(n) = stack.pop() {
        if seen.insert(n) {
            if let Some(next) = adj.get(n) {
                stack.extend(next.iter().copied());
            }
        }
    }
    seen
}

/// Cheap `YYYY-MM-DD` shape check (digits + dashes at the right spots). Not a
/// calendar-validity check — just enough to reject obviously-wrong date stamps.
fn is_iso_date(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b[..4].iter().all(u8::is_ascii_digit)
        && b[5..7].iter().all(u8::is_ascii_digit)
        && b[8..10].iter().all(u8::is_ascii_digit)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_date_shape() {
        assert!(is_iso_date("2026-05-29"));
        assert!(!is_iso_date(""));
        assert!(!is_iso_date("2026-5-9"));
        assert!(!is_iso_date("2026/05/29"));
        assert!(!is_iso_date("not-a-date"));
        assert!(!is_iso_date("2026-05-29T00")); // too long
    }
}
