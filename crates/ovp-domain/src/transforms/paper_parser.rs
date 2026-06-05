use ovp_core::{DropReason, FilterDecision, Record, StepId, Transform};
use serde::Deserialize;

use crate::body::DomainBody;
use crate::paper_doc::{PaperDoc, PaperSections};
use crate::response::ModelResponse;
use crate::source_doc::SourceKind;

use super::paper_prompt_builder::{PAPER_PROMPT_ID, PAPER_SCHEMA_VERSION};

/// Parses a paper `ModelResponse` (the JSON the paper prompt asked for)
/// into a `PaperDoc`. `arxiv_id` / `authors` / `categories` come from the
/// source's `PaperMeta` (authoritative frontmatter), not the LLM echo.
///
/// In the unified pipeline it's broadcast every Model record; it claims
/// only paper-prompt responses and drops article-prompt ones.
pub struct PaperParser {
    step: StepId,
    date_stamp: String,
}

impl PaperParser {
    pub fn new(step: impl Into<String>, date_stamp: impl Into<String>) -> Self {
        Self { step: StepId::new(step.into()), date_stamp: date_stamp.into() }
    }
}

impl Transform<DomainBody> for PaperParser {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let model = match record.body {
            DomainBody::Model(m) => *m,
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.paper_parser.wrong_variant",
                    format!("expected Model, got {}", other.variant_name()),
                ));
            }
        };

        if model.prompt_id.as_str() != PAPER_PROMPT_ID {
            return FilterDecision::Drop(DropReason::new(
                "transform.paper_parser.wrong_prompt",
                format!(
                    "model carries prompt_id={}, parser expects {}",
                    model.prompt_id.as_str(),
                    PAPER_PROMPT_ID
                ),
            ));
        }
        if model.schema_version != PAPER_SCHEMA_VERSION {
            return FilterDecision::Drop(DropReason::new(
                "transform.paper_parser.schema_mismatch",
                format!(
                    "model carries schema_version={}, parser expects {}",
                    model.schema_version, PAPER_SCHEMA_VERSION
                ),
            ));
        }

        let paper_meta = match &model.origin.source_kind {
            SourceKind::Paper(m) => m.clone(),
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.paper_parser.origin_not_paper",
                    format!("origin source_kind is {}, expected paper", other.name()),
                ));
            }
        };

        let doc = match parse_into_paper(&model, &paper_meta, &self.date_stamp) {
            Ok(d) => d,
            Err(reason) => return FilterDecision::Drop(reason),
        };

        let next = Record {
            id: record.id,
            body: DomainBody::InterpretedPaper(Box::new(doc)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "paper interpreted");
        FilterDecision::Forward(vec![next])
    }
}

#[derive(Debug, Deserialize)]
struct PaperJsonPayload {
    title: String,
    #[serde(default)]
    tags: Vec<String>,
    sections: SectionsJson,
}

#[derive(Debug, Deserialize)]
struct SectionsJson {
    metadata: String,
    core_contribution: String,
    background: String,
    method: String,
    experiments: String,
    key_insights: String,
    reproduction: String,
    limitations: String,
    related_work: String,
    personal_notes: String,
}

fn parse_into_paper(
    model: &ModelResponse,
    meta: &crate::source_doc::PaperMeta,
    date_stamp: &str,
) -> Result<PaperDoc, DropReason> {
    let text = strip_code_fence(model.content.text());
    let payload: PaperJsonPayload = serde_json::from_str(text).map_err(|e| {
        DropReason::new(
            "transform.paper_parser.json_parse",
            format!("could not parse paper model output as JSON: {e}"),
        )
    })?;

    if payload.sections.core_contribution.trim().is_empty() {
        return Err(DropReason::new(
            "transform.paper_parser.empty_core_contribution",
            "model returned empty core_contribution",
        ));
    }
    if payload.tags.is_empty() {
        return Err(DropReason::new(
            "transform.paper_parser.empty_tags",
            "model returned no tags",
        ));
    }

    let s = payload.sections;
    Ok(PaperDoc {
        title: payload.title,
        source_url: model.origin.source_url.clone(),
        arxiv_id: meta.arxiv_id.clone(),
        authors: meta.authors.clone(),
        categories: meta.categories.clone(),
        date: date_stamp.to_string(),
        source_date: meta.published.clone(),
        tags: payload.tags,
        sections: PaperSections {
            metadata: s.metadata,
            core_contribution: s.core_contribution,
            background: s.background,
            method: s.method,
            experiments: s.experiments,
            key_insights: s.key_insights,
            reproduction: s.reproduction,
            limitations: s.limitations,
            related_work: s.related_work,
            personal_notes: s.personal_notes,
        },
    })
}

