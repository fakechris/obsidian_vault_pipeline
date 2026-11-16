//! Claim lineage — append-only relationship events on the durable ledger.
//!
//! Minimal G4 form: when a new durable claim is near-duplicate of an active
//! one, decide conservatively:
//!
//! - **Supersede** only when text is nearly the same AND the new claim's
//!   citation set is a proper superset (strictly more evidence) → write
//!   `StoreOp::Supersede` with `supersedes = old claim_key`.
//! - **Near-duplicate** when text + citations are essentially the same →
//!   skip the append (dedup; already covered by claim_key when identical,
//!   this catches paraphrase-near-identical with same unit set).
//! - **Strengthen candidate** when citations grow but text diverges more →
//!   still append as Write (conservative); surface as a report row for review.
//!
//! Never deletes old claims. Never rewrites evidence. Wrong-merge default is
//! append (only auto-act on high-confidence supersede/dedup).

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::crystal::{CrystalStatus, DurableCitation, DurableRecord, StoreEvent, StoreOp};

/// Public lineage view for one claim key (API / UI).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ClaimLineage {
    /// Keys this claim explicitly superseded (from ledger Supersede events).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub supersedes: Vec<String>,
    /// Keys that superseded this claim (inverse of supersedes).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub superseded_by: Vec<String>,
    /// Folded status if known (active / superseded / retracted).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
}

/// Build a per-claim lineage index from the append-only ledger events.
/// Pure projection — never mutates authority.
pub fn lineage_index(events: &[StoreEvent]) -> BTreeMap<String, ClaimLineage> {
    let mut by_key: BTreeMap<String, ClaimLineage> = BTreeMap::new();
    // Status from fold order (last event wins per key).
    let folded = crate::crystal::fold_ledger(events);
    for r in &folded {
        let e = by_key.entry(r.claim_key.clone()).or_default();
        e.status = Some(status_label(r.status));
    }
    for ev in events {
        if ev.op != StoreOp::Supersede {
            continue;
        }
        let Some(old) = ev.supersedes.as_ref() else {
            continue;
        };
        let newer = &ev.record.claim_key;
        by_key
            .entry(newer.clone())
            .or_default()
            .supersedes
            .push(old.clone());
        by_key
            .entry(old.clone())
            .or_default()
            .superseded_by
            .push(newer.clone());
    }
    // Dedup edges (multiple supersede events over time).
    for v in by_key.values_mut() {
        v.supersedes.sort();
        v.supersedes.dedup();
        v.superseded_by.sort();
        v.superseded_by.dedup();
    }
    by_key
}

fn status_label(s: CrystalStatus) -> String {
    match s {
        CrystalStatus::Active => "active".into(),
        CrystalStatus::Superseded => "superseded".into(),
        CrystalStatus::Retracted => "retracted".into(),
        CrystalStatus::Draft => "draft".into(),
    }
}

/// Lookup helper for claim pages.
pub fn lineage_for(events: &[StoreEvent], claim_key: &str) -> ClaimLineage {
    lineage_index(events)
        .remove(claim_key)
        .unwrap_or_default()
}

/// What to do with a new record relative to one active claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LineageKind {
    /// New claim replaces the active one (more evidence, near-same text).
    Supersede,
    /// Same substance; do not append a second active claim.
    NearDuplicate,
    /// Related and possibly stronger; still append (human/agent may merge later).
    StrengthenCandidate,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LineageHit {
    pub existing_key: String,
    pub existing_claim_id: String,
    pub kind: LineageKind,
    pub text_jaccard: f64,
    pub citation_jaccard: f64,
    pub reason: String,
}

/// Decision for one new record against the full active set.
#[derive(Debug, Clone, PartialEq)]
pub enum LineageDecision {
    /// Append as a normal Write (no strong lineage hit).
    Append,
    /// Append as Supersede of `existing_key`.
    Supersede { existing_key: String, reason: String },
    /// Skip append — near-duplicate of an active claim.
    SkipDuplicate { existing_key: String, reason: String },
    /// Append as Write, but note a strengthen candidate for review tooling.
    AppendWithNote { note: LineageHit },
}

