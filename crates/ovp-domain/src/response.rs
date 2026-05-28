use serde::{Deserialize, Serialize};

use crate::prompt::PromptId;
use crate::source_doc::SourceDoc;

/// Domain view of a ModelClient reply. LLMInvoker constructs this from
/// `ovp_llm::ModelReply`. Preserves prompt provenance (`prompt_id`,
/// `schema_version`) so ArticleParser can validate compatibility.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelResponse {
    pub prompt_id: PromptId,
    pub schema_version: u32,
    pub model: String,
    pub content: ResponseContent,
    pub input_tokens: u32,
    pub output_tokens: u32,
    /// Forwarded by LLMInvoker from the upstream PromptRequest. Lets
    /// ArticleParser populate `source_url`, `author`, area, etc. without
    /// asking the LLM to echo them back.
    pub origin: Box<SourceDoc>,
}

/// Where the response body lives. v1 keeps everything inline. Future
/// `Stored(ResponseId)` variant will let large responses move into a
/// content-addressable side store without breaking this enum's API.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "storage", rename_all = "snake_case")]
pub enum ResponseContent {
    Inline { text: String },
}

impl ResponseContent {
    pub fn text(&self) -> &str {
        match self {
            ResponseContent::Inline { text } => text,
        }
    }
}
