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
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "role", rename_all = "snake_case")]
pub enum ModelMessage {
    User { content: String },
    Assistant { content: String },
}
