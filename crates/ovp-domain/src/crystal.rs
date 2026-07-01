//! M22 — Crystal pre-write gates (citation linter + deterministic provenance
//! scoring). A *Crystal candidate* is a cross-source synthesis: each claim must
//! cite the grounded Units that support it. Before any (future) durable write, a
//! candidate passes through two gates here:
//!
//! 1. **Citation linter** ([`lint_candidate`]) — MECHANICAL, no model: does every
//!    citation resolve to a real accepted Unit, and is the cited quote a verbatim
//!    substring of that Unit's already-source-verified quote? Reuses the SAME
//!    matcher as the truth-layer validator (`deterministic_contains`) so the
//!    Crystal gate can never drift from the reader trunk. The authoritative line
//!    is taken FROM the resolved Unit, never from the model — so line-number drift
//!    is impossible by construction.
//! 2. **Provenance scoring** ([`score_candidate`]) — DETERMINISTIC signals only
//!    (distinct supporting sources, verbatim+accepted ratio, citation
//!    concentration). Recommends `Durable | Caveated | Quarantine`. A claim that
//!    fails the linter is forced to `Quarantine` — it can never be written
//!    durably (the Crystal analog of the trunk's `accepted_without_quote=0`).
//!
//! What is deliberately NOT here: the semantic "claim strength exceeds evidence
//! strength / over-synthesis" judgment is a SEPARATE, model-based, clearly
//! labeled step (a review workflow) — it is not mixed into this deterministic
//! score, so the score stays auditable. No durable write, no graph, no Referent.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// M32 — the `crystal-synth` turnkey stages (catalog collection, keyword
/// clustering, cross-source synthesis + claim-strength model calls, grounded
/// filtering). Pure/deterministic helpers plus two cassette-replayable model
/// stages; reuses the gate functions in this module and NEVER touches demoted
/// substrate.
pub mod synth;

use crate::units::validator::deterministic_contains;
use crate::units::{Unit, UnitStatus};

// ---- Candidate input shapes (deserialized from a synthesis draft) ----

/// One structured, resolvable citation: which accepted Unit, in which case, and
/// the verbatim span the claim leans on. `claimed_line` is optional and ADVISORY
/// — the linter resolves the authoritative line from the Unit, not from this.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Citation {
    pub case_id: String,
    pub unit_id: String,
    pub quote: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claimed_line: Option<usize>,
}

/// One cross-source synthesis claim with its supporting citations.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CrystalClaim {
    pub id: String,
    pub claim: String,
    #[serde(default)]
    pub theme: String,
    #[serde(default)]
    pub citations: Vec<Citation>,
    /// Recorded counter-evidence / limit / cross-source tension (or None).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub caveat: Option<String>,
}

/// A Crystal candidate: the synthesis draft before any gate. NOT durable.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CrystalCandidate {
    pub items: Vec<CrystalClaim>,
}

/// Per-case accepted Units the linter resolves citations against. Built from the
/// reader packs' `units.accepted.json` (one entry per case_id). BTreeMap for
/// deterministic iteration.
pub type GroundingIndex = BTreeMap<String, Vec<Unit>>;

// ---- Linter output ----

/// Why a single citation failed to ground (None ⇒ it grounded cleanly).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CitationDefect {
    /// The cited `case_id` is not in the grounding index.
    CaseNotFound,
    /// The cited `unit_id` does not exist in that case.
    UnitNotFound,
    /// The Unit exists but was not Accepted (so it is not a grounded fact).
    UnitNotAccepted,
    /// The cited quote is not a verbatim substring of the Unit's source-verified quote.
    QuoteNotInUnit,
}

/// The verdict for one citation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CitationVerdict {
    pub case_id: String,
    pub unit_id: String,
    pub grounded: bool,
    /// Authoritative source line, resolved FROM the Unit (never the model).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resolved_line: Option<usize>,
    /// Set iff the candidate supplied a `claimed_line` that disagrees with the
    /// resolved line — advisory (the resolved line is authoritative).
    pub claimed_line_mismatch: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub defect: Option<CitationDefect>,
}

/// The lint result for one claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimLint {
    pub claim_id: String,
    pub n_citations: usize,
    pub n_grounded: usize,
    pub distinct_sources: usize,
    pub has_caveat: bool,
    /// True iff there is ≥1 citation and EVERY citation grounded cleanly.
    pub fully_grounded: bool,
    pub citations: Vec<CitationVerdict>,
}

/// The whole-candidate lint report.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CrystalLintReport {
    pub n_claims: usize,
    pub n_fully_grounded: usize,
    pub n_with_defects: usize,
    pub claims: Vec<ClaimLint>,
}

/// Lint one citation against the grounding index. Reuses the validator's
/// `deterministic_contains` so "verbatim" means exactly what the truth layer means.
fn lint_citation(c: &Citation, index: &GroundingIndex) -> CitationVerdict {
    let mut v = CitationVerdict {
        case_id: c.case_id.clone(),
        unit_id: c.unit_id.clone(),
        grounded: false,
        resolved_line: None,
        claimed_line_mismatch: false,
        defect: None,
    };
    let Some(units) = index.get(&c.case_id) else {
        v.defect = Some(CitationDefect::CaseNotFound);
        return v;
    };
    let Some(unit) = units.iter().find(|u| u.id == c.unit_id) else {
        v.defect = Some(CitationDefect::UnitNotFound);
        return v;
    };
    if unit.status != UnitStatus::Accepted {
        v.defect = Some(CitationDefect::UnitNotAccepted);
        return v;
    }
    // The Unit's quote is already source-verified by the validator. The claim's
    // cited quote must be a verbatim substring of it → transitive grounding.
    if !deterministic_contains(&unit.evidence.quote, &c.quote) {
        v.defect = Some(CitationDefect::QuoteNotInUnit);
        return v;
    }
    let resolved = unit.evidence.location.as_ref().map(|l| l.line);
    v.resolved_line = resolved;
    v.claimed_line_mismatch = matches!((c.claimed_line, resolved), (Some(a), Some(b)) if a != b);
    v.grounded = true;
    v
}

