use std::collections::HashSet;

use ovp_core::{DropReason, EventKind, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;
use crate::canonical_slug::CanonicalSlug;
use crate::concept_registry::ConceptRegistry;
use crate::interpreted::ExtractedConcept;

/// Promotes v1 candidate concepts to canonical when the `ConceptRegistry`
/// knows them, AND (v2) gates the extracted concept map. Pure: same input +
/// same registry → same output.
///
/// v1 promotion is alias-aware: a candidate that's an alias of a canonical
/// slug is promoted to the *canonical* spelling, and duplicates collapse.
///
/// v2 gate (when `doc.concepts` is non-empty): drops, with observable events,
/// concepts that are invalid-slug / `promote=false` / carry a `reject_reason` /
/// lack a definition, evidence, or owned claims; collapses duplicate slugs and
/// `merge_with` targets (first survivor wins, deterministic). It encodes ONLY
/// general rules — no benchmark slugs, no Nowledge rules, no article specifics.
/// What to mint is the prompt's judgment; the benchmark validates it.
pub struct ConceptResolver {
    step: StepId,
    registry: ConceptRegistry,
}

impl ConceptResolver {
    pub fn new(step: impl Into<String>, registry: ConceptRegistry) -> Self {
        Self { step: StepId::new(step.into()), registry }
    }

    /// Convenience constructor: build a canonical-only registry from a
    /// slug slice. Keeps test/call sites terse.
    pub fn from_slugs(step: impl Into<String>, slugs: &[&str]) -> Self {
        Self::new(step, ConceptRegistry::from_slugs(slugs))
    }

    pub fn inventory_size(&self) -> usize {
        self.registry.canonical_count()
    }
}

impl Transform<DomainBody> for ConceptResolver {
    fn step_id(&self) -> &StepId { &self.step }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let mut doc = match record.body {
            DomainBody::Interpreted(d) => *d,
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.concept_resolver.wrong_variant",
                    format!("expected Interpreted, got {}", other.variant_name()),
                ));
            }
        };

        // Resolve each candidate through the registry. Known ones promote
        // to their canonical spelling (deduped); unknowns stay candidates.
        // Order is preserved within each list.
        let mut promoted: Vec<String> = Vec::new();
        let mut remaining: Vec<String> = Vec::new();
        for cand in std::mem::take(&mut doc.concept_candidates) {
            match self.registry.resolve(&cand) {
                Some(canon) => {
                    let canon = canon.to_string();
                    if !promoted.contains(&canon) && !doc.canonical_concepts.contains(&canon) {
                        promoted.push(canon);
                    }
                }
                None => remaining.push(cand),
            }
        }
        doc.canonical_concepts.extend(promoted);
        doc.concept_candidates = remaining;

        // v2 gate: filter/merge the extracted concept map in place. No-op when
        // `concepts` is empty (v1), so v1 behavior is untouched.
        let mut drop_events: Vec<EventKind> = Vec::new();
        if !doc.concepts.is_empty() {
            let (kept, drops) = gate_concepts(std::mem::take(&mut doc.concepts));
            doc.concepts = kept;
            for reason in drops {
                drop_events.push(EventKind::FilterDropped {
                    record_id: record.id.clone(),
                    step_id: self.step.clone(),
                    reason,
                });
            }
        }

        let next = Record {
            id: record.id,
            body: DomainBody::Interpreted(Box::new(doc)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "concept resolution applied");
        if drop_events.is_empty() {
            FilterDecision::Forward(vec![next])
        } else {
            FilterDecision::ForwardWithEvents { records: vec![next], events: drop_events }
        }
    }
}