/// Strip a leading ```json / trailing ``` if the model wrapped its reply.
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
    use crate::prompt::PromptId;
    use crate::response::ResponseContent;
    use crate::source_doc::{PaperMeta, SourceDoc, SourceKind};
    use ovp_core::{RecordId, RecordMeta, RunId};

    const PAPER_JSON: &str = r#"{
      "title": "Deep GraphRAG: A Balanced Approach",
      "tags": ["GraphRAG", "RAG", "retrieval", "RL"],
      "sections": {
        "metadata": "arXiv 2601.11144 (cs.IR)",
        "core_contribution": "Balances global and local retrieval via DW-GRPO.",
        "background": "RAG over graphs is unbalanced.",
        "method": "Hierarchical retrieval + adaptive integration.",
        "experiments": "Beats baselines on X/Y/Z.",
        "key_insights": "Compact 1.5B matches 70B integration.",
        "reproduction": "pip install ...; run train.py.",
        "limitations": "Single-domain eval.",
        "related_work": "HippoRAG, GraphRAG.",
        "personal_notes": "Use when graph structure matters."
      }
    }"#;

    fn paper_meta() -> PaperMeta {
        PaperMeta {
            arxiv_id: "2601.11144".into(),
            authors: vec!["Yuejie Li".into(), "Ke Yang".into()],
            categories: vec!["cs.IR".into(), "cs.AI".into()],
            published: Some("2026-01-16".into()),
        }
    }

    fn model_record(json: &str, prompt_id: &str, schema: u32) -> Record<DomainBody> {
        let mut origin = SourceDoc::article(
            "Deep GraphRAG",
            "https://arxiv.org/abs/2601.11144",
            None,
            None,
            vec![],
            "",
        );
        origin.source_kind = SourceKind::Paper(paper_meta());
        let resp = ModelResponse {
            prompt_id: PromptId::new(prompt_id),
            schema_version: schema,
            model: "fake".into(),
            content: ResponseContent::Inline { text: json.into() },
            input_tokens: 0,
            output_tokens: 0,
            origin: Box::new(origin),
        };
        Record::new(
            RecordId::new("r"),
            DomainBody::Model(Box::new(resp)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn happy_path_produces_paper() {
        let mut parser = PaperParser::new("paper_parser", "2026-05-29");
        match parser.process(model_record(PAPER_JSON, "paper_interpret/v1", 1)) {
            FilterDecision::Forward(rs) => {
                let body = match &rs[0].body {
                    DomainBody::InterpretedPaper(d) => d,
                    other => panic!("expected InterpretedPaper, got {}", other.variant_name()),
                };
                assert!(body.title.contains("Deep GraphRAG"));
                assert_eq!(body.arxiv_id, "2601.11144");
                assert_eq!(body.authors.len(), 2);
                assert_eq!(body.categories, vec!["cs.IR", "cs.AI"]);
                assert_eq!(body.date, "2026-05-29");
                assert_eq!(body.source_date.as_deref(), Some("2026-01-16"));
                assert_eq!(body.tags.len(), 4);
                assert!(body.sections.core_contribution.contains("DW-GRPO"));
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn wrong_prompt_drops() {
        let mut parser = PaperParser::new("paper_parser", "2026-05-29");
        match parser.process(model_record(PAPER_JSON, "article_interpret/v1", 1)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.paper_parser.wrong_prompt");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn bad_json_drops() {
        let mut parser = PaperParser::new("paper_parser", "2026-05-29");
        match parser.process(model_record("not json", "paper_interpret/v1", 1)) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.paper_parser.json_parse");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn code_fence_wrapped_parses() {
        let wrapped = format!("```json\n{PAPER_JSON}\n```");
        let mut parser = PaperParser::new("paper_parser", "2026-05-29");
        match parser.process(model_record(&wrapped, "paper_interpret/v1", 1)) {
            FilterDecision::Forward(_) => {}
            other => panic!("expected Forward, got {other:?}"),
        }
    }
}
