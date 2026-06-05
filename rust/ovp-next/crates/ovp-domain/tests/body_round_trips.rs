//! Serde round-trip checks for every DomainBody variant + ResponseContent.
//! These are the contract for cassette files and any future Record<DomainBody>
//! persistence — if they break, replay-only tests blow up at runtime.

use ovp_domain::*;

fn sample_source() -> SourceDoc {
    SourceDoc::article(
        "A Guide",
        "https://example.com/guide",
        Some("Marcus Moretti".into()),
        Some("2026-04-27".into()),
        vec!["clippings".into()],
        "# Heading\n\nbody\n",
    )
}

fn sample_prompt() -> PromptRequest {
    PromptRequest {
        prompt_id: PromptId::new("article_interpret/v1"),
        schema_version: 1,
        model: "claude-sonnet-4-6".into(),
        system: "you are a careful summarizer".into(),
        user: "summarize the following article in six dimensions".into(),
        max_tokens: 4096,
        origin: Box::new(sample_source()),
    }
}

fn sample_model_response() -> ModelResponse {
    ModelResponse {
        prompt_id: PromptId::new("article_interpret/v1"),
        schema_version: 1,
        model: "claude-sonnet-4-6".into(),
        content: ResponseContent::Inline { text: "model-output".into() },
        input_tokens: 1024,
        output_tokens: 512,
        origin: Box::new(sample_source()),
    }
}

fn sample_interpreted() -> InterpretedDoc {
    InterpretedDoc {
        title: "A Guide".into(),
        source_url: "https://example.com/guide".into(),
        author: Some("Marcus Moretti".into()),
        date: "2026-05-04".into(),
        doc_type: "article".into(),
        area: "ai".into(),
        tags: vec!["AI".into(), "PM".into()],
        canonical_concepts: vec![],
        concept_candidates: vec!["compound-engineering".into(), "product-strategy".into()],
        dimensions: Dimensions {
            one_liner: "Agent-native PM is …".into(),
            explanation: Explanation {
                what: "…".into(),
                why: "…".into(),
                how: "…".into(),
            },
            details: vec!["detail 1".into(), "detail 2".into(), "detail 3".into()],
            structure: None,
            actions: vec!["short-term: …".into()],
            linked_concepts: vec!["agent-native-product-management".into()],
        },
        schema: InterpretationSchema::ArticleV1,
        concepts: Vec::new(),
    }
}

#[test]
fn source_doc_round_trips_json() {
    let original = sample_source();
    let json = serde_json::to_string(&original).unwrap();
    let back: SourceDoc = serde_json::from_str(&json).unwrap();
    assert_eq!(original, back);
}

#[test]
fn prompt_request_round_trips_json() {
    let original = sample_prompt();
    let json = serde_json::to_string(&original).unwrap();
    let back: PromptRequest = serde_json::from_str(&json).unwrap();
    assert_eq!(original, back);
}

#[test]
fn model_response_round_trips_json() {
    let original = sample_model_response();
    let json = serde_json::to_string(&original).unwrap();
    let back: ModelResponse = serde_json::from_str(&json).unwrap();
    assert_eq!(original, back);
}

#[test]
fn interpreted_doc_round_trips_json() {
    let original = sample_interpreted();
    let json = serde_json::to_string(&original).unwrap();
    let back: InterpretedDoc = serde_json::from_str(&json).unwrap();
    assert_eq!(original, back);
}

#[test]
fn domain_body_carries_discriminator() {
    let body = DomainBody::Source(Box::new(sample_source()));
    let json = serde_json::to_string(&body).unwrap();
    assert!(
        json.contains("\"kind\":\"source\""),
        "expected `kind: source` tag, got: {json}"
    );
    let back: DomainBody = serde_json::from_str(&json).unwrap();
    assert_eq!(body, back);
}

#[test]
fn domain_body_variant_names() {
    assert_eq!(DomainBody::Source(Box::new(sample_source())).variant_name(), "source");
    assert_eq!(DomainBody::Prompt(Box::new(sample_prompt())).variant_name(), "prompt");
    assert_eq!(DomainBody::Model(Box::new(sample_model_response())).variant_name(), "model");
    assert_eq!(DomainBody::Interpreted(Box::new(sample_interpreted())).variant_name(), "interpreted");
}

#[test]
fn response_content_inline_tag() {
    let c = ResponseContent::Inline { text: "hi".into() };
    let json = serde_json::to_string(&c).unwrap();
    assert!(
        json.contains("\"storage\":\"inline\""),
        "expected `storage: inline` tag, got: {json}"
    );
    let back: ResponseContent = serde_json::from_str(&json).unwrap();
    assert_eq!(c, back);
    assert_eq!(c.text(), "hi");
}

#[test]
fn record_of_domain_body_round_trips() {
    // Sanity: ovp-core's Record<B> composes with DomainBody.
    use ovp_core::{Record, RecordId, RecordMeta, RunId};

    let rec = Record::new(
        RecordId::new("r-1"),
        DomainBody::Source(Box::new(sample_source())),
        RecordMeta { run_id: RunId::new("run-1"), seq: 0 },
    );
    let json = serde_json::to_string(&rec).unwrap();
    let back: Record<DomainBody> = serde_json::from_str(&json).unwrap();
    assert_eq!(rec, back);
}
