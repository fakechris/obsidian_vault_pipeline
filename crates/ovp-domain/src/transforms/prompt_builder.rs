use ovp_core::{DropReason, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;
use crate::prompt::{PromptId, PromptRequest};
use crate::source_doc::SourceDoc;

const ARTICLE_PROMPT_TEMPLATE: &str = include_str!("../../prompts/article_interpret.md");

/// `PROMPT_ID/SCHEMA_VERSION` for the article interpretation prompt. Bump
/// `ARTICLE_SCHEMA_VERSION` when you change the prompt asset in a way that
/// would invalidate cached cassettes. ArticleParser refuses responses
/// produced under a different version.
pub const ARTICLE_PROMPT_ID: &str = "article_interpret/v1";
pub const ARTICLE_SCHEMA_VERSION: u32 = 1;

/// Default model + max_tokens for v1. Production wiring may override
/// either via CLI flags or config.
pub const DEFAULT_ARTICLE_MODEL: &str = "claude-sonnet-4-6";
pub const DEFAULT_ARTICLE_MAX_TOKENS: u32 = 4096;

/// Builds a `PromptRequest` from a `SourceDoc`. Pure: same `SourceDoc`
/// always produces the same `PromptRequest` (modulo construction-time
/// `model` / `max_tokens` overrides on the builder itself).
pub struct PromptBuilder {
    step: StepId,
    model: String,
    max_tokens: u32,
}

impl PromptBuilder {
    pub fn new(step: impl Into<String>) -> Self {
        Self {
            step: StepId::new(step.into()),
            model: DEFAULT_ARTICLE_MODEL.to_string(),
            max_tokens: DEFAULT_ARTICLE_MAX_TOKENS,
        }
    }

    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = model.into();
        self
    }

    pub fn with_max_tokens(mut self, n: u32) -> Self {
        self.max_tokens = n;
        self
    }

    /// Build the PromptRequest for a given SourceDoc. Public so tests
    /// can verify prompt content directly without going through the
    /// trait machinery.
    pub fn build_request(&self, source: &SourceDoc) -> PromptRequest {
        let (system, user) = split_prompt_template(ARTICLE_PROMPT_TEMPLATE, source);
        PromptRequest {
            prompt_id: PromptId::new(ARTICLE_PROMPT_ID),
            schema_version: ARTICLE_SCHEMA_VERSION,
            model: self.model.clone(),
            system,
            user,
            max_tokens: self.max_tokens,
        }
    }
}

impl Transform<DomainBody> for PromptBuilder {
    fn step_id(&self) -> &StepId { &self.step }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let source_doc = match record.body {
            DomainBody::Source(s) => *s,
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.prompt_builder.wrong_variant",
                    format!("expected Source, got {}", other.variant_name()),
                ));
            }
        };

        let request = self.build_request(&source_doc);
        let next = Record {
            id: record.id,
            body: DomainBody::Prompt(Box::new(request)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "prompt built");
        FilterDecision::Forward(vec![next])
    }
}

/// Split the asset into (system, user) by treating everything up to the
/// `## The article` marker as system content and everything after as the
/// user message — with the `{{TITLE}} / {{SOURCE_URL}} / {{BODY_MARKDOWN}}`
/// placeholders filled in.
fn split_prompt_template(template: &str, source: &SourceDoc) -> (String, String) {
    let marker = "## The article";
    let (system, user_template) = match template.split_once(marker) {
        Some((sys, rest)) => (sys.trim_end().to_string(), rest.to_string()),
        None => (template.to_string(), String::new()),
    };
    let user = user_template
        .replace("{{TITLE}}", &source.title)
        .replace("{{SOURCE_URL}}", &source.source_url)
        .replace("{{BODY_MARKDOWN}}", &source.body_markdown);
    (system, format!("{marker}{}", user))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_source() -> SourceDoc {
        SourceDoc {
            title: "Test Title".into(),
            source_url: "https://example.com/test".into(),
            author: None,
            published: None,
            tags: vec![],
            body_markdown: "Body content here.\n".into(),
        }
    }

    #[test]
    fn build_request_fills_placeholders() {
        let pb = PromptBuilder::new("prompt_builder");
        let req = pb.build_request(&sample_source());

        assert_eq!(req.prompt_id.as_str(), "article_interpret/v1");
        assert_eq!(req.schema_version, 1);
        assert_eq!(req.model, DEFAULT_ARTICLE_MODEL);
        assert_eq!(req.max_tokens, DEFAULT_ARTICLE_MAX_TOKENS);

        // System message contains the JSON schema spec.
        assert!(req.system.contains("six-dimension"));
        assert!(req.system.contains("\"one_liner\""));

        // User message contains the actual article content + URL + title.
        assert!(req.user.contains("Test Title"));
        assert!(req.user.contains("https://example.com/test"));
        assert!(req.user.contains("Body content here."));
    }

    #[test]
    fn build_request_is_deterministic() {
        let pb = PromptBuilder::new("prompt_builder");
        let a = pb.build_request(&sample_source());
        let b = pb.build_request(&sample_source());
        assert_eq!(a, b);
    }

    #[test]
    fn builder_overrides_take_effect() {
        let pb = PromptBuilder::new("prompt_builder")
            .with_model("claude-opus-4-7")
            .with_max_tokens(8192);
        let req = pb.build_request(&sample_source());
        assert_eq!(req.model, "claude-opus-4-7");
        assert_eq!(req.max_tokens, 8192);
    }
}
