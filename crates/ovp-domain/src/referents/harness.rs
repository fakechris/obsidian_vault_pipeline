//! M14b hand-harness: accepted Units → ReferentCandidates. Reads the M14a.8
//! `units.accepted.json` (it NEVER re-extracts Source→Unit), harvests seed
//! surfaces deterministically, classifies via one model call, and re-grounds via
//! the deterministic [`validator`]. Same `source → client → parse → validate`
//! shape as the units harness — no GraphAssembler / manifest / DomainBody.

use std::path::Path;

use ovp_llm::{CallError, ModelClient};

use crate::units::Unit;

use super::parser::parse_referent_envelope;
use super::prompt::referent_model_request;
use super::validator::{referents_parse_failed, validate_referents};
use super::ReferentExtraction;

/// Read the M14a.8 `units.accepted.json` (a `Vec<Unit>`) from disk.
pub fn read_accepted_units(path: &Path) -> Result<Vec<Unit>, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("reading {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("parsing accepted units {}: {e}", path.display()))
}

/// Deterministic seed harvest: every `arguments[].surface`, EXCEPT a
/// `role=="topic"` arg that did not locate in its unit's quote (the directive
/// paraphrase population — a handle for an action, not an object). Seeds are
/// classifier input + audit, NOT auto-emitted referents. Order-preserving, unique.
pub fn seed_surfaces(units: &[Unit]) -> Vec<String> {
    let mut seen = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for u in units {
        for a in &u.arguments {
            if a.role == "topic" && !a.locatable {
                continue;
            }
            let s = a.surface.trim();
            if s.is_empty() {
                continue;
            }
            if seen.insert(s.to_lowercase()) {
                out.push(s.to_string());
            }
        }
    }
    out
}

/// Pure half: model reply text + accepted units → validated referent extraction.
/// A parse failure is recorded in `report.parse_error`, not thrown away.
pub fn extract_referents(reply_text: &str, units: &[Unit], case_id: &str) -> ReferentExtraction {
    match parse_referent_envelope(reply_text) {
        Ok(values) => validate_referents(&values, units, case_id),
        Err(e) => referents_parse_failed(case_id, e.detail),
    }
}

/// One end-to-end run: the validated extraction PLUS the raw model reply.
#[derive(Debug, Clone, PartialEq)]
pub struct ReferentExtractionRun {
    pub extraction: ReferentExtraction,
    pub raw_reply: String,
    pub seeds: Vec<String>,
}

/// Full half: classify the accepted units (replay or live), then validate.
/// `Err(CallError)` only for client/transport failure; a badly-parsing reply
/// still yields `Ok` carrying the parse error.
pub fn run_referent_extraction(
    units: &[Unit],
    case_id: &str,
    client: &mut dyn ModelClient,
) -> Result<ReferentExtractionRun, CallError> {
    let seeds = seed_surfaces(units);
    let request = referent_model_request(units, &seeds);
    let reply = client.call(&request)?;
    let extraction = extract_referents(&reply.text, units, case_id);
    Ok(ReferentExtractionRun { extraction, raw_reply: reply.text, seeds })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;
    use ovp_llm::{ModelReply, ModelRequest, StopReason, Usage};

    struct Canned {
        text: String,
    }
    impl ModelClient for Canned {
        fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
            Ok(ModelReply {
                model: "canned".into(),
                text: self.text.clone(),
                stop_reason: StopReason::EndTurn,
                usage: Usage { input_tokens: 1, output_tokens: 1 },
            })
        }
    }

    fn units() -> Vec<Unit> {
        let raw = vec![
            serde_json::json!({"kind":"assertion","text":"IdeaBlocks replace prose chunks.",
              "evidence_ref":"p001.s001","evidence_quote":"IdeaBlocks replace prose chunks.",
              "attribution":"author","modality":"asserted","arguments":[{"surface":"IdeaBlocks","role":"subject"}]}),
            serde_json::json!({"kind":"directive","text":"Try reading raw logs.",
              "evidence_ref":"p001.s001","evidence_quote":"IdeaBlocks replace prose chunks.",
              "attribution":"author","modality":"asserted","arguments":[{"surface":"reading raw logs","role":"topic"}]}),
        ];
        validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![], "IdeaBlocks replace prose chunks.")).units
    }

    #[test]
    fn seed_drops_nonlocatable_topic_args() {
        let seeds = seed_surfaces(&units());
        assert!(seeds.iter().any(|s| s == "IdeaBlocks"));
        assert!(!seeds.iter().any(|s| s == "reading raw logs"), "non-locatable topic handle dropped");
    }

    #[test]
    fn end_to_end_with_canned_reply() {
        let u = units();
        let reply = format!(
            r#"{{"referents":[{{"kind":"entity","surface_names":["IdeaBlocks"],"support_unit_ids":["{}"],"rationale":"named construct"}}]}}"#,
            u[0].id
        );
        let mut client = Canned { text: reply };
        let run = run_referent_extraction(&u, "t", &mut client).unwrap();
        assert_eq!(run.extraction.referents.len(), 1);
        assert_eq!(run.extraction.report.referents_ungrounded, 0);
        assert!(run.raw_reply.contains("IdeaBlocks"));
    }

    #[test]
    fn bad_json_yields_ok_with_parse_error() {
        let mut client = Canned { text: "the model refused".into() };
        let run = run_referent_extraction(&units(), "t", &mut client).unwrap();
        assert!(run.extraction.report.parse_error.is_some());
        assert!(run.extraction.referents.is_empty());
    }
}
