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
    /// M13 v2 concept map: source-grounded concepts each carrying their OWN
    /// definition + claims + evidence. **Empty for v1 responses** (the legacy
    /// `concept_candidates` + shared-`one_liner` path still applies). Populated
    /// only by the v2 `article_concept_map` prompt; when non-empty,
    /// `ConceptResolver` gates it and `EvergreenConceptWriter` mints each note
    /// from its concept's own fields instead of the article one-liner.
    #[serde(default)]
    pub concepts: Vec<ExtractedConcept>,
}

/// What kind of thing a concept is. Small, closed vocabulary (no Nowledge
/// terms). Drives nothing structural in v2 beyond being recorded on the note.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ConceptKind {
    Concept,
    Principle,
    Procedure,
    Taxonomy,
    System,
    Claim,
}

/// A single source-grounded concept extracted from an article (v2 concept
/// map). Unlike v1 — where a flat `linked_concepts` slug list forced the
/// writer to fabricate per-concept content from article-level fields — each
/// `ExtractedConcept` owns its `definition`, `claims`, and `evidence`. The
/// `merge_with` / `reject_reason` / `promote` hints feed the `ConceptResolver`
/// gate; `evidence` is the grounding the gate requires before minting.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExtractedConcept {
    pub slug: String,
    pub title: String,
    #[serde(default)]
    pub aliases: Vec<String>,
    pub kind: ConceptKind,
    pub definition: String,
    #[serde(default)]
    pub evidence: Vec<String>,
    #[serde(default)]
    pub claims: Vec<String>,
    #[serde(default)]
    pub related: Vec<String>,
    #[serde(default)]
    pub merge_with: Vec<String>,
    #[serde(default)]
    pub reject_reason: Option<String>,
    #[serde(default)]
    pub promote: bool,
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
