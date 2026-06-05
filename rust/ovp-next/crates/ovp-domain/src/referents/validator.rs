//! Deterministic validation of LLM-proposed referents against the accepted units.
//!
//! The LLM proposes (surfaces + kind + boundary + support); this disposes:
//! - GROUNDING (the headline invariant, M14b's `accepted_without_quote==0`): a
//!   live candidate must have a surface that render-normalized-substring-matches a
//!   supporting accepted unit's `text + quote`. Ungrounded ⇒ rejected. NO fuzzy.
//! - SUPPORT REAL: support_unit_ids filtered to ids that exist in the accepted set
//!   and actually ground a surface; evidence_refs DERIVED from those (never trusted
//!   from the model) so ref_id/locatable are copied from the real unit.
//! - CONCEPT-NEEDS-BOUNDARY: `kind==Concept` without a non-degenerate boundary is
//!   downgraded to `ambiguous` (structural teeth against "everything is a concept").
//! - DIRECTIVE-TOPIC FORCE-LOCAL: a candidate supported only by a directive unit
//!   whose surface is that unit's non-locatable `role=topic` arg is forced to
//!   `local_phrase` (the action, not an object) regardless of the LLM's label.
//! - DEDUP: live candidates sharing the canonical surface, or the same support+kind,
//!   are merged (the deterministic floor under the LLM's grouping).
//!
//! Pure + deterministic given (candidates, accepted units).

use std::collections::BTreeMap;

use sha2::{Digest, Sha256};

use crate::units::normalize::{contains_ci, render_norm};
use crate::units::{Unit, UnitKind};

use super::parser::RawReferent;
use super::{
    Boundary, KindCounts, ReferentCandidate, ReferentEvidence, ReferentExtraction, ReferentKind,
    ReferentReport,
};

const SCHEMA_VERSION: u32 = 1;

/// A surface counts as grounded only if its normalized form is long enough that a
/// substring hit is meaningful: ≥3 chars normally, ≥2 if it contains a CJK char
/// (render_norm drops whitespace/punctuation, so very short ASCII over-matches).
fn long_enough(surface: &str) -> bool {
    let n = render_norm(surface);
    let len = n.chars().count();
    if n.is_empty() {
        return false;
    }
    if surface.chars().any(is_cjk) {
        len >= 2
    } else {
        len >= 3
    }
}

fn is_cjk(c: char) -> bool {
    matches!(c, '\u{3400}'..='\u{9FFF}' | '\u{F900}'..='\u{FAFF}' | '\u{20000}'..='\u{2FA1F}')
}

fn haystack(u: &Unit) -> String {
    format!("{} {}", u.text, u.evidence.quote)
}

/// Validate raw referent candidates against the accepted units.
pub fn validate_referents(raw: &[serde_json::Value], units: &[Unit], case_id: &str) -> ReferentExtraction {
    let by_id: BTreeMap<&str, &Unit> = units.iter().map(|u| (u.id.as_str(), u)).collect();

    let mut live: Vec<ReferentCandidate> = Vec::new();
    let mut rejected: Vec<ReferentCandidate> = Vec::new();

    for (idx, value) in raw.iter().enumerate() {
        let r: RawReferent = match serde_json::from_value(value.clone()) {
            Ok(r) => r,
            Err(e) => {
                rejected.push(malformed(idx, value, &e.to_string()));
                continue;
            }
        };
        match classify_one(idx, &r, &by_id) {
            Ok(c) => live.push(c),
            Err(c) => rejected.push(c),
        }
    }

    let duplicates_collapsed = dedup(&mut live);
    let report = build_report(&live, &rejected, duplicates_collapsed, None);
    ReferentExtraction {
        case_id: case_id.to_string(),
        schema_version: SCHEMA_VERSION,
        referents: live,
        rejected,
        report,
    }
}

/// Build an extraction that records a parse failure (so the pack still exists).
pub fn referents_parse_failed(case_id: &str, detail: String) -> ReferentExtraction {
    ReferentExtraction {
        case_id: case_id.to_string(),
        schema_version: SCHEMA_VERSION,
        referents: Vec::new(),
        rejected: Vec::new(),
        report: build_report(&[], &[], 0, Some(detail)),
    }
}