/// Gate an extracted v2 concept map with GENERAL rules only. Returns the
/// surviving concepts and a `DropReason` per rejected/merged concept (the
/// caller turns each into an observable `FilterDropped` event).
///
/// **Two-phase, so merge is order-independent.** A real model can emit a synonym
/// *before* the canonical concept it should merge into; a single-pass gate that
/// only merged against already-kept slugs would then leak the synonym. So:
///
/// - **Phase 1 (structural validity):** drop invalid-slug / `promote=false` /
///   `reject_reason` / no-definition-or-evidence-or-claims concepts. Collect the
///   survivors and the set of every surviving slug + alias.
/// - **Phase 2 (dedup + merge):** a survivor whose `merge_with` names ANY OTHER
///   surviving slug/alias is a synonym → drop it, regardless of emission order.
///   The first survivor of a duplicate slug wins (deterministic).
///
/// A pathological *mutual* merge (`A.merge_with=[B]` and `B.merge_with=[A]`)
/// drops both — models should point synonyms at a canonical root, not at each
/// other; we never silently keep a duplicate.
///
/// **Spelling-robust.** Dedup + merge matching compare a normalized [`norm_key`]
/// (ASCII case-fold + `_`/space → `-`, collapsed) rather than raw bytes, so a
/// model that varies case/separator across `slug` / `aliases` / `merge_with`
/// (`Idea-Block`, `idea_block`, `idea block`) still collapses to one concept
/// instead of minting duplicate identities. (CJK fullwidth/halfwidth is NOT
/// folded — out of scope; ASCII case/separator is the realistic model drift.)
fn gate_concepts(concepts: Vec<ExtractedConcept>) -> (Vec<ExtractedConcept>, Vec<DropReason>) {
    let mut drops: Vec<DropReason> = Vec::new();

    // Phase 1: structural validity. `present` holds the normalized key of every
    // surviving slug + alias, so Phase 2 can resolve a `merge_with` target
    // whether it was emitted before or after the synonym and regardless of
    // case/separator spelling.
    let mut survivors: Vec<ExtractedConcept> = Vec::new();
    let mut present: HashSet<String> = HashSet::new();
    for c in concepts {
        let slug = match CanonicalSlug::parse(&c.slug) {
            Ok(s) => s.into_string(),
            Err(e) => {
                drops.push(DropReason::new(
                    "transform.concept_resolver.invalid_slug",
                    format!("`{}`: {e}", c.slug),
                ));
                continue;
            }
        };
        if !c.promote {
            drops.push(DropReason::new(
                "transform.concept_resolver.not_promoted",
                format!("`{slug}`: {}", c.reject_reason.as_deref().unwrap_or("promote=false")),
            ));
            continue;
        }
        if let Some(reason) = c.reject_reason.as_deref().filter(|r| !r.trim().is_empty()) {
            drops.push(DropReason::new(
                "transform.concept_resolver.rejected",
                format!("`{slug}`: {reason}"),
            ));
            continue;
        }
        // Content-aware grounding floor: a non-empty Vec of whitespace-only
        // entries is NOT grounding. Match how `EvergreenConcept::from_extracted`
        // consumes these (trim + drop-empty), so the gate never passes a concept
        // that would mint a claim-less, ungrounded note.
        let has_evidence = c.evidence.iter().any(|e| !e.trim().is_empty());
        let has_claim = c.claims.iter().any(|c| !c.trim().is_empty());
        if c.definition.trim().is_empty() || !has_evidence || !has_claim {
            drops.push(DropReason::new(
                "transform.concept_resolver.low_evidence",
                format!("`{slug}`: missing definition, evidence, or claims"),
            ));
            continue;
        }
        present.insert(norm_key(&slug));
        for a in &c.aliases {
            if !a.trim().is_empty() {
                present.insert(norm_key(a));
            }
        }
        let mut c = c;
        c.slug = slug;
        survivors.push(c);
    }

    // Phase 2: dedup + order-independent merge.
    let mut kept: Vec<ExtractedConcept> = Vec::new();
    // Dedup keys are SLUGS ONLY (normalized) — NOT aliases. A distinct concept
    // whose slug happens to equal a prior concept's alias must not be dropped as
    // a "duplicate"; alias matching belongs to merge resolution (via `present`).
    let mut kept_keys: HashSet<String> = HashSet::new();
    for c in survivors {
        let slug_key = norm_key(&c.slug);
        if kept_keys.contains(&slug_key) {
            drops.push(DropReason::new(
                "transform.concept_resolver.duplicate",
                format!("`{}`: duplicate slug", c.slug),
            ));
            continue;
        }
        // A `merge_with` that names another surviving concept (not itself) makes
        // this a synonym. Self-references are ignored; targets that did not
        // survive Phase 1 do not count. Compared by normalized key.
        let own: HashSet<String> = std::iter::once(slug_key.clone())
            .chain(c.aliases.iter().filter(|a| !a.trim().is_empty()).map(|a| norm_key(a)))
            .collect();
        if let Some(target) = c
            .merge_with
            .iter()
            .filter(|t| !t.trim().is_empty())
            .map(|t| norm_key(t))
            .find(|t| !own.contains(t) && present.contains(t))
        {
            drops.push(DropReason::new(
                "transform.concept_resolver.merged",
                format!("`{}`: merged into `{target}`", c.slug),
            ));
            continue;
        }
        kept_keys.insert(slug_key);
        kept.push(c);
    }
    (kept, drops)
}

