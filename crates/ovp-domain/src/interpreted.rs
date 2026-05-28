use serde::{Deserialize, Serialize};

/// The structured result of ArticleParser. Frontmatter-shaped envelope
/// plus a typed six-dimension body. ArticleVaultPlanSink renders this
/// to markdown for the vault; the contract assertion engine inspects
/// `Dimensions` fields directly without parsing markdown.
///
/// v1 covers the article shape only. When paper / github interpretation
/// arrives, this struct splits into an enum keyed by source kind.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InterpretedDoc {
    pub title: String,
    pub source_url: String,
    pub author: Option<String>,
    /// ISO 8601 date the interpretation was produced. **Not** the source
    /// publication date. Matches the legacy article convention; papers
    /// will use a separate `source_date` field when they land.
    pub date: String,
    /// `"article"` in v1. Later: `"paper"`, `"github_overview"`, ...
    pub doc_type: String,
    /// PARA area: `"ai"` | `"tools"` | `"investing"` | `"programming"`.
    pub area: String,
    pub tags: Vec<String>,
    pub canonical_concepts: Vec<String>,
    pub concept_candidates: Vec<String>,
    pub dimensions: Dimensions,
}

/// The six dimensions the article contract requires. Each field is a
/// concrete, assert-able piece of the interpretation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Dimensions {
    /// Dim 1 — Definition: one-sentence concept summary.
    pub one_liner: String,
    /// Dim 2 — Explanation: what / why / how.
    pub explanation: Explanation,
    /// Dim 3 — Details: ≥3 specific, verifiable points.
    pub details: Vec<String>,
    /// Dim 4 — Structure: ASCII / mermaid diagram or table. Optional —
    /// some articles don't lend themselves to a structural view.
    pub structure: Option<String>,
    /// Dim 5 — Actionable: ≥1 concrete suggestion (short-term / long-term).
    pub actions: Vec<String>,
    /// Dim 6 — Linking: related concepts as plain slug strings.
    /// Promotion into canonical_concepts vs concept_candidates is
    /// decided by the absorb step, not by the interpreter.
    pub linked_concepts: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Explanation {
    pub what: String,
    pub why: String,
    pub how: String,
}
