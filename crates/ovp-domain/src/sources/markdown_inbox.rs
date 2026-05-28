use std::path::{Path, PathBuf};

use ovp_core::{
    FilterError, Record, RecordId, RecordMeta, RunId, Source, SourceOutput, StepId,
};
use serde::Deserialize;

use crate::body::DomainBody;
use crate::source_doc::SourceDoc;

/// Reads a single Obsidian-style markdown clipping (YAML frontmatter +
/// body) from disk and emits it as one `Record<DomainBody::Source(...)>`.
///
/// v1 only reads one file per run. A directory-scanning variant comes
/// when bulk processing becomes a real need.
pub struct MarkdownInboxSource {
    step: StepId,
    run_id: RunId,
    input_path: PathBuf,
    emitted: bool,
}

impl MarkdownInboxSource {
    pub fn new(
        step: impl Into<String>,
        run_id: RunId,
        input_path: impl Into<PathBuf>,
    ) -> Self {
        Self {
            step: StepId::new(step.into()),
            run_id,
            input_path: input_path.into(),
            emitted: false,
        }
    }

    fn read_and_parse(path: &Path) -> Result<SourceDoc, FilterError> {
        let raw = std::fs::read_to_string(path).map_err(|e| {
            FilterError::new(
                "source.markdown_inbox.io",
                format!("read {}: {e}", path.display()),
            )
        })?;
        parse_clipping(&raw).map_err(|e| {
            FilterError::new(
                "source.markdown_inbox.parse",
                format!("{}: {}", path.display(), e),
            )
        })
    }
}

impl Source<DomainBody> for MarkdownInboxSource {
    fn step_id(&self) -> &StepId { &self.step }

    fn produce(&mut self) -> SourceOutput<DomainBody> {
        if self.emitted {
            return SourceOutput::Exhausted;
        }
        self.emitted = true;

        match Self::read_and_parse(&self.input_path) {
            Ok(source_doc) => {
                let record_id = source_doc_record_id(&self.input_path);
                let rec = Record::new(
                    RecordId::new(record_id),
                    DomainBody::Source(Box::new(source_doc)),
                    RecordMeta { run_id: self.run_id.clone(), seq: 0 },
                )
                .with_step(self.step.clone(), "ingested");
                SourceOutput::Records(vec![rec])
            }
            Err(e) => SourceOutput::Error(e),
        }
    }
}

fn source_doc_record_id(path: &Path) -> String {
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");
    format!("src-{stem}")
}

/// Frontmatter shape we accept from Obsidian clippings. Matches what
/// `fixtures/article_clean/input.md` actually carries. Extra fields are
/// ignored (serde default behavior) so author/tags being absent is fine.
#[derive(Debug, Default, Deserialize)]
struct ClippingFrontmatter {
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    source: Option<String>,
    /// Author can be a string OR a list (e.g. `[ "[[Marcus Moretti]]" ]`).
    /// We accept both via the helper below.
    #[serde(default)]
    author: Option<AuthorField>,
    #[serde(default)]
    published: Option<String>,
    #[serde(default)]
    tags: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum AuthorField {
    One(String),
    Many(Vec<String>),
}

impl AuthorField {
    /// Normalize to a single string. Strips Obsidian wikilink brackets
    /// (`[[Marcus Moretti]]` → `Marcus Moretti`) — legacy clippings
    /// often wrap authors in them.
    fn into_string(self) -> String {
        let raw = match self {
            AuthorField::One(s) => s,
            AuthorField::Many(v) => v.join(", "),
        };
        strip_wikilink(&raw)
    }
}

fn strip_wikilink(s: &str) -> String {
    let t = s.trim();
    if t.starts_with("[[") && t.ends_with("]]") && t.len() >= 4 {
        t[2..t.len() - 2].to_string()
    } else {
        t.to_string()
    }
}

/// Split a clipping into (frontmatter_yaml, body_markdown). Frontmatter
/// is delimited by `---\n` at the start and a second `---\n` somewhere
/// later. If there's no frontmatter the entire file is the body.
fn split_frontmatter(raw: &str) -> (Option<&str>, &str) {
    let trimmed = raw.trim_start_matches('\u{feff}'); // strip BOM if present
    if let Some(rest) = trimmed.strip_prefix("---\n") {
        if let Some(end_idx) = rest.find("\n---\n") {
            let fm = &rest[..end_idx];
            let body = &rest[end_idx + "\n---\n".len()..];
            return (Some(fm), body);
        }
        // Also accept `\n---` at EOF (no trailing newline)
        if let Some(end_idx) = rest.find("\n---") {
            let fm = &rest[..end_idx];
            let body = rest[end_idx + "\n---".len()..].trim_start_matches('\n');
            return (Some(fm), body);
        }
    }
    (None, trimmed)
}

pub(crate) fn parse_clipping(raw: &str) -> Result<SourceDoc, String> {
    let (fm_str, body) = split_frontmatter(raw);
    let fm: ClippingFrontmatter = match fm_str {
        Some(s) => serde_yaml::from_str(s).map_err(|e| format!("yaml: {e}"))?,
        None => ClippingFrontmatter::default(),
    };
    let title = fm.title.unwrap_or_else(|| "Untitled".to_string());
    let source_url = fm.source.unwrap_or_default();
    let author = fm.author.map(|a| a.into_string());
    Ok(SourceDoc {
        title,
        source_url,
        author,
        published: fm.published,
        tags: fm.tags,
        body_markdown: body.to_string(),
    })
}

