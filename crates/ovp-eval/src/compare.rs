//! The deterministic comparison. Takes the two [`NormalizedSubject`]s (either
//! may be absent if its side failed) and produces structured, clearly-labeled
//! metrics — never a single subjective "quality score". Every metric says what
//! it is (lexical set overlap, token-overlap grounding, …) so a reader is never
//! misled into treating a lexical signal as a semantic verdict.
//!
//! The two systems are EXPECTED to differ (different unit types, different
//! retrieval models); the output is observational — "here is what each side
//! produced and where they diverge" — to help find pipeline problems.

use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;

use crate::normalize::{tokenize, NormalizedSubject};

/// Whether a side produced a usable subject, and why not if it didn't.
#[derive(Debug, Clone, Serialize)]
pub struct SideStatus {
    pub available: bool,
    pub detail: Option<String>,
}

impl SideStatus {
    pub fn ok() -> Self {
        Self { available: true, detail: None }
    }
    pub fn failed(detail: impl Into<String>) -> Self {
        Self { available: false, detail: Some(detail.into()) }
    }
}

/// Lexical concept set overlap. NOT semantic.
#[derive(Debug, Clone, Serialize)]
pub struct ConceptOverlap {
    pub metric: String,
    pub normalization: String,
    pub ovp_count: usize,
    pub nowledge_count: usize,
    pub shared_count: usize,
    /// EXACT counts of the asymmetric difference — never truncated.
    pub ovp_only_count: usize,
    pub nowledge_only_count: usize,
    pub shared: Vec<String>,
    /// Display lists, capped at `TOP_N`. `*_truncated` says whether the exact
    /// `*_count` exceeds what is shown here — so a reader never mistakes the
    /// shown length for the true count.
    pub ovp_only: Vec<String>,
    pub nowledge_only: Vec<String>,
    pub ovp_only_truncated: bool,
    pub nowledge_only_truncated: bool,
    pub jaccard_lexical: f64,
}

/// Deterministic claim comparison. The LLM judge is OFF in v1.
#[derive(Debug, Clone, Serialize)]
pub struct ClaimDiff {
    pub metric: String,
    pub ovp_claim_count: usize,
    pub nowledge_claim_count: usize,
    pub ovp_by_section: BTreeMap<String, usize>,
    pub nowledge_by_section: BTreeMap<String, usize>,
    /// ovp claims that share ≥3 significant tokens with some Nowledge claim.
    pub lexically_overlapping_claims: usize,
    pub llm_judge: String,
}

/// Lexical token-overlap grounding against the original input.
#[derive(Debug, Clone, Serialize)]
pub struct GroundingAudit {
    pub metric: String,
    pub threshold: f64,
    /// Which text BOTH sides' claims were matched against (the single shared
    /// reference). In `split` mode this is the local markdown — NOT what
    /// Nowledge actually fetched — so it can bias Nowledge's rate; stated here.
    pub reference_source: String,
    pub warning: String,
    pub ovp_grounded: usize,
    pub ovp_ungrounded: usize,
    pub ovp_rate: f64,
    pub nowledge_grounded: usize,
    pub nowledge_ungrounded: usize,
    pub nowledge_rate: f64,
    pub ovp_ungrounded_examples: Vec<String>,
    pub nowledge_ungrounded_examples: Vec<String>,
}

/// Structural counts. No auto-fail; fragmentation is a heuristic ratio.
#[derive(Debug, Clone, Serialize)]
pub struct StructureQuality {
    pub ovp_concepts: usize,
    pub ovp_claims: usize,
    pub ovp_sections: usize,
    pub nowledge_memory_titles: usize,
    pub nowledge_memories: usize,
    /// Whole-store crystal count (NOT this input). `None` = endpoint failed.
    pub nowledge_global_crystals: Option<usize>,
    pub crystal_status: String,
    /// Whether a per-INPUT crystal comparison is possible. Nowledge has no
    /// source-scoped crystal API (crystals are cross-source synthesized), so v1
    /// cannot compare crystals for THIS input — stated, not faked as 0.
    pub current_input_crystal_comparison: String,
    /// claims / concepts; ~1 = one claim per concept, higher = more scattered.
    pub ovp_fragmentation: f64,
    pub nowledge_fragmentation: f64,
    pub note: String,
}

