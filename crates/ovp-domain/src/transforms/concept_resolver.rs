use std::collections::HashSet;

use ovp_core::{DropReason, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;

/// Promotes candidate concepts to canonical when they appear in a known
/// inventory of existing evergreen slugs. Pure: same input + same
/// inventory → same output.
///
/// v1.1 takes the inventory at construction time (typically a hardcoded
/// or config-loaded set). A future version will read it from a
/// CanonicalStore, but that crate doesn't exist yet — the only thing
/// promotion needs is "does this slug already have a page?".
///
/// Does NOT add slugs to the inventory. New candidate slugs that do
/// not match stay candidates; promotion is a separate human-reviewed
/// step (the "absorb" stage in the legacy pipeline).
pub struct ConceptResolver {
    step: StepId,
    inventory: HashSet<String>,
}

impl ConceptResolver {
    pub fn new(step: impl Into<String>, inventory: HashSet<String>) -> Self {
        Self { step: StepId::new(step.into()), inventory }
    }

    /// Convenience constructor for callers building from a slice.
    pub fn from_slugs(step: impl Into<String>, slugs: &[&str]) -> Self {
        let inventory: HashSet<String> = slugs.iter().map(|s| s.to_string()).collect();
        Self::new(step, inventory)
    }

    pub fn inventory_size(&self) -> usize { self.inventory.len() }
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

        // Promote: any candidate slug that's in the inventory becomes
        // canonical. Order is preserved.
        let (promoted, remaining): (Vec<_>, Vec<_>) = doc
            .concept_candidates
            .into_iter()
            .partition(|slug| self.inventory.contains(slug));

        // Existing canonicals are preserved (in case the parser ever
        // emits them directly — currently always []).
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
            DomainBody::Source(Box::new(SourceDoc {
                title: "".into(),
                source_url: "".into(),
                author: None,
                published: None,
                tags: vec![],
                body_markdown: "".into(),
            })),
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
