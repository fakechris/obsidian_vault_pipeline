use serde::{Deserialize, Serialize};

/// Provider-neutral chat reply. `ovp-domain::LLMInvoker` maps this into
/// the domain's `ModelResponse` body. Token counts let downstream stages
/// log cost without re-counting.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelReply {
    pub model: String,
    pub text: String,
    pub stop_reason: StopReason,
    pub usage: Usage,
    /// Provider content blocks in their original order. Absent in legacy
    /// cassette replies, where `text` remains the compatibility surface.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub blocks: Option<Vec<ReplyBlock>>,
    /// Verbatim provider stop reason when it is not recognized. This
    /// diagnostic is deliberately not part of persisted cassette JSON.
    #[serde(default, skip)]
    pub raw_stop_reason: Option<String>,
}

impl ModelReply {
    /// Return provider-issued tool calls only when the turn explicitly stopped
    /// for tool use. In particular, `MaxTokens` makes every included call
    /// non-executable (`max_tokens_mid_tool`); raising the budget and retrying
    /// is the caller's decision.
    pub fn executable_tool_calls(&self) -> Option<Vec<ExecutableToolCall<'_>>> {
        if self.stop_reason != StopReason::ToolUse {
            return None;
        }

        let calls: Vec<_> = self
            .blocks
            .as_deref()
            .unwrap_or_default()
            .iter()
            .filter_map(|block| match block {
                ReplyBlock::ToolUse { id, name, input } => {
                    Some(ExecutableToolCall { id, name, input })
                }
                ReplyBlock::Text { .. } => None,
            })
            .collect();
        (!calls.is_empty()).then_some(calls)
    }

    /// Whether this reply represents a completed successful answer.
    pub fn is_final_success(&self) -> bool {
        self.stop_reason.is_final_success()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    EndTurn,
    MaxTokens,
    StopSequence,
    ToolUse,
    Refusal,
    Unknown,
}

impl StopReason {
    /// Unknown, truncated, refused, and tool-intermediate turns are never final
    /// successes. This keeps future provider stop strings fail-closed.
    pub fn is_final_success(&self) -> bool {
        matches!(self, Self::EndTurn | Self::StopSequence)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ReplyBlock {
    Text {
        text: String,
    },
    ToolUse {
        id: String,
        name: String,
        input: serde_json::Value,
    },
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ExecutableToolCall<'a> {
    pub id: &'a str,
    pub name: &'a str,
    pub input: &'a serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_cassette_reply_without_protocol_fields_still_deserializes() {
        let raw = r#"{"model":"m","text":"done","stop_reason":"unknown","usage":{"input_tokens":1,"output_tokens":2}}"#;
        let reply: ModelReply = serde_json::from_str(raw).unwrap();

        assert_eq!(reply.stop_reason, StopReason::Unknown);
        assert_eq!(reply.blocks, None);
        assert_eq!(reply.raw_stop_reason, None);
        assert!(!reply.is_final_success());
    }
}