/// One query across the comparable (source-scoped) lane and the Nowledge-only
/// global background lane.
#[derive(Debug, Clone, Serialize)]
pub struct RetrievalRow {
    pub query: String,
    // --- comparable lane: each side over THIS input, lexical ---
    pub ovp_hits: usize,
    pub ovp_grounded: usize,
    pub ovp_top: Vec<String>,
    /// Nowledge retrieval restricted to THIS source's extracted memories.
    pub nowledge_scoped_hits: usize,
    pub nowledge_scoped_grounded: usize,
    pub nowledge_scoped_top: Vec<String>,
    // --- background lane: Nowledge whole-store search (NOT this input) ---
    pub nowledge_global_hits: usize,
    pub nowledge_global_top: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RetrievalComparison {
    pub metric: String,
    /// What the comparable lane is, and why the global lane is not comparable.
    pub comparable_lane: String,
    pub background_lane: String,
    pub queries: Vec<String>,
    pub ovp_status: String,
    pub nowledge_scoped_status: String,
    pub nowledge_global_status: String,
    pub rows: Vec<RetrievalRow>,
}

/// The whole comparison — serialized verbatim to `score.json`. Structured counts
/// + labeled lexical metrics only.
#[derive(Debug, Clone, Serialize)]
pub struct Comparison {
    pub case_id: String,
    /// How the two sides got their input — whether they saw the same bytes.
    pub input_mode: String,
    pub ovp: SideStatus,
    pub nowledge: SideStatus,
    pub concept_overlap: Option<ConceptOverlap>,
    pub claim_diff: Option<ClaimDiff>,
    pub grounding: Option<GroundingAudit>,
    pub structure: Option<StructureQuality>,
    pub retrieval: RetrievalComparison,
    /// Human-oriented observations pulled from the metrics above.
    pub findings: Vec<String>,
}

const TOP_N: usize = 25;
const EXAMPLES: usize = 8;

/// Inputs to [`build`]. Grouped to keep the signature honest (and clippy happy).
pub struct BuildInputs<'a> {
    pub case_id: &'a str,
    pub input_mode: &'a str,
    pub ovp: Option<&'a NormalizedSubject>,
    pub nowledge: Option<&'a NormalizedSubject>,
    pub ovp_status: SideStatus,
    pub nowledge_status: SideStatus,
    pub grounding_threshold: f64,
    pub queries: &'a [String],
    /// Nowledge whole-store search hits (the background lane; not comparable).
    pub nowledge_global: &'a [crate::normalize::NormRetrievalHit],
    pub retrieval_ovp_status: String,
    pub retrieval_scoped_status: String,
    pub retrieval_global_status: String,
    pub crystal_status: String,
    /// Human description of the text both sides were grounded against.
    pub grounding_reference: String,
}

/// Build the full comparison. Subjects already carry their comparable-lane
/// retrieval + grounding; `nowledge_global` is the separate background lane.
pub fn build(inp: BuildInputs<'_>) -> Comparison {
    let BuildInputs {
        case_id,
        input_mode,
        ovp,
        nowledge,
        ovp_status,
        nowledge_status,
        grounding_threshold,
        queries,
        nowledge_global,
        retrieval_ovp_status,
        retrieval_scoped_status,
        retrieval_global_status,
        crystal_status,
        grounding_reference,
    } = inp;

    let concept_overlap = match (ovp, nowledge) {
        (Some(o), Some(n)) => Some(concept_overlap(o, n)),
        _ => None,
    };
    let claim_diff = match (ovp, nowledge) {
        (Some(o), Some(n)) => Some(claim_diff(o, n)),
        _ => None,
    };
    let grounding = Some(grounding_audit(ovp, nowledge, grounding_threshold, grounding_reference));
    let structure = match (ovp, nowledge) {
        (Some(o), Some(n)) => Some(structure_quality(o, n, crystal_status)),
        _ => None,
    };
    let retrieval = retrieval_comparison(
        ovp,
        nowledge,
        nowledge_global,
        queries,
        retrieval_ovp_status,
        retrieval_scoped_status,
        retrieval_global_status,
    );

    let findings =
        findings(input_mode, &concept_overlap, &grounding, &structure, &retrieval, &ovp_status, &nowledge_status);

    Comparison {
        case_id: case_id.to_string(),
        input_mode: input_mode.to_string(),
        ovp: ovp_status,
        nowledge: nowledge_status,
        concept_overlap,
        claim_diff,
        grounding,
        structure,
        retrieval,
        findings,
    }
}