/// `Ok(live candidate)` or `Err(rejected candidate carrying reject_reason)`.
/// Both variants are the same (intentionally large) type — this is a verdict, not
/// an error path, so the large-Err lint does not apply.
#[allow(clippy::result_large_err)]
fn classify_one(
    idx: usize,
    r: &RawReferent,
    by_id: &BTreeMap<&str, &Unit>,
) -> Result<ReferentCandidate, ReferentCandidate> {
    let reject = |reason: &str, surfaces: &[String]| {
        let s0 = surfaces.first().cloned().unwrap_or_default();
        ReferentCandidate {
            id: referent_id(idx, &s0),
            surface_names: surfaces.to_vec(),
            kind: ReferentKind::Noise,
            subtype: r.subtype.clone(),
            support_unit_ids: r.support_unit_ids.clone(),
            evidence_refs: Vec::new(),
            rationale: r.rationale.clone(),
            boundary: None,
            reject_reason: Some(reason.to_string()),
            confidence: 0.0,
        }
    };

    // Support units that exist in the accepted set, in declared order, unique.
    // The LLM sometimes truncates ids to the `u-NNN` index prefix; resolve those
    // by unique prefix (the index is unique, so this never weakens grounding).
    let mut support: Vec<&Unit> = Vec::new();
    for uid in &r.support_unit_ids {
        if let Some(u) = resolve_support(by_id, uid) {
            if !support.iter().any(|s| s.id == u.id) {
                support.push(u);
            }
        }
    }
    if support.is_empty() {
        return Err(reject("no_support", &r.surface_names));
    }

    // Keep only surfaces that are long enough AND grounded in ≥1 support unit's
    // text+quote (render-normalized). Strip invented/short surfaces.
    let grounded_surfaces: Vec<String> = r
        .surface_names
        .iter()
        .map(|s| s.trim().to_string())
        .filter(|s| long_enough(s) && support.iter().any(|u| contains_ci(&haystack(u), s)))
        .collect();
    if grounded_surfaces.is_empty() {
        return Err(reject("ungrounded", &r.surface_names));
    }

    // Support units that actually ground a kept surface → evidence_refs (derived,
    // never trusted from the model). locatable = grounded in that unit's QUOTE.
    let mut evidence_refs: Vec<ReferentEvidence> = Vec::new();
    let mut support_ids: Vec<String> = Vec::new();
    for u in &support {
        let grounds = grounded_surfaces.iter().any(|s| contains_ci(&haystack(u), s));
        if !grounds {
            continue;
        }
        let locatable = grounded_surfaces.iter().any(|s| contains_ci(&u.evidence.quote, s));
        evidence_refs.push(ReferentEvidence {
            unit_id: u.id.clone(),
            ref_id: u.evidence.ref_id.clone(),
            locatable,
        });
        support_ids.push(u.id.clone());
    }
    if evidence_refs.is_empty() {
        return Err(reject("ungrounded", &r.surface_names));
    }

    // kind + deterministic downgrades.
    let mut kind = r.kind;
    let mut boundary = r.boundary.clone();
    let mut rationale = r.rationale.clone();

    // DIRECTIVE-TOPIC FORCE-LOCAL: only support is a directive whose non-locatable
    // role=topic arg surface equals one of our surfaces → it is the action.
    if support_ids.len() == 1 {
        let u = support[0];
        if u.kind == UnitKind::Directive
            && u.arguments.iter().any(|a| {
                a.role == "topic"
                    && !a.locatable
                    && grounded_surfaces.iter().any(|s| render_norm(s) == render_norm(&a.surface))
            })
            && kind != ReferentKind::Noise
        {
            kind = ReferentKind::LocalPhrase;
            rationale = format!("[forced local: directive topic arg] {rationale}");
        }
    }

    // CONCEPT GATE — a concept survives only if its boundary is (a) present and not
    // a restatement of the surface, (b) SOURCED from its support units (provenance —
    // boundary text must trace to support, not to outside/rejected units), and (c)
    // NOT a single-support claim whose boundary merely restates that one unit's
    // predicate. Any failure DOWNGRADES to ambiguous (kept, never promoted). These
    // are deterministic + post-checkable, so they bind the over-mint structurally
    // rather than by prompt taste, and they exercise the ambiguous lane.
    if kind == ReferentKind::Concept {
        let support_units: Vec<&Unit> =
            support.iter().copied().filter(|u| support_ids.contains(&u.id)).collect();
        let downgrade = boundary_downgrade_reason(boundary.as_ref(), &grounded_surfaces[0], &support_units);
        if let Some(reason) = downgrade {
            kind = ReferentKind::Ambiguous;
            boundary = None;
            rationale = format!("[downgraded: {reason}] {rationale}");
        }
    }
    // boundary only meaningful on a concept.
    if kind != ReferentKind::Concept {
        boundary = None;
    }

    let quote_grounded = evidence_refs.iter().any(|e| e.locatable);
    let confidence = confidence_of(quote_grounded, support_ids.len());

    Ok(ReferentCandidate {
        id: referent_id(idx, &grounded_surfaces[0]),
        surface_names: grounded_surfaces,
        kind,
        subtype: r.subtype.clone().filter(|s| !s.trim().is_empty()),
        support_unit_ids: support_ids,
        evidence_refs,
        rationale,
        boundary,
        reject_reason: None,
        confidence,
    })
}

