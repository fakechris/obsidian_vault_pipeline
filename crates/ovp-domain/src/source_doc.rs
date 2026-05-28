use serde::{Deserialize, Serialize};

/// Raw clipped article as it sits in the inbox. Frontmatter fields are
/// hoisted out as typed fields; the body is the remaining markdown.
///
/// v1 covers the article shape only. Paper / GitHub source kinds will
/// arrive as additional variants when those increments land.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceDoc {
    pub title: String,
    pub source_url: String,
    pub author: Option<String>,
    /// ISO 8601 date string from the source. Not parsed in v1.
    pub published: Option<String>,
    pub tags: Vec<String>,
    pub body_markdown: String,
}