fn concept_overlap(ovp: &NormalizedSubject, nowledge: &NormalizedSubject) -> ConceptOverlap {
    let ovp_keys: BTreeSet<&str> = ovp.concepts.iter().map(|c| c.key.as_str()).collect();
    let now_keys: BTreeSet<&str> = nowledge.concepts.iter().map(|c| c.key.as_str()).collect();
    let shared: Vec<String> = ovp_keys.intersection(&now_keys).map(|s| s.to_string()).collect();
    let ovp_only: Vec<String> = ovp_keys.difference(&now_keys).map(|s| s.to_string()).collect();
    let nowledge_only: Vec<String> = now_keys.difference(&ovp_keys).map(|s| s.to_string()).collect();
    let union = ovp_keys.union(&now_keys).count();
    let jaccard = if union == 0 { 0.0 } else { round3(shared.len() as f64 / union as f64) };
    // Exact difference counts BEFORE capping the display lists, so score.json
    // reports the true asymmetry (e.g. 100 nowledge-only, not the shown 25).
    let ovp_only_count = ovp_only.len();
    let nowledge_only_count = nowledge_only.len();
    ConceptOverlap {
        metric: "LEXICAL_SET_OVERLAP (not semantic)".to_string(),
        normalization: "lowercase; non-alphanumeric → '-'; collapse".to_string(),
        ovp_count: ovp_keys.len(),
        nowledge_count: now_keys.len(),
        shared_count: shared.len(),
        ovp_only_count,
        nowledge_only_count,
        shared,
        ovp_only_truncated: ovp_only_count > TOP_N,
        nowledge_only_truncated: nowledge_only_count > TOP_N,
        ovp_only: cap(ovp_only),
        nowledge_only: cap(nowledge_only),
        jaccard_lexical: jaccard,
    }
}

fn claim_diff(ovp: &NormalizedSubject, nowledge: &NormalizedSubject) -> ClaimDiff {
    let ovp_by_section = count_by_section(ovp);
    let nowledge_by_section = count_by_section(nowledge);
    // Cheap deterministic overlap: an ovp claim "overlaps" if it shares ≥3
    // significant tokens with some Nowledge claim.
    let now_token_sets: Vec<BTreeSet<String>> =
        nowledge.claims.iter().map(|c| tokenize(&c.text).into_iter().collect()).collect();
    let mut overlapping = 0usize;
    for c in &ovp.claims {
        let toks: BTreeSet<String> = tokenize(&c.text).into_iter().collect();
        if now_token_sets.iter().any(|n| toks.intersection(n).count() >= 3) {
            overlapping += 1;
        }
    }
    ClaimDiff {
        metric: "LEXICAL_CLAIM_DIFF (not semantic)".to_string(),
        ovp_claim_count: ovp.claims.len(),
        nowledge_claim_count: nowledge.claims.len(),
        ovp_by_section,
        nowledge_by_section,
        lexically_overlapping_claims: overlapping,
        llm_judge: "not run (v1 is deterministic; enable explicitly when an LLM judge lands)"
            .to_string(),
    }
}

fn grounding_audit(
    ovp: Option<&NormalizedSubject>,
    nowledge: Option<&NormalizedSubject>,
    threshold: f64,
    reference_source: String,
) -> GroundingAudit {
    let (og, ou, orate, oex) = side_grounding(ovp);
    let (ng, nu, nrate, nex) = side_grounding(nowledge);
    GroundingAudit {
        metric: "LEXICAL_TOKEN_OVERLAP grounding vs a single shared reference".to_string(),
        threshold,
        reference_source,
        warning: "lexical only — misses paraphrase and legitimate synthesis; \
                  low grounding flags claims to inspect, it does NOT prove hallucination. \
                  BOTH sides are scored against ONE shared reference (see reference_source); \
                  when that is not what a side actually ingested, its rate can be biased."
            .to_string(),
        ovp_grounded: og,
        ovp_ungrounded: ou,
        ovp_rate: orate,
        nowledge_grounded: ng,
        nowledge_ungrounded: nu,
        nowledge_rate: nrate,
        ovp_ungrounded_examples: oex,
        nowledge_ungrounded_examples: nex,
    }
}