/// Lint a whole Crystal candidate. Pure + deterministic.
pub fn lint_candidate(candidate: &CrystalCandidate, index: &GroundingIndex) -> CrystalLintReport {
    let mut claims = Vec::with_capacity(candidate.items.len());
    for item in &candidate.items {
        let citations: Vec<CitationVerdict> =
            item.citations.iter().map(|c| lint_citation(c, index)).collect();
        let n_grounded = citations.iter().filter(|c| c.grounded).count();
        let mut sources: Vec<&str> =
            citations.iter().filter(|c| c.grounded).map(|c| c.case_id.as_str()).collect();
        sources.sort_unstable();
        sources.dedup();
        let fully_grounded = !citations.is_empty() && n_grounded == citations.len();
        claims.push(ClaimLint {
            claim_id: item.id.clone(),
            n_citations: citations.len(),
            n_grounded,
            distinct_sources: sources.len(),
            has_caveat: item.caveat.as_ref().is_some_and(|s| !s.trim().is_empty()),
            fully_grounded,
            citations,
        });
    }
    let n_fully_grounded = claims.iter().filter(|c| c.fully_grounded).count();
    let n_with_defects = claims.iter().filter(|c| !c.fully_grounded).count();
    CrystalLintReport {
        n_claims: claims.len(),
        n_fully_grounded,
        n_with_defects,
        claims,
    }
}

// ---- Provenance scoring (deterministic) ----

/// Advisory recommendation for a claim. NOT a durable write — just where the
/// gate would route it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProvenanceClass {
    /// Strong, multi-source, fully grounded — eligible for durable Crystal.
    Durable,
    /// Grounded but thin/single-source — keep as a caveated insight, not durable truth.
    Caveated,
    /// A citation failed the linter — cannot be written durably under any score.
    Quarantine,
}

/// Frozen thresholds (set BEFORE the judging run; do not tune after). A claim is
/// Durable only if it is fully grounded, draws on ≥2 distinct sources, and clears
/// the score bar; anything grounded-but-weak is Caveated; ungrounded is Quarantine.
const DURABLE_MIN_SCORE: f64 = 0.70;
const DURABLE_MIN_SOURCES: usize = 2;

/// Deterministic provenance signals + score for one claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProvenanceScore {
    pub claim_id: String,
    pub distinct_sources: usize,
    pub cross_article: bool,
    pub all_citations_grounded: bool,
    /// 1.0 = every citation grounded; lower = some defects.
    pub grounded_fraction: f64,
    /// Concentration penalty: 1.0 all citations one Unit, 0.5 one case, 0.0 spread.
    pub concentration: f64,
    /// Composite 0..1 (see source for the frozen weights).
    pub score: f64,
    pub class: ProvenanceClass,
}

fn concentration(citations: &[CitationVerdict]) -> f64 {
    let grounded: Vec<&CitationVerdict> = citations.iter().filter(|c| c.grounded).collect();
    if grounded.len() <= 1 {
        return 1.0;
    }
    let mut units: Vec<(&str, &str)> = grounded.iter().map(|c| (c.case_id.as_str(), c.unit_id.as_str())).collect();
    units.sort_unstable();
    units.dedup();
    if units.len() == 1 {
        return 1.0; // all from one Unit
    }
    let mut cases: Vec<&str> = grounded.iter().map(|c| c.case_id.as_str()).collect();
    cases.sort_unstable();
    cases.dedup();
    if cases.len() == 1 {
        0.5 // multiple units but one source
    } else {
        0.0
    }
}

/// Score one claim from its lint result. Weights (frozen): grounding completeness
/// 0.5, source diversity 0.3 (capped at 3 sources), spread 0.2.
pub fn score_claim(lint: &ClaimLint) -> ProvenanceScore {
    let grounded_fraction =
        if lint.n_citations == 0 { 0.0 } else { lint.n_grounded as f64 / lint.n_citations as f64 };
    let conc = concentration(&lint.citations);
    let diversity = (lint.distinct_sources.min(3) as f64) / 3.0;
    let score = 0.5 * grounded_fraction + 0.3 * diversity + 0.2 * (1.0 - conc);
    let class = if !lint.fully_grounded {
        ProvenanceClass::Quarantine
    } else if score >= DURABLE_MIN_SCORE && lint.distinct_sources >= DURABLE_MIN_SOURCES {
        ProvenanceClass::Durable
    } else {
        ProvenanceClass::Caveated
    };
    ProvenanceScore {
        claim_id: lint.claim_id.clone(),
        distinct_sources: lint.distinct_sources,
        cross_article: lint.distinct_sources >= 2,
        all_citations_grounded: lint.fully_grounded,
        grounded_fraction,
        concentration: conc,
        score,
        class,
    }
}

/// Score a whole candidate (one score per claim, lint order preserved).
pub fn score_candidate(report: &CrystalLintReport) -> Vec<ProvenanceScore> {
    report.claims.iter().map(score_claim).collect()
}

// ---- Claim-strength gate (the SEMANTIC half) ----
//
// The deterministic gates above answer "do the citations resolve and how strong
// is the provenance?". They CANNOT answer "does the claim overreach the evidence
// it cites?". That judgment is made by a labeled LLM judge (a review workflow,
// NOT this module) which sees each claim PLUS its cited units' quote + attribution
// + modality, and returns a [`ClaimStrengthVerdict`]. The *combination* of the
// deterministic provenance class with that verdict is done HERE, deterministically,
// so the final routing stays auditable — only the per-claim judgment is the model's.

