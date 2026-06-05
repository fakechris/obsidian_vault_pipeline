//! M14b — local ReferentCandidate extraction (experimental spike).
//!
//! Tests the `Source → Unit → Referent` direction: given the M14a.8 *accepted*
//! Units, name the OBJECTS those units talk about as **local** ReferentCandidates.
//!
//! Hard line (mirrors the M14a spike discipline):
//! - This is NOT canonicalization. No evergreen, no concept promotion, no
//!   canonical slugs, no alias-merge-into-canonical, no registry, no vault write.
//!   `surface_names` are local document strings, never slugs.
//! - A candidate may ONLY come from accepted Units — `Unit.text`,
//!   `Unit.arguments`, `evidence_quote`. The article title / metadata / free
//!   keywords / v2 `concepts[]` / MOC / KnowledgeMEM are FORBIDDEN sources.
//! - The deterministic [`validator`] owns grounding (a surface must trace to a
//!   supporting accepted unit under the SAME render-normalization that accepted
//!   the units); the LLM only classifies. `referents_ungrounded == 0` in the live
//!   set is the M14b analogue of M14a's `accepted_without_quote == 0`.
//! - NOT a `DomainBody` variant, NOT wired to a manifest / GraphAssembler /
//!   RunCycle. Runs via a hand-harness and produces a review pack.

use serde::{Deserialize, Serialize};

pub mod harness;
pub mod parser;
pub mod prompt;
pub mod review_pack;
pub mod validator;

pub use harness::{
    extract_referents, read_accepted_units, run_referent_extraction, seed_surfaces,
    ReferentExtractionRun,
};
pub use parser::{parse_referent_envelope, RawReferent};
pub use prompt::{build_referent_prompt, referent_model_request, REFERENT_PROMPT_ID};
pub use review_pack::write_referent_review_pack;
pub use validator::validate_referents;

/// What an object the units talk about IS. A CLOSED enum on purpose — a misclassi-
/// fication is the failure mode here, so a typo'd kind fails deserialize into a
/// rejected candidate rather than slipping through. `Concept` is NOT the default.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferentKind {
    /// A specific named thing: product, system, library, file, person, org, model,
    /// benchmark, or a named construct/feature of one (IdeaBlock, Raindrop, EverOS).
    Entity,
    /// A reusable, re-implementable abstraction with a statable boundary. Rare.
    Concept,
    /// Could be entity or concept and the units don't decide — KEEP, don't force.
    Ambiguous,
    /// Source-local wording / a model-coined handle for an action or recommendation
    /// (a directive's gerund subject), or a rhetorical phrase — not a reusable object.
    LocalPhrase,
    /// Not a real referent: a bare property/predicate, a placeholder, metadata
    /// (author/product/handle/marketing figure), or the article's umbrella thesis.
    Noise,
}

impl ReferentKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ReferentKind::Entity => "entity",
            ReferentKind::Concept => "concept",
            ReferentKind::Ambiguous => "ambiguous",
            ReferentKind::LocalPhrase => "local_phrase",
            ReferentKind::Noise => "noise",
        }
    }
}

/// What a genuine concept includes (and ideally excludes). Required IFF
/// `kind == Concept`; sourced ONLY from supporting units, never world knowledge.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Boundary {
    pub includes: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub excludes: Option<String>,
}

/// A back-pointer to a supporting accepted unit. `ref_id` / `locatable` are COPIED
/// from that unit by the validator (audit provenance), never trusted from the LLM.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReferentEvidence {
    pub unit_id: String,
    pub ref_id: String,
    /// Was a surface grounded in this unit's QUOTE (not just text)? A stronger
    /// signal than text-only grounding.
    pub locatable: bool,
}

/// A local object the accepted units talk about.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReferentCandidate {
    /// Deterministic: `r-<index>-<hash8 of render_norm(surface_names[0])>`.
    pub id: String,
    /// Local in-document strings (NO slug). `[0]` is the most canonical surface.
    pub surface_names: Vec<String>,
    pub kind: ReferentKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subtype: Option<String>,
    pub support_unit_ids: Vec<String>,
    pub evidence_refs: Vec<ReferentEvidence>,
    pub rationale: String,
    /// `Some` IFF `kind == Concept`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub boundary: Option<Boundary>,
    /// `Some` IFF this candidate is on the rejected list.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reject_reason: Option<String>,
    /// Deterministic 0..1 (computed in Rust, not taken from the LLM).
    pub confidence: f64,
}

/// Per-kind tallies over the LIVE candidate set.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct KindCounts {
    pub entity: usize,
    pub concept: usize,
    pub ambiguous: usize,
    pub local_phrase: usize,
    pub noise: usize,
}

/// Fact-level metrics over one referent extraction — grounding invariants + the
/// regression alarms (concept_rate climbing toward v2, ambiguous_rate too timid).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReferentReport {
    pub total_candidates: usize,
    pub live: usize,
    pub rejected: usize,
    /// HARD INVARIANT: must be 0. A live candidate always traces to a support unit.
    pub referents_ungrounded: usize,
    pub kind_counts: KindCounts,
    /// concepts / live — alarm if it climbs toward v2's "everything is a concept".
    pub concept_rate: f64,
    /// ambiguous / live — health metric; too high (>~0.30) means the rubric is timid.
    pub ambiguous_rate: f64,
    /// Live candidates with >1 support unit (in-document repetition = real referent).
    pub grouped_candidates: usize,
    /// Live candidates merged by the deterministic dedup backstop.
    pub duplicates_collapsed: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parse_error: Option<String>,
}

/// The full result of classifying one source's accepted units into referents.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReferentExtraction {
    pub case_id: String,
    pub schema_version: u32,
    /// Grounded, structurally valid candidates (reject_reason == None).
    pub referents: Vec<ReferentCandidate>,
    /// Dropped candidates, each with a reject_reason.
    pub rejected: Vec<ReferentCandidate>,
    pub report: ReferentReport,
}

impl ReferentExtraction {
    pub fn by_kind(&self, kind: ReferentKind) -> impl Iterator<Item = &ReferentCandidate> {
        self.referents.iter().filter(move |r| r.kind == kind)
    }
}