fn side_grounding(s: Option<&NormalizedSubject>) -> (usize, usize, f64, Vec<String>) {
    match s {
        None => (0, 0, 0.0, Vec::new()),
        Some(s) => {
            let g = s.structure.grounded_claims;
            let u = s.structure.ungrounded_claims;
            let total = g + u;
            let rate = if total == 0 { 0.0 } else { round3(g as f64 / total as f64) };
            let examples: Vec<String> = s
                .claims
                .iter()
                .filter(|c| !c.grounded)
                .take(EXAMPLES)
                .map(|c| truncate(&c.text, 160))
                .collect();
            (g, u, rate, examples)
        }
    }
}

fn structure_quality(
    ovp: &NormalizedSubject,
    nowledge: &NormalizedSubject,
    crystal_status: String,
) -> StructureQuality {
    StructureQuality {
        ovp_concepts: ovp.structure.concept_count,
        ovp_claims: ovp.structure.claim_count,
        ovp_sections: ovp.structure.section_count,
        nowledge_memory_titles: nowledge.structure.concept_count,
        nowledge_memories: nowledge.structure.memory_count,
        nowledge_global_crystals: nowledge.structure.global_crystal_count,
        crystal_status,
        current_input_crystal_comparison:
            "UNAVAILABLE — Nowledge exposes no source-scoped crystal API (crystals are \
             cross-source synthesized); the count shown is whole-store context, not this input"
                .to_string(),
        ovp_fragmentation: frag(ovp.structure.claim_count, ovp.structure.concept_count),
        nowledge_fragmentation: frag(nowledge.structure.claim_count, nowledge.structure.concept_count),
        note: "fragmentation = claims / concepts (heuristic; not a pass/fail). \
               ovp concepts are canonical evergreen nodes; nowledge 'concepts' are \
               atomic-memory titles — different granularity, compare with care."
            .to_string(),
    }
}

fn retrieval_comparison(
    ovp: Option<&NormalizedSubject>,
    nowledge: Option<&NormalizedSubject>,
    nowledge_global: &[crate::normalize::NormRetrievalHit],
    queries: &[String],
    ovp_status: String,
    scoped_status: String,
    global_status: String,
) -> RetrievalComparison {
    let mut rows = Vec::new();
    for q in queries {
        // Comparable lane: each side over THIS input, lexically.
        let (oh, og, ot) = scoped_row(ovp, q);
        let (nh, ng, nt) = scoped_row(nowledge, q);
        // Background lane: Nowledge whole-store search (NOT this input).
        let global: Vec<_> = nowledge_global.iter().filter(|h| h.query == *q).collect();
        let global_top: Vec<String> = global.iter().take(3).map(|h| truncate(&h.title, 80)).collect();
        rows.push(RetrievalRow {
            query: q.clone(),
            ovp_hits: oh,
            ovp_grounded: og,
            ovp_top: ot,
            nowledge_scoped_hits: nh,
            nowledge_scoped_grounded: ng,
            nowledge_scoped_top: nt,
            nowledge_global_hits: global.len(),
            nowledge_global_top: global_top,
        });
    }
    RetrievalComparison {
        metric: "RETRIEVAL — two lanes; only the scoped lane is comparable".to_string(),
        comparable_lane:
            "SCOPED: ovp-rag over the freshly-built vault vs Nowledge lexical over THIS source's \
             extracted memories — both lexical, both over this input. Deficiency findings come ONLY from here."
                .to_string(),
        background_lane:
            "GLOBAL: Nowledge /memories/search over the WHOLE store — may hit unrelated prior sources; \
             context only, NEVER a source of ovp-deficiency conclusions."
                .to_string(),
        queries: queries.to_vec(),
        ovp_status,
        nowledge_scoped_status: scoped_status,
        nowledge_global_status: global_status,
        rows,
    }
}

