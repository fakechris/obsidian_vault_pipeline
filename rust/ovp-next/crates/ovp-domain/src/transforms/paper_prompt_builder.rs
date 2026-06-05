use ovp_core::{DropReason, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;
use crate::prompt::{PromptId, PromptRequest};
use crate::source_doc::{SourceDoc, SourceKind};

const PAPER_PROMPT_TEMPLATE: &str = include_str!("../../prompts/paper_interpret.md");

/// Prompt asset id + schema version for the paper deep-dive. Bump the
/// version when the prompt changes in a way that invalidates cassettes;
/// `PaperParser` refuses mismatched versions and the cassette namespace
/// changes.
pub const PAPER_PROMPT_ID: &str = "paper_interpret/v1";
pub const PAPER_SCHEMA_VERSION: u32 = 1;
pub const DEFAULT_PAPER_MODEL: &str = "claude-sonnet-4-6";
/// Papers are longer than articles; allow more output budget.
pub const DEFAULT_PAPER_MAX_TOKENS: u32 = 8192;

/// Builds a paper `PromptRequest` from a `SourceKind::Paper` source.
/// In the unified pipeline it's broadcast every Source record; it claims
/// papers and drops everything else (articles → handled by `PromptBuilder`).
pub struct PaperPromptBuilder {
    step: StepId,
    model: String,
    max_tokens: u32,
}

impl PaperPromptBuilder {
    pub fn new(step: impl Into<String>) -> Self {
        Self {
            step: StepId::new(step.into()),
            model: DEFAULT_PAPER_MODEL.to_string(),
            max_tokens: DEFAULT_PAPER_MAX_TOKENS,
        }
    }

    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = model.into();
        self
    }

    pub fn build_request(&self, source: &SourceDoc) -> PromptRequest {
        let (system, user) = split_template(PAPER_PROMPT_TEMPLATE, source);
        PromptRequest {
            prompt_id: PromptId::new(PAPER_PROMPT_ID),
            schema_version: PAPER_SCHEMA_VERSION,
            model: self.model.clone(),
            system,
            user,
            max_tokens: self.max_tokens,
            origin: Box::new(source.clone()),
        }
    }
}

impl Transform<DomainBody> for PaperPromptBuilder {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let source_doc = match record.body {
            DomainBody::Source(s) => *s,
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.paper_prompt_builder.wrong_variant",
                    format!("expected Source, got {}", other.variant_name()),
                ));
            }
        };

        if !matches!(source_doc.source_kind, SourceKind::Paper(_)) {
            return FilterDecision::Drop(DropReason::new(
                "transform.paper_prompt_builder.wrong_kind",
                format!("expected paper, got {}", source_doc.source_kind.name()),
            ));
        }

        let request = self.build_request(&source_doc);
        let next = Record {
            id: record.id,
            body: DomainBody::Prompt(Box::new(request)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "paper prompt built");
        FilterDecision::Forward(vec![next])
    }
}

fn split_template(template: &str, source: &SourceDoc) -> (String, String) {
    let marker = "## The paper";
    let (system, user_template) = match template.split_once(marker) {
        Some((sys, rest)) => (sys.trim_end().to_string(), rest.to_string()),
        None => (template.to_string(), String::new()),
    };
    let user = user_template
        .replace("{{TITLE}}", &source.title)
        .replace("{{SOURCE_URL}}", &source.source_url)
        .replace("{{BODY_MARKDOWN}}", &source.body_markdown);
    (system, format!("{marker}{user}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::{PaperMeta, SourceDoc, SourceKind};

    fn paper_source() -> SourceDoc {
        let mut d = SourceDoc::article(
            "Deep GraphRAG",
            "https://arxiv.org/abs/2601.11144",
            None,
            None,
            vec![],
            "Abstract: ...",
        );
        d.source_kind = SourceKind::Paper(PaperMeta {
            arxiv_id: "2601.11144".into(),
            authors: vec!["Yuejie Li".into()],
            categories: vec!["cs.IR".into()],
            published: Some("2026-01-16".into()),
        });
        d
    }

    #[test]
    fn build_request_uses_paper_prompt() {
        let pb = PaperPromptBuilder::new("paper_prompt_builder");
        let req = pb.build_request(&paper_source());
        assert_eq!(req.prompt_id.as_str(), "paper_interpret/v1");
        assert_eq!(req.schema_version, 1);
        assert_eq!(req.max_tokens, DEFAULT_PAPER_MAX_TOKENS);
        assert!(req.system.contains("academic paper"));
        assert!(req.system.contains("core_contribution"));
        assert!(req.user.contains("Deep GraphRAG"));
        assert!(req.user.contains("https://arxiv.org/abs/2601.11144"));
        assert!(req.user.contains("Abstract: ..."));
    }

    #[test]
    fn drops_article_kind() {
        use ovp_core::{RecordId, RecordMeta, RunId};
        let mut pb = PaperPromptBuilder::new("paper_prompt_builder");
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(SourceDoc::article(
                "T", "u", None, None, vec![], "b",
            ))),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match pb.process(rec) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.paper_prompt_builder.wrong_kind");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn forwards_paper_kind() {
        use ovp_core::{RecordId, RecordMeta, RunId};
        let mut pb = PaperPromptBuilder::new("paper_prompt_builder");
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(paper_source())),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match pb.process(rec) {
            FilterDecision::Forward(rs) => {
                assert!(matches!(rs[0].body, DomainBody::Prompt(_)));
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }
}
