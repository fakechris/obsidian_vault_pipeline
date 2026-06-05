use serde::{Deserialize, Serialize};
use std::collections::HashSet;

use crate::error::ManifestError;

/// A pipeline topology, deserialized from TOML.
///
/// v0.1 keeps this deliberately flat: a list of named nodes plus a list
/// of directed edges. Each node name resolves to a registered Source,
/// Transform, or Sink at runtime (via the GraphRunner's registry).
///
/// Example TOML:
/// ```toml
/// [pipeline]
/// nodes = ["src", "tx", "snk"]
/// edges = [["src", "tx"], ["tx", "snk"]]
/// ```
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PipelineManifest {
    pub pipeline: PipelineBody,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PipelineBody {
    pub nodes: Vec<String>,
    pub edges: Vec<[String; 2]>,
}

impl PipelineManifest {
    pub fn parse(toml_str: &str) -> Result<Self, ManifestError> {
        let m: Self = toml::from_str(toml_str).map_err(|e| ManifestError::Parse(e.to_string()))?;
        m.validate()?;
        Ok(m)
    }

    pub fn nodes(&self) -> &[String] { &self.pipeline.nodes }
    pub fn edges(&self) -> &[[String; 2]] { &self.pipeline.edges }

    pub fn validate(&self) -> Result<(), ManifestError> {
        if self.pipeline.nodes.is_empty() {
            return Err(ManifestError::EmptyPipeline);
        }
        let mut seen = HashSet::new();
        for n in &self.pipeline.nodes {
            if !seen.insert(n.clone()) {
                return Err(ManifestError::DuplicateNode(n.clone()));
            }
        }
        for [from, to] in &self.pipeline.edges {
            if !seen.contains(from) {
                return Err(ManifestError::EdgeMissingEndpoint {
                    edge: (from.clone(), to.clone()),
                    missing: from.clone(),
                });
            }
            if !seen.contains(to) {
                return Err(ManifestError::EdgeMissingEndpoint {
                    edge: (from.clone(), to.clone()),
                    missing: to.clone(),
                });
            }
        }
        Ok(())
    }

    /// Returns nodes in a deterministic topological order, or
    /// `ManifestError::CycleDetected` via `GraphError` if there is a cycle.
    ///
    /// v0.1: simple Kahn-style sort, breaks ties by manifest declaration order.
    pub fn topo_order(&self) -> Result<Vec<String>, crate::error::GraphError> {
        let nodes: Vec<String> = self.pipeline.nodes.clone();
        let order_index: std::collections::HashMap<&str, usize> = nodes
            .iter()
            .enumerate()
            .map(|(i, n)| (n.as_str(), i))
            .collect();

        let mut indeg: std::collections::HashMap<String, usize> =
            nodes.iter().map(|n| (n.clone(), 0usize)).collect();
        let mut adj: std::collections::HashMap<String, Vec<String>> =
            nodes.iter().map(|n| (n.clone(), Vec::new())).collect();
        for [from, to] in &self.pipeline.edges {
            adj.get_mut(from).expect("validated").push(to.clone());
            *indeg.get_mut(to).expect("validated") += 1;
        }

        let mut frontier: Vec<String> = nodes
            .iter()
            .filter(|n| indeg.get(n.as_str()).copied().unwrap_or(0) == 0)
            .cloned()
            .collect();
        // stable order: by manifest declaration position
        frontier.sort_by_key(|n| order_index.get(n.as_str()).copied().unwrap_or(usize::MAX));

        let mut out: Vec<String> = Vec::with_capacity(nodes.len());
        while let Some(n) = frontier.first().cloned() {
            frontier.remove(0);
            let downstream = adj.get(&n).cloned().unwrap_or_default();
            out.push(n);
            for d in downstream {
                let e = indeg.get_mut(&d).expect("validated");
                *e -= 1;
                if *e == 0 {
                    // insert into frontier keeping manifest-declaration order
                    let idx = order_index.get(d.as_str()).copied().unwrap_or(usize::MAX);
                    let pos = frontier
                        .binary_search_by_key(&idx, |x| {
                            order_index.get(x.as_str()).copied().unwrap_or(usize::MAX)
                        })
                        .unwrap_or_else(|p| p);
                    frontier.insert(pos, d);
                }
            }
        }

        if out.len() != nodes.len() {
            return Err(crate::error::GraphError::CycleDetected);
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_minimal() {
        let toml_str = r#"
            [pipeline]
            nodes = ["src", "tx", "snk"]
            edges = [["src", "tx"], ["tx", "snk"]]
        "#;
        let m = PipelineManifest::parse(toml_str).unwrap();
        assert_eq!(m.nodes(), &["src", "tx", "snk"]);
        assert_eq!(m.edges().len(), 2);
    }

    #[test]
    fn parse_rejects_duplicate_node() {
        let toml_str = r#"
            [pipeline]
            nodes = ["src", "src"]
            edges = []
        "#;
        assert!(matches!(
            PipelineManifest::parse(toml_str),
            Err(ManifestError::DuplicateNode(_))
        ));
    }

    #[test]
    fn parse_rejects_unknown_edge_endpoint() {
        let toml_str = r#"
            [pipeline]
            nodes = ["src"]
            edges = [["src", "missing"]]
        "#;
        assert!(matches!(
            PipelineManifest::parse(toml_str),
            Err(ManifestError::EdgeMissingEndpoint { .. })
        ));
    }

    #[test]
    fn topo_sort_linear() {
        let m = PipelineManifest::parse(
            r#"
                [pipeline]
                nodes = ["src", "tx", "snk"]
                edges = [["src", "tx"], ["tx", "snk"]]
            "#,
        )
        .unwrap();
        assert_eq!(m.topo_order().unwrap(), vec!["src", "tx", "snk"]);
    }

    #[test]
    fn topo_sort_detects_cycle() {
        let m = PipelineManifest::parse(
            r#"
                [pipeline]
                nodes = ["a", "b"]
                edges = [["a", "b"], ["b", "a"]]
            "#,
        )
        .unwrap();
        assert!(matches!(
            m.topo_order(),
            Err(crate::error::GraphError::CycleDetected)
        ));
    }
}