/// A side's hits for one query from its comparable (source-scoped) lane.
fn scoped_row(s: Option<&NormalizedSubject>, query: &str) -> (usize, usize, Vec<String>) {
    let Some(s) = s else { return (0, 0, Vec::new()) };
    let hits: Vec<_> = s.retrieval.iter().filter(|h| h.query == query).collect();
    let grounded = hits.iter().filter(|h| h.grounded).count();
    let top: Vec<String> = hits.iter().take(3).map(|h| truncate(&h.title, 80)).collect();
    (hits.len(), grounded, top)
}

#[allow(clippy::too_many_arguments)]
fn findings(
    input_mode: &str,
    concept: &Option<ConceptOverlap>,
    grounding: &Option<GroundingAudit>,
    structure: &Option<StructureQuality>,
    retrieval: &RetrievalComparison,
    ovp_status: &SideStatus,
    nowledge_status: &SideStatus,
) -> Vec<String> {
    let mut f = Vec::new();
    f.push(format!("input mode: {input_mode}"));
    if !input_mode.starts_with("shared") && !input_mode.starts_with("materialized") {
        f.push(
            "the two sides did NOT necessarily see byte-identical input — treat cross-system \
             differences as observations, not quality gaps (use --materialize-from-nowledge or a \
             shared --input for a strict same-input run)"
                .to_string(),
        );
    }
    if input_mode.starts_with("split") {
        f.push(
            "split mode: grounding for BOTH sides is measured against the local --input markdown, \
             NOT against the URL Nowledge actually fetched — Nowledge's grounding rate may be \
             biased downward if the two differ. Compare grounding rates with that in mind."
                .to_string(),
        );
    }
    if !ovp_status.available {
        f.push(format!("ovp side unavailable: {}", ovp_status.detail.as_deref().unwrap_or("unknown")));
    }
    if !nowledge_status.available {
        f.push(format!("nowledge side unavailable: {}", nowledge_status.detail.as_deref().unwrap_or("unknown")));
    }
    if let Some(c) = concept {
        f.push(format!(
            "concept overlap (lexical): {} shared, {} ovp-only, {} nowledge-only (Jaccard {:.3})",
            c.shared_count, c.ovp_only_count, c.nowledge_only_count, c.jaccard_lexical
        ));
        if c.shared_count == 0 && c.ovp_count > 0 && c.nowledge_count > 0 {
            f.push(
                "zero lexical concept overlap — expected: ovp emits canonical slugs, Nowledge emits \
                 memory-fact titles (different unit types). Inspect manually for SEMANTIC overlap."
                    .to_string(),
            );
        }
    }
    if let Some(g) = grounding {
        if g.ovp_ungrounded > 0 {
            f.push(format!(
                "ovp: {} of {} claims not lexically grounded in the input (rate {:.2}) — candidates for hallucination/synthesis review",
                g.ovp_ungrounded, g.ovp_grounded + g.ovp_ungrounded, g.ovp_rate
            ));
        }
        if g.nowledge_ungrounded > 0 {
            f.push(format!(
                "nowledge: {} of {} memories not lexically grounded in the input (rate {:.2})",
                g.nowledge_ungrounded, g.nowledge_grounded + g.nowledge_ungrounded, g.nowledge_rate
            ));
        }
    }
    if let Some(s) = structure {
        let crystals = match s.nowledge_global_crystals {
            Some(n) => format!("{n} (global, NOT this input)"),
            None => format!("unavailable ({})", s.crystal_status),
        };
        f.push(format!(
            "structure: ovp {} concepts / {} claims ({} sections); nowledge {} memory-titles / {} memories; crystals {crystals}",
            s.ovp_concepts, s.ovp_claims, s.ovp_sections, s.nowledge_memory_titles, s.nowledge_memories
        ));
        f.push(
            "per-input crystal comparison UNAVAILABLE: Nowledge has no source-scoped crystal API; \
             crystals are cross-source — not derivable for this single input"
                .to_string(),
        );
    }
    // Retrieval deficiency findings come ONLY from the comparable (scoped) lane.
    for row in &retrieval.rows {
        if row.ovp_hits == 0 && row.nowledge_scoped_hits > 0 {
            f.push(format!(
                "retrieval gap (scoped, comparable): ovp-rag returned 0 for '{}' over this input while Nowledge's own extracted memories matched {} — the ovp lexical ranker may be missing this query",
                truncate(&row.query, 60),
                row.nowledge_scoped_hits
            ));
        }
        if row.nowledge_scoped_hits == 0 && row.ovp_hits > 0 {
            f.push(format!(
                "retrieval gap (scoped, comparable): Nowledge's extracted memories matched 0 for '{}' while ovp-rag returned {}",
                truncate(&row.query, 60),
                row.ovp_hits
            ));
        }
    }
    f
}