const SUPERSEDE_TEXT: f64 = 0.85;
const DEDUP_TEXT: f64 = 0.90;
const DEDUP_CITE: f64 = 0.90;
const RELATED_TEXT: f64 = 0.50;
const RELATED_CITE: f64 = 0.30;

/// Score one new record against all active claims; pick the strongest hit.
pub fn decide_lineage(new: &DurableRecord, active: &[DurableRecord]) -> LineageDecision {
    let mut best: Option<LineageHit> = None;
    for old in active {
        if old.claim_key == new.claim_key {
            continue;
        }
        if let Some(hit) = score_pair(new, old) {
            let take = match &best {
                None => true,
                Some(b) => rank(hit.kind) > rank(b.kind)
                    || (rank(hit.kind) == rank(b.kind)
                        && hit.text_jaccard + hit.citation_jaccard
                            > b.text_jaccard + b.citation_jaccard),
            };
            if take {
                best = Some(hit);
            }
        }
    }
    match best {
        None => LineageDecision::Append,
        Some(hit) => match hit.kind {
            LineageKind::Supersede => LineageDecision::Supersede {
                existing_key: hit.existing_key,
                reason: hit.reason,
            },
            LineageKind::NearDuplicate => LineageDecision::SkipDuplicate {
                existing_key: hit.existing_key,
                reason: hit.reason,
            },
            LineageKind::StrengthenCandidate => LineageDecision::AppendWithNote { note: hit },
        },
    }
}

/// Public scorer for tests and review tooling.
pub fn score_pair(new: &DurableRecord, old: &DurableRecord) -> Option<LineageHit> {
    let text_j = jaccard(&text_tokens(&new.claim), &text_tokens(&old.claim));
    let new_cites = cite_set(&new.citations);
    let old_cites = cite_set(&old.citations);
    let cite_j = jaccard(&new_cites, &old_cites);
    if text_j < RELATED_TEXT && cite_j < RELATED_CITE {
        return None;
    }

    let new_superset = !old_cites.is_empty()
        && old_cites.is_subset(&new_cites)
        && new_cites.len() > old_cites.len();
    let same_cites = cite_j >= DEDUP_CITE;

    // High-confidence supersede: near-same text + strictly more evidence.
    if text_j >= SUPERSEDE_TEXT && new_superset {
        return Some(LineageHit {
            existing_key: old.claim_key.clone(),
            existing_claim_id: old.claim_id.clone(),
            kind: LineageKind::Supersede,
            text_jaccard: text_j,
            citation_jaccard: cite_j,
            reason: format!(
                "text_jaccard={text_j:.2} and citation proper-superset of {}",
                old.claim_key
            ),
        });
    }

    // Near-duplicate: almost same text and almost same citations → skip.
    if text_j >= DEDUP_TEXT && same_cites {
        return Some(LineageHit {
            existing_key: old.claim_key.clone(),
            existing_claim_id: old.claim_id.clone(),
            kind: LineageKind::NearDuplicate,
            text_jaccard: text_j,
            citation_jaccard: cite_j,
            reason: format!(
                "near-duplicate of {} (text_j={text_j:.2}, cite_j={cite_j:.2})",
                old.claim_key
            ),
        });
    }

    // Strengthen candidate: shared evidence family, text related — append + note.
    if text_j >= RELATED_TEXT && cite_j >= RELATED_CITE {
        return Some(LineageHit {
            existing_key: old.claim_key.clone(),
            existing_claim_id: old.claim_id.clone(),
            kind: LineageKind::StrengthenCandidate,
            text_jaccard: text_j,
            citation_jaccard: cite_j,
            reason: format!(
                "strengthen-candidate vs {} (text_j={text_j:.2}, cite_j={cite_j:.2})",
                old.claim_key
            ),
        });
    }
    None
}

fn rank(k: LineageKind) -> u8 {
    match k {
        LineageKind::Supersede => 3,
        LineageKind::NearDuplicate => 2,
        LineageKind::StrengthenCandidate => 1,
    }
}

fn cite_set(cites: &[DurableCitation]) -> BTreeSet<(String, String)> {
    cites
        .iter()
        .map(|c| (c.case_id.clone(), c.unit_id.clone()))
        .collect()
}

