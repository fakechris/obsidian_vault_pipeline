//! `NodeRegistry` — the compiled-in catalog mapping `NodeKind` → node factory.
//!
//! **Assembly-only.** This is NOT a business/identity registry (it holds no
//! domain state, no authority, and is never consulted at runtime — only during
//! `GraphAssembler::assemble`). It exists solely to turn a `NodeKind` string
//! into the right `register_*` call. See `docs/architecture.md` "Deprecated
//! vocabulary" for why `registry` is otherwise a discouraged word here.

use std::collections::HashMap;

use ovp_core::GraphRunner;
use ovp_domain::{
    ArticleParser, ArticleVaultPlanSink, ConceptResolver, DomainBody, EvergreenConceptWriter,
    EvergreenSink, InboxScanSource, LLMInvoker, MarkdownInboxSource, PaperParser,
    PaperPromptBuilder, PaperVaultPlanSink, PromptBuilder, RouteBySourceKind, SourceResolver,
};

use crate::error::AssemblyError;
use crate::node_kind::{kinds, NodeCategory, NodeKind};
use crate::spec::NodeConfig;
use crate::wiring::AppWiring;

/// Everything a factory needs to build + register one node. The `runner` and
/// `wiring` are disjoint mutable borrows of caller locals, so a factory may
/// take a client out of `wiring` and then register the node into `runner`.
///
/// Internal plumbing: the node set is compiled in via `with_domain_nodes`, so
/// this and `NodeFactory` are crate-private — not public vocabulary. Promote to
/// `pub` only if/when in-process custom-node registration becomes a real need.
pub(crate) struct NodeBuildArgs<'a> {
    pub(crate) runner: &'a mut GraphRunner<DomainBody>,
    pub(crate) node_id: &'a str,
    pub(crate) config: &'a NodeConfig,
    pub(crate) wiring: &'a mut AppWiring,
}

