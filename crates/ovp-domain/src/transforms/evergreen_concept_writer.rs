use ovp_core::{FilterDecision, Record, RecordId, RecordMeta, StepId, Transform};

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
/// AUTO-all (mint every surviving candidate); the legacy AUTO/ESCALATE/
/// REJECT lanes are a later refinement. Cross-document dedup of the same
/// slug is the canonical store's job, not this transform's.
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
        let source_url = interp.source_url.clone();
        let meta = record.meta.clone();
        let provenance = record.provenance.clone();
        let concepts: Vec<EvergreenConcept> = interp
            .concept_candidates
            .iter()
            .map(|slug| EvergreenConcept::from_candidate(slug.clone(), source_url.clone()))
            .collect();

        if concepts.is_empty() {
            // Nothing new to mint; just forward the article record.
            let next = record.with_step(self.step.clone(), "no new evergreen concepts");
            return FilterDecision::Forward(vec![next]);
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
        FilterDecision::FanOut(out)
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
