use ovp_core::{DropReason, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;
use crate::concept_registry::ConceptRegistry;

/// Promotes candidate concepts to canonical when the `ConceptRegistry`
/// knows them. Pure: same input + same registry → same output.
///
/// Promotion is alias-aware: a candidate that's an alias of a canonical
/// slug is promoted to the *canonical* spelling, and duplicates collapse.
/// Unknown candidates stay candidates — minting a new evergreen is a
/// separate human-reviewed step (the legacy "absorb" stage), not this.
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

        let next = Record {
            id: record.id,
            body: DomainBody::Interpreted(Box::new(doc)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "concept resolution applied");
        FilterDecision::Forward(vec![next])
    }
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
}
