use ovp_core::{
    DropReason, EventKind, FilterDecision, FilterError, Record, RecordId, RecordMeta, StepId,
    Transform,
};

use crate::body::DomainBody;
use crate::evergreen::EvergreenConcept;
use crate::interpreted::InterpretationSchema;

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
        // Branch on the EXPLICIT schema marker (M13.3), never on
        // `concepts.is_empty()`. That distinction is what lets a v2 doc whose
        // map gated to empty fail loud here instead of silently minting the v1
        // candidate path.
        match interp.schema {
            InterpretationSchema::ConceptMapV2 => {
                // v2 (M13.2): mint each note from its OWN gated concept — the
                // concept's definition + owned claims + related, NOT the article
                // one_liner or token-matched article claims. The ConceptResolver
                // already validated/filtered these concepts.
                if interp.concepts.is_empty() {
                    // The model produced a concept map but the gate dropped /
                    // merged every concept. This is a FAILED extraction, not a
                    // v1 doc — error LOUD (records_errored++ → run-cycle /
                    // review-run report the run as not clean) rather than fall
                    // back to v1 candidate minting.
                    return FilterDecision::Error(FilterError::new(
                        "transform.evergreen.empty_concept_map",
                        format!(
                            "v2 concept-map doc `{}` produced zero mintable concepts after \
                             gating; refusing to fall back to the v1 candidate path",
                            interp.title
                        ),
                    ));
                }
                for c in &interp.concepts {
                    concepts.push(EvergreenConcept::from_extracted(c, &interp.title, &interp.source_url));
                }
            }
            InterpretationSchema::ArticleV1 => {
                // v1 legacy: mint from candidates (shared one_liner definition +
                // token-matched article claims). Invalid slugs drop observably.
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
    use crate::interpreted::{ConceptKind, Dimensions, ExtractedConcept, Explanation, InterpretedDoc};
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
            schema: InterpretationSchema::ArticleV1,
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

    // ---- M13.2 v2 concept-map path ----

    fn ec(slug: &str, definition: &str, claims: &[&str], related: &[&str]) -> ExtractedConcept {
        ExtractedConcept {
            slug: slug.into(),
            title: String::new(),
            aliases: vec![],
            kind: ConceptKind::Concept,
            definition: definition.into(),
            evidence: vec!["e".into()],
            claims: claims.iter().map(|s| s.to_string()).collect(),
            related: related.iter().map(|s| s.to_string()).collect(),
            merge_with: vec![],
            reject_reason: None,
            promote: true,
        }
    }

    #[test]
    fn v2_concepts_carry_their_own_definition_and_claims() {
        // Two concepts from the SAME article must produce two DIFFERENT
        // definitions — proving the writer no longer synthesizes from the
        // article one_liner, and does not recycle one concept's claims onto
        // another.
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let mut d = interp(vec![]);
        d.schema = InterpretationSchema::ConceptMapV2;
        // A shared article thesis the writer must NOT fall back to.
        d.dimensions.one_liner = "An article-level synthesis line.".into();
        d.concepts = vec![
            ec(
                "idea-block",
                "A question-answer packet that replaces a prose chunk as the unit.",
                &["IdeaBlocks gave 2.29x better retrieval."],
                &["chunking-problem"],
            ),
            ec(
                "chunking-problem",
                "The chunk is a structurally neutral container with no idea boundary.",
                &["Half a table loses its meaning when split."],
                &[],
            ),
        ];
        let out = match w.process(record(d)) {
            FilterDecision::FanOut(rs) => rs,
            other => panic!("expected FanOut, got {other:?}"),
        };
        assert_eq!(out.len(), 3, "article + 2 evergreens");
        let a = match &out[1].body {
            DomainBody::EvergreenConcept(c) => c,
            other => panic!("expected EvergreenConcept, got {other:?}"),
        };
        let b = match &out[2].body {
            DomainBody::EvergreenConcept(c) => c,
            other => panic!("expected EvergreenConcept, got {other:?}"),
        };
        // Per-concept definitions, distinct, and NOT the article one_liner.
        assert_eq!(a.slug, "idea-block");
        assert_eq!(
            a.definition,
            "A question-answer packet that replaces a prose chunk as the unit."
        );
        assert_ne!(a.definition, b.definition);
        assert_ne!(a.definition, "An article-level synthesis line.");
        // Claims are owned, not recycled across concepts.
        assert!(a.source_claims.iter().any(|s| s.contains("2.29x")));
        assert!(!b.source_claims.iter().any(|s| s.contains("2.29x")));
        assert!(b.source_claims.iter().any(|s| s.contains("Half a table")));
        // Related is concept-owned and carried through.
        assert_eq!(a.related, vec!["chunking-problem"]);
        assert!(b.related.is_empty());
        // Provenance still threads the article source.
        assert_eq!(a.provenance_source_url, "https://example.com/post");
        assert_eq!(a.source_title, "T");
    }

    #[test]
    fn v2_path_ignores_v1_candidates() {
        // When concepts[] is present, the legacy concept_candidates list is
        // not used — the v2 map is authoritative.
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let mut d = interp(vec!["legacy-candidate"]);
        d.schema = InterpretationSchema::ConceptMapV2;
        d.concepts = vec![ec("real-concept", "A real definition.", &[], &[])];
        let out = match w.process(record(d)) {
            FilterDecision::FanOut(rs) => rs,
            other => panic!("expected FanOut, got {other:?}"),
        };
        let slugs: Vec<&str> = out[1..]
            .iter()
            .map(|r| match &r.body {
                DomainBody::EvergreenConcept(c) => c.slug.as_str(),
                _ => panic!("expected EvergreenConcept"),
            })
            .collect();
        assert_eq!(slugs, vec!["real-concept"], "v1 candidate ignored under v2");
    }

    #[test]
    fn v2_concept_with_blank_title_derives_from_slug() {
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let mut d = interp(vec![]);
        d.schema = InterpretationSchema::ConceptMapV2;
        d.concepts = vec![ec("vector-database", "A store for embeddings.", &[], &[])];
        let out = match w.process(record(d)) {
            FilterDecision::FanOut(rs) => rs,
            other => panic!("expected FanOut, got {other:?}"),
        };
        match &out[1].body {
            DomainBody::EvergreenConcept(c) => assert_eq!(c.title, "Vector Database"),
            other => panic!("expected EvergreenConcept, got {other:?}"),
        }
    }

    // ---- M13.3 empty-map fallback guard (branch on the schema marker) ----

    #[test]
    fn v2_empty_after_gate_errors_does_not_mint_v1_candidates() {
        // A v2 doc whose concept map gated to empty must FAIL LOUD
        // (FilterDecision::Error → records_errored++ → run-cycle/review-run
        // report not-clean), NEVER silently fall back to minting the leftover
        // v1 `concept_candidates`.
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let mut d = interp(vec!["should-not-mint"]); // a stray v1 candidate present
        d.schema = InterpretationSchema::ConceptMapV2;
        d.concepts = vec![]; // gated to empty upstream
        match w.process(record(d)) {
            FilterDecision::Error(e) => {
                assert_eq!(e.code.as_str(), "transform.evergreen.empty_concept_map");
            }
            other => panic!("expected Error (loud), got {other:?}"),
        }
    }

    #[test]
    fn v1_empty_concepts_still_uses_legacy_candidate_path() {
        // The default (v1) schema must keep minting from candidates exactly as
        // before — the marker, not concepts.is_empty(), is what gates v2.
        let mut w = EvergreenConceptWriter::new("evg_writer");
        let d = interp(vec!["legacy-a"]); // schema defaults to ArticleV1; concepts empty
        match w.process(record(d)) {
            FilterDecision::FanOut(rs) => {
                assert_eq!(rs.len(), 2, "article + 1 legacy-minted evergreen");
                match &rs[1].body {
                    DomainBody::EvergreenConcept(c) => assert_eq!(c.slug, "legacy-a"),
                    other => panic!("expected EvergreenConcept, got {other:?}"),
                }
            }
            other => panic!("expected FanOut, got {other:?}"),
        }
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