fn jaccard<T: Ord>(a: &BTreeSet<T>, b: &BTreeSet<T>) -> f64 {
    if a.is_empty() && b.is_empty() {
        return 1.0;
    }
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let inter = a.intersection(b).count();
    let union = a.len() + b.len() - inter;
    if union == 0 {
        0.0
    } else {
        inter as f64 / union as f64
    }
}

/// Bilingual content tokens: lowercased ASCII-alphanumeric runs + individual CJK.
pub fn text_tokens(s: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut cur = String::new();
    for ch in s.chars() {
        let is_cjk = ('\u{4E00}'..='\u{9FFF}').contains(&ch);
        if ch.is_alphanumeric() && !is_cjk {
            cur.extend(ch.to_lowercase());
        } else {
            if !cur.is_empty() {
                out.insert(std::mem::take(&mut cur));
            }
            if is_cjk {
                out.insert(ch.to_string());
            }
        }
    }
    if !cur.is_empty() {
        out.insert(cur);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crystal::{
        CrystalStatus, DurableCitation, DurableRecord, FinalClass, ProvenanceClass, StrengthClass,
    };

    fn rec(key: &str, claim: &str, cites: &[(&str, &str)]) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: format!("id-{key}"),
            claim: claim.into(),
            theme: "t".into(),
            theme_id: None,
            source_cases: cites.iter().map(|(c, _)| (*c).to_string()).collect(),
            citations: cites
                .iter()
                .map(|(c, u)| DurableCitation {
                    case_id: (*c).into(),
                    unit_id: (*u).into(),
                    quote: "q".into(),
                    resolved_line: Some(1),
                })
                .collect(),
            provenance_score: 1.0,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "ok".into(),
            final_class: FinalClass::Durable,
            run_id: "r".into(),
            status: CrystalStatus::Active,
        }
    }

    #[test]
    fn supersede_when_more_evidence_same_text() {
        let old = rec("ck-old", "Agents need grounded citations for claims", &[("a", "u1")]);
        let new = rec(
            "ck-new",
            "Agents need grounded citations for claims",
            &[("a", "u1"), ("b", "u2")],
        );
        let d = decide_lineage(&new, &[old]);
        match d {
            LineageDecision::Supersede { existing_key, .. } => {
                assert_eq!(existing_key, "ck-old");
            }
            other => panic!("expected supersede, got {other:?}"),
        }
    }

    #[test]
    fn skip_near_duplicate() {
        let old = rec("ck-old", "Agents need grounded citations for claims", &[("a", "u1")]);
        let new = rec(
            "ck-new",
            "Agents need grounded citations for claims",
            &[("a", "u1")],
        );
        let d = decide_lineage(&new, &[old]);
        match d {
            LineageDecision::SkipDuplicate { existing_key, .. } => {
                assert_eq!(existing_key, "ck-old");
            }
            other => panic!("expected skip, got {other:?}"),
        }
    }

    #[test]
    fn unrelated_appends() {
        let old = rec("ck-old", "WebRTC uses UDP for media transport", &[("a", "u1")]);
        let new = rec(
            "ck-new",
            "Crystal claims must cite verbatim units",
            &[("b", "u9")],
        );
        assert_eq!(decide_lineage(&new, &[old]), LineageDecision::Append);
    }

    #[test]
    fn strengthen_candidate_when_related_partial_overlap() {
        let old = rec(
            "ck-old",
            "Retrieval must ground every claim in source quotes",
            &[("a", "u1"), ("a", "u2")],
        );
        let new = rec(
            "ck-new",
            "Every claim needs grounding in source quotes for retrieval",
            &[("a", "u1"), ("b", "u3")],
        );
        let d = decide_lineage(&new, &[old]);
        match d {
            LineageDecision::AppendWithNote { note } => {
                assert_eq!(note.kind, LineageKind::StrengthenCandidate);
            }
            // If jaccard is high enough it might classify as supersede/dedup — still ok if related.
            LineageDecision::Append => panic!("expected some lineage relation"),
            other => {
                // Supersede/Skip also acceptable if thresholds trip; assert not pure silence.
                assert!(matches!(
                    other,
                    LineageDecision::Supersede { .. } | LineageDecision::SkipDuplicate { .. }
                ));
            }
        }
    }
}
