use serde::{Deserialize, Serialize};

/// Raw clipped source as it sits in the inbox. Frontmatter fields are
/// hoisted out as typed fields; the body is the remaining markdown.
///
/// `kind` is the typed discriminator `RouteBySourceKind` dispatches on.
/// Article sources carry no extra metadata; papers carry `PaperMeta`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceDoc {
    pub title: String,
    pub source_url: String,
    pub author: Option<String>,
    /// ISO 8601 date string from the source. Not parsed.
    pub published: Option<String>,
    pub tags: Vec<String>,
    pub body_markdown: String,
    /// Number of source-file lines BEFORE `body_markdown` begins (the YAML
    /// frontmatter block + its `---` delimiters). M19: lets evidence line
    /// numbers be reported FILE-relative (what a reader sees opening the file)
    /// instead of body-relative. `#[serde(default)]` = 0 for older records and
    /// frontmatter-less bodies.
    #[serde(default)]
    pub body_line_offset: usize,
    /// What kind of source this is. Defaults to `Article`. Named
    /// `source_kind` (not `kind`) to avoid colliding with `DomainBody`'s
    /// internal `kind` tag when a `Source` body is serialized.
    #[serde(default)]
    pub source_kind: SourceKind,
}

impl SourceDoc {
    /// Construct an article-kind SourceDoc (the common case; keeps test
    /// and call sites terse now that `kind` exists).
    pub fn article(
        title: impl Into<String>,
        source_url: impl Into<String>,
        author: Option<String>,
        published: Option<String>,
        tags: Vec<String>,
        body_markdown: impl Into<String>,
    ) -> Self {
        Self {
            title: title.into(),
            source_url: source_url.into(),
            author,
            published,
            tags,
            body_markdown: body_markdown.into(),
            body_line_offset: 0,
            source_kind: SourceKind::Article,
        }
    }
}

/// The typed source-kind discriminator. A sum type over named structs,
/// not a bag of optional fields — invariant #3. New kinds (github, ...)
/// add variants; routing matches exhaustively.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SourceKind {
    #[default]
    Article,
    Paper(PaperMeta),
}

impl SourceKind {
    pub fn name(&self) -> &'static str {
        match self {
            SourceKind::Article => "article",
            SourceKind::Paper(_) => "paper",
        }
    }
}

/// Paper-specific frontmatter carried by `SourceKind::Paper`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperMeta {
    pub arxiv_id: String,
    pub authors: Vec<String>,
    pub categories: Vec<String>,
    /// ISO 8601 publication date, if known.
    pub published: Option<String>,
}