/// How a claim relates to the evidence it actually cited (the LLM judge's call).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StrengthClass {
    /// The claim is supported, at its stated strength, by the cited quotes.
    Supported,
    /// The claim asserts more than the cited quotes support (scope/quantifier creep).
    Overreach,
    /// The claim fuses distinct/partial points into a generalization the citations don't jointly support.
    OverSynthesized,
    /// The claim states as a system fact what the cited unit attributes/hedges as opinion (attribution/modality mismatch).
    OpinionAsFact,
}

/// One claim's semantic verdict, produced by the labeled LLM judge over the
/// claim + its cited units' quote/attribution/modality. Deserialized from the
/// claim-strength review workflow.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimStrengthVerdict {
    pub claim_id: String,
    pub strength: StrengthClass,
    /// Do the cited quotes, taken together, actually suffice for the claim?
    pub evidence_sufficient: bool,
    #[serde(default)]
    pub rationale: String,
}

/// The final routing for a claim after BOTH gates. A claim is `Durable` only when
/// the deterministic provenance gate AND the semantic claim-strength gate both
/// pass; ungrounded claims are `Reject`; everything grounded-but-weak (thin
/// provenance, overreach, over-synthesis, opinion-as-fact, insufficient evidence,
/// or a missing strength verdict) is `Caveated` — kept as a reviewable insight,
/// never written as durable truth.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FinalClass {
    Durable,
    Caveated,
    Reject,
}

/// Whether the supplied claim-strength verdicts COMPLETELY and cleanly cover a
/// candidate's claims. A `full pre-write run` requires `complete()` — partial /
/// duplicate / unknown verdicts must fail loud, never silently downgrade.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct StrengthCoverage {
    /// Candidate claim ids with NO verdict.
    pub missing: Vec<String>,
    /// claim_ids appearing more than once in the verdicts.
    pub duplicate: Vec<String>,
    /// verdict claim_ids that are not in the candidate.
    pub unknown: Vec<String>,
}

impl StrengthCoverage {
    pub fn complete(&self) -> bool {
        self.missing.is_empty() && self.duplicate.is_empty() && self.unknown.is_empty()
    }
}

/// Compute coverage of `verdicts` over the candidate's `claim_ids` (in candidate
/// order for `missing`; sorted+deduped for `duplicate`/`unknown`). Deterministic.
pub fn strength_coverage(claim_ids: &[String], verdicts: &[ClaimStrengthVerdict]) -> StrengthCoverage {
    use std::collections::BTreeSet;
    let claim_set: BTreeSet<String> = claim_ids.iter().cloned().collect();
    // count verdicts per id
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for v in verdicts {
        *counts.entry(v.claim_id.clone()).or_insert(0) += 1;
    }
    let mut missing: Vec<String> = Vec::new();
    for c in claim_ids {
        if !counts.contains_key(c) {
            missing.push(c.clone());
        }
    }
    let mut duplicate: Vec<String> = Vec::new();
    let mut unknown: Vec<String> = Vec::new();
    for (id, n) in &counts {
        if *n > 1 {
            duplicate.push(id.clone());
        }
        if !claim_set.contains(id) {
            unknown.push(id.clone());
        }
    }
    StrengthCoverage { missing, duplicate, unknown }
}

/// Combine the deterministic provenance class with the (optional) semantic
/// verdict into a final routing. Deterministic + total — the audit point.
pub fn final_routing(provenance: ProvenanceClass, strength: Option<&ClaimStrengthVerdict>) -> FinalClass {
    // Ungrounded can never be durable or even caveated-truth — reject outright.
    if provenance == ProvenanceClass::Quarantine {
        return FinalClass::Reject;
    }
    // No semantic verdict yet → not eligible for durable; hold as caveated.
    let Some(v) = strength else {
        return FinalClass::Caveated;
    };
    let semantically_clean = v.strength == StrengthClass::Supported && v.evidence_sufficient;
    if provenance == ProvenanceClass::Durable && semantically_clean {
        FinalClass::Durable
    } else {
        FinalClass::Caveated
    }
}

// ---- M23 minimal durable Crystal store ----
//
// Markdown/HTML is a VIEW; this is the truth layer. The store is an append-only
// event ledger (one JSON event per line). A claim is identified by a deterministic
// `claim_key` (hash of claim text + its citation set) so re-running the same input
// is idempotent. Supersede/retract are append events, never in-place edits — the
// history is always reconstructible. Only `final == Durable` claims are ever
// written; `caveated`/`reject` stay in the review output, never in durable truth.

/// Lifecycle of a durable claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CrystalStatus {
    Active,
    Superseded,
    Retracted,
    Draft,
}

/// An append-only ledger operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StoreOp {
    Write,
    Supersede,
    Retract,
}

/// A citation as persisted in a durable record (full chain, resolved line).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DurableCitation {
    pub case_id: String,
    pub unit_id: String,
    pub quote: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resolved_line: Option<usize>,
}

/// A durable Crystal record — the full audit chain for one claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DurableRecord {
    /// Deterministic identity (hash of claim text + citation set) — idempotency key.
    pub claim_key: String,
    pub claim_id: String,
    pub claim: String,
    pub theme: String,
    pub source_cases: Vec<String>,
    pub citations: Vec<DurableCitation>,
    pub provenance_score: f64,
    pub provenance_class: ProvenanceClass,
    pub strength: StrengthClass,
    pub strength_rationale: String,
    pub final_class: FinalClass,
    pub run_id: String,
    pub status: CrystalStatus,
}

/// One append-only ledger event.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StoreEvent {
    pub op: StoreOp,
    pub record: DurableRecord,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub supersedes: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// Deterministic claim identity: hash of the claim text + its citation set
