//! M14a end-to-end offline test: drive the hand-harness with an inline fake
//! model over a realistic multi-unit reply, then write + inspect the review
//! pack. Proves the public API (run_unit_extraction → write_unit_review_pack)
//! classifies grounded vs ungrounded units and is deterministic — without any
//! network or cassette.

use ovp_domain::source_doc::SourceDoc;
use ovp_domain::units::{run_unit_extraction, write_unit_review_pack, UnitStatus};
use ovp_llm::{CallError, ModelClient, ModelReply, ModelRequest, StopReason, Usage};

struct FakeModel {
    reply: String,
}
impl ModelClient for FakeModel {
    fn call(&mut self, _req: &ModelRequest) -> Result<ModelReply, CallError> {
        Ok(ModelReply {
            model: "fake".into(),
            text: self.reply.clone(),
            stop_reason: StopReason::EndTurn,
            usage: Usage { input_tokens: 1, output_tokens: 1 },
            blocks: None,
            raw_stop_reason: None,
        })
    }
}

const BODY: &str = "\
# Why the chunk is a bad unit

A chunk of text is a structurally neutral container. It knows nothing about
where its ideas begin or end.

Blockify, a preprocessing layer from Iternal Technologies, converts documents
into IdeaBlocks.
";

/// A reply mixing: one clean accepted unit, one whose quote is absent (reject),
/// one with an argument that drifts (needs-review), and one malformed (reject).
const REPLY: &str = r#"{
  "units": [
    {
      "kind": "assertion", "subtype": "observation",
      "text": "A chunk is a structurally neutral container.",
      "evidence_ref": "p002",
      "evidence_quote": "A chunk of text is a structurally neutral container.",
      "attribution": "author", "modality": "asserted",
      "arguments": [{"surface": "chunk", "role": "subject"}]
    },
    {
      "kind": "assertion",
      "text": "Vectors should be stored in Pinecone.",
      "evidence_ref": "p002",
      "evidence_quote": "You should always use Pinecone for vector storage.",
      "attribution": "author", "modality": "asserted",
      "arguments": []
    },
    {
      "kind": "relation",
      "text": "Blockify converts documents into IdeaBlocks.",
      "evidence_ref": "p003",
      "evidence_quote": "Blockify, a preprocessing layer from Iternal Technologies, converts documents\ninto IdeaBlocks.",
      "attribution": "author", "modality": "asserted",
      "arguments": [{"surface": "Azure AI Search", "role": "instrument"}]
    },
    {
      "kind": "assertion",
      "text": "missing required modality",
      "evidence_ref": "p002",
      "evidence_quote": "A chunk of text is a structurally neutral container.",
      "attribution": "author"
    }
  ]
}"#;

fn source() -> SourceDoc {
    SourceDoc::article("Why the chunk is a bad unit", "https://e/rag", None, None, vec![], BODY)
}

#[test]
fn end_to_end_classifies_and_writes_pack() {
    let mut model = FakeModel { reply: REPLY.into() };
    let ex = run_unit_extraction(&source(), &mut model).expect("client ok").extraction;

    // 4 emitted. M14a.2: argument drift is ADVISORY (does not gate), so the
    // grounded unit with a drifting arg is ACCEPTED, not needs-review.
    // → 2 accepted (both grounded), 0 needs-review, 2 rejected (quote-not-found
    //   + malformed).
    assert_eq!(ex.report.total, 4);
    assert_eq!(ex.report.accepted, 2, "both grounded units (arg drift is advisory)");
    assert_eq!(ex.report.needs_review, 0);
    assert_eq!(ex.report.rejected, 2, "absent-quote + malformed");
    assert_eq!(ex.report.accepted_without_quote, 0, "hard invariant");
    assert!(ex.report.parse_error.is_none());

    // The arg-drift unit is accepted but carries an advisory.
    assert_eq!(ex.report.argument_drift_advisory, 1);
    let drifted = ex.accepted().find(|u| u.text.starts_with("Blockify")).unwrap();
    assert!(drifted.issues.iter().any(|i| i.code == "unit.argument_drift_advisory"));

    // Write the pack and confirm REVIEW.md surfaces the grounding.
    let dir = tempfile::tempdir().unwrap();
    write_unit_review_pack(dir.path(), BODY, &ex, Some(REPLY)).unwrap();
    let review = std::fs::read_to_string(dir.path().join("REVIEW.md")).unwrap();
    assert!(review.contains("Accepted units (2)"));
    assert!(review.contains("structurally neutral container"));
    assert!(review.contains("invariant holds"));
}

#[test]
fn deterministic_across_runs() {
    let mut m1 = FakeModel { reply: REPLY.into() };
    let mut m2 = FakeModel { reply: REPLY.into() };
    let a = run_unit_extraction(&source(), &mut m1).unwrap();
    let b = run_unit_extraction(&source(), &mut m2).unwrap();
    assert_eq!(a, b, "extraction + raw reply both deterministic");
}

#[test]
fn unit_status_enum_is_reachable() {
    // Smoke: the public status enum is usable by consumers.
    assert_ne!(UnitStatus::Accepted, UnitStatus::Rejected);
}
