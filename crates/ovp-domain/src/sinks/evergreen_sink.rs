use ovp_core::{
    CanonicalKey, CanonicalUpsertOp, ContentHash, Event, EventKind, OpId, Record, RunId, Sink,
    SinkOutput, StepId, VaultCreateOp, WriteOp,
};
use sha2::{Digest, Sha256};

use crate::body::DomainBody;
use crate::evergreen::EvergreenConcept;
use crate::vault_layout::VaultLayout;

/// Writes a minted `EvergreenConcept` to the vault: a `VaultCreate` for
/// the stub evergreen page at `10-Knowledge/Evergreen/<slug>.md` AND a
/// `CanonicalUpsert` registering the concept's canonical identity. This
/// is the first real `CanonicalUpsert` write surface in the system.
///
/// The stub page body is derived purely from slug/title, so re-minting
/// the same concept is an idempotent VaultCreate (the applier skips on
/// matching hash). Document-specific provenance rides in the
/// CanonicalUpsert payload, where the canonical store merges it later.
///
/// The CanonicalUpsert payload is a JSON `String` for now — the typed
/// payload lands with the canonical store stage (which has the concrete
/// reader to validate it against). See invariants.md "Known stubs".
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

        let body_md = render_stub(&concept);
        let path = self.layout.evergreen_note(&concept.slug);
        let create = WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new(format!("op-evergreen-{}", concept.slug)),
            path: path.clone(),
            after_hash: ContentHash::new(hex_sha256(body_md.as_bytes())),
            body: body_md,
            reason: "mint evergreen concept".into(),
            originating_record: record.id.clone(),
        });

        let payload = canonical_payload_json(&concept, path.as_str());
        let upsert = WriteOp::CanonicalUpsert(CanonicalUpsertOp {
            op_id: OpId::new(format!("op-canon-{}", concept.slug)),
            key: CanonicalKey::new(concept.slug.clone()),
            before_hash: None,
            after_hash: ContentHash::new(hex_sha256(payload.as_bytes())),
            payload,
            reason: "register canonical concept".into(),
            originating_record: record.id.clone(),
        });

        SinkOutput { plan_ops: vec![create, upsert], extra_events: vec![] }
    }
}

fn render_stub(c: &EvergreenConcept) -> String {
    // Deterministic from slug/title only (no provenance) → idempotent.
    let mut s = String::new();
    s.push_str("---\n");
    s.push_str(&format!("title: {}\n", yaml_quote(&c.title)));
    s.push_str("type: evergreen\n");
    s.push_str(&format!("slug: {}\n", c.slug));
    s.push_str("status: stub\n");
    s.push_str("---\n\n");
    s.push_str(&format!("# {}\n\n", c.title));
    s.push_str("> Stub evergreen. Expand with an atomic definition and links.\n");
    s
}

/// Minimal JSON payload for the CanonicalUpsert. Hand-built (not serde)
/// to keep the field order stable for hashing. Typed payload comes with
/// the canonical store stage.
fn canonical_payload_json(c: &EvergreenConcept, evergreen_path: &str) -> String {
    format!(
        "{{\"slug\":{slug},\"title\":{title},\"evergreen_path\":{path},\"provenance_source_url\":{prov}}}",
        slug = json_str(&c.slug),
        title = json_str(&c.title),
        path = json_str(evergreen_path),
        prov = json_str(&c.provenance_source_url),
    )
}

fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn yaml_quote(s: &str) -> String {
    let needs = s.is_empty()
        || s.starts_with([
            '-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%',
            '@', '`',
        ])
        || s.contains(": ")
        || s.contains(" #")
        || s.contains('\n');
    if needs {
        format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        s.to_string()
    }
}

fn hex_sha256(bytes: &[u8]) -> String {
    let hash = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    use std::fmt::Write;
    for b in hash.iter() {
        write!(s, "{:02x}", b).expect("infallible");
    }
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