/// (sorted `case:unit` pairs). Same claim+citations ⇒ same key ⇒ idempotent.
pub fn claim_key(claim_text: &str, citations: &[DurableCitation]) -> String {
    let mut pairs: Vec<String> =
        citations.iter().map(|c| format!("{}:{}", c.case_id, c.unit_id)).collect();
    pairs.sort();
    let mut h = Sha256::new();
    h.update(claim_text.trim().as_bytes());
    h.update([0u8]);
    h.update(pairs.join("|").as_bytes());
    format!("ck-{:x}", h.finalize())[..19].to_string()
}

/// A deterministic run id derived from the set of claim ids being written, so a
/// re-run with the same durable set is stable (no wall-clock). Caller may override.
pub fn default_run_id(claim_ids: &[String]) -> String {
    let mut ids = claim_ids.to_vec();
    ids.sort();
    let mut h = Sha256::new();
    h.update(ids.join("|").as_bytes());
    format!("run-{:x}", h.finalize())[..12].to_string()
}

/// Assemble a durable record from the gate outputs for one claim. `lint.citations`
/// and `claim.citations` are in the same order (the linter maps over them), so the
/// quote (candidate) and resolved line (linter) are zipped per citation.
pub fn build_durable_record(
    claim: &CrystalClaim,
    lint: &ClaimLint,
    score: &ProvenanceScore,
    strength: &ClaimStrengthVerdict,
    final_class: FinalClass,
    run_id: &str,
) -> DurableRecord {
    let citations: Vec<DurableCitation> = claim
        .citations
        .iter()
        .zip(lint.citations.iter())
        .map(|(c, v)| DurableCitation {
            case_id: c.case_id.clone(),
            unit_id: c.unit_id.clone(),
            quote: c.quote.clone(),
            resolved_line: v.resolved_line,
        })
        .collect();
    let mut source_cases: Vec<String> = citations.iter().map(|c| c.case_id.clone()).collect();
    source_cases.sort();
    source_cases.dedup();
    DurableRecord {
        claim_key: claim_key(&claim.claim, &citations),
        claim_id: claim.id.clone(),
        claim: claim.claim.clone(),
        theme: claim.theme.clone(),
        source_cases,
        citations,
        provenance_score: score.score,
        provenance_class: score.class,
        strength: strength.strength,
        strength_rationale: strength.rationale.clone(),
        final_class,
        run_id: run_id.to_string(),
        status: CrystalStatus::Active,
    }
}

/// Fold the append-only ledger into current state: the latest event per
/// `claim_key` decides its status (Write→Active, Retract→Retracted,
/// Supersede→Superseded). Returns records sorted by claim_id, status applied.
pub fn fold_ledger(events: &[StoreEvent]) -> Vec<DurableRecord> {
    let mut state: BTreeMap<String, DurableRecord> = BTreeMap::new();
    for ev in events {
        let key = ev.record.claim_key.clone();
        // Write and Supersede both make THIS record active (a Supersede also flips
        // its predecessor to Superseded, below); Retract marks this record retracted.
        let status = match ev.op {
            StoreOp::Write | StoreOp::Supersede => CrystalStatus::Active,
            StoreOp::Retract => CrystalStatus::Retracted,
        };
        let mut rec = ev.record.clone();
        rec.status = status;
        state.insert(key, rec);
        // A supersede also flips the superseded predecessor, if present.
        if ev.op == StoreOp::Supersede {
            if let Some(prev) = &ev.supersedes {
                if let Some(r) = state.get_mut(prev) {
                    r.status = CrystalStatus::Superseded;
                }
            }
        }
    }
    let mut out: Vec<DurableRecord> = state.into_values().collect();
    out.sort_by(|a, b| a.claim_id.cmp(&b.claim_id));
    out
}

/// The set of `claim_key`s currently Active in the ledger (idempotency check: a
/// Write for an already-active key is a no-op append).
pub fn active_keys(events: &[StoreEvent]) -> std::collections::BTreeSet<String> {
    fold_ledger(events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .map(|r| r.claim_key)
        .collect()
}

/// A non-durable (caveated/reject) claim, with enough context to be read on its
/// own — so a human reviewer does not have to re-open the candidate (M23 P2).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReviewEntry {
    pub claim_id: String,
    pub claim: String,
    pub theme: String,
    pub final_class: FinalClass,
    pub strength: StrengthClass,
    pub evidence_sufficient: bool,
    pub rationale: String,
}

/// Scope/policy header for a rendered Crystal view (M24). Counts are computed
/// from the data; this carries the human-set framing.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct CrystalHeader {
    pub title: String,
    pub scope: String,
    pub not_claiming: String,
}

