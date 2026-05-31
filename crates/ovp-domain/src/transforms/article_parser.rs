use ovp_core::{DropReason, FilterDecision, Record, StepId, Transform};
use serde::Deserialize;

use crate::body::DomainBody;
use crate::interpreted::{Dimensions, Explanation, ExtractedConcept, InterpretedDoc};
use crate::response::ModelResponse;

use super::prompt_builder::{
    ARTICLE_PROMPT_ID, ARTICLE_SCHEMA_VERSION, CONCEPT_MAP_PROMPT_ID, CONCEPT_MAP_SCHEMA_VERSION,
};

/// Parses a `ModelResponse` body containing the JSON spec our prompt
/// asked for, validating the schema version and producing an
/// `InterpretedDoc`. Pure: same input → same output.
///
/// Drops on:
/// - wrong variant (not a Model body)
/// - schema_version mismatch
/// - JSON shape mismatch (parse error)
/// - empty one_liner / details / actions (the dimensions the contract
///   asserts are non-empty)
pub struct ArticleParser {
    step: StepId,
    /// The `area` to stamp on every InterpretedDoc this parser produces.
    /// v1 hardcodes a single area per parser instance — multi-area
    /// routing is a v1.1+ concern.
    area: String,
    /// The interpretation date stamped onto outputs. Defaults to today's
    /// ISO date but callers can override (e.g. tests) for determinism.
    date_stamp: String,
}

impl ArticleParser {
    pub fn new(step: impl Into<String>, area: impl Into<String>, date_stamp: impl Into<String>) -> Self {
        Self {
            step: StepId::new(step.into()),
            area: area.into(),
            date_stamp: date_stamp.into(),
        }
    }
}

impl Transform<DomainBody> for ArticleParser {
    fn step_id(&self) -> &StepId { &self.step }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let model = match record.body {
            DomainBody::Model(m) => *m,
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.article_parser.wrong_variant",
                    format!("expected Model, got {}", other.variant_name()),
                ));
            }
        };

        // In the unified pipeline this parser is broadcast every Model
        // record; it only claims article-prompt responses (v1 or the v2
        // concept-map prompt) and lets the paper parser claim paper-prompt
        // ones. `is_v2` selects the concept-map handling below.
        let (expected_version, is_v2) = match model.prompt_id.as_str() {
            ARTICLE_PROMPT_ID => (ARTICLE_SCHEMA_VERSION, false),
            CONCEPT_MAP_PROMPT_ID => (CONCEPT_MAP_SCHEMA_VERSION, true),
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.article_parser.wrong_prompt",
                    format!(
                        "model response carries prompt_id={other}, parser expects {ARTICLE_PROMPT_ID} or {CONCEPT_MAP_PROMPT_ID}"
                    ),
                ));
            }
        };

        if model.schema_version != expected_version {
            return FilterDecision::Drop(DropReason::new(
                "transform.article_parser.schema_mismatch",
                format!(
                    "model response carries schema_version={}, parser expects {expected_version} for {}",
                    model.schema_version,
                    model.prompt_id.as_str()
                ),
            ));
        }

        let interpreted = match parse_into_interpreted(&model, &self.area, &self.date_stamp, is_v2) {
            Ok(d) => d,
            Err(reason) => return FilterDecision::Drop(reason),
        };

        let next = Record {
            id: record.id,
            body: DomainBody::Interpreted(Box::new(interpreted)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "interpreted");
        FilterDecision::Forward(vec![next])
    }
}

/// Mirror of the JSON shape the prompt asks the model to emit.
#[derive(Debug, Deserialize)]
struct ModelJsonPayload {
    title: String,
    #[serde(default)]
    tags: Vec<String>,
    #[serde(default)]
    #[allow(dead_code)] // top-level mirror of dimensions.linked_concepts; kept for prompt symmetry
    linked_concepts: Vec<String>,
    dimensions: DimensionsJson,
    /// v2 concept map. Absent/empty for v1 responses; required non-empty for v2.
    #[serde(default)]
    concepts: Vec<ExtractedConcept>,
}

