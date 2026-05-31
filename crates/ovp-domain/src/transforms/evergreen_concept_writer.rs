use ovp_core::{
    DropReason, EventKind, FilterDecision, Record, RecordId, RecordMeta, StepId, Transform,
};

use crate::body::DomainBody;
use crate::evergreen::EvergreenConcept;

/// Mints new evergreen concepts from an article's surviving
/// `concept_candidates` — the ones `ConceptResolver` did NOT promote, so
/// they have no canonical page yet. Fans out: forwards the original
/// `Interpreted` record (so the article note is still written) plus one
/// `EvergreenConcept` record per new candidate (which `EvergreenSink`
/// turns into the evergreen `VaultCreate` + `CanonicalUpsert`).
///
/// Pure: same `InterpretedDoc` → same fan-out. This is the legacy
/// "absorb" equivalent for the mint-new-evergreen half. v1 policy is
/// AUTO-all (mint every surviving candidate); mint/enrich/escalate/reject
/// policy lanes are a later refinement. When the same slug recurs across
/// documents, `RunCycle`'s reconcile (`ovp_domain::reconcile_evergreen_write`,
/// a pure merge) enriches the existing note before apply; the canonical store
/// keeps the first writer's provenance and does NOT merge. Semantic dedup of
/// near-duplicate claims is still future.
pub struct EvergreenConceptWriter {
    step: StepId,
}

impl EvergreenConceptWriter {
    pub fn new(step: impl Into<String>) -> Self {
        Self { step: StepId::new(step.into()) }
    }
}

impl Transform<DomainBody> for EvergreenConceptWriter {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let interp = match &record.body {
            DomainBody::Interpreted(d) => d.as_ref(),
            other => {
                // Pass non-article variants through untouched (e.g. paper
                // interpretations route past this node). Wrong-but-harmless
                // variants other than Interpreted are simply forwarded.
                let name = other.variant_name();
                let next = record.with_step(self.step.clone(), format!("passthrough ({name})"));
                return FilterDecision::Forward(vec![next]);
            }
        };

        // One EvergreenConcept per surviving candidate, deterministic order.
        // Each is minted *rich* from the interpreted article (definition +
        // source-backed claims + related + provenance) so the evergreen note
        // is a grounded knowledge unit, not a bare stub (M12a). Candidates that
        // aren't a valid canonical slug are DROPPED here (not minted) with an
        // observable `FilterDropped` event — a divergent slug would write the
        // canonical record where rebuilds can't find it (see `CanonicalSlug`).
        let meta = record.meta.clone();
        let provenance = record.provenance.clone();
        let record_id = record.id.clone();
        let mut concepts: Vec<EvergreenConcept> = Vec::new();
        let mut drop_events: Vec<EventKind> = Vec::new();
        for raw in &interp.concept_candidates {
            match EvergreenConcept::try_mint(raw, interp) {
                Ok(c) => concepts.push(c),
                Err(e) => drop_events.push(EventKind::FilterDropped {
                    record_id: record_id.clone(),
                    step_id: self.step.clone(),
                    reason: DropReason::new(
                        "transform.evergreen.invalid_slug",
                        format!("dropped concept candidate `{raw}` ({}): {e}", e.code()),
                    ),
                }),
            }
        }

        if concepts.is_empty() {
            // Nothing new to mint; just forward the article record. If we
            // dropped any candidates, carry the drop events so the skip is
            // observable.
            let next = record.with_step(self.step.clone(), "no new evergreen concepts");
            if drop_events.is_empty() {
                return FilterDecision::Forward(vec![next]);
            }
            return FilterDecision::ForwardWithEvents { records: vec![next], events: drop_events };
        }

