//! M14a — Grounded Knowledge Unit extraction (experimental spike).
//!
//! A **parallel, deletable** extraction root: `Source → Unit`, where every Unit
//! is backed by a verbatim quote the validator confirms exists in the source.
//! This module is NOT wired into the typed pipeline (no `DomainBody` variant, no
//! manifest, no GraphAssembler) — it runs via a hand-harness ([`harness`]) and
//! produces a human-inspectable review pack ([`review_pack`]). See
//! `docs/stage-m14a-grounded-units.md`.
//!
//! Hard line: the validator enforces **grounding + structure** (quote found in
//! source, required enums present, arguments locatable). It does NOT and cannot
//! enforce *semantic correctness* (is `text` faithful, is the attribution/
//! modality value right) — that is for human review.

use serde::{Deserialize, Serialize};

pub mod harness;
pub mod parser;
pub mod prompt;
pub mod review_pack;
pub mod source_map;
pub mod validator;

pub use harness::{extract_units, read_source_from_path, run_unit_extraction, UnitExtractionRun};
pub use parser::{parse_envelope, ParseError, RawUnit};
pub use prompt::{build_unit_prompt, unit_model_request, UNIT_PROMPT_ID, UNIT_SCHEMA_VERSION};
pub use review_pack::write_unit_review_pack;
pub use source_map::{annotate, find_paragraph, paragraphs, Paragraph};
pub use validator::validate;

/// What kind of thing a Unit states. Deliberately tiny — classification beyond
/// this (entity vs concept, promotion) is explicitly out of M14a scope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UnitKind {
    /// A fact / claim / definition / observation / result / limitation.
    Assertion,
    /// A recommendation / decision / procedure step.
    Directive,
    /// The source explicitly connects two things (both in `arguments`).
    Relation,
    /// An open problem / research question the source poses.
    Question,
}

/// Whose voice a Unit is in. REQUIRED on every unit — distinguishing the
/// author's own assertion from a reported/disputed view is the single most
/// important thing a paraphrase-memory loses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Attribution {
    /// The article's own assertion, in its own voice.
    Author,
    /// A statement the article attributes to someone else.
    QuotedPerson,
    /// An inference the source does not state outright (use sparingly).
    SystemInterpretation,
}

/// How strongly a Unit is held. REQUIRED. `Contested`/`Negated` are how a view
/// the author disputes is kept out of the author's mouth.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Modality {
    Asserted,
    Suggested,
    Uncertain,
    /// A view the author argues against / presents as disputed.
    Contested,
    /// A statement that something is NOT the case.
    Negated,
}

/// An object/term a Unit is about. `surface` is the text as it appears; M14a
/// does NOT classify these into entity/concept (that is M14b).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Argument {
    pub surface: String,
    #[serde(default)]
    pub role: String,
    /// Set by the validator: was `surface` found in the quote or near-context?
    #[serde(default)]
    pub locatable: bool,
}

/// How the validator matched the quote against the source. The confidence ladder
/// that decides accepted-eligible (`Exact`/`Whitespace`) vs `needs_review`
/// (`Relaxed`) vs rejected (no match).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MatchKind {
    /// Verbatim substring of the source.
    Exact,
    /// Matches after whitespace-insensitive comparison.
    Whitespace,
    /// Matches after a faithful plain-text render of BOTH sides — markdown link
    /// text extraction, smart-quote/dash + fullwidth-CJK folding, emphasis strip,
    /// case-fold. Still grounded (the rendering is faithful + deterministic), so
    /// accepted; located at paragraph granularity (exact sub-offsets are lost in
    /// the transform). M14a.RCA showed this is the dominant recoverable failure:
    /// the model copies the *rendered* text, the source is raw markdown.
    Rendered,
}

/// Where the quote was found in the source — derived by the validator, never
/// trusted from the model.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvidenceLocation {
    pub byte_start: usize,
    pub byte_end: usize,
    /// 1-based line number of `byte_start` in the source body.
    pub line: usize,
    pub match_kind: MatchKind,
}

/// The quote a Unit is grounded on, the paragraph it was anchored to, and its
/// derived location (if the quote was found within that paragraph).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitEvidence {
    /// The `pNNN` id the model declared the quote came from (M14a.1).
    pub paragraph_ref: String,
    pub quote: String,
    /// `None` ⇒ the quote was not found in the referenced paragraph (the unit is
    /// rejected, or — if found in a *different* paragraph — needs review).
    pub location: Option<EvidenceLocation>,
}

/// Validation verdict for a Unit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UnitStatus {
    Accepted,
    Rejected,
    NeedsReview,
}

/// A single validation finding on a Unit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidationIssue {
    /// Stable dotted code, e.g. `unit.no_evidence`, `unit.quote_not_found`.
    pub code: String,
    pub detail: String,
}

impl ValidationIssue {
    pub fn new(code: &str, detail: impl Into<String>) -> Self {
        Self { code: code.to_string(), detail: detail.into() }
    }
}

/// A validated knowledge unit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unit {
    /// Deterministic id: `u-<index>-<quote-hash8>`.
    pub id: String,
    pub kind: UnitKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subtype: Option<String>,
    pub text: String,
    pub evidence: UnitEvidence,
    pub attribution: Attribution,
    pub modality: Modality,
    #[serde(default)]
    pub arguments: Vec<Argument>,
    pub status: UnitStatus,
    #[serde(default)]
    pub issues: Vec<ValidationIssue>,
}

/// Automatic, fact-level metrics over one extraction. No "is the KB smarter"
/// scoring — just the grounding invariants.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ValidationReport {
    pub total: usize,
    pub accepted: usize,
    pub rejected: usize,
    pub needs_review: usize,
    /// Units whose quote was found in source / total units (0.0 if total == 0).
    pub quote_found_rate: f64,
    /// HARD INVARIANT: must be 0. An accepted unit always has a located quote.
    pub accepted_without_quote: usize,
    /// Locatable arguments / total arguments (1.0 if there are no arguments).
    pub argument_locatable_rate: f64,
    /// Groups of accepted units sharing a normalized text or quote (each group is
    /// a list of unit ids). Surfaced, not auto-resolved.
    pub duplicate_groups: Vec<Vec<String>>,
    /// Set when the model output could not be parsed as the unit envelope.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parse_error: Option<String>,
}

/// The full result of extracting + validating one source.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SourceExtraction {
    pub source_id: String,
    /// SHA-256 (hex) of the normalized source body.
    pub source_fingerprint: String,
    pub title: String,
    pub source_url: String,
    pub schema_version: u32,
    pub units: Vec<Unit>,
    pub report: ValidationReport,
}

impl SourceExtraction {
    pub fn accepted(&self) -> impl Iterator<Item = &Unit> {
        self.units.iter().filter(|u| u.status == UnitStatus::Accepted)
    }
    pub fn rejected(&self) -> impl Iterator<Item = &Unit> {
        self.units.iter().filter(|u| u.status == UnitStatus::Rejected)
    }
    pub fn needs_review(&self) -> impl Iterator<Item = &Unit> {
        self.units.iter().filter(|u| u.status == UnitStatus::NeedsReview)
    }
}
