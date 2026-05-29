use std::collections::HashSet;

use ovp_core::GraphRunner;
use ovp_domain::DomainBody;

use crate::error::AssemblyError;
use crate::node_kind::{NodeCategory, NodeKind};
use crate::registry::{NodeBuildArgs, NodeRegistry};
use crate::spec::DomainPipelineSpec;
use crate::wiring::AppWiring;

/// Builds a runnable `GraphRunner<DomainBody>` from a `DomainPipelineSpec` and
/// the app's `AppWiring`, using a `NodeRegistry` of compiled-in factories.
///
/// Validation happens before any node is built (unknown kind, category vs
/// edges), so assembly fails loudly and never leaves a half-wired runner.
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
        // 1. Every kind must be known, and categories must agree with the
        //    topology (source ⇒ no inbound edge, sink ⇒ no outbound edge).
        self.validate_categories(spec)?;

        // 2. Topology drives the runner; node order matches the manifest.
        let mut runner = GraphRunner::new(spec.topology().clone(), wiring.run_id().clone());

        // 3. Build + register each node through its factory.
        for node_id in spec.topology().nodes() {
            let na = spec.assembly().get(node_id).expect("validated by DomainPipelineSpec::parse");
            let kind = NodeKind::new(na.kind.clone());
            let factory = self.registry.factory_of(&kind).ok_or_else(|| AssemblyError::UnknownKind {
                node_id: node_id.clone(),
                kind: na.kind.clone(),
            })?;
            let mut args = NodeBuildArgs {
                runner: &mut runner,
                node_id: node_id.as_str(),
                config: &na.config,
                wiring: &mut wiring,
            };
            factory(&mut args)?;
        }

        Ok(runner)
    }

    fn validate_categories(&self, spec: &DomainPipelineSpec) -> Result<(), AssemblyError> {
        let edges = spec.topology().edges();
        let has_inbound: HashSet<&str> = edges.iter().map(|[_, to]| to.as_str()).collect();
        let has_outbound: HashSet<&str> = edges.iter().map(|[from, _]| from.as_str()).collect();

        for node_id in spec.topology().nodes() {
            let na = spec.assembly().get(node_id).expect("validated by DomainPipelineSpec::parse");
            let kind = NodeKind::new(na.kind.clone());
            let category = self.registry.category_of(&kind).ok_or_else(|| AssemblyError::UnknownKind {
                node_id: node_id.clone(),
                kind: na.kind.clone(),
            })?;
            match category {
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
}