// --- small helpers --------------------------------------------------------

fn count_by_section(s: &NormalizedSubject) -> BTreeMap<String, usize> {
    let mut m = BTreeMap::new();
    for c in &s.claims {
        *m.entry(c.section.clone()).or_insert(0) += 1;
    }
    m
}

fn frag(claims: usize, concepts: usize) -> f64 {
    if concepts == 0 {
        0.0
    } else {
        round3(claims as f64 / concepts as f64)
    }
}

fn cap(mut v: Vec<String>) -> Vec<String> {
    v.truncate(TOP_N);
    v
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        return s.to_string();
    }
    let end = s.char_indices().nth(max).map(|(i, _)| i).unwrap_or(s.len());
    format!("{}…", &s[..end])
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::normalize::{NormConcept, NormRetrievalHit, NormSource, NormStructure, NormalizedSubject};

    fn subj(system: &str, concept_keys: &[&str]) -> NormalizedSubject {
        NormalizedSubject {
            system: system.into(),
            source: NormSource::default(),
            concepts: concept_keys
                .iter()
                .map(|k| NormConcept { key: (*k).into(), label: (*k).into(), kind: "x".into() })
                .collect(),
            claims: vec![],
            structure: NormStructure {
                concept_count: concept_keys.len(),
                ..NormStructure::default()
            },
            retrieval: vec![],
            notes: vec![],
        }
    }

    fn inputs<'a>(
        ovp: Option<&'a NormalizedSubject>,
        nowledge: Option<&'a NormalizedSubject>,
        ovp_status: SideStatus,
        nowledge_status: SideStatus,
        queries: &'a [String],
        global: &'a [NormRetrievalHit],
    ) -> BuildInputs<'a> {
        BuildInputs {
            case_id: "case",
            input_mode: "shared-markdown",
            ovp,
            nowledge,
            ovp_status,
            nowledge_status,
            grounding_threshold: 0.5,
            queries,
            nowledge_global: global,
            retrieval_ovp_status: "available".into(),
            retrieval_scoped_status: "available".into(),
            retrieval_global_status: "available".into(),
            crystal_status: "available (global, 50)".into(),
            grounding_reference: "the shared local --input markdown".into(),
        }
    }

    #[test]
    fn concept_overlap_partitions_shared_and_only_with_full_counts() {
        let o = subj("ovp-next", &["rag", "agent", "moc"]);
        let n = subj("nowledge-mem", &["rag", "memory", "agent"]);
        let c = concept_overlap(&o, &n);
        assert_eq!(c.shared, vec!["agent".to_string(), "rag".to_string()]);
        assert_eq!(c.shared_count, 2);
        assert_eq!(c.ovp_only, vec!["moc".to_string()]);
        assert_eq!(c.ovp_only_count, 1);
        assert_eq!(c.nowledge_only, vec!["memory".to_string()]);
        assert_eq!(c.nowledge_only_count, 1);
        // |shared|=2, |union|=4 → 0.5
        assert!((c.jaccard_lexical - 0.5).abs() < 1e-9);
    }

    #[test]
    fn concept_only_counts_are_exact_even_when_display_truncated() {
        // 30 nowledge-only keys → the display list caps at TOP_N but the count
        // must stay exact (the M8.1 fix: score.json never loses the true count).
        let ovp_keys: Vec<String> = vec!["shared".into()];
        let now_keys: Vec<String> =
            std::iter::once("shared".to_string()).chain((0..30).map(|i| format!("n{i:02}"))).collect();
        let o = subj("ovp-next", &ovp_keys.iter().map(|s| s.as_str()).collect::<Vec<_>>());
        let n = subj("nowledge-mem", &now_keys.iter().map(|s| s.as_str()).collect::<Vec<_>>());
        let c = concept_overlap(&o, &n);
        assert_eq!(c.nowledge_only_count, 30, "exact count preserved");
        assert!(c.nowledge_only.len() <= TOP_N, "display list capped");
        assert!(c.nowledge_only_truncated, "truncation flagged");
    }

    #[test]
    fn build_with_missing_nowledge_yields_partial_with_findings() {
        let o = subj("ovp-next", &["a"]);
        let queries = vec!["q1".to_string()];
        let cmp = build(inputs(
            Some(&o),
            None,
            SideStatus::ok(),
            SideStatus::failed("connection refused"),
            &queries,
            &[],
        ));
        assert!(cmp.concept_overlap.is_none(), "no overlap without both sides");
        assert!(cmp.structure.is_none());
        assert!(cmp.grounding.is_some());
        assert_eq!(cmp.retrieval.rows.len(), 1);
        assert!(
            cmp.findings.iter().any(|f| f.contains("nowledge side unavailable")),
            "should surface the loud failure: {:?}",
            cmp.findings
        );
    }

    #[test]
    fn scoped_lane_drives_retrieval_findings_not_global() {
        // ovp 0 in the scoped lane, Nowledge 0 in the scoped lane, but Nowledge
        // global has hits → there must be NO ovp-deficiency finding (global is
        // background only). This is the M8.1 honesty fix.
        let o = subj("ovp-next", &["a"]);
        let n = subj("nowledge-mem", &["b"]); // both empty scoped retrieval
        let queries = vec!["q1".to_string()];
        let global = vec![NormRetrievalHit {
            query: "q1".into(),
            rank: 0,
            title: "unrelated prior source".into(),
            snippet: "x".into(),
            grounded: false,
        }];
        let cmp = build(inputs(Some(&o), Some(&n), SideStatus::ok(), SideStatus::ok(), &queries, &global));
        let row = &cmp.retrieval.rows[0];
        assert_eq!(row.nowledge_global_hits, 1, "global hit recorded");
        assert_eq!(row.nowledge_scoped_hits, 0, "scoped lane empty");
        assert!(
            !cmp.findings.iter().any(|f| f.contains("retrieval gap")),
            "global hits must NOT produce an ovp-deficiency finding: {:?}",
            cmp.findings
        );
    }

    #[test]
    fn split_mode_emits_byte_identical_and_grounding_caveats() {
        let o = subj("ovp-next", &["a"]);
        let n = subj("nowledge-mem", &["b"]);
        let queries = vec!["q1".to_string()];
        let mut inp = inputs(Some(&o), Some(&n), SideStatus::ok(), SideStatus::ok(), &queries, &[]);
        inp.input_mode = "split (ovp = local --input markdown; nowledge = fetched --url — NOT byte-identical)";
        inp.grounding_reference = "the local --input markdown (split mode: NOT the URL Nowledge fetched)".into();
        let cmp = build(inp);
        assert!(
            cmp.findings.iter().any(|f| f.contains("did NOT necessarily see byte-identical")),
            "split must carry the not-byte-identical caveat: {:?}",
            cmp.findings
        );
        assert!(
            cmp.findings.iter().any(|f| f.contains("grounding for BOTH sides is measured against the local --input")),
            "split must carry the grounding caveat: {:?}",
            cmp.findings
        );
        assert!(cmp.grounding.unwrap().reference_source.contains("local --input markdown"));
    }

    #[test]
    fn scoped_row_counts_hits_and_grounding() {
        let mut o = subj("ovp-next", &[]);
        o.retrieval = vec![
            NormRetrievalHit { query: "q".into(), rank: 0, title: "A".into(), snippet: "x".into(), grounded: true },
            NormRetrievalHit { query: "q".into(), rank: 1, title: "B".into(), snippet: "y".into(), grounded: false },
        ];
        let (hits, grounded, top) = scoped_row(Some(&o), "q");
        assert_eq!(hits, 2);
        assert_eq!(grounded, 1);
        assert_eq!(top, vec!["A".to_string(), "B".to_string()]);
    }
}