/// Normalization key for gate-internal dedup + merge matching ONLY (never the
/// minted slug — the surviving concept keeps its `CanonicalSlug`-parsed spelling).
/// Folds the spellings a model realistically varies for the SAME concept: ASCII
/// case, and `_` / space → `-`, with runs of `-` collapsed. Pure ASCII; CJK is
/// left intact (lowercasing/`-`-mapping a CJK string is a no-op).
fn norm_key(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_dash = false;
    for ch in s.trim().chars() {
        let c = if ch == '_' || ch == ' ' || ch == '-' { '-' } else { ch.to_ascii_lowercase() };
        if c == '-' {
            if prev_dash {
                continue;
            }
            prev_dash = true;
        } else {
            prev_dash = false;
        }
        out.push(c);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::interpreted::{Dimensions, Explanation, InterpretationSchema, InterpretedDoc};
    use ovp_core::{RecordId, RecordMeta, RunId};

    fn interp(candidates: Vec<&str>) -> InterpretedDoc {
        InterpretedDoc {
            title: "T".into(),
            source_url: "https://example.com/".into(),
            author: None,
            date: "2026-05-28".into(),
            doc_type: "article".into(),
            area: "ai".into(),
            tags: vec![],
            canonical_concepts: vec![],
            concept_candidates: candidates.into_iter().map(String::from).collect(),
            dimensions: Dimensions {
                one_liner: "x".into(),
                explanation: Explanation { what: "".into(), why: "".into(), how: "".into() },
                details: vec![],
                structure: None,
                actions: vec![],
                linked_concepts: vec![],
            },
            schema: InterpretationSchema::ArticleV1,
            concepts: Vec::new(),
        }
    }

    fn record(d: InterpretedDoc) -> Record<DomainBody> {
        Record::new(
            RecordId::new("r-1"),
            DomainBody::Interpreted(Box::new(d)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn promotes_matching_candidates() {
        let mut r = ConceptResolver::from_slugs("cr", &["ai-agent", "competitive-advantage"]);
        let doc = interp(vec![
            "ai-agent",
            "business-process-management",
            "competitive-advantage",
            "digital-transformation",
        ]);
        match r.process(record(doc)) {
            FilterDecision::Forward(mut rs) => {
                let body = match rs.pop().unwrap().body {
                    DomainBody::Interpreted(d) => *d,
                    _ => unreachable!(),
                };
                assert_eq!(body.canonical_concepts, vec!["ai-agent", "competitive-advantage"]);
                assert_eq!(
                    body.concept_candidates,
                    vec!["business-process-management", "digital-transformation"]
                );
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn empty_inventory_no_ops() {
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let doc = interp(vec!["a", "b", "c"]);
        match r.process(record(doc)) {
            FilterDecision::Forward(mut rs) => {
                let body = match rs.pop().unwrap().body {
                    DomainBody::Interpreted(d) => *d,
                    _ => unreachable!(),
                };
                assert!(body.canonical_concepts.is_empty());
                assert_eq!(body.concept_candidates, vec!["a", "b", "c"]);
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn no_matches_keeps_candidates() {
        let mut r = ConceptResolver::from_slugs("cr", &["zzz-never-matches"]);
        let doc = interp(vec!["a", "b"]);
        match r.process(record(doc)) {
            FilterDecision::Forward(mut rs) => {
                let body = match rs.pop().unwrap().body {
                    DomainBody::Interpreted(d) => *d,
                    _ => unreachable!(),
                };
                assert!(body.canonical_concepts.is_empty());
                assert_eq!(body.concept_candidates, vec!["a", "b"]);
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn wrong_variant_drops() {
        use crate::source_doc::SourceDoc;
        let mut r = ConceptResolver::from_slugs("cr", &["x"]);
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(SourceDoc::article("", "", None, None, vec![], ""))),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match r.process(rec) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.concept_resolver.wrong_variant");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn promotes_alias_to_canonical_spelling() {
        use crate::concept_registry::ConceptRegistry;
        let mut reg = ConceptRegistry::new();
        reg.insert_canonical("ai-agent");
        reg.insert_alias("ai-agents", "ai-agent");
        let mut r = ConceptResolver::new("cr", reg);
        // Candidate uses the alias spelling AND the canonical; both should
        // collapse to the single canonical slug.
        let doc = interp(vec!["ai-agents", "ai-agent", "unrelated"]);
        match r.process(record(doc)) {
            FilterDecision::Forward(mut rs) => {
                let body = match rs.pop().unwrap().body {
                    DomainBody::Interpreted(d) => *d,
                    _ => unreachable!(),
                };
                assert_eq!(body.canonical_concepts, vec!["ai-agent"]);
                assert_eq!(body.concept_candidates, vec!["unrelated"]);
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn preserves_existing_canonicals() {
        let mut r = ConceptResolver::from_slugs("cr", &["new-canonical"]);
        let mut doc = interp(vec!["new-canonical", "still-candidate"]);
        doc.canonical_concepts = vec!["already-canonical".into()];
        match r.process(record(doc)) {
            FilterDecision::Forward(mut rs) => {
                let body = match rs.pop().unwrap().body {
                    DomainBody::Interpreted(d) => *d,
                    _ => unreachable!(),
                };
                assert_eq!(body.canonical_concepts, vec!["already-canonical", "new-canonical"]);
                assert_eq!(body.concept_candidates, vec!["still-candidate"]);
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    // ---- M13.2 v2 concept-map gate ----

    use crate::interpreted::{ConceptKind, ExtractedConcept};

    fn concept(slug: &str, promote: bool) -> ExtractedConcept {
        ExtractedConcept {
            slug: slug.into(),
            title: slug.into(),
            aliases: vec![],
            kind: ConceptKind::Concept,
            definition: format!("{slug} is a specific thing."),
            evidence: vec![format!("evidence for {slug}")],
            claims: vec![format!("{slug} owned claim")],
            related: vec![],
            merge_with: vec![],
            reject_reason: None,
            promote,
        }
    }

    fn doc_with_concepts(cs: Vec<ExtractedConcept>) -> InterpretedDoc {
        let mut d = interp(vec![]);
        d.schema = InterpretationSchema::ConceptMapV2;
        d.concepts = cs;
        d
    }

    fn codes(events: &[EventKind]) -> Vec<String> {
        events
            .iter()
            .filter_map(|e| match e {
                EventKind::FilterDropped { reason, .. } => Some(reason.code.as_str().to_string()),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn v2_gate_drops_bad_concepts_with_events() {
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let good = concept("idea-block", true);
        let not_promoted = concept("data-pipeline", false);
        let mut rejected = concept("knowledge-unit", true);
        rejected.reject_reason = Some("synonym of idea-block".into());
        let mut low = concept("vector-geometry", true);
        low.claims = vec![];
        let bad_slug = concept("a/b", true);
        let doc = doc_with_concepts(vec![good, not_promoted, rejected, low, bad_slug]);
        match r.process(record(doc)) {
            FilterDecision::ForwardWithEvents { records, events } => {
                let body = match &records[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert_eq!(
                    body.concepts.iter().map(|c| c.slug.as_str()).collect::<Vec<_>>(),
                    vec!["idea-block"],
                    "only the valid promoted concept survives"
                );
                let cs = codes(&events);
                assert!(cs.contains(&"transform.concept_resolver.not_promoted".to_string()));
                assert!(cs.contains(&"transform.concept_resolver.rejected".to_string()));
                assert!(cs.contains(&"transform.concept_resolver.low_evidence".to_string()));
                assert!(cs.contains(&"transform.concept_resolver.invalid_slug".to_string()));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn v2_gate_merges_and_dedups() {
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let a = concept("idea-block", true);
        let mut syn = concept("qa-packet", true);
        syn.merge_with = vec!["idea-block".into()];
        let dup = concept("idea-block", true);
        let doc = doc_with_concepts(vec![a, syn, dup]);
        match r.process(record(doc)) {
            FilterDecision::ForwardWithEvents { records, events } => {
                let body = match &records[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert_eq!(
                    body.concepts.iter().map(|c| c.slug.as_str()).collect::<Vec<_>>(),
                    vec!["idea-block"]
                );
                let cs = codes(&events);
                assert!(cs.contains(&"transform.concept_resolver.merged".to_string()));
                assert!(cs.contains(&"transform.concept_resolver.duplicate".to_string()));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn merge_target_later_still_collapses_synonym() {
        // Regression (M13.2 follow-up): the synonym is emitted BEFORE the
        // canonical concept it merges into. A single-pass gate that only merged
        // against already-kept slugs would leak the synonym; the two-phase gate
        // collapses it regardless of order.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let mut syn = concept("qa-packet", true);
        syn.merge_with = vec!["idea-block".into()];
        let canonical = concept("idea-block", true);
        let doc = doc_with_concepts(vec![syn, canonical]); // synonym FIRST
        match r.process(record(doc)) {
            FilterDecision::ForwardWithEvents { records, events } => {
                let body = match &records[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert_eq!(
                    body.concepts.iter().map(|c| c.slug.as_str()).collect::<Vec<_>>(),
                    vec!["idea-block"],
                    "canonical survives, synonym collapses even when emitted first"
                );
                assert!(codes(&events).contains(&"transform.concept_resolver.merged".to_string()));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn mutual_merge_drops_both_never_leaks_a_duplicate() {
        // Pathological: A and B each name the other as their merge target. The
        // gate never silently keeps a duplicate — it drops both (a model should
        // point synonyms at a canonical root, not at each other).
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let mut a = concept("alpha", true);
        a.merge_with = vec!["beta".into()];
        let mut b = concept("beta", true);
        b.merge_with = vec!["alpha".into()];
        match r.process(record(doc_with_concepts(vec![a, b]))) {
            FilterDecision::ForwardWithEvents { records, events } => {
                let body = match &records[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert!(body.concepts.is_empty(), "mutual merge drops both, no leak");
                assert_eq!(
                    codes(&events)
                        .iter()
                        .filter(|c| *c == "transform.concept_resolver.merged")
                        .count(),
                    2
                );
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn self_referential_merge_is_ignored() {
        // A concept naming itself in `merge_with` is not a synonym of anything;
        // it must be kept, not dropped.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let mut c = concept("idea-block", true);
        c.merge_with = vec!["idea-block".into()];
        match r.process(record(doc_with_concepts(vec![c]))) {
            FilterDecision::Forward(rs) => {
                let body = match &rs[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert_eq!(body.concepts.len(), 1, "self-merge kept, not dropped");
            }
            other => panic!("expected Forward (no drops), got {other:?}"),
        }
    }

    #[test]
    fn merge_into_dropped_target_does_not_merge() {
        // If the merge target did not survive Phase 1 (e.g. it was low-evidence),
        // the synonym is NOT collapsed into a thing that won't exist — it is
        // kept (it may be the only carrier of that idea).
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let mut target = concept("weak-target", true);
        target.claims = vec![]; // low-evidence → dropped in Phase 1
        let mut syn = concept("real-concept", true);
        syn.merge_with = vec!["weak-target".into()];
        match r.process(record(doc_with_concepts(vec![target, syn]))) {
            FilterDecision::ForwardWithEvents { records, events } => {
                let body = match &records[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert_eq!(
                    body.concepts.iter().map(|c| c.slug.as_str()).collect::<Vec<_>>(),
                    vec!["real-concept"],
                    "synonym kept when its merge target was dropped"
                );
                let cs = codes(&events);
                assert!(cs.contains(&"transform.concept_resolver.low_evidence".to_string()));
                assert!(!cs.contains(&"transform.concept_resolver.merged".to_string()));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn resolver_preserves_v2_schema_marker_through_the_gate() {
        // Phase 1 (M13.3): the resolver may gate concepts but must NOT touch the
        // schema marker — the writer relies on it to fail loud on an empty map.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let doc = doc_with_concepts(vec![concept("kept-a", true), concept("kept-b", true)]);
        assert_eq!(doc.schema, InterpretationSchema::ConceptMapV2);
        let out = match r.process(record(doc)) {
            FilterDecision::Forward(rs) => rs,
            FilterDecision::ForwardWithEvents { records, .. } => records,
            other => panic!("unexpected: {other:?}"),
        };
        match &out[0].body {
            DomainBody::Interpreted(d) => {
                assert_eq!(d.schema, InterpretationSchema::ConceptMapV2, "marker preserved");
            }
            _ => unreachable!(),
        }
    }

    fn kept_slugs(records: &[Record<DomainBody>]) -> Vec<String> {
        match &records[0].body {
            DomainBody::Interpreted(d) => d.concepts.iter().map(|c| c.slug.clone()).collect(),
            _ => unreachable!(),
        }
    }

    #[test]
    fn merge_target_case_separator_drift_still_collapses() {
        // Audit finding #1: a synonym whose `merge_with` names the canonical
        // concept with case/separator drift (`Idea_Block` vs `idea-block`) must
        // still collapse — byte-exact matching would leak it as a duplicate.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let canonical = concept("idea-block", true);
        let mut syn = concept("qa-packet", true);
        syn.merge_with = vec!["Idea_Block".into()]; // case + `_` drift
        match r.process(record(doc_with_concepts(vec![canonical, syn]))) {
            FilterDecision::ForwardWithEvents { records, events } => {
                assert_eq!(kept_slugs(&records), vec!["idea-block"]);
                assert!(codes(&events).contains(&"transform.concept_resolver.merged".to_string()));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn duplicate_slug_case_separator_variant_collapses() {
        // Audit finding #2: two spellings of one concept (`vector-database` vs
        // `Vector_Database`) must collapse to one minted identity, not two.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let a = concept("vector-database", true);
        let b = concept("Vector_Database", true); // same concept, drifted spelling
        match r.process(record(doc_with_concepts(vec![a, b]))) {
            FilterDecision::ForwardWithEvents { records, events } => {
                assert_eq!(
                    kept_slugs(&records),
                    vec!["vector-database"],
                    "first spelling survives; the variant is a duplicate"
                );
                assert!(codes(&events).contains(&"transform.concept_resolver.duplicate".to_string()));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn whitespace_only_evidence_or_claims_drops_low_evidence() {
        // Audit finding #3: a non-empty Vec of whitespace-only entries is not
        // grounding — the gate must drop it (matching how from_extracted trims),
        // not mint a claim-less note.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let mut blank_ev = concept("ev-ghost", true);
        blank_ev.evidence = vec!["   ".into()];
        let mut blank_cl = concept("cl-ghost", true);
        blank_cl.claims = vec!["\n".into()];
        let good = concept("solid", true);
        match r.process(record(doc_with_concepts(vec![blank_ev, blank_cl, good]))) {
            FilterDecision::ForwardWithEvents { records, events } => {
                assert_eq!(kept_slugs(&records), vec!["solid"]);
                let n_low = codes(&events)
                    .iter()
                    .filter(|c| *c == "transform.concept_resolver.low_evidence")
                    .count();
                assert_eq!(n_low, 2, "both whitespace-only concepts drop low_evidence");
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn slug_equal_to_another_concepts_alias_is_not_a_duplicate() {
        // Audit finding #4 (verifier crashed; assessed manually): a DISTINCT
        // concept whose slug equals a prior concept's alias must NOT be dropped
        // as a duplicate — dedup keys are slugs only. Order-independent.
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let mk = || {
            let mut a = concept("idea-block", true);
            a.aliases = vec!["packet".into()];
            let b = concept("packet", true); // distinct concept, no merge_with
            (a, b)
        };
        // Forward order.
        let (a, b) = mk();
        match r.process(record(doc_with_concepts(vec![a, b]))) {
            FilterDecision::Forward(rs) => {
                let mut got = kept_slugs(&rs);
                got.sort();
                assert_eq!(got, vec!["idea-block", "packet"], "both kept, no false duplicate");
            }
            other => panic!("expected Forward (no drops), got {other:?}"),
        }
        // Reverse order — same outcome.
        let (a, b) = mk();
        match r.process(record(doc_with_concepts(vec![b, a]))) {
            FilterDecision::Forward(rs) => {
                let mut got = kept_slugs(&rs);
                got.sort();
                assert_eq!(got, vec!["idea-block", "packet"], "order-independent");
            }
            other => panic!("expected Forward (no drops), got {other:?}"),
        }
    }

    #[test]
    fn v2_gate_all_valid_no_events() {
        let mut r = ConceptResolver::from_slugs("cr", &[]);
        let doc = doc_with_concepts(vec![concept("a-concept", true), concept("b-concept", true)]);
        match r.process(record(doc)) {
            FilterDecision::Forward(rs) => {
                let body = match &rs[0].body {
                    DomainBody::Interpreted(d) => d,
                    _ => unreachable!(),
                };
                assert_eq!(body.concepts.len(), 2, "both valid concepts kept, no drop events");
            }
            other => panic!("expected Forward (no drops), got {other:?}"),
        }
    }
}