#[derive(Debug, Deserialize)]
struct DimensionsJson {
    one_liner: String,
    explanation: ExplanationJson,
    #[serde(default)]
    details: Vec<String>,
    #[serde(default)]
    structure: Option<String>,
    #[serde(default)]
    actions: Vec<String>,
    #[serde(default)]
    linked_concepts: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ExplanationJson {
    what: String,
    why: String,
    how: String,
}

fn parse_into_interpreted(
    model: &ModelResponse,
    area: &str,
    date_stamp: &str,
    is_v2: bool,
) -> Result<InterpretedDoc, DropReason> {
    let text = model.content.text();
    let raw_json = strip_code_fence(text);
    let payload: ModelJsonPayload = serde_json::from_str(raw_json).map_err(|e| {
        DropReason::new(
            "transform.article_parser.json_parse",
            format!("could not parse model output as JSON: {e}"),
        )
    })?;

    if payload.dimensions.one_liner.trim().is_empty() {
        return Err(DropReason::new(
            "transform.article_parser.empty_one_liner",
            "model returned empty one_liner",
        ));
    }
    if payload.dimensions.details.is_empty() {
        return Err(DropReason::new(
            "transform.article_parser.empty_details",
            "model returned no details (expected ≥1)",
        ));
    }
    if payload.dimensions.actions.is_empty() {
        return Err(DropReason::new(
            "transform.article_parser.empty_actions",
            "model returned no actions (expected ≥1)",
        ));
    }

    // v2 must carry a concept map. Fail LOUD on a v2 response with no
    // concepts[] — never silently fall back to the v1 shared-one_liner path.
    // (The ConceptResolver gate decides per-concept promotion/rejection later;
    // the parser only guards the envelope.)
    if is_v2 && payload.concepts.is_empty() {
        return Err(DropReason::new(
            "transform.article_parser.empty_concepts",
            "v2 concept-map response carried no concepts[]",
        ));
    }

    let dims = payload.dimensions;
    Ok(InterpretedDoc {
        title: payload.title,
        source_url: model.origin.source_url.clone(),
        author: model.origin.author.clone(),
        date: date_stamp.to_string(),
        doc_type: "article".to_string(),
        area: area.to_string(),
        tags: payload.tags,
        // v1: every linked concept starts as a candidate. Promotion to
        // canonical happens in a separate absorb stage (not in v1).
        canonical_concepts: Vec::new(),
        concept_candidates: dims.linked_concepts.clone(),
        dimensions: Dimensions {
            one_liner: dims.one_liner,
            explanation: Explanation {
                what: dims.explanation.what,
                why: dims.explanation.why,
                how: dims.explanation.how,
            },
            details: dims.details,
            structure: dims.structure,
            actions: dims.actions,
            linked_concepts: dims.linked_concepts,
        },
        // v2 carries the concept map; v1 leaves it empty (legacy candidate path).
        concepts: if is_v2 { payload.concepts } else { Vec::new() },
    })
}

/// Strip a leading ```json / trailing ``` if the model wrapped its reply
/// in a markdown code fence (it shouldn't, but does roughly 30% of the
/// time in our experience).
fn strip_code_fence(text: &str) -> &str {
    let t = text.trim();
    if let Some(rest) = t.strip_prefix("```json") {
        return rest.trim_start_matches('\n').trim_end_matches("```").trim();
    }
    if let Some(rest) = t.strip_prefix("```") {
        return rest.trim_start_matches('\n').trim_end_matches("```").trim();
    }
    t
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_core::{RecordId, RecordMeta, RunId};

    use crate::prompt::PromptId;
    use crate::response::ResponseContent;
    use crate::source_doc::SourceDoc;

    const HAPPY_JSON: &str = r#"{
  "title": "Agent-native PM",
  "tags": ["AI", "PM"],
  "linked_concepts": ["agent-native-pm"],
  "dimensions": {
    "one_liner": "Agent-native PM treats the conversation as the work.",
    "explanation": {
      "what": "what...",
      "why": "why...",
      "how": "how..."
    },
    "details": ["detail-1", "detail-2", "detail-3"],
    "structure": null,
    "actions": ["short-term: try it"],
    "linked_concepts": ["agent-native-pm", "compound-engineering"]
  }
}"#;

    fn source() -> SourceDoc {
        SourceDoc::article(
            "Source Title",
            "https://example.com/article",
            Some("Author".into()),
            None,
            vec![],
            "",
        )
    }