/// Why a concept must be downgraded to ambiguous, or `None` if it survives.
/// (a) no/empty boundary, or boundary == the surface; (b) PROVENANCE: <50% of the
/// boundary's content tokens trace to a support unit (boundary sourced elsewhere —
/// e.g. from a rejected/non-support unit, the "Agents/u-029" leak).
///
/// We deliberately do NOT add a single-support "predicate-restatement" downgrade:
/// a GENUINE single-support concept (e.g. rag's `semantic deduplication`, defined
/// in one unit) is deterministically indistinguishable from a claim-as-concept —
/// both have a boundary highly contained in their one support unit. That rule
/// false-downgraded a real concept while missing other claim-concepts, so the
/// concept-vs-claim call stays a semantic (prompt/review) judgment, not a gate.
fn boundary_downgrade_reason(
    boundary: Option<&Boundary>,
    surface0: &str,
    support_units: &[&Unit],
) -> Option<&'static str> {
    let Some(b) = boundary else {
        return Some("concept without a real boundary");
    };
    let inc = b.includes.trim();
    if inc.is_empty() || render_norm(inc) == render_norm(surface0) {
        return Some("concept without a real boundary");
    }
    let btoks = content_tokens(&format!("{} {}", b.includes, b.excludes.as_deref().unwrap_or("")));
    if btoks.is_empty() {
        return Some("concept without a real boundary");
    }
    // PROVENANCE against the union of support units — boundary must come from them.
    let union: String = support_units.iter().map(|u| haystack(u)).collect::<Vec<_>>().join(" ");
    let prov_hits = btoks.iter().filter(|t| contains_ci(&union, t)).count();
    if (prov_hits as f64) / (btoks.len() as f64) < 0.5 {
        return Some("boundary not sourced from support units");
    }
    None
}

/// Content tokens of a phrase for boundary-provenance: ASCII word runs ≥4 chars
/// (dropping a small stopword set) plus individual CJK chars. Lowercased.
fn content_tokens(s: &str) -> Vec<String> {
    const STOP: &[&str] = &[
        "that", "this", "with", "from", "into", "than", "then", "they", "them", "their", "what",
        "when", "which", "while", "where", "your", "you", "the", "and", "for", "are", "was", "were",
        "not", "but", "its", "it's", "have", "has", "had", "via", "per", "only", "more", "most",
        "such", "each", "every", "both", "some", "any", "all", "one", "two", "three",
    ];
    let mut out = Vec::new();
    let mut cur = String::new();
    let flush = |cur: &mut String, out: &mut Vec<String>| {
        if cur.len() >= 4 && !STOP.contains(&cur.as_str()) {
            out.push(cur.clone());
        }
        cur.clear();
    };
    for c in s.chars() {
        if is_cjk(c) {
            flush(&mut cur, &mut out);
            out.push(c.to_string());
        } else if c.is_ascii_alphanumeric() {
            cur.push(c.to_ascii_lowercase());
        } else {
            flush(&mut cur, &mut out);
        }
    }
    flush(&mut cur, &mut out);
    out
}

/// Resolve a (possibly truncated) support id to an accepted unit: exact match, or
/// the UNIQUE unit whose id starts with `uid-` (the model truncating to the index
/// prefix `u-NNN`). Ambiguous prefixes resolve to nothing (never a wrong unit).
fn resolve_support<'a>(by_id: &BTreeMap<&str, &'a Unit>, uid: &str) -> Option<&'a Unit> {
    let uid = uid.trim();
    if uid.is_empty() {
        return None;
    }
    if let Some(u) = by_id.get(uid) {
        return Some(u);
    }
    let pfx = format!("{uid}-");
    let mut matches = by_id.values().filter(|u| u.id.starts_with(&pfx));
    let first = *matches.next()?;
    if matches.next().is_some() {
        return None; // ambiguous → refuse
    }
    Some(first)
}