/// Render a human-readable Crystal view: a scope/policy header, durable (Active)
/// claims with expandable provenance, and a clearly-separated review section
/// where each caveated/rejected claim is readable ON ITS OWN (claim text + theme
/// + strength + rationale) — never mixed into durable truth. Deterministic.
pub fn render_crystal_md(
    header: &CrystalHeader,
    active: &[DurableRecord],
    review: &[ReviewEntry],
) -> String {
    let title = if header.title.trim().is_empty() { "Crystal" } else { header.title.trim() };
    let mut m = format!("# {title} — durable knowledge\n\n");
    if !header.scope.trim().is_empty() {
        m.push_str(&format!("**Scope:** {}\n\n", header.scope.trim()));
    }
    m.push_str(&format!(
        "**Durable claims:** {} · **Review (caveated/rejected, not durable):** {}\n\n",
        active.len(),
        review.len()
    ));
    m.push_str(
        "**Evidence policy:** every durable claim is grounded — claim → cited accepted unit → \
         verbatim quote → source line — and passed both the citation/provenance gate and the \
         claim-strength gate. Caveated/rejected claims are listed in the Review section and are \
         NOT durable truth.\n\n",
    );
    if !header.not_claiming.trim().is_empty() {
        m.push_str(&format!("**What this Crystal is NOT claiming:** {}\n\n", header.not_claiming.trim()));
    }
    m.push_str("---\n\n## Durable claims\n\n");
    for (i, r) in active.iter().enumerate() {
        m.push_str(&format!("### {}. {}\n\n", i + 1, r.claim.trim()));
        m.push_str(&format!(
            "_{} · sources: {} · provenance {:.2} ({:?}) · strength {:?} · {:?} · key `{}`_\n\n",
            r.theme, r.source_cases.join(", "), r.provenance_score, r.provenance_class,
            r.strength, r.final_class, r.claim_key
        ));
        m.push_str(&format!("<details><summary>Provenance — {} citation(s)</summary>\n\n", r.citations.len()));
        for c in &r.citations {
            let line = c.resolved_line.map(|l| format!("line {l}")).unwrap_or_else(|| "—".into());
            m.push_str(&format!("- ({}) `{}` · {}: “{}”\n", c.case_id, c.unit_id, line, c.quote.trim()));
        }
        m.push_str("\n</details>\n\n");
    }
    m.push_str("---\n\n## Review (NOT durable) — caveated / rejected\n\n");
    if review.is_empty() {
        m.push_str("_none_\n");
    } else {
        for e in review {
            m.push_str(&format!("### {} [{:?}] — {}\n\n", e.claim_id, e.final_class, e.theme));
            m.push_str(&format!("{}\n\n", e.claim.trim()));
            m.push_str(&format!(
                "_strength: {:?} · evidence_sufficient: {} · why not durable:_ {}\n\n",
                e.strength, e.evidence_sufficient, e.rationale.trim()
            ));
        }
    }
    m
}

// ---- M25 Crystal Review Workbench: human-decision → revised candidate ----
//
// A human (helped by an AI evidence review + KMEM 旁证) decides what to do with a
// caveated claim. The decision is NEVER a durability verdict — it can only author
// a REVISED structured candidate that re-enters the SAME gate (linter +
// provenance + claim-strength). `apply_decisions` turns accepted rewrites/splits
// into a fresh `CrystalCandidate`; the gate (not the human) then decides durable.

/// What the reviewer chose for one caveated claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewAction {
    /// Replace the claim with one revised claim (narrower text and/or fewer citations).
    Rewrite,
    /// Replace the claim with several narrower claims (each re-gated independently).
    Split,
    /// Leave it caveated (no candidate produced).
    KeepCaveated,
    /// Discard it (no candidate produced).
    Reject,
}

/// A reviewer's decision for one caveated claim. `revisions` carries the new
/// claim(s) for `Rewrite` (exactly 1) / `Split` (≥2); ignored otherwise. Each
/// revision is a full structured claim (text + citations) so the linter can
/// re-verify verbatim grounding and the strength gate can re-judge scope.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReviewDecision {
    pub claim_id: String,
    pub action: ReviewAction,
    #[serde(default)]
    pub revisions: Vec<CrystalClaim>,
    #[serde(default)]
    pub note: String,
}

/// Outcome of applying decisions: the revised candidate + a per-decision log.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ApplyOutcome {
    pub revised: CrystalCandidate,
    /// `(original_claim_id, action, n_revisions)` in decision order.
    pub log: Vec<(String, ReviewAction, usize)>,
    /// Decisions whose `claim_id` is not in the original candidate (fail-loud signal).
    pub unknown: Vec<String>,
}

