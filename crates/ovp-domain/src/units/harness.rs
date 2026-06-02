//! The M14a hand-harness: drive a `ModelClient` end-to-end for one source and
//! produce a [`SourceExtraction`]. No GraphRunner, no `DomainBody`, no manifest —
//! just `source → prompt → client → parse → validate`. The same function backs
//! both the offline test (with an inline fake client) and the live CLI shim
//! (with the real cached/anthropic client).

use std::path::Path;

use ovp_llm::{CallError, ModelClient};

use crate::source_doc::SourceDoc;

use super::parser::parse_envelope;
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

/// Full half: call the client (replay cassette or live), then extract. Returns
/// `Err(CallError)` ONLY for client/transport failures (the operator's network,
/// a cache miss) — a reply that parses badly still yields an `Ok` extraction
/// carrying the parse error, because that is a reviewable model-quality outcome,
/// not an I/O failure.
pub fn run_unit_extraction(
    source: &SourceDoc,
    client: &mut dyn ModelClient,
) -> Result<SourceExtraction, CallError> {
    let request = unit_model_request(source);
    let reply = client.call(&request)?;
    Ok(extract_units(&reply.text, source))
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
           "evidence_quote":"A chunk is a structurally neutral container.",
           "attribution":"author","modality":"asserted",
           "arguments":[{"surface":"chunk","role":"subject"}]}
        ]}"#;
        let mut client = CannedClient { text: reply.into() };
        let ex = run_unit_extraction(&source(), &mut client).unwrap();
        assert_eq!(ex.report.accepted, 1);
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn bad_json_yields_ok_extraction_with_parse_error() {
        let mut client = CannedClient { text: "not json".into() };
        let ex = run_unit_extraction(&source(), &mut client).unwrap();
        assert!(ex.report.parse_error.is_some());
        assert_eq!(ex.units.len(), 0);
    }
}
