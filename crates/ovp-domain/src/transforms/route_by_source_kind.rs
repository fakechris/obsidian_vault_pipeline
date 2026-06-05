use ovp_core::{DropReason, EventKind, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;

/// The observable routing decision point. Classifies a `Source` record by
/// its `SourceKind`, emits a `source_routed` event recording the chosen
/// route, and forwards the record unchanged.
///
/// The runner broadcasts a node's output to all downstream edges, so the
/// actual kind-filtering happens at the kind-specific prompt builders
/// (each drops records whose kind it doesn't handle). `RouteBySourceKind`
/// exists to make the routing decision auditable in the event log, and to
/// be the single explicit place a new source kind gets acknowledged.
///
/// Pure: no I/O. Wrong-variant (non-Source) records drop.
pub struct RouteBySourceKind {
    step: StepId,
}

impl RouteBySourceKind {
    pub fn new(step: impl Into<String>) -> Self {
        Self { step: StepId::new(step.into()) }
    }
}

impl Transform<DomainBody> for RouteBySourceKind {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        // `name()` returns &'static str, so this borrow of `record.body`
        // ends before we move `record` below.
        let kind: &'static str = match &record.body {
            DomainBody::Source(s) => s.source_kind.name(),
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.route_by_source_kind.wrong_variant",
                    format!("expected Source, got {}", other.variant_name()),
                ));
            }
        };

        let event = EventKind::SourceRouted {
            record_id: record.id.clone(),
            step_id: self.step.clone(),
            source_kind: kind.to_string(),
        };
        let next = record.with_step(self.step.clone(), format!("routed: {kind}"));
        FilterDecision::ForwardWithEvents { records: vec![next], events: vec![event] }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::{PaperMeta, SourceDoc, SourceKind};
    use ovp_core::{RecordId, RecordMeta, RunId};

    fn source_record(kind: SourceKind) -> Record<DomainBody> {
        let mut doc = SourceDoc::article("T", "https://example.com/x", None, None, vec![], "body");
        doc.source_kind = kind;
        Record::new(
            RecordId::new("r-1"),
            DomainBody::Source(Box::new(doc)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    fn paper_kind() -> SourceKind {
        SourceKind::Paper(PaperMeta {
            arxiv_id: "2601.11144".into(),
            authors: vec!["A".into()],
            categories: vec!["cs.IR".into()],
            published: None,
        })
    }

    #[test]
    fn routes_article_with_event() {
        let mut r = RouteBySourceKind::new("route");
        match r.process(source_record(SourceKind::Article)) {
            FilterDecision::ForwardWithEvents { records, events } => {
                assert_eq!(records.len(), 1);
                assert_eq!(events.len(), 1);
                match &events[0] {
                    EventKind::SourceRouted { source_kind, .. } => assert_eq!(source_kind, "article"),
                    other => panic!("expected SourceRouted, got {other:?}"),
                }
                // Record forwarded unchanged (still a Source).
                assert!(matches!(records[0].body, DomainBody::Source(_)));
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn routes_paper_with_event() {
        let mut r = RouteBySourceKind::new("route");
        match r.process(source_record(paper_kind())) {
            FilterDecision::ForwardWithEvents { events, .. } => match &events[0] {
                EventKind::SourceRouted { source_kind, .. } => assert_eq!(source_kind, "paper"),
                other => panic!("expected SourceRouted, got {other:?}"),
            },
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn wrong_variant_drops() {
        use crate::prompt::{PromptId, PromptRequest};
        let mut r = RouteBySourceKind::new("route");
        let prompt = PromptRequest {
            prompt_id: PromptId::new("x/v1"),
            schema_version: 1,
            model: "m".into(),
            system: "s".into(),
            user: "u".into(),
            max_tokens: 10,
            origin: Box::new(SourceDoc::article("t", "u", None, None, vec![], "")),
        };
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Prompt(Box::new(prompt)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match r.process(rec) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.route_by_source_kind.wrong_variant");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }
}
