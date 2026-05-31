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
/// caller turns each into an observable `FilterDropped` event). Deterministic
/// and order-stable: the first survivor of a duplicate/merge wins.
fn gate_concepts(concepts: Vec<ExtractedConcept>) -> (Vec<ExtractedConcept>, Vec<DropReason>) {
    let mut kept: Vec<ExtractedConcept> = Vec::new();
    let mut kept_slugs: HashSet<String> = HashSet::new();
    let mut drops: Vec<DropReason> = Vec::new();
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
        if c.definition.trim().is_empty() || c.evidence.is_empty() || c.claims.is_empty() {
            drops.push(DropReason::new(
                "transform.concept_resolver.low_evidence",
                format!("`{slug}`: missing definition, evidence, or claims"),
            ));
            continue;
        }
        if kept_slugs.contains(&slug) {
            drops.push(DropReason::new(
                "transform.concept_resolver.duplicate",
                format!("`{slug}`: duplicate slug"),
            ));
            continue;
        }
        if let Some(target) = c.merge_with.iter().find(|t| kept_slugs.contains(*t)) {
            drops.push(DropReason::new(
                "transform.concept_resolver.merged",
                format!("`{slug}`: merged into `{target}`"),
            ));
            continue;
        }
        let mut c = c;
        c.slug = slug.clone();
        kept_slugs.insert(slug);
        for a in &c.aliases {
            kept_slugs.insert(a.clone());
        }
        kept.push(c);
    }
    (kept, drops)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::interpreted::{Dimensions, Explanation, InterpretedDoc};
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