/// Base 0.5; +0.2 if any surface is quote-grounded (not just text); +0.2 if ≥2
/// support units (in-document repetition is the strongest real-referent signal).
fn confidence_of(quote_grounded: bool, support_count: usize) -> f64 {
    let mut c: f64 = 0.5;
    if quote_grounded {
        c += 0.2;
    }
    if support_count >= 2 {
        c += 0.2;
    }
    c.clamp(0.0, 1.0)
}

/// Merge live candidates sharing the canonical surface, or the same support set +
/// kind. Returns the number of merges. Deterministic (first occurrence wins).
fn dedup(live: &mut Vec<ReferentCandidate>) -> usize {
    let mut merges = 0usize;
    let mut i = 0;
    while i < live.len() {
        let mut j = i + 1;
        while j < live.len() {
            if same_referent(&live[i], &live[j]) {
                let other = live.remove(j);
                for s in other.surface_names {
                    if !live[i].surface_names.iter().any(|x| render_norm(x) == render_norm(&s)) {
                        live[i].surface_names.push(s);
                    }
                }
                for uid in other.support_unit_ids {
                    if !live[i].support_unit_ids.contains(&uid) {
                        live[i].support_unit_ids.push(uid);
                    }
                }
                for ev in other.evidence_refs {
                    if !live[i].evidence_refs.iter().any(|e| e.unit_id == ev.unit_id) {
                        live[i].evidence_refs.push(ev);
                    }
                }
                let quote_grounded = live[i].evidence_refs.iter().any(|e| e.locatable);
                live[i].confidence = confidence_of(quote_grounded, live[i].support_unit_ids.len());
                merges += 1;
            } else {
                j += 1;
            }
        }
        i += 1;
    }
    merges
}

fn same_referent(a: &ReferentCandidate, b: &ReferentCandidate) -> bool {
    let canon = |c: &ReferentCandidate| render_norm(&c.surface_names[0]);
    if canon(a) == canon(b) {
        return true;
    }
    if a.kind == b.kind {
        let mut sa = a.support_unit_ids.clone();
        let mut sb = b.support_unit_ids.clone();
        sa.sort();
        sb.sort();
        if !sa.is_empty() && sa == sb {
            return true;
        }
    }
    false
}

fn build_report(
    live: &[ReferentCandidate],
    rejected: &[ReferentCandidate],
    duplicates_collapsed: usize,
    parse_error: Option<String>,
) -> ReferentReport {
    let mut kc = KindCounts::default();
    for c in live {
        match c.kind {
            ReferentKind::Entity => kc.entity += 1,
            ReferentKind::Concept => kc.concept += 1,
            ReferentKind::Ambiguous => kc.ambiguous += 1,
            ReferentKind::LocalPhrase => kc.local_phrase += 1,
            ReferentKind::Noise => kc.noise += 1,
        }
    }
    let n = live.len().max(1) as f64;
    ReferentReport {
        total_candidates: live.len() + rejected.len(),
        live: live.len(),
        rejected: rejected.len(),
        // By construction every live candidate is grounded; counted for the invariant.
        referents_ungrounded: live.iter().filter(|c| c.evidence_refs.is_empty()).count(),
        concept_rate: (kc.concept as f64 / n * 1000.0).round() / 1000.0,
        ambiguous_rate: (kc.ambiguous as f64 / n * 1000.0).round() / 1000.0,
        grouped_candidates: live.iter().filter(|c| c.support_unit_ids.len() > 1).count(),
        duplicates_collapsed,
        kind_counts: kc,
        parse_error,
    }
}

fn malformed(idx: usize, value: &serde_json::Value, err: &str) -> ReferentCandidate {
    let surfaces = value
        .get("surface_names")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    let s0: String = {
        let v: &Vec<String> = &surfaces;
        v.first().cloned().unwrap_or_default()
    };
    ReferentCandidate {
        id: referent_id(idx, &s0),
        surface_names: surfaces,
        kind: ReferentKind::Noise,
        subtype: None,
        support_unit_ids: Vec::new(),
        evidence_refs: Vec::new(),
        rationale: String::new(),
        boundary: None,
        reject_reason: Some(format!("malformed: {err}")),
        confidence: 0.0,
    }
}

