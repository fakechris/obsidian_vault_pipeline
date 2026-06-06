//! The M14a hand-harness: drive a `ModelClient` end-to-end for one source and
//! produce a [`SourceExtraction`]. No GraphRunner, no `DomainBody`, no manifest —
//! just `source → prompt → client → parse → validate`. The same function backs
//! both the offline test (with an inline fake client) and the live CLI shim
//! (with the real cached/anthropic client).

use std::path::Path;

use ovp_llm::{CallError, ModelClient};

use crate::model_reply::{json_repair_request, parse_reply_value, RepairNote};
use crate::source_doc::SourceDoc;

use super::critic::{apply_repairs, run_unit_critique, CriticReply, RepairLog};
use super::parser::{parse_envelope, units_from_value};
use super::prompt::unit_model_request;
use super::validator::{extraction_parse_failed, validate};
use super::SourceExtraction;

/// Read a markdown clipping from disk into a [`SourceDoc`], reusing the exact
/// inbox parser the v1/v2 paths use. Public entry for the M14a CLI shim (which
/// lives in another crate and so cannot reach the crate-internal reader).
pub fn read_source_from_path(path: &Path) -> Result<SourceDoc, String> {
    crate::sources::markdown_inbox::read_source_doc(path)
        .map_err(|e| format!("{}: {}", e.code.as_str(), e.detail))
}

/// Pure half: turn a model reply's text + the source into a validated
/// extraction. A parse failure is recorded in `report.parse_error` (so the
/// review pack still exists) rather than thrown away.
pub fn extract_units(reply_text: &str, source: &SourceDoc) -> SourceExtraction {
    match parse_envelope(reply_text) {
        Ok(values) => validate(&values, source),
        Err(e) => extraction_parse_failed(source, e.detail),
    }
}

/// Resilient base extraction (M19). Parse the reply with parser-local recovery;
/// on an unrecoverable JSON defect, attempt ONE bounded model JSON-repair call.
/// Returns the raw unit values (for the critic merge), the validated extraction,
/// and an optional repair note. The salvaged reply ALWAYS re-runs through the
/// SAME `validate`, so repair can never bypass grounding. A repair `CallError`
/// (replay cache-miss / transport) is swallowed → fail loud with the original
/// defect (never silent-accept).
fn resilient_unit_extract(
    reply_text: &str,
    source: &SourceDoc,
    client: &mut dyn ModelClient,
) -> (Vec<serde_json::Value>, SourceExtraction, Option<RepairNote>) {
    match parse_reply_value(reply_text) {
        Ok((value, note)) => match units_from_value(&value) {
            Ok(values) => {
                let ext = validate(&values, source);
                (values, ext, note.map(|_| RepairNote::parser_local("units")))
            }
            // Envelope parsed but `units` is missing/not-an-array — a content
            // issue, not a JSON-syntax one. Don't repair; surface it.
            Err(e) => (Vec::new(), extraction_parse_failed(source, e.detail), None),
        },
        Err(defect) => {
            let repaired = client
                .call(&json_repair_request(reply_text))
                .ok()
                .and_then(|r| parse_reply_value(&r.text).ok())
                .and_then(|(v, _)| units_from_value(&v).ok());
            match repaired {
                Some(values) => {
                    let ext = validate(&values, source);
                    (values, ext, Some(RepairNote::model_repair("units", &defect)))
                }
                None => (
                    Vec::new(),
                    extraction_parse_failed(source, format!("units: {defect}")),
                    None,
                ),
            }
        }
    }
}

/// One end-to-end run: the validated extraction PLUS the raw model reply text.
/// The raw reply is first-class output — without it, a parse error / malformed
/// unit / validator drop can't be diagnosed as model-side vs parser-side.
#[derive(Debug, Clone, PartialEq)]
pub struct UnitExtractionRun {
    pub extraction: SourceExtraction,
    pub raw_reply: String,
}

/// Full half: call the client (replay cassette or live), then extract. Returns
/// `Err(CallError)` ONLY for client/transport failures (the operator's network,
/// a cache miss) — a reply that parses badly still yields an `Ok` run carrying
/// the parse error, because that is a reviewable model-quality outcome, not an
/// I/O failure. The raw reply is returned so the caller can persist it.
pub fn run_unit_extraction(
    source: &SourceDoc,
    client: &mut dyn ModelClient,
) -> Result<UnitExtractionRun, CallError> {
    let request = unit_model_request(source);
    let reply = client.call(&request)?;
    let extraction = extract_units(&reply.text, source);
    Ok(UnitExtractionRun { extraction, raw_reply: reply.text })
}

