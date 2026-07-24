use serde::{Deserialize, Serialize};

/// Provider-neutral chat request. `ovp-domain` constructs these from
/// `PromptRequest` and hands them to a `ModelClient`. Providers map this
/// onto their wire format internally.
///
/// Does not derive `Eq` because `temperature` is `f32`. Use the request's
/// SHA-256 (via `request_key`) for identity in caches and tests.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelRequest {
    pub model: String,
    pub system: Option<String>,
    pub messages: Vec<ModelMessage>,
    pub max_tokens: u32,
    pub temperature: Option<f32>,
    /// Provider-neutral tool definitions. This field is absent from legacy
    /// request JSON when no tools are supplied, preserving cassette keys.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<ToolDef>>,
    /// Per-request cache namespace hint (e.g. `article_interpret/v1`).
    /// A deliberate, provider-neutral hint at the request boundary so one
    /// `CachedModelClient` can file article vs. paper cassettes under the
    /// right prompt namespace. `#[serde(skip)]` so it is NOT part of the
    /// request hash — cassette keys stay stable, the namespace only
    /// chooses the directory. Providers (Anthropic) ignore it entirely.
    #[serde(skip)]
    pub cache_namespace: Option<String>,
}

impl ModelRequest {
    /// Set the per-request cache namespace hint (builder style).
    pub fn with_cache_namespace(mut self, namespace: impl Into<String>) -> Self {
        self.cache_namespace = Some(namespace.into());
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "role", rename_all = "snake_case")]
pub enum ModelMessage {
    User { content: String },
    Assistant { content: String },
    AssistantBlocks {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        blocks: Vec<AssistantBlock>,
    },
    /// A complete tool-result turn. Since this variant cannot carry trailing
    /// text, every result maps to the front of the next Anthropic user message
    /// (`tool_result_adjacency_all`).
    ToolResults {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        results: Vec<ToolResultBlock>,
    },
}

/// Provider-neutral tool definition.
///
/// Only `(name, version)` enters `ModelRequest` serialization and therefore
/// `request_key`. Provider-facing documentation and schemas remain available
/// in memory for wire mapping without invalidating cassettes when edited.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub version: String,
    #[serde(skip)]
    pub description: String,
    #[serde(skip)]
    pub input_schema: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AssistantBlock {
    Text {
        text: String,
    },
    ToolUse {
        id: String,
        name: String,
        input: serde_json::Value,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolResultBlock {
    pub tool_call_id: String,
    pub content: String,
    pub is_error: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_less_request_serialization_is_byte_identical_to_legacy_json() {
        let request = ModelRequest {
            model: "test".into(),
            system: None,
            messages: vec![ModelMessage::User { content: "hi".into() }],
            max_tokens: 100,
            temperature: None,
            tools: None,
            cache_namespace: None,
        };

        assert_eq!(
            serde_json::to_string(&request).unwrap(),
            r#"{"model":"test","system":null,"messages":[{"role":"user","content":"hi"}],"max_tokens":100,"temperature":null}"#
        );
    }
}
