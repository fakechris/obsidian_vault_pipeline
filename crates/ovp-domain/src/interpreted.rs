use serde::{Deserialize, Deserializer, Serialize};

/// Deserializer that accepts both a missing field AND JSON `null`, treating
/// either as `Default::default()`. `#[serde(default)]` alone only handles
/// the missing case — a model that emits `"merge_with": null` would otherwise
/// fail the parse with "invalid type: null, expected a sequence". A real LLM
/// does this routinely for "no items here" semantics, so the v2 concept-map
/// fields use this to match the prompt's "null is fine" promise.
pub(crate) fn null_to_default<'de, T, D>(deserializer: D) -> Result<T, D::Error>
where
    T: Default + Deserialize<'de>,
    D: Deserializer<'de>,
{
    let opt = Option::deserialize(deserializer)?;
    Ok(opt.unwrap_or_default())
}

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
    /// Which interpretation schema produced this doc (M13.3). The
    /// `EvergreenConceptWriter` branches on THIS, never on `concepts.is_empty()`,
    /// so a v2 doc whose concept map gates to empty fails LOUD instead of
    /// silently falling back to the v1 candidate / shared-`one_liner` path.
    /// `#[serde(default)]` → ArticleV1, so pre-marker serialized docs and the v1
    /// path keep their meaning.
    #[serde(default)]
    pub schema: InterpretationSchema,
    /// M13 v2 concept map: source-grounded concepts each carrying their OWN
    /// definition + claims + evidence. **Empty for v1 responses** (the legacy
    /// `concept_candidates` + shared-`one_liner` path still applies). Populated
    /// only by the v2 `article_concept_map` prompt; when non-empty,
    /// `ConceptResolver` gates it and `EvergreenConceptWriter` mints each note
    /// from its concept's own fields instead of the article one-liner.
    #[serde(default)]
    pub concepts: Vec<ExtractedConcept>,
}

/// Which interpretation prompt/schema produced an [`InterpretedDoc`]. An
/// EXPLICIT marker — not inferred from whether `concepts` is empty — so the
/// writer can tell "v1 doc" apart from "v2 doc whose map gated to empty" and
/// fail loud on the latter rather than silently mint the v1 candidate path.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum InterpretationSchema {
    /// Legacy `article_interpret/v1`: flat `concept_candidates` + a shared
    /// article-level `one_liner` definition.
    #[default]
    ArticleV1,
    /// `article_concept_map/v2`: per-concept `concepts` with their own
    /// definitions / claims / evidence.
    ConceptMapV2,
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
    #[serde(default, deserialize_with = "null_to_default")]
    pub aliases: Vec<String>,
    pub kind: ConceptKind,
    pub definition: String,
    #[serde(default, deserialize_with = "null_to_default")]
    pub evidence: Vec<String>,
    #[serde(default, deserialize_with = "null_to_default")]
    pub claims: Vec<String>,
    #[serde(default, deserialize_with = "null_to_default")]
    pub related: Vec<String>,
    #[serde(default, deserialize_with = "null_to_default")]
    pub merge_with: Vec<String>,
    #[serde(default)]
    pub reject_reason: Option<String>,
    /// REQUIRED (no serde default). A real model omitting `promote` is a common
    /// failure mode; defaulting a missing field to `false` would silently drop
    /// every concept as `not_promoted`, leaving the run "successful" with an
    /// empty concept map. Keeping it required makes a missing field fail LOUD at
    /// JSON parse (`transform.article_parser.json_parse`, "missing field
    /// `promote`") rather than silently. An explicit `false` is still a valid
    /// "do not mint this" signal the gate honors.
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