/// One end-to-end critic-repaired run (M14a.8). The `base` is the frozen v5
/// extraction (its raw reply parsed + validated, unchanged); `extraction` is the
/// merged set after bounded TRIM/ADD repairs, re-validated by the SAME validator.
/// All four raw texts are first-class output for the inspectable pack.
#[derive(Debug, Clone, PartialEq)]
pub struct RepairedRun {
    /// The frozen-v5 extraction (no repairs) — the conservative-floor baseline.
    pub base: SourceExtraction,
    /// The repaired extraction (base ∪ repairs, re-validated). Equals `base` when
    /// the critic found nothing.
    pub extraction: SourceExtraction,
    pub repair_log: RepairLog,
    pub critic: CriticReply,
    pub base_reply: String,
    pub critic_reply: String,
    /// JSON salvage notes (M19): empty when the base reply parsed cleanly, one
    /// entry when parser-local recovery or a bounded model repair was applied.
    pub json_repair: Vec<RepairNote>,
}

/// Critic-assisted bounded repair: run the FROZEN v5 extractor on `base_client`
/// (a replay client over the committed v5 cassette → deterministic baseline),
/// run the independent critic on `critic_client` (live/record under
/// `unit_critic/v1`), apply bounded TRIM/ADD repairs, and re-validate the merged
/// raw set ONCE. Grounding/accept rules are the validator's — unchanged.
///
/// `Err(CallError)` only for a client/transport failure on either call. A base
/// reply that parses badly still yields an `Ok` run whose `base`/`extraction`
/// carry the parse error (a reviewable outcome, not an I/O failure).
pub fn run_unit_extraction_repaired(
    source: &SourceDoc,
    base_client: &mut dyn ModelClient,
    critic_client: &mut dyn ModelClient,
) -> Result<RepairedRun, CallError> {
    // 1. Base extraction with M19 resilient parse (parser-local recovery + one
    //    bounded model repair). Keep the RAW values so re-validation of the
    //    no-repair case is byte-identical (the conservative floor).
    let base_request = unit_model_request(source);
    let base_reply = base_client.call(&base_request)?;
    let (base_raw, base, base_repair) =
        resilient_unit_extract(&base_reply.text, source, base_client);
    let json_repair: Vec<RepairNote> = base_repair.into_iter().collect();

    // 2. Independent critic (live/record) over the base accepted units.
    let (critic, critic_reply) = run_unit_critique(source, &base.units, critic_client)?;

    // 3. Bounded repairs → re-validate the merged raw set exactly once.
    let (merged_raw, repair_log) = apply_repairs(&base_raw, &base.units, &critic);
    let extraction = if merged_raw.is_empty() {
        base.clone()
    } else {
        validate(&merged_raw, source)
    };

    Ok(RepairedRun {
        base,
        extraction,
        repair_log,
        critic,
        base_reply: base_reply.text,
        critic_reply,
        json_repair,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_llm::{ModelReply, ModelRequest, StopReason, Usage};

    /// Returns a fixed reply for any request — stands in for the model.
    struct CannedClient {
        text: String,
    }
    impl ModelClient for CannedClient {
        fn call(&mut self, _req: &ModelRequest) -> Result<ModelReply, CallError> {
            Ok(ModelReply {
                model: "canned".into(),
                text: self.text.clone(),
                stop_reason: StopReason::EndTurn,
                usage: Usage { input_tokens: 1, output_tokens: 1 },
            })
        }
    }

    fn source() -> SourceDoc {
        SourceDoc::article(
            "T",
            "https://e/x",
            None,
            None,
            vec![],
            "A chunk is a structurally neutral container. It knows nothing about ownership.",
        )
    }

    #[test]
    fn run_extraction_end_to_end() {
        let reply = r#"{"units":[
          {"kind":"assertion","text":"A chunk is structurally neutral.",
           "evidence_ref":"p001",
           "evidence_quote":"A chunk is a structurally neutral container.",
           "attribution":"author","modality":"asserted",
           "arguments":[{"surface":"chunk","role":"subject"}]}
        ]}"#;
        let mut client = CannedClient { text: reply.into() };
        let run = run_unit_extraction(&source(), &mut client).unwrap();
        assert_eq!(run.extraction.report.accepted, 1);
        assert_eq!(run.extraction.report.accepted_without_quote, 0);
        assert!(run.raw_reply.contains("structurally neutral"), "raw reply captured");
    }

    #[test]
    fn bad_json_yields_ok_extraction_with_parse_error() {
        let mut client = CannedClient { text: "not json".into() };
        let run = run_unit_extraction(&source(), &mut client).unwrap();
        assert!(run.extraction.report.parse_error.is_some());
        assert_eq!(run.extraction.units.len(), 0);
        assert_eq!(run.raw_reply, "not json", "raw reply preserved even on parse error");
    }

    // ---- M19 resilient base extraction ----

    /// Returns each scripted reply in turn (one per `call`), then repeats the last.
    struct ScriptedClient {
        replies: Vec<String>,
        i: usize,
    }
    impl ModelClient for ScriptedClient {
        fn call(&mut self, _req: &ModelRequest) -> Result<ModelReply, CallError> {
            let text = self.replies.get(self.i).or_else(|| self.replies.last()).cloned().unwrap_or_default();
            self.i += 1;
            Ok(ModelReply {
                model: "scripted".into(),
                text,
                stop_reason: StopReason::EndTurn,
                usage: Usage { input_tokens: 1, output_tokens: 1 },
            })
        }
    }

    /// A grounded unit whose evidence_quote matches `source()`'s body.
    fn grounded_unit_json(text: &str) -> String {
        format!(
            r#"{{"kind":"assertion","text":"{text}","evidence_ref":"p001","evidence_quote":"A chunk is a structurally neutral container.","attribution":"author","modality":"asserted","arguments":[]}}"#
        )
    }

    #[test]
    fn resilient_extract_recovers_unescaped_backslash_parser_local() {
        // m18-04 class: a stray backslash in a string. evidence_quote still
        // matches the source → the recovered unit is accepted. No client call.
        let reply = format!(r#"{{"units":[{}]}}"#, grounded_unit_json(r"see path C:\Users\app"));
        let mut client = CannedClient { text: "UNUSED".into() };
        let (values, ext, note) = resilient_unit_extract(&reply, &source(), &mut client);
        assert_eq!(values.len(), 1);
        assert_eq!(ext.report.accepted, 1);
        assert_eq!(ext.report.accepted_without_quote, 0);
        assert_eq!(note.unwrap().method, "parser-local: unescaped-backslash");
    }

    #[test]
    fn resilient_extract_uses_bounded_model_repair_on_missing_quote() {
        // m18-06/m18-19 class: unrecoverable locally → one repair call fixes it.
        let broken = r#"{"units":[ "kind":"assertion" ]}"#; // structural garbage
        let fixed = format!(r#"{{"units":[{}]}}"#, grounded_unit_json("a chunk is neutral"));
        let mut client = ScriptedClient { replies: vec![fixed], i: 0 };
        let (values, ext, note) = resilient_unit_extract(broken, &source(), &mut client);
        assert_eq!(values.len(), 1);
        assert_eq!(ext.report.accepted, 1);
        let n = note.expect("repair note");
        assert_eq!(n.stage, "units");
        assert!(n.method.starts_with("model-repair"), "got {}", n.method);
    }

    #[test]
    fn resilient_extract_fails_loud_when_repair_also_bad() {
        let broken = r#"{"units":[ "kind": ]}"#;
        // repair call returns junk too → fail loud, no silent accept.
        let mut client = ScriptedClient { replies: vec!["still broken".into()], i: 0 };
        let (values, ext, note) = resilient_unit_extract(broken, &source(), &mut client);
        assert!(values.is_empty());
        assert_eq!(ext.units.len(), 0);
        assert!(ext.report.parse_error.is_some());
        assert!(note.is_none());
    }

    #[test]
    fn repaired_units_still_pass_through_grounding_validator() {
        // The repair returns syntactically valid JSON but with an UNGROUNDED
        // quote (not in source). It must NOT be accepted — repair cannot bypass
        // grounding. accepted_without_quote stays 0.
        let broken = r#"{"units":[ bad ]}"#;
        let ungrounded = r#"{"units":[{"kind":"assertion","text":"x","evidence_ref":"p001","evidence_quote":"THIS QUOTE IS NOT IN THE SOURCE AT ALL","attribution":"author","modality":"asserted","arguments":[]}]}"#;
        let mut client = ScriptedClient { replies: vec![ungrounded.into()], i: 0 };
        let (_values, ext, _note) = resilient_unit_extract(broken, &source(), &mut client);
        assert_eq!(ext.report.accepted, 0, "ungrounded repaired unit must not be accepted");
        assert_eq!(ext.report.accepted_without_quote, 0, "grounding invariant holds");
    }
}
