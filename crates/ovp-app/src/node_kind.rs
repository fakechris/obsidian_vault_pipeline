//! `NodeKind` — the stable identifier mapping a manifest node to a concrete
//! node factory, and `NodeCategory` — which of the four runner registration
//! slots it occupies. A `NodeKind` is `<category>.<name>` by convention
//! (`source.markdown_inbox`, `effect.llm_invoker`, ...).

/// Which `GraphRunner` registration slot a node occupies.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeCategory {
    Source,
    Transform,
    Effect,
    Sink,
}

impl NodeCategory {
    /// The `NodeKind` prefix this category uses.
    pub fn prefix(self) -> &'static str {
        match self {
            NodeCategory::Source => "source",
            NodeCategory::Transform => "transform",
            NodeCategory::Effect => "effect",
            NodeCategory::Sink => "sink",
        }
    }
}

/// Stable identifier for a concrete node factory in the `NodeRegistry`.
///
/// A thin newtype over a string (not a bare `String`) so it is greppable and
/// owns the `<category>.<name>` convention. Manifests reference a `NodeKind`
/// by its string; `with_domain_nodes` registers the compiled-in set.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct NodeKind(String);

impl NodeKind {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// The category implied by the `<category>.` prefix, if recognized.
    pub fn category(&self) -> Option<NodeCategory> {
        match self.0.split('.').next() {
            Some("source") => Some(NodeCategory::Source),
            Some("transform") => Some(NodeCategory::Transform),
            Some("effect") => Some(NodeCategory::Effect),
            Some("sink") => Some(NodeCategory::Sink),
            _ => None,
        }
    }
}

impl std::fmt::Display for NodeKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// The canonical kind strings for the compiled-in domain node set. Manifests
/// should reference these exact values.
pub mod kinds {
    pub const MARKDOWN_INBOX: &str = "source.markdown_inbox";
    pub const INBOX_SCAN: &str = "source.inbox_scan";
    pub const SOURCE_RESOLVER: &str = "transform.source_resolver";
    pub const ROUTE_BY_SOURCE_KIND: &str = "transform.route_by_source_kind";
    pub const PROMPT_BUILDER: &str = "transform.prompt_builder";
    pub const PAPER_PROMPT_BUILDER: &str = "transform.paper_prompt_builder";
    pub const LLM_INVOKER: &str = "effect.llm_invoker";
    pub const ARTICLE_PARSER: &str = "transform.article_parser";
    pub const PAPER_PARSER: &str = "transform.paper_parser";
    pub const CONCEPT_RESOLVER: &str = "transform.concept_resolver";
    pub const EVERGREEN_CONCEPT_WRITER: &str = "transform.evergreen_concept_writer";
    pub const ARTICLE_VAULT_PLAN: &str = "sink.article_vault_plan";
    pub const PAPER_VAULT_PLAN: &str = "sink.paper_vault_plan";
    pub const EVERGREEN_SINK: &str = "sink.evergreen_sink";
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn category_from_prefix() {
        assert_eq!(NodeKind::new(kinds::MARKDOWN_INBOX).category(), Some(NodeCategory::Source));
        assert_eq!(NodeKind::new(kinds::SOURCE_RESOLVER).category(), Some(NodeCategory::Transform));
        assert_eq!(NodeKind::new(kinds::LLM_INVOKER).category(), Some(NodeCategory::Effect));
        assert_eq!(NodeKind::new(kinds::ARTICLE_VAULT_PLAN).category(), Some(NodeCategory::Sink));
        assert_eq!(NodeKind::new("bogus.kind").category(), None);
        assert_eq!(NodeKind::new("noprefix").category(), None);
    }

    #[test]
    fn every_known_kind_prefix_matches_a_category() {
        for k in [
            kinds::MARKDOWN_INBOX,
            kinds::INBOX_SCAN,
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
            assert!(NodeKind::new(k).category().is_some(), "kind {k} has no category prefix");
        }
    }
}
