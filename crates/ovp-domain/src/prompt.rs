use serde::{Deserialize, Serialize};

/// Identifier of a versioned prompt asset. Stable string like
/// `article_interpret/v1`. ArticleParser uses this to refuse responses
/// whose schema doesn't match what it knows how to parse.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PromptId(pub String);

impl PromptId {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }
    pub fn as_str(&self) -> &str { &self.0 }
}

/// What PromptBuilder produces. Pipeline-internal type — the LLM-wire
/// shape (`ovp_llm::ModelRequest`) is constructed by LLMInvoker.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PromptRequest {
    pub prompt_id: PromptId,
    /// Schema version of the prompt asset that produced this request.
    /// Parser uses this to detect drift; mismatched versions drop with
    /// `transform.article_parser.schema_mismatch`.
    pub schema_version: u32,
    pub model: String,
    pub system: String,
    pub user: String,
    pub max_tokens: u32,
}
