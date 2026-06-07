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
}