    fn model_record(json: &str, schema_version: u32) -> Record<DomainBody> {
        let resp = ModelResponse {
            prompt_id: PromptId::new("article_interpret/v1"),
            schema_version,
            model: "fake".into(),
            content: ResponseContent::Inline { text: json.into() },
            input_tokens: 0,
            output_tokens: 0,
            origin: Box::new(source()),
        };
        Record::new(
            RecordId::new("r"),
            DomainBody::Model(Box::new(resp)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn happy_path_produces_interpreted() {
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        match parser.process(model_record(HAPPY_JSON, 1)) {
            FilterDecision::Forward(rs) => {
                assert_eq!(rs.len(), 1);
                let body = match &rs[0].body {
                    DomainBody::Interpreted(d) => d,
                    other => panic!("expected Interpreted, got {}", other.variant_name()),
                };
                assert_eq!(body.title, "Agent-native PM");
                assert_eq!(body.source_url, "https://example.com/article");
                assert_eq!(body.author.as_deref(), Some("Author"));
                assert_eq!(body.area, "ai");
                assert_eq!(body.date, "2026-05-27");
                assert_eq!(body.doc_type, "article");
                assert_eq!(body.tags, vec!["AI", "PM"]);
                assert_eq!(body.canonical_concepts.len(), 0);
                assert_eq!(body.concept_candidates.len(), 2);
                assert_eq!(body.dimensions.details.len(), 3);
                assert!(body.dimensions.structure.is_none());
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn schema_mismatch_drops() {
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        match parser.process(model_record(HAPPY_JSON, 99)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.schema_mismatch");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn bad_json_drops() {
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        match parser.process(model_record("not even json", 1)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.json_parse");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn empty_one_liner_drops() {
        let json = HAPPY_JSON.replace(
            "Agent-native PM treats the conversation as the work.",
            "",
        );
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        match parser.process(model_record(&json, 1)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.empty_one_liner");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn code_fence_wrapped_json_parses() {
        let wrapped = format!("```json\n{}\n```", HAPPY_JSON);
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        match parser.process(model_record(&wrapped, 1)) {
            FilterDecision::Forward(_) => {}
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn wrong_variant_drops() {
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(source())),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match parser.process(rec) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.wrong_variant");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    // ---- M13 v2 concept map ----

    const V2_JSON: &str = r#"{
  "title": "RAG, rebuilt",
  "tags": ["RAG"],
  "dimensions": {
    "one_liner": "An article-level synthesis line for the primary note.",
    "explanation": { "what": "w", "why": "y", "how": "h" },
    "details": ["d1", "d2", "d3"],
    "structure": null,
    "actions": ["a1"]
  },
  "concepts": [
    { "slug": "idea-block", "title": "IdeaBlock", "aliases": ["qa-packet"], "kind": "concept",
      "definition": "A question-answer packet that replaces a prose chunk as the unit.",
      "evidence": ["validated answer"], "claims": ["2.29x better retrieval"],
      "related": ["chunking-problem"], "promote": true },
    { "slug": "chunking-problem", "title": "Chunking Problem", "kind": "principle",
      "definition": "The chunk is a structurally neutral container with no idea boundary.",
      "evidence": ["no idea boundary"], "claims": ["half a table loses its meaning"],
      "promote": true }
  ]
}"#;

    fn model_record_v2(json: &str, schema_version: u32) -> Record<DomainBody> {
        let resp = ModelResponse {
            prompt_id: PromptId::new("article_concept_map/v2"),
            schema_version,
            model: "fake".into(),
            content: ResponseContent::Inline { text: json.into() },
            input_tokens: 0,
            output_tokens: 0,
            origin: Box::new(source()),
        };
        Record::new(
            RecordId::new("r"),
            DomainBody::Model(Box::new(resp)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn v2_parses_concept_map_with_distinct_definitions() {
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-31");
        match parser.process(model_record_v2(V2_JSON, 2)) {
            FilterDecision::Forward(rs) => {
                let d = match &rs[0].body {
                    DomainBody::Interpreted(d) => d,
                    other => panic!("expected Interpreted, got {}", other.variant_name()),
                };
                assert_eq!(d.concepts.len(), 2);
                assert_eq!(d.concepts[0].slug, "idea-block");
                assert_eq!(d.concepts[0].aliases, vec!["qa-packet"]);
                assert_eq!(d.concepts[1].slug, "chunking-problem");
                // Each concept owns its OWN definition (not the article one_liner).
                assert_ne!(d.concepts[0].definition, d.concepts[1].definition);
                assert_ne!(d.concepts[0].definition, d.dimensions.one_liner);
                assert!(d.concepts[0].claims.iter().any(|c| c.contains("2.29x")));
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn v2_missing_concepts_drops_loud() {
        // A v2 response with no concepts[] must fail loud, never fall back to v1.
        let no_concepts = V2_JSON.replace("\"concepts\"", "\"concepts_absent\"");
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-31");
        match parser.process(model_record_v2(&no_concepts, 2)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.empty_concepts");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn v2_wrong_schema_version_drops() {
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-31");
        match parser.process(model_record_v2(V2_JSON, 1)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.schema_mismatch");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn v2_concept_missing_promote_drops_loud() {
        // Regression (M13.2 follow-up): `promote` is REQUIRED. A real model
        // omitting it must fail LOUD at parse — never default to `false`, which
        // would silently drop every concept as not_promoted and leave the run
        // "successful" with an empty concept map.
        let missing = V2_JSON.replacen(", \"promote\": true }", " }", 1);
        assert_ne!(missing, V2_JSON, "the test fixture must actually drop a promote field");
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-31");
        match parser.process(model_record_v2(&missing, 2)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.article_parser.json_parse");
                assert!(
                    reason.detail.contains("promote"),
                    "drop reason should name the missing field, got: {}",
                    reason.detail
                );
            }
            other => panic!("expected Drop (loud), got {other:?}"),
        }
    }

    #[test]
    fn v1_response_has_empty_concepts() {
        // v1 path is untouched: no concept map, never synthesized from one_liner.
        let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-27");
        match parser.process(model_record(HAPPY_JSON, 1)) {
            FilterDecision::Forward(rs) => match &rs[0].body {
                DomainBody::Interpreted(d) => assert!(d.concepts.is_empty()),
                other => panic!("expected Interpreted, got {}", other.variant_name()),
            },
            other => panic!("expected Forward, got {other:?}"),
        }
    }
}
