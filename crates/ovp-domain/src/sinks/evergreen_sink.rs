use ovp_core::{
    CanonicalKey, CanonicalUpsertOp, ContentHash, Event, EventKind, OpId, Record, RunId, Sink,
    SinkOutput, StepId, VaultCreateOp, WriteOp,
};

use crate::body::DomainBody;
use crate::canonical::CanonicalConcept;
use crate::evergreen::EvergreenConcept;
use crate::evergreen_note::{content_hash, frontmatter, EvergreenNote};
use crate::vault_layout::VaultLayout;

/// Writes a minted `EvergreenConcept` to the vault: a `VaultCreate` for the
/// evergreen page at `10-Knowledge/Evergreen/<slug>.md` AND a `CanonicalUpsert`
/// registering the concept's canonical identity. This is the first real
/// `CanonicalUpsert` write surface in the system.
///
/// The body is chosen by [`render_body`]: a *grounded* note (definition +
/// source-backed claims + source link + related) when the concept carries
/// M12a minting content, else the legacy provenance-free `stub`. Either way the
/// body is a pure function of the `EvergreenConcept` fields, so re-minting the
/// *same* concept is an idempotent `VaultCreate` (the applier skips on matching
/// hash). **Cross-document caveat (M12a):** the grounded body intentionally
/// carries per-document grounding (definition, claims, source link), so two
/// *distinct* articles surfacing the same new slug render *different* bodies —
/// the second `VaultCreate` then hits an existing path with a different hash and
/// is reported `OpResult::Failed` (fail-loud, no overwrite), which halts a
/// `CompositePlanApplier` cycle. Cross-document merge/skip of a shared slug is
/// deferred to M12b; the limitation is pinned by an ovp-stores e2e test.
///
/// The CanonicalUpsert payload carries only canonical identity (slug, title,
/// path, provenance) — the rich grounding lives in the vault note body, not the
/// canonical store, so MOC / knowledge-index rebuilds are unaffected.
pub struct EvergreenSink {
    step: StepId,
    run_id: RunId,
    layout: VaultLayout,
}

impl EvergreenSink {
    pub fn new(step: impl Into<String>, run_id: RunId) -> Self {
        Self { step: StepId::new(step.into()), run_id, layout: VaultLayout::new() }
    }
}

impl Sink<DomainBody> for EvergreenSink {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn consume(&mut self, record: Record<DomainBody>) -> SinkOutput {
        let concept = match record.body {
            DomainBody::EvergreenConcept(c) => *c,
            other => {
                // Other variants (the article note) are not ours; emit a
                // visibility event, no op. (Mirrors ArticleVaultPlanSink.)
                let extra = Event::new(
                    self.run_id.clone(),
                    ovp_core::EventTs::new(0),
                    EventKind::FilterDropped {
                        record_id: record.id.clone(),
                        step_id: self.step.clone(),
                        reason: ovp_core::DropReason::new(
                            "sink.evergreen.wrong_variant",
                            format!("expected EvergreenConcept, got {}", other.variant_name()),
                        ),
                    },
                );
                return SinkOutput { plan_ops: vec![], extra_events: vec![extra] };
            }
        };

        let body_md = render_body(&concept);
        let path = self.layout.evergreen_note(&concept.slug);
        let create = WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new(format!("op-evergreen-{}", concept.slug)),
            path: path.clone(),
            after_hash: ContentHash::new(content_hash(body_md.as_bytes())),
            body: body_md,
            reason: "mint evergreen concept".into(),
            originating_record: record.id.clone(),
        });

        let payload = CanonicalConcept {
            slug: concept.slug.clone(),
            title: concept.title.clone(),
            evergreen_path: path.as_str().to_string(),
            provenance_source_url: concept.provenance_source_url.clone(),
        }
        .to_payload();
        let upsert = WriteOp::CanonicalUpsert(CanonicalUpsertOp {
            op_id: OpId::new(format!("op-canon-{}", concept.slug)),
            key: CanonicalKey::new(concept.slug.clone()),
            before_hash: None,
            after_hash: ContentHash::new(content_hash(payload.as_bytes())),
            payload,
            reason: "register canonical concept".into(),
            originating_record: record.id.clone(),
        });

        SinkOutput { plan_ops: vec![create, upsert], extra_events: vec![] }
    }
}

/// Choose the body: a grounded note when the concept carries minting content
/// (M12a — definition or source-backed claims), else the legacy provenance-free
/// stub (thin concepts: fixtures, seeding). Both are pure functions of the
/// concept, so re-minting the same concept is an idempotent `VaultCreate`. The
/// grounded body is rendered via [`EvergreenNote`], the single home of the
/// note format (so the same-slug reconcile in `ovp-run` can parse + merge it).
fn render_body(c: &EvergreenConcept) -> String {
    if c.definition.trim().is_empty() && c.source_claims.is_empty() {
        render_stub(c)
    } else {
        EvergreenNote::from_concept(c).render()
    }
}

