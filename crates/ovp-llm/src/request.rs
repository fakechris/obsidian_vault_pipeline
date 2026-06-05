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
}