fn referent_id(idx: usize, surface0: &str) -> String {
    let mut h = Sha256::new();
    h.update(render_norm(surface0).as_bytes());
    let digest = h.finalize();
    let mut hex = String::with_capacity(16);
    use std::fmt::Write;
    for b in digest.iter().take(4) {
        write!(hex, "{b:02x}").expect("infallible");
    }
    format!("r-{idx:03}-{hex}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::units::{
        Argument, Attribution, EvidenceLocation, MatchKind, Modality, UnitEvidence, UnitStatus,
    };

    /// Build a Unit directly — the referent validator reads only
    /// id/kind/text/evidence.quote/arguments, so a real location is irrelevant.
    fn mk_unit(id: &str, kind: UnitKind, text: &str, quote: &str, args: &[(&str, &str, bool)]) -> Unit {
        Unit {
            id: id.to_string(),
            kind,
            subtype: None,
            text: text.to_string(),
            evidence: UnitEvidence {
                ref_id: "p001.s001".into(),
                quote: quote.to_string(),
                location: Some(EvidenceLocation { byte_start: 0, byte_end: 1, line: 1, match_kind: MatchKind::Exact }),
            },
            attribution: Attribution::Author,
            modality: Modality::Asserted,
            arguments: args
                .iter()
                .map(|(s, r, l)| Argument { surface: s.to_string(), role: r.to_string(), locatable: *l })
                .collect(),
            status: UnitStatus::Accepted,
            issues: vec![],
        }
    }

    fn units() -> Vec<Unit> {
        vec![
            mk_unit("u-000-aaaaaaaa", UnitKind::Assertion, "IdeaBlocks replace prose chunks.",
                    "IdeaBlocks replace prose chunks.", &[("IdeaBlocks", "subject", true)]),
            mk_unit("u-001-bbbbbbbb", UnitKind::Assertion, "Floor raising means making the agent reliable.",
                    "Floor raising means making the agent reliable.", &[("floor raising", "subject", true)]),
            mk_unit("u-002-cccccccc", UnitKind::Directive, "Teams try reading raw logs as the firehose.",
                    "Use raw logs as the firehose.", &[("reading raw logs", "topic", false)]),
        ]
    }

    fn uid(units: &[Unit], i: usize) -> String {
        units[i].id.clone()
    }

    #[test]
    fn grounded_entity_is_live() {
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"entity","surface_names":["IdeaBlocks","IdeaBlock"],
            "support_unit_ids":[uid(&u,0)],"rationale":"named construct"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents.len(), 1);
        assert_eq!(ex.referents[0].kind, ReferentKind::Entity);
        assert_eq!(ex.report.referents_ungrounded, 0);
        assert!(!ex.referents[0].evidence_refs.is_empty());
    }

    #[test]
    fn ungrounded_surface_is_rejected() {
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"concept","surface_names":["quantum entanglement"],
            "support_unit_ids":[uid(&u,0)],"rationale":"invented"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert!(ex.referents.is_empty());
        assert_eq!(ex.rejected[0].reject_reason.as_deref(), Some("ungrounded"));
    }

    #[test]
    fn truncated_support_id_resolves_by_unique_prefix() {
        let u = units();
        // The LLM truncates "u-000-aaaaaaaa" to "u-000"; it must still resolve.
        let raw = vec![serde_json::json!({
            "kind":"entity","surface_names":["IdeaBlocks"],"support_unit_ids":["u-000"],"rationale":"x"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents.len(), 1, "{:?}", ex.rejected);
        assert_eq!(ex.referents[0].support_unit_ids, vec!["u-000-aaaaaaaa".to_string()]);
    }

    #[test]
    fn support_id_not_in_accepted_set_drops_to_no_support() {
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"entity","surface_names":["IdeaBlocks"],
            "support_unit_ids":["u-999-deadbeef"],"rationale":"x"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.rejected[0].reject_reason.as_deref(), Some("no_support"));
    }

    #[test]
    fn concept_without_boundary_downgrades_to_ambiguous() {
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"concept","surface_names":["IdeaBlocks"],
            "support_unit_ids":[uid(&u,0)],"rationale":"no boundary given"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents[0].kind, ReferentKind::Ambiguous);
        assert!(ex.referents[0].boundary.is_none());
    }

    #[test]
    fn concept_with_real_boundary_stays_concept() {
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"concept","surface_names":["floor raising"],
            "support_unit_ids":[uid(&u,1)],
            "boundary":{"includes":"making the agent reliable where reliability matters"},
            "rationale":"a reusable reliability methodology"
        })];
        // floor raising is grounded in unit[1]'s text.
        let ex = validate_referents(&raw, &u, "t");
        // unit[1] is a directive, but its topic arg is "picking golden cases", not
        // "floor raising", so force-local does NOT fire; boundary is real → concept.
        assert_eq!(ex.referents[0].kind, ReferentKind::Concept, "{:?}", ex.referents[0]);
        assert!(ex.referents[0].boundary.is_some());
    }

    #[test]
    fn concept_boundary_from_nonsupport_is_downgraded() {
        // The "Agents/u-029" leak: surface grounds in the support unit, but the
        // boundary text comes from OUTSIDE the support → provenance fails.
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"concept","surface_names":["IdeaBlocks"],
            "support_unit_ids":[uid(&u,0)],
            "boundary":{"includes":"quantum chromodynamics describes gluon confinement fields"},
            "rationale":"boundary not from the supporting unit"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents[0].kind, ReferentKind::Ambiguous);
        assert!(ex.referents[0].boundary.is_none());
    }

    #[test]
    fn genuine_single_support_concept_survives_provenance() {
        // A single-support concept whose boundary IS drawn from its one support
        // unit must SURVIVE (we do not penalize single-support per se).
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"concept","surface_names":["floor raising"],
            "support_unit_ids":[uid(&u,1)],
            "boundary":{"includes":"making the agent reliable where reliability matters"},
            "rationale":"reusable reliability methodology, boundary from u-001"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents[0].kind, ReferentKind::Concept, "{:?}", ex.referents[0]);
    }

    #[test]
    fn directive_topic_handle_is_forced_local() {
        let u = units();
        // u[2] is a Directive whose role=topic, non-locatable arg is "reading raw
        // logs" (in its text, not its quote). The LLM mislabels it concept.
        let raw = vec![serde_json::json!({
            "kind":"concept","surface_names":["reading raw logs"],
            "support_unit_ids":[uid(&u,2)],
            "boundary":{"includes":"the practice of mining raw logs for signal"},
            "rationale":"looks concept-shaped but is an action"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents.len(), 1, "{:?}", ex.rejected);
        assert_eq!(ex.referents[0].kind, ReferentKind::LocalPhrase);
    }

    #[test]
    fn dedup_merges_same_canonical_surface() {
        let u = units();
        let raw = vec![
            serde_json::json!({"kind":"entity","surface_names":["IdeaBlocks"],"support_unit_ids":[uid(&u,0)],"rationale":"a"}),
            serde_json::json!({"kind":"entity","surface_names":["ideablocks"],"support_unit_ids":[uid(&u,0)],"rationale":"b"}),
        ];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents.len(), 1);
        assert_eq!(ex.report.duplicates_collapsed, 1);
    }

    #[test]
    fn min_length_guard_blocks_short_ascii() {
        let u = units();
        // "is" render-norms to "is" (2 chars) and would substring-hit; must be blocked.
        let raw = vec![serde_json::json!({
            "kind":"entity","surface_names":["is"],"support_unit_ids":[uid(&u,0)],"rationale":"x"
        })];
        let ex = validate_referents(&raw, &u, "t");
        assert!(ex.referents.is_empty(), "2-char ascii surface must not ground");
    }

    #[test]
    fn malformed_kind_becomes_rejected_not_fatal() {
        let u = units();
        let raw = vec![
            serde_json::json!({"kind":"nonsense","surface_names":["x"]}),
            serde_json::json!({"kind":"entity","surface_names":["IdeaBlocks"],"support_unit_ids":[uid(&u,0)],"rationale":"ok"}),
        ];
        let ex = validate_referents(&raw, &u, "t");
        assert_eq!(ex.referents.len(), 1, "the good one survives");
        assert!(ex.rejected.iter().any(|r| r.reject_reason.as_deref().unwrap_or("").starts_with("malformed")));
    }

    #[test]
    fn deterministic_under_repeat() {
        let u = units();
        let raw = vec![serde_json::json!({
            "kind":"entity","surface_names":["IdeaBlocks"],"support_unit_ids":[uid(&u,0)],"rationale":"x"
        })];
        assert_eq!(validate_referents(&raw, &u, "t"), validate_referents(&raw, &u, "t"));
    }
}