fn render_stub(c: &EvergreenConcept) -> String {
    // Deterministic from slug/title only (no provenance) → idempotent.
    let mut s = frontmatter(&c.title, &c.slug, "stub");
    s.push_str(&format!("# {}\n\n", c.title));
    s.push_str("> Stub evergreen. Expand with an atomic definition and links.\n");
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_core::{RecordId, RecordMeta};

    fn record(slug: &str) -> Record<DomainBody> {
        Record::new(
            RecordId::new(format!("evg-{slug}")),
            DomainBody::EvergreenConcept(Box::new(EvergreenConcept::from_candidate(
                slug,
                "https://example.com/src",
            ))),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn emits_vault_create_and_canonical_upsert() {
        let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("run"));
        let out = sink.consume(record("agent-native-pm"));
        assert_eq!(out.plan_ops.len(), 2);

        let create = match &out.plan_ops[0] {
            WriteOp::VaultCreate(o) => o,
            other => panic!("expected VaultCreate, got {other:?}"),
        };
        assert_eq!(create.path.as_str(), "10-Knowledge/Evergreen/agent-native-pm.md");
        assert!(create.body.contains("type: evergreen"));
        assert!(create.body.contains("slug: agent-native-pm"));
        assert!(create.body.contains("# Agent Native Pm"));

        let upsert = match &out.plan_ops[1] {
            WriteOp::CanonicalUpsert(o) => o,
            other => panic!("expected CanonicalUpsert, got {other:?}"),
        };
        assert_eq!(upsert.key.as_str(), "agent-native-pm");
        assert!(upsert.before_hash.is_none());
        assert!(upsert.payload.contains("\"slug\":\"agent-native-pm\""));
        assert!(upsert.payload.contains("\"evergreen_path\":\"10-Knowledge/Evergreen/agent-native-pm.md\""));
        assert!(upsert.payload.contains("\"provenance_source_url\":\"https://example.com/src\""));
    }

    #[test]
    fn stub_body_is_provenance_free_for_idempotence() {
        // Two concepts, same slug, different provenance → identical stub
        // body (provenance only in the CanonicalUpsert payload), so the
        // VaultCreate is idempotent across documents.
        let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("run"));
        let a = sink.consume(record("rag"));
        let mut other = EvergreenConcept::from_candidate("rag", "https://OTHER/doc");
        other.title = "Rag".into();
        let rec = Record::new(
            RecordId::new("evg-rag"),
            DomainBody::EvergreenConcept(Box::new(other)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        let b = sink.consume(rec);
        let body_a = match &a.plan_ops[0] {
            WriteOp::VaultCreate(o) => &o.body,
            _ => unreachable!(),
        };
        let body_b = match &b.plan_ops[0] {
            WriteOp::VaultCreate(o) => &o.body,
            _ => unreachable!(),
        };
        assert_eq!(body_a, body_b, "stub body must not vary by provenance");
    }

    fn rich_record(slug: &str) -> Record<DomainBody> {
        let mut c = EvergreenConcept::from_candidate(slug, "https://example.com/src");
        c.definition = format!("{} is a worked example concept.", c.title);
        c.source_claims = vec![
            "First grounded claim about the concept.".into(),
            "Second grounded claim.".into(),
        ];
        c.source_title = "Origin Article".into();
        c.related = vec!["ai-agent".into(), "vector-db".into()];
        Record::new(
            RecordId::new(format!("evg-{slug}")),
            DomainBody::EvergreenConcept(Box::new(c)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn rich_concept_renders_grounded_not_stub_body() {
        let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("run"));
        let out = sink.consume(rich_record("rag"));
        // Still emits both ops (VaultCreate + CanonicalUpsert).
        assert_eq!(out.plan_ops.len(), 2);
        assert!(matches!(out.plan_ops[1], WriteOp::CanonicalUpsert(_)));

        let body = match &out.plan_ops[0] {
            WriteOp::VaultCreate(o) => &o.body,
            other => panic!("expected VaultCreate, got {other:?}"),
        };
        assert!(!body.contains("Stub evergreen"), "a grounded note must not be a stub");
        assert!(body.contains("status: minted"));
        assert!(body.contains("> Rag is a worked example concept."), "definition rendered");
        assert!(body.contains("## Source-backed claims"));
        assert!(body.contains("- First grounded claim about the concept."));
        assert!(body.contains("## Source"));
        assert!(body.contains("[Origin Article](https://example.com/src)"));
        assert!(body.contains("## Related"));
        assert!(body.contains("[[ai-agent]]"));
    }

    #[test]
    fn rich_body_is_deterministic_for_idempotence() {
        // Same minted content → identical body + hash, so a second apply skips.
        let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("run"));
        let a = sink.consume(rich_record("rag"));
        let b = sink.consume(rich_record("rag"));
        let (ba, ha) = match &a.plan_ops[0] {
            WriteOp::VaultCreate(o) => (&o.body, &o.after_hash),
            _ => unreachable!(),
        };
        let (bb, hb) = match &b.plan_ops[0] {
            WriteOp::VaultCreate(o) => (&o.body, &o.after_hash),
            _ => unreachable!(),
        };
        assert_eq!(ba, bb, "rich body deterministic from concept content");
        assert_eq!(ha, hb, "deterministic body → stable content hash");
    }

    #[test]
    fn rich_minting_leaves_canonical_payload_minimal() {
        // M12a keeps grounding in the vault note body only; the canonical
        // payload (read by MOC + knowledge index) is unchanged.
        let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("run"));
        let out = sink.consume(rich_record("rag"));
        let upsert = match &out.plan_ops[1] {
            WriteOp::CanonicalUpsert(o) => o,
            other => panic!("expected CanonicalUpsert, got {other:?}"),
        };
        assert!(upsert.payload.contains("\"slug\":\"rag\""));
        assert!(upsert.payload.contains("\"provenance_source_url\":\"https://example.com/src\""));
        assert!(!upsert.payload.contains("source_claims"), "claims stay out of canonical store");
        assert!(!upsert.payload.contains("definition"), "definition stays out of canonical store");
    }

    #[test]
    fn wrong_variant_event_no_op() {
        use crate::source_doc::SourceDoc;
        let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("run"));
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(SourceDoc::article("t", "u", None, None, vec![], ""))),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        let out = sink.consume(rec);
        assert!(out.plan_ops.is_empty());
        assert_eq!(out.extra_events.len(), 1);
    }
}
