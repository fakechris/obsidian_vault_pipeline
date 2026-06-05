use std::collections::{BTreeMap, HashSet};

use ovp_core::PipelineManifest;
use serde::Deserialize;

use crate::error::AssemblyError;

/// Per-node **static** config from the manifest. Named optional fields only —
/// not a `serde_json::Value` and not a `HashMap<String, _>`, so the no-untyped-
/// payload invariants hold. `deny_unknown_fields` makes a typo fail loudly.
///
/// Every field is the **name** of an `AppWiring` entry to bind, never a value:
/// per invariant #8, runtime/wiring values (the `ModelClient`, the
/// `ConceptRegistry`, run_id, date, area, input path, model name, …) live in
/// `AppWiring`, not in the static manifest. Config only says *which* wiring a
/// node binds to.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeConfig {
    /// `effect.llm_invoker`: name of the `AppWiring` client to bind.
    #[serde(default)]
    pub client: Option<String>,
    /// `transform.concept_resolver`: name of the `AppWiring` registry to bind.
    #[serde(default)]
    pub registry: Option<String>,
}

/// One `[assembly.<node_id>]` entry: which kind to build + its static config.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct NodeAssembly {
    pub(crate) kind: String,
    #[serde(default)]
    pub(crate) config: NodeConfig,
}

#[derive(Debug, Deserialize)]
struct AssemblyDoc {
    #[serde(default)]
    assembly: BTreeMap<String, NodeAssembly>,
}

/// The enriched, app-layer pipeline manifest: topology (reused verbatim from
/// `ovp-core::PipelineManifest`, read from the `[pipeline]` section) plus a
/// per-node `kind`+`config` overlay (read from `[assembly.<id>]`).
///
/// Both slices come from the SAME TOML file. The topology-only
/// `PipelineManifest::parse` ignores the `[assembly]` section, so existing
/// topology consumers (the `graph` subcommand, the fake runner) are unaffected
/// by enriching a manifest.
#[derive(Debug)]
pub struct DomainPipelineSpec {
    topology: PipelineManifest,
    assembly: BTreeMap<String, NodeAssembly>,
}

impl DomainPipelineSpec {
    /// Parse from enriched-manifest TOML. Validates that the `[pipeline]` node
    /// set and the `[assembly.*]` entries describe exactly the same nodes.
    pub fn parse(toml_str: &str) -> Result<Self, AssemblyError> {
        let topology = PipelineManifest::parse(toml_str).map_err(|e| AssemblyError::Manifest(e.into()))?;
        let doc: AssemblyDoc =
            toml::from_str(toml_str).map_err(|e| AssemblyError::Parse(e.to_string()))?;
        let spec = Self { topology, assembly: doc.assembly };
        spec.validate_sections()?;
        Ok(spec)
    }

    /// Every topology node must have an assembly entry and vice versa — catches
    /// a node added to one section but not the other.
    fn validate_sections(&self) -> Result<(), AssemblyError> {
        for node in self.topology.nodes() {
            if !self.assembly.contains_key(node) {
                return Err(AssemblyError::SpecMismatch {
                    detail: format!("node `{node}` in [pipeline] has no [assembly.{node}] entry"),
                });
            }
        }
        let node_set: HashSet<&str> = self.topology.nodes().iter().map(|s| s.as_str()).collect();
        for key in self.assembly.keys() {
            if !node_set.contains(key.as_str()) {
                return Err(AssemblyError::SpecMismatch {
                    detail: format!("[assembly.{key}] has no matching node in [pipeline].nodes"),
                });
            }
        }
        Ok(())
    }

    pub fn topology(&self) -> &PipelineManifest {
        &self.topology
    }

    /// The build `kind` declared for a node id (e.g. `"source.markdown_inbox"`),
    /// read from its `[assembly.<id>]` overlay entry; `None` if the id is not in
    /// the spec. Read-only accessor for diagnostics (the M7 review harness's
    /// processor-chain capture). Does not affect assembly or any invariant.
    pub fn node_kind(&self, id: &str) -> Option<&str> {
        self.assembly.get(id).map(|a| a.kind.as_str())
    }

    pub(crate) fn assembly(&self) -> &BTreeMap<String, NodeAssembly> {
        &self.assembly
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ENRICHED: &str = r#"
        [pipeline]
        nodes = ["src", "snk"]
        edges = [["src", "snk"]]

        [assembly.src]
        kind = "source.markdown_inbox"

        [assembly.snk]
        kind = "sink.article_vault_plan"
    "#;

    #[test]
    fn parses_topology_and_assembly() {
        let spec = DomainPipelineSpec::parse(ENRICHED).unwrap();
        assert_eq!(spec.topology().nodes(), &["src", "snk"]);
        assert_eq!(spec.assembly().get("src").unwrap().kind, "source.markdown_inbox");
    }

    #[test]
    fn topology_only_parser_ignores_assembly_overlay() {
        // The core topology parser must still read the same enriched file.
        let m = PipelineManifest::parse(ENRICHED).unwrap();
        assert_eq!(m.nodes(), &["src", "snk"]);
    }

    #[test]
    fn rejects_node_without_assembly_entry() {
        let toml = r#"
            [pipeline]
            nodes = ["src", "snk"]
            edges = [["src", "snk"]]
            [assembly.src]
            kind = "source.markdown_inbox"
        "#;
        let err = DomainPipelineSpec::parse(toml).unwrap_err();
        assert!(matches!(err, AssemblyError::SpecMismatch { .. }), "got {err:?}");
    }

    #[test]
    fn rejects_orphan_assembly_entry() {
        let toml = r#"
            [pipeline]
            nodes = ["src"]
            edges = []
            [assembly.src]
            kind = "source.markdown_inbox"
            [assembly.ghost]
            kind = "sink.article_vault_plan"
        "#;
        let err = DomainPipelineSpec::parse(toml).unwrap_err();
        assert!(matches!(err, AssemblyError::SpecMismatch { .. }), "got {err:?}");
    }

    #[test]
    fn rejects_unknown_config_field() {
        let toml = r#"
            [pipeline]
            nodes = ["src"]
            edges = []
            [assembly.src]
            kind = "source.markdown_inbox"
            config = { bogus = "x" }
        "#;
        let err = DomainPipelineSpec::parse(toml).unwrap_err();
        assert!(matches!(err, AssemblyError::Parse(_)), "got {err:?}");
    }
}