        let mut out: Vec<Record<DomainBody>> = Vec::with_capacity(concepts.len() + 1);
        // Forward the original article record first (so downstream
        // ordering is article-note-then-evergreens).
        out.push(
            record.with_step(self.step.clone(), format!("minting {} evergreen(s)", concepts.len())),
        );
        for c in concepts {
            let id = RecordId::new(format!("evg-{}", c.slug));
            let rec = Record::new(
                id,
                DomainBody::EvergreenConcept(Box::new(c)),
                RecordMeta { run_id: meta.run_id.clone(), seq: meta.seq },
            );
            let rec = Record { provenance: provenance.clone(), ..rec }
                .with_step(self.step.clone(), "proposed evergreen");
            out.push(rec);
        }
        // FanOut when every candidate was valid; ForwardWithEvents when we
        // also need to report dropped candidates alongside the fan-out.
        if drop_events.is_empty() {
            FilterDecision::FanOut(out)
        } else {
            FilterDecision::ForwardWithEvents { records: out, events: drop_events }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::interpreted::{Dimensions, Explanation, InterpretedDoc};
    use ovp_core::RunId;

    fn interp(candidates: Vec<&str>) -> InterpretedDoc {
        InterpretedDoc {
            title: "T".into(),
            source_url: "https://example.com/post".into(),
            author: None,
            date: "2026-05-29".into(),
            doc_type: "article".into(),
            area: "ai".into(),
            tags: vec![],
            canonical_concepts: vec!["already-canonical".into()],
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
            RecordId::new("src-1"),
            DomainBody::Interpreted(Box::new(d)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn fans_out_article_plus_one_evergreen_per_candidate() {
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let out = match w.process(record(interp(vec!["new-a", "new-b"]))) {
            FilterDecision::FanOut(rs) => rs,
            other => panic!("expected FanOut, got {other:?}"),
        };
        assert_eq!(out.len(), 3, "article + 2 evergreens");
        assert!(matches!(out[0].body, DomainBody::Interpreted(_)), "article first");
        let slugs: Vec<&str> = out[1..]
            .iter()
            .map(|r| match &r.body {
                DomainBody::EvergreenConcept(c) => c.slug.as_str(),
                _ => panic!("expected EvergreenConcept"),
            })
            .collect();
        assert_eq!(slugs, vec!["new-a", "new-b"]);
        // Provenance source URL is carried.
        if let DomainBody::EvergreenConcept(c) = &out[1].body {
            assert_eq!(c.provenance_source_url, "https://example.com/post");
        }
    }

    #[test]
    fn minted_concepts_carry_definition_and_claims() {
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let mut d = interp(vec!["rag"]);
        d.dimensions.one_liner = "RAG augments generation with retrieval.".into();
        d.dimensions.details = vec![
            "RAG retrieves documents before generation.".into(),
            "It reduces hallucination.".into(),
        ];
        let out = match w.process(record(d)) {
            FilterDecision::FanOut(rs) => rs,
            other => panic!("expected FanOut, got {other:?}"),
        };
        let c = match &out[1].body {
            DomainBody::EvergreenConcept(c) => c,
            other => panic!("expected EvergreenConcept, got {other:?}"),
        };
        // The writer threads the interpreted article's grounding onto the mint.
        assert_eq!(c.definition, "RAG augments generation with retrieval.");
        assert!(!c.source_claims.is_empty(), "source-backed claims attached");
        assert!(c.source_claims.iter().any(|s| s.contains("retrieves documents")));
        assert_eq!(c.source_title, "T");
    }

    #[test]
    fn drops_invalid_slug_candidates_observably() {
        let mut w = EvergreenConceptWriter::new("evg_writer");
        // `bad/slug` (separator) and `  ` (empty) are invalid; `good` mints.
        let out = match w.process(record(interp(vec!["good", "bad/slug", "  "]))) {
            FilterDecision::ForwardWithEvents { records, events } => (records, events),
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        };
        let (records, events) = out;
        // Article + the one valid evergreen only.
        assert_eq!(records.len(), 2, "article + 1 valid evergreen");
        assert!(matches!(records[0].body, DomainBody::Interpreted(_)));
        match &records[1].body {
            DomainBody::EvergreenConcept(c) => assert_eq!(c.slug, "good"),
            other => panic!("expected EvergreenConcept, got {other:?}"),
        }
        // Two dropped candidates → two observable FilterDropped events.
        assert_eq!(events.len(), 2);
        for ev in &events {
            match ev {
                EventKind::FilterDropped { reason, .. } => {
                    assert_eq!(reason.code.as_str(), "transform.evergreen.invalid_slug");
                }
                other => panic!("expected FilterDropped, got {other:?}"),
            }
        }
    }

    #[test]
    fn all_invalid_candidates_forwards_article_with_events() {
        let mut w = EvergreenConceptWriter::new("evg_writer");
        match w.process(record(interp(vec!["a/b"]))) {
            FilterDecision::ForwardWithEvents { records, events } => {
                assert_eq!(records.len(), 1);
                assert!(matches!(records[0].body, DomainBody::Interpreted(_)));
                assert_eq!(events.len(), 1);
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn no_candidates_forwards_article_only() {
        let mut w = EvergreenConceptWriter::new("evg_writer");
        match w.process(record(interp(vec![]))) {
            FilterDecision::Forward(rs) => {
                assert_eq!(rs.len(), 1);
                assert!(matches!(rs[0].body, DomainBody::Interpreted(_)));
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn non_interpreted_passes_through() {
        use crate::source_doc::SourceDoc;
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(SourceDoc::article("t", "u", None, None, vec![], ""))),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match w.process(rec) {
            FilterDecision::Forward(rs) => assert!(matches!(rs[0].body, DomainBody::Source(_))),
            other => panic!("expected Forward, got {other:?}"),
        }
    }
}