/// Builds one concrete node from `(config, wiring)` and registers it into the
/// runner under `node_id` via the matching `register_*` method.
pub(crate) type NodeFactory = Box<dyn Fn(&mut NodeBuildArgs<'_>) -> Result<(), AssemblyError>>;

struct RegisteredNode {
    category: NodeCategory,
    factory: NodeFactory,
}

/// Catalog of node factories keyed by `NodeKind`.
pub struct NodeRegistry {
    nodes: HashMap<NodeKind, RegisteredNode>,
}

impl NodeRegistry {
    pub(crate) fn new() -> Self {
        Self { nodes: HashMap::new() }
    }

    /// Register a factory under `kind`. The kind's `<category>.` prefix must
    /// match `category` (debug-asserted — a registration-time hygiene check).
    /// Crate-private: the node set is compiled in via `with_domain_nodes`.
    pub(crate) fn register(&mut self, kind: NodeKind, category: NodeCategory, factory: NodeFactory) {
        debug_assert_eq!(
            kind.category(),
            Some(category),
            "NodeKind `{kind}` prefix does not match its category"
        );
        self.nodes.insert(kind, RegisteredNode { category, factory });
    }

    pub(crate) fn category_of(&self, kind: &NodeKind) -> Option<NodeCategory> {
        self.nodes.get(kind).map(|n| n.category)
    }

    pub(crate) fn factory_of(&self, kind: &NodeKind) -> Option<&NodeFactory> {
        self.nodes.get(kind).map(|n| &n.factory)
    }

    /// The compiled-in domain node set. Every kind the three shipped manifests
    /// (`article`, `article_evergreen`, `unified`) need, plus `inbox_scan`.
    pub fn with_domain_nodes() -> Self {
        let mut r = Self::new();

        // ---- sources ----
        r.register(
            NodeKind::new(kinds::MARKDOWN_INBOX),
            NodeCategory::Source,
            Box::new(|a| {
                let input = require_input(a)?;
                let src = MarkdownInboxSource::new(a.node_id, a.wiring.run_id().clone(), input);
                a.runner.register_source(a.node_id, src);
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::INBOX_SCAN),
            NodeCategory::Source,
            Box::new(|a| {
                let input = require_input(a)?;
                let src = InboxScanSource::new(a.node_id, a.wiring.run_id().clone(), input);
                a.runner.register_source(a.node_id, src);
                Ok(())
            }),
        );

        // ---- transforms (pure) ----
        r.register(
            NodeKind::new(kinds::SOURCE_RESOLVER),
            NodeCategory::Transform,
            Box::new(|a| {
                a.runner.register_transform(a.node_id, SourceResolver::new(a.node_id));
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::ROUTE_BY_SOURCE_KIND),
            NodeCategory::Transform,
            Box::new(|a| {
                a.runner.register_transform(a.node_id, RouteBySourceKind::new(a.node_id));
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::PROMPT_BUILDER),
            NodeCategory::Transform,
            Box::new(|a| {
                a.runner.register_transform(a.node_id, PromptBuilder::new(a.node_id));
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::PAPER_PROMPT_BUILDER),
            NodeCategory::Transform,
            Box::new(|a| {
                a.runner.register_transform(a.node_id, PaperPromptBuilder::new(a.node_id));
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::ARTICLE_PARSER),
            NodeCategory::Transform,
            Box::new(|a| {
                let parser = ArticleParser::new(a.node_id, a.wiring.area(), a.wiring.date_stamp());
                a.runner.register_transform(a.node_id, parser);
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::PAPER_PARSER),
            NodeCategory::Transform,
            Box::new(|a| {
                let parser = PaperParser::new(a.node_id, a.wiring.date_stamp());
                a.runner.register_transform(a.node_id, parser);
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::CONCEPT_RESOLVER),
            NodeCategory::Transform,
            Box::new(|a| {
                let name = a.config.registry.as_deref().ok_or_else(|| {
                    AssemblyError::MissingConfig { node_id: a.node_id.to_string(), field: "registry" }
                })?;
                let registry = a.wiring.registry(name).cloned().ok_or_else(|| {
                    AssemblyError::MissingWiring {
                        node_id: a.node_id.to_string(),
                        name: name.to_string(),
                    }
                })?;
                a.runner.register_transform(a.node_id, ConceptResolver::new(a.node_id, registry));
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::EVERGREEN_CONCEPT_WRITER),
            NodeCategory::Transform,
            Box::new(|a| {
                a.runner
                    .register_transform(a.node_id, EvergreenConceptWriter::new(a.node_id));
                Ok(())
            }),
        );

        // ---- effectful transforms ----
        r.register(
            NodeKind::new(kinds::LLM_INVOKER),
            NodeCategory::Effect,
            Box::new(|a| {
                let name = a.config.client.as_deref().ok_or_else(|| {
                    AssemblyError::MissingConfig { node_id: a.node_id.to_string(), field: "client" }
                })?;
                let client = a.wiring.take_client(name).ok_or_else(|| AssemblyError::MissingWiring {
                    node_id: a.node_id.to_string(),
                    name: name.to_string(),
                })?;
                a.runner
                    .register_effectful_transform(a.node_id, LLMInvoker::new(a.node_id, client));
                Ok(())
            }),
        );

        // ---- sinks ----
        r.register(
            NodeKind::new(kinds::ARTICLE_VAULT_PLAN),
            NodeCategory::Sink,
            Box::new(|a| {
                let sink = ArticleVaultPlanSink::new(a.node_id, a.wiring.run_id().clone());
                a.runner.register_sink(a.node_id, sink);
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::PAPER_VAULT_PLAN),
            NodeCategory::Sink,
            Box::new(|a| {
                let sink = PaperVaultPlanSink::new(a.node_id, a.wiring.run_id().clone());
                a.runner.register_sink(a.node_id, sink);
                Ok(())
            }),
        );
        r.register(
            NodeKind::new(kinds::EVERGREEN_SINK),
            NodeCategory::Sink,
            Box::new(|a| {
                let sink = EvergreenSink::new(a.node_id, a.wiring.run_id().clone());
                a.runner.register_sink(a.node_id, sink);
                Ok(())
            }),
        );

        r
    }
}

impl Default for NodeRegistry {
    fn default() -> Self {
        Self::with_domain_nodes()
    }
}

/// Shared helper: a source needs `AppWiring::input_path`.
fn require_input(a: &NodeBuildArgs<'_>) -> Result<std::path::PathBuf, AssemblyError> {
    a.wiring
        .input_path()
        .map(|p| p.to_path_buf())
        .ok_or_else(|| AssemblyError::MissingWiring {
            node_id: a.node_id.to_string(),
            name: "input_path".to_string(),
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn with_domain_nodes_has_every_shipped_kind() {
        let r = NodeRegistry::with_domain_nodes();
        for k in [
            kinds::MARKDOWN_INBOX,
            kinds::SOURCE_RESOLVER,
            kinds::ROUTE_BY_SOURCE_KIND,
            kinds::PROMPT_BUILDER,
            kinds::PAPER_PROMPT_BUILDER,
            kinds::LLM_INVOKER,
            kinds::ARTICLE_PARSER,
            kinds::PAPER_PARSER,
            kinds::CONCEPT_RESOLVER,
            kinds::EVERGREEN_CONCEPT_WRITER,
            kinds::ARTICLE_VAULT_PLAN,
            kinds::PAPER_VAULT_PLAN,
            kinds::EVERGREEN_SINK,
        ] {
            assert!(r.category_of(&NodeKind::new(k)).is_some(), "missing factory for {k}");
        }
    }

    #[test]
    fn unknown_kind_has_no_factory() {
        let r = NodeRegistry::with_domain_nodes();
        assert!(r.factory_of(&NodeKind::new("transform.nonexistent")).is_none());
    }
}
