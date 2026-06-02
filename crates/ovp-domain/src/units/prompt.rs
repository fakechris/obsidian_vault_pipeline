//! Build the unit-extraction prompt + the wire request. Mirrors how the v1
//! `PromptBuilder` splits its asset and how `LLMInvoker` builds a `ModelRequest`
//! (so cassettes file under the `unit_extract/v1` namespace), but lives entirely
//! in the M14a spike — it does NOT touch the shipped prompt builder.

use ovp_llm::{ModelMessage, ModelRequest};

use crate::source_doc::SourceDoc;

use super::source_map::annotate;

const UNIT_PROMPT_TEMPLATE: &str = include_str!("../../prompts/unit_extraction.md");

/// Cassette namespace + schema marker for the M14a unit prompt. `v2` is the
/// M14a.1 evidence-ref-hardened prompt (paragraph markers + `evidence_ref`);
/// bumped from `v1` so the new prompt re-records rather than replaying the old
/// free-quote cassettes. Distinct from every article prompt id.
pub const UNIT_PROMPT_ID: &str = "unit_extract/v2";
pub const UNIT_SCHEMA_VERSION: u32 = 2;

/// Default model + token budget. The live client overrides the model via
/// `OVP_LLM_MODEL` (as in M13.3), so this is just the offline/default value.
pub const DEFAULT_UNIT_MODEL: &str = "claude-sonnet-4-6";
pub const DEFAULT_UNIT_MAX_TOKENS: u32 = 8192;

/// Split the asset into (system, user) on the `## The article` marker and fill
/// the `{{TITLE}} / {{SOURCE_URL}} / {{BODY_MARKDOWN}}` placeholders.
pub fn build_unit_prompt(source: &SourceDoc) -> (String, String) {
    let marker = "## The article";
    let (system, user_template) = match UNIT_PROMPT_TEMPLATE.split_once(marker) {
        Some((sys, rest)) => (sys.trim_end().to_string(), rest.to_string()),
        None => (UNIT_PROMPT_TEMPLATE.to_string(), String::new()),
    };
    // The model is shown the paragraph-tagged body so it can anchor each unit's
    // evidence_ref to a `[pNNN]` id (validator re-derives the same ids).
    let user = user_template
        .replace("{{TITLE}}", &source.title)
        .replace("{{SOURCE_URL}}", &source.source_url)
        .replace("{{BODY_MARKDOWN}}", &annotate(&source.body_markdown));
    (system, format!("{marker}{user}"))
}

/// Build the provider-neutral request, filing it under the unit cassette
/// namespace. `model`/`max_tokens` default unless overridden by the caller.
pub fn unit_model_request(source: &SourceDoc) -> ModelRequest {
    let (system, user) = build_unit_prompt(source);
    ModelRequest {
        model: DEFAULT_UNIT_MODEL.to_string(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: DEFAULT_UNIT_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(UNIT_PROMPT_ID.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn src() -> SourceDoc {
        SourceDoc::article("My Title", "https://e/x", None, None, vec![], "Body text here.\n")
    }

    #[test]
    fn prompt_forbids_concepts_and_requires_quotes() {
        let (system, _user) = build_unit_prompt(&src());
        assert!(system.contains("knowledge units"));
        assert!(system.to_lowercase().contains("verbatim"));
        assert!(system.contains("evidence_quote"));
        // It must explicitly steer AWAY from the v2 concept framing.
        assert!(system.contains("Do **not** output concepts") || system.contains("NOT building a knowledge base"));
    }

    #[test]
    fn user_message_carries_the_paragraph_tagged_article() {
        let (_system, user) = build_unit_prompt(&src());
        assert!(user.contains("My Title"));
        assert!(user.contains("https://e/x"));
        // Body is annotated with a paragraph marker the model anchors evidence to.
        assert!(user.contains("[p001] Body text here."));
    }

    #[test]
    fn system_requires_evidence_ref() {
        let (system, _user) = build_unit_prompt(&src());
        assert!(system.contains("evidence_ref"));
        assert!(system.contains("[p001]") || system.contains("pNNN"));
    }

    #[test]
    fn request_uses_unit_v2_namespace() {
        let req = unit_model_request(&src());
        assert_eq!(req.cache_namespace.as_deref(), Some("unit_extract/v2"));
        assert!(req.system.is_some());
        assert_eq!(req.messages.len(), 1);
    }
}
