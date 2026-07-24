use ovp_core::{
    DropReason, EffectfulTransform, FilterDecision, FilterError, Record, StepId,
};
use ovp_llm::{
    CallError, ModelClient, ModelMessage, ModelRequest, StopReason,
};

use crate::body::DomainBody;
use crate::response::{ModelResponse, ResponseContent};

/// EffectfulTransform that calls a `ModelClient` to turn a `PromptRequest`
/// into a `ModelResponse`. The only node in the article pipeline that
/// touches I/O, hence the `EffectfulTransform` trait identity instead of
/// `Transform`.
///
/// CallError → FilterError translation happens here, at the I/O boundary;
/// the pipeline downstream of LLMInvoker only ever sees pipeline-native
/// error types.
pub struct LLMInvoker {
    step: StepId,
    client: Box<dyn ModelClient>,
}

impl LLMInvoker {
    pub fn new(step: impl Into<String>, client: Box<dyn ModelClient>) -> Self {
        Self {
            step: StepId::new(step.into()),
            client,
        }
    }
}

impl EffectfulTransform<DomainBody> for LLMInvoker {
    fn step_id(&self) -> &StepId { &self.step }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let prompt = match record.body {
            DomainBody::Prompt(p) => *p,
            other => {
                return FilterDecision::Drop(DropReason::new(
                    "transform.llm_invoker.wrong_variant",
                    format!("expected Prompt, got {}", other.variant_name()),
                ));
            }
        };

        // Tag the request with its prompt namespace so a shared cache
        // (one CachedModelClient behind a unified pipeline's single
        // LLMInvoker) files this cassette under the right prompt dir
        // (e.g. article_interpret/v1 vs paper_interpret/v1).
        let wire_request = ModelRequest {
            model: prompt.model.clone(),
            system: Some(prompt.system.clone()),
            messages: vec![ModelMessage::User { content: prompt.user.clone() }],
            max_tokens: prompt.max_tokens,
            temperature: None,
            tools: None,
            cache_namespace: Some(prompt.prompt_id.as_str().to_string()),
        };

        let reply = match self.client.call(&wire_request) {
            Ok(r) => r,
            Err(err) => return FilterDecision::Error(call_error_to_filter_error(err)),
        };

        let response = ModelResponse {
            prompt_id: prompt.prompt_id.clone(),
            schema_version: prompt.schema_version,
            model: reply.model,
            content: ResponseContent::Inline { text: reply.text },
            input_tokens: reply.usage.input_tokens,
            output_tokens: reply.usage.output_tokens,
            origin: prompt.origin,
        };

        // We don't surface stop_reason on the body in v1, but we drop on
        // MaxTokens because a truncated response will fail the parser
        // downstream — clearer to drop with a specific reason here.
        if matches!(reply.stop_reason, StopReason::MaxTokens) {
            return FilterDecision::Drop(DropReason::new(
                "transform.llm_invoker.max_tokens",
                "model hit max_tokens before completing the response",
            ));
        }

        let next = Record {
            id: record.id,
            body: DomainBody::Model(Box::new(response)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), "llm response received");
        FilterDecision::Forward(vec![next])
    }
}

fn call_error_to_filter_error(err: CallError) -> FilterError {
    let (code, detail) = match err {
        CallError::CacheMiss { key } => ("transform.llm_invoker.cache_miss", format!("key={key}")),
        CallError::Provider { code, detail } => (
            "transform.llm_invoker.provider",
            format!("{code}: {detail}"),
        ),
        CallError::Transport { detail } => ("transform.llm_invoker.transport", detail),
        CallError::Decode { detail } => ("transform.llm_invoker.decode", detail),
        CallError::BudgetExhausted { detail } => ("transform.llm_invoker.budget_exhausted", detail),
        CallError::Unexpected { detail } => ("transform.llm_invoker.unexpected", detail),
    };
    FilterError::new(code, detail)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_core::{RecordId, RecordMeta, RunId};
    use ovp_llm::{FixtureModelClient, ModelReply, Usage};

    use crate::prompt::{PromptId, PromptRequest};
    use crate::source_doc::SourceDoc;

    fn source() -> SourceDoc {
        SourceDoc::article("T", "https://x.example/a", None, None, vec![], "body")
    }

    fn prompt_record() -> Record<DomainBody> {
        let prompt = PromptRequest {
            prompt_id: PromptId::new("article_interpret/v1"),
            schema_version: 1,
            model: "fake-model".into(),
            system: "system".into(),
            user: "user".into(),
            max_tokens: 100,
            origin: Box::new(source()),
        };
        Record::new(
            RecordId::new("r-1"),
            DomainBody::Prompt(Box::new(prompt)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    fn wire_reply(text: &str, stop: StopReason) -> ModelReply {
        ModelReply {
            model: "fake-model".into(),
            text: text.into(),
            stop_reason: stop,
            usage: Usage { input_tokens: 10, output_tokens: 20 },
            blocks: None,
            raw_stop_reason: None,
        }
    }

    /// Pre-register a fixture reply for whatever ModelRequest LLMInvoker
    /// will produce from the prompt record above.
    fn fixture_with_reply(reply: ModelReply) -> FixtureModelClient {
        let mut f = FixtureModelClient::new();
        let req = ModelRequest {
            model: "fake-model".into(),
            system: Some("system".into()),
            messages: vec![ModelMessage::User { content: "user".into() }],
            max_tokens: 100,
            temperature: None,
            tools: None,
            cache_namespace: None,
        };
        f.insert(&req, reply);
        f
    }

    #[test]
    fn happy_path_produces_model_response() {
        let client = fixture_with_reply(wire_reply("{json:body}", StopReason::EndTurn));
        let mut invoker = LLMInvoker::new("llm_invoker", Box::new(client));

        let decision = invoker.process(prompt_record());
        let rs = match decision {
            FilterDecision::Forward(rs) => rs,
            other => panic!("expected Forward, got {other:?}"),
        };
        assert_eq!(rs.len(), 1);
        let body = match &rs[0].body {
            DomainBody::Model(m) => m,
            other => panic!("expected Model variant, got {}", other.variant_name()),
        };
        assert_eq!(body.content.text(), "{json:body}");
        assert_eq!(body.input_tokens, 10);
        assert_eq!(body.output_tokens, 20);
        assert_eq!(body.origin.source_url, "https://x.example/a");
    }

    #[test]
    fn wrong_variant_drops() {
        let client = fixture_with_reply(wire_reply("x", StopReason::EndTurn));
        let mut invoker = LLMInvoker::new("llm_invoker", Box::new(client));
        let rec = Record::new(
            RecordId::new("r-x"),
            DomainBody::Source(Box::new(source())),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match invoker.process(rec) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.llm_invoker.wrong_variant");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }

    #[test]
    fn cache_miss_errors() {
        let client = FixtureModelClient::new(); // empty
        let mut invoker = LLMInvoker::new("llm_invoker", Box::new(client));
        match invoker.process(prompt_record()) {
            FilterDecision::Error(e) => {
                assert_eq!(e.code.as_str(), "transform.llm_invoker.cache_miss");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn max_tokens_stop_drops_with_reason() {
        let client = fixture_with_reply(wire_reply("truncated", StopReason::MaxTokens));
        let mut invoker = LLMInvoker::new("llm_invoker", Box::new(client));
        match invoker.process(prompt_record()) {
            FilterDecision::Drop(reason) => {
                assert_eq!(reason.code.as_str(), "transform.llm_invoker.max_tokens");
            }
            other => panic!("expected Drop, got {other:?}"),
        }
    }
}