/// Build a revised candidate from reviewer decisions over the ORIGINAL candidate.
/// Only `Rewrite`/`Split` produce candidate claims; `KeepCaveated`/`Reject` drop
/// out (they never become durable). New claim ids are derived (`<id>r`, `<id>s1`…)
/// when a revision omits its own id, so they are traceable to the source claim.
/// Deterministic. Does NOT decide durability — that is the gate's job downstream.
pub fn apply_decisions(original: &CrystalCandidate, decisions: &[ReviewDecision]) -> ApplyOutcome {
    use std::collections::BTreeSet;
    let known: BTreeSet<&str> = original.items.iter().map(|c| c.id.as_str()).collect();
    let mut items: Vec<CrystalClaim> = Vec::new();
    let mut log: Vec<(String, ReviewAction, usize)> = Vec::new();
    let mut unknown: Vec<String> = Vec::new();
    for d in decisions {
        if !known.contains(d.claim_id.as_str()) {
            unknown.push(d.claim_id.clone());
            continue;
        }
        let n = match d.action {
            ReviewAction::Rewrite | ReviewAction::Split => {
                for (i, rev) in d.revisions.iter().enumerate() {
                    let mut c = rev.clone();
                    if c.id.trim().is_empty() {
                        c.id = if d.action == ReviewAction::Rewrite {
                            format!("{}r", d.claim_id)
                        } else {
                            format!("{}s{}", d.claim_id, i + 1)
                        };
                    }
                    items.push(c);
                }
                d.revisions.len()
            }
            ReviewAction::KeepCaveated | ReviewAction::Reject => 0,
        };
        log.push((d.claim_id.clone(), d.action, n));
    }
    ApplyOutcome { revised: CrystalCandidate { items }, log, unknown }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;

    /// Build accepted Units for a one-case grounding index from a body + quotes.
    fn index_for(case_id: &str, body: &str, quotes: &[&str]) -> GroundingIndex {
        let raw: Vec<_> = quotes
            .iter()
            .enumerate()
            .map(|(i, q)| serde_json::json!({
                "kind": "assertion", "text": format!("t{i}"), "evidence_ref": "p001",
                "evidence_quote": q, "attribution": "author", "modality": "asserted", "arguments": []
            }))
            .collect();
        let ex = validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![], body));
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        let mut idx = GroundingIndex::new();
        idx.insert(case_id.to_string(), units);
        idx
    }

    fn unit_id(index: &GroundingIndex, case: &str, n: usize) -> String {
        index[case][n].id.clone()
    }

    #[test]
    fn verbatim_citation_grounds_and_resolves_line_from_unit() {
        let body = "# H\n\nA chunk is a structurally neutral container. It knows nothing about ownership.";
        let idx = index_for("m18-01", body, &["A chunk is a structurally neutral container."]);
        let uid = unit_id(&idx, "m18-01", 0);
        let cand = CrystalCandidate { items: vec![CrystalClaim {
            id: "c1".into(), claim: "chunks are neutral".into(), theme: "x".into(),
            citations: vec![Citation { case_id: "m18-01".into(), unit_id: uid, quote: "structurally neutral container".into(), claimed_line: Some(999) }],
            caveat: None,
        }]};
        let rep = lint_candidate(&cand, &idx);
        assert_eq!(rep.n_fully_grounded, 1);
        let cv = &rep.claims[0].citations[0];
        assert!(cv.grounded);
        assert!(cv.resolved_line.is_some(), "line resolved from the unit, not the model");
        assert!(cv.claimed_line_mismatch, "the bogus claimed_line=999 is flagged, but resolved line wins");
    }

    #[test]
    fn nonverbatim_quote_is_quarantined() {
        let body = "A chunk is a structurally neutral container.";
        let idx = index_for("m18-01", body, &["A chunk is a structurally neutral container."]);
        let uid = unit_id(&idx, "m18-01", 0);
        let cand = CrystalCandidate { items: vec![CrystalClaim {
            id: "c1".into(), claim: "x".into(), theme: "x".into(),
            citations: vec![Citation { case_id: "m18-01".into(), unit_id: uid, quote: "chunks are wonderful and free".into(), claimed_line: None }],
            caveat: None,
        }]};
        let rep = lint_candidate(&cand, &idx);
        assert_eq!(rep.claims[0].citations[0].defect, Some(CitationDefect::QuoteNotInUnit));
        assert!(!rep.claims[0].fully_grounded);
        assert_eq!(score_candidate(&rep)[0].class, ProvenanceClass::Quarantine,
            "a non-verbatim citation can never be durable");
    }

    #[test]
    fn missing_case_and_unit_defects() {
        let idx = index_for("m18-01", "A chunk is neutral.", &["A chunk is neutral."]);
        let cand = CrystalCandidate { items: vec![CrystalClaim {
            id: "c1".into(), claim: "x".into(), theme: "x".into(),
            citations: vec![
                Citation { case_id: "m18-99".into(), unit_id: "u-x".into(), quote: "z".into(), claimed_line: None },
                Citation { case_id: "m18-01".into(), unit_id: "u-nope".into(), quote: "z".into(), claimed_line: None },
            ],
            caveat: None,
        }]};
        let rep = lint_candidate(&cand, &idx);
        let defects: Vec<_> = rep.claims[0].citations.iter().map(|c| c.defect.clone()).collect();
        assert_eq!(defects, vec![Some(CitationDefect::CaseNotFound), Some(CitationDefect::UnitNotFound)]);
    }

    #[test]
    fn cross_source_grounded_claim_is_durable() {
        // Two distinct cases, both verbatim → cross-article, spread, fully grounded.
        let mut idx = index_for("m18-01", "A chunk is a structurally neutral container.",
            &["A chunk is a structurally neutral container."]);
        let idx2 = index_for("m18-02", "Memory is scarce working memory in agents.",
            &["Memory is scarce working memory in agents."]);
        idx.extend(idx2);
        let u1 = unit_id(&idx, "m18-01", 0);
        let u2 = unit_id(&idx, "m18-02", 0);
        let cand = CrystalCandidate { items: vec![CrystalClaim {
            id: "c1".into(), claim: "grounded cross-source".into(), theme: "x".into(),
            citations: vec![
                Citation { case_id: "m18-01".into(), unit_id: u1, quote: "structurally neutral container".into(), claimed_line: None },
                Citation { case_id: "m18-02".into(), unit_id: u2, quote: "scarce working memory".into(), claimed_line: None },
            ],
            caveat: Some("benchmark-dependent".into()),
        }]};
        let rep = lint_candidate(&cand, &idx);
        assert!(rep.claims[0].fully_grounded);
        assert_eq!(rep.claims[0].distinct_sources, 2);
        let s = &score_candidate(&rep)[0];
        assert!(s.cross_article);
        assert_eq!(s.class, ProvenanceClass::Durable, "score={}", s.score);
    }

    #[test]
    fn single_source_grounded_claim_is_caveated_not_durable() {
        let idx = index_for("m18-01", "A chunk is a structurally neutral container. It knows nothing.",
            &["A chunk is a structurally neutral container.", "It knows nothing."]);
        let u0 = unit_id(&idx, "m18-01", 0);
        let cand = CrystalCandidate { items: vec![CrystalClaim {
            id: "c1".into(), claim: "single source".into(), theme: "x".into(),
            citations: vec![Citation { case_id: "m18-01".into(), unit_id: u0, quote: "structurally neutral".into(), claimed_line: None }],
            caveat: None,
        }]};
        let rep = lint_candidate(&cand, &idx);
        let s = &score_candidate(&rep)[0];
        assert!(rep.claims[0].fully_grounded);
        assert_eq!(s.class, ProvenanceClass::Caveated, "single source must not be durable (score={})", s.score);
    }

    #[test]
    fn empty_citations_claim_is_quarantined() {
        let idx = index_for("m18-01", "x.", &["x."]);
        let cand = CrystalCandidate { items: vec![CrystalClaim {
            id: "c1".into(), claim: "uncited".into(), theme: "x".into(), citations: vec![], caveat: None,
        }]};
        let rep = lint_candidate(&cand, &idx);
        assert!(!rep.claims[0].fully_grounded);
        assert_eq!(score_candidate(&rep)[0].class, ProvenanceClass::Quarantine);
    }

    // ---- claim-strength combiner ----

    fn verdict(strength: StrengthClass, ok: bool) -> ClaimStrengthVerdict {
        ClaimStrengthVerdict { claim_id: "c1".into(), strength, evidence_sufficient: ok, rationale: String::new() }
    }

    #[test]
    fn durable_requires_both_gates_pass() {
        let v = verdict(StrengthClass::Supported, true);
        assert_eq!(final_routing(ProvenanceClass::Durable, Some(&v)), FinalClass::Durable);
    }

    #[test]
    fn ungrounded_is_reject_regardless_of_strength() {
        let v = verdict(StrengthClass::Supported, true);
        assert_eq!(final_routing(ProvenanceClass::Quarantine, Some(&v)), FinalClass::Reject);
        assert_eq!(final_routing(ProvenanceClass::Quarantine, None), FinalClass::Reject);
    }

    #[test]
    fn missing_strength_verdict_holds_as_caveated() {
        // Grounded + durable provenance but no semantic judgment yet → not durable.
        assert_eq!(final_routing(ProvenanceClass::Durable, None), FinalClass::Caveated);
    }

    #[test]
    fn semantic_defects_downgrade_durable_to_caveated() {
        for s in [StrengthClass::Overreach, StrengthClass::OverSynthesized, StrengthClass::OpinionAsFact] {
            let v = verdict(s, true);
            assert_eq!(final_routing(ProvenanceClass::Durable, Some(&v)), FinalClass::Caveated,
                "{s:?} must not be durable");
        }
        // Supported but evidence insufficient also downgrades.
        let weak = verdict(StrengthClass::Supported, false);
        assert_eq!(final_routing(ProvenanceClass::Durable, Some(&weak)), FinalClass::Caveated);
    }

    #[test]
    fn caveated_provenance_stays_caveated_even_when_semantically_clean() {
        let v = verdict(StrengthClass::Supported, true);
        assert_eq!(final_routing(ProvenanceClass::Caveated, Some(&v)), FinalClass::Caveated);
    }

    // ---- strength coverage ----

    fn vfor(id: &str) -> ClaimStrengthVerdict {
        ClaimStrengthVerdict { claim_id: id.into(), strength: StrengthClass::Supported, evidence_sufficient: true, rationale: String::new() }
    }

    #[test]
    fn coverage_complete_when_one_to_one() {
        let ids = vec!["c1".to_string(), "c2".to_string()];
        let cov = strength_coverage(&ids, &[vfor("c1"), vfor("c2")]);
        assert!(cov.complete(), "{cov:?}");
    }

    #[test]
    fn coverage_flags_missing_duplicate_unknown() {
        let ids = vec!["c1".to_string(), "c2".to_string(), "c3".to_string()];
        // c2 missing; c1 duplicated; c9 unknown.
        let cov = strength_coverage(&ids, &[vfor("c1"), vfor("c1"), vfor("c3"), vfor("c9")]);
        assert!(!cov.complete());
        assert_eq!(cov.missing, vec!["c2"]);
        assert_eq!(cov.duplicate, vec!["c1"]);
        assert_eq!(cov.unknown, vec!["c9"]);
    }

    #[test]
    fn coverage_empty_verdicts_is_all_missing() {
        let ids = vec!["c1".to_string(), "c2".to_string()];
        let cov = strength_coverage(&ids, &[]);
        assert_eq!(cov.missing, vec!["c1", "c2"]);
        assert!(!cov.complete());
    }

    // ---- durable store ----

    fn rec(key: &str, claim_id: &str) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(), claim_id: claim_id.into(), claim: "c".into(), theme: "t".into(),
            source_cases: vec!["m18-01".into()], citations: vec![DurableCitation {
                case_id: "m18-01".into(), unit_id: "u-0".into(), quote: "q".into(), resolved_line: Some(12) }],
            provenance_score: 0.9, provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported, strength_rationale: "ok".into(),
            final_class: FinalClass::Durable, run_id: "r1".into(), status: CrystalStatus::Active,
        }
    }

    #[test]
    fn claim_key_is_deterministic_and_citation_sensitive() {
        let c1 = vec![DurableCitation { case_id: "m18-01".into(), unit_id: "u-0".into(), quote: "q".into(), resolved_line: None }];
        let c2 = vec![DurableCitation { case_id: "m18-02".into(), unit_id: "u-9".into(), quote: "q".into(), resolved_line: None }];
        assert_eq!(claim_key("same claim", &c1), claim_key("same claim", &c1), "stable");
        assert_ne!(claim_key("same claim", &c1), claim_key("same claim", &c2), "citation-sensitive");
        assert_ne!(claim_key("claim A", &c1), claim_key("claim B", &c1), "text-sensitive");
    }

    #[test]
    fn fold_write_then_retract_then_supersede() {
        let mut a = rec("ck-a", "c1");
        a.status = CrystalStatus::Active;
        let events = vec![
            StoreEvent { op: StoreOp::Write, record: rec("ck-a", "c1"), supersedes: None, reason: None },
            StoreEvent { op: StoreOp::Write, record: rec("ck-b", "c2"), supersedes: None, reason: None },
            StoreEvent { op: StoreOp::Retract, record: rec("ck-b", "c2"), supersedes: None, reason: Some("wrong".into()) },
            StoreEvent { op: StoreOp::Supersede, record: rec("ck-c", "c1b"), supersedes: Some("ck-a".into()), reason: None },
        ];
        let state = fold_ledger(&events);
        let by_key: std::collections::BTreeMap<_, _> = state.iter().map(|r| (r.claim_key.as_str(), r.status)).collect();
        assert_eq!(by_key["ck-a"], CrystalStatus::Superseded);
        assert_eq!(by_key["ck-b"], CrystalStatus::Retracted);
        assert_eq!(by_key["ck-c"], CrystalStatus::Active);
        // only ck-c is active.
        assert_eq!(active_keys(&events).into_iter().collect::<Vec<_>>(), vec!["ck-c".to_string()]);
    }

    #[test]
    fn active_keys_supports_idempotent_append() {
        let events = vec![StoreEvent { op: StoreOp::Write, record: rec("ck-a", "c1"), supersedes: None, reason: None }];
        // Re-writing ck-a would be a no-op: the caller skips keys already active.
        assert!(active_keys(&events).contains("ck-a"));
    }

    #[test]
    fn render_separates_durable_from_review() {
        let header = CrystalHeader {
            title: "Agent Memory".into(),
            scope: "memory + context engineering".into(),
            not_claiming: "not a universal theory".into(),
        };
        let review = vec![ReviewEntry {
            claim_id: "c2".into(),
            claim: "All agents must use file-backed memory.".into(),
            theme: "memory".into(),
            final_class: FinalClass::Caveated,
            strength: StrengthClass::OverSynthesized,
            evidence_sufficient: false,
            rationale: "fuses single-source quotes into a universal".into(),
        }];
        let md = render_crystal_md(&header, &[rec("ck-a", "c1")], &review);
        assert!(md.contains("Agent Memory — durable knowledge"));
        assert!(md.contains("**Scope:** memory + context engineering"));
        assert!(md.contains("NOT claiming"));
        assert!(md.contains("Provenance — 1 citation"));
        assert!(md.contains("Review (NOT durable)"));
        // P2: the caveated claim is readable on its own — claim text present.
        assert!(md.contains("All agents must use file-backed memory."), "review must show claim text");
        assert!(md.contains("OverSynthesized"));
        assert!(md.contains("fuses single-source quotes"));
    }

    // ---- review decisions → revised candidate ----

    fn claim(id: &str) -> CrystalClaim {
        CrystalClaim {
            id: id.into(), claim: format!("claim {id}"), theme: "t".into(),
            citations: vec![Citation { case_id: "m18-01".into(), unit_id: "u-0".into(), quote: "q".into(), claimed_line: None }],
            caveat: None,
        }
    }

    #[test]
    fn apply_rewrite_split_keep_reject() {
        let orig = CrystalCandidate { items: vec![claim("c1"), claim("c2"), claim("c3"), claim("c4")] };
        let mut rw = claim("");
        rw.claim = "narrower c1".into();
        let decisions = vec![
            ReviewDecision { claim_id: "c1".into(), action: ReviewAction::Rewrite, revisions: vec![rw], note: "narrow".into() },
            ReviewDecision { claim_id: "c2".into(), action: ReviewAction::Split,
                revisions: vec![{ let mut a = claim(""); a.claim = "part a".into(); a }, { let mut b = claim(""); b.claim = "part b".into(); b }], note: String::new() },
            ReviewDecision { claim_id: "c3".into(), action: ReviewAction::KeepCaveated, revisions: vec![], note: String::new() },
            ReviewDecision { claim_id: "c4".into(), action: ReviewAction::Reject, revisions: vec![], note: String::new() },
        ];
        let out = apply_decisions(&orig, &decisions);
        let ids: Vec<&str> = out.revised.items.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(ids, vec!["c1r", "c2s1", "c2s2"], "rewrite→c1r, split→c2s1/c2s2; keep/reject drop");
        assert!(out.unknown.is_empty());
        assert_eq!(out.revised.items[0].claim, "narrower c1");
    }

    #[test]
    fn apply_flags_unknown_claim_id() {
        let orig = CrystalCandidate { items: vec![claim("c1")] };
        let decisions = vec![ReviewDecision { claim_id: "c9".into(), action: ReviewAction::KeepCaveated, revisions: vec![], note: String::new() }];
        let out = apply_decisions(&orig, &decisions);
        assert_eq!(out.unknown, vec!["c9"]);
        assert!(out.revised.items.is_empty());
    }

    #[test]
    fn apply_preserves_explicit_revision_id() {
        let orig = CrystalCandidate { items: vec![claim("c1")] };
        let mut rev = claim("c1-narrow");
        rev.claim = "x".into();
        let decisions = vec![ReviewDecision { claim_id: "c1".into(), action: ReviewAction::Rewrite, revisions: vec![rev], note: String::new() }];
        let out = apply_decisions(&orig, &decisions);
        assert_eq!(out.revised.items[0].id, "c1-narrow", "explicit id kept");
    }

    #[test]
    fn build_record_zips_quote_and_resolved_line() {
        let body = "# H\n\nA chunk is a structurally neutral container.";
        let idx = index_for("m18-01", body, &["A chunk is a structurally neutral container."]);
        let uid = unit_id(&idx, "m18-01", 0);
        let claim = CrystalClaim {
            id: "c1".into(), claim: "chunks neutral".into(), theme: "x".into(),
            citations: vec![Citation { case_id: "m18-01".into(), unit_id: uid, quote: "structurally neutral".into(), claimed_line: None }],
            caveat: None,
        };
        let cand = CrystalCandidate { items: vec![claim.clone()] };
        let rep = lint_candidate(&cand, &idx);
        let score = &score_candidate(&rep)[0];
        let v = ClaimStrengthVerdict { claim_id: "c1".into(), strength: StrengthClass::Supported, evidence_sufficient: true, rationale: "r".into() };
        let dr = build_durable_record(&claim, &rep.claims[0], score, &v, FinalClass::Durable, "run-x");
        assert_eq!(dr.citations.len(), 1);
        assert!(dr.citations[0].resolved_line.is_some(), "line resolved from unit");
        assert_eq!(dr.citations[0].quote, "structurally neutral");
        assert_eq!(dr.source_cases, vec!["m18-01"]);
        assert!(dr.claim_key.starts_with("ck-"));
    }
}
