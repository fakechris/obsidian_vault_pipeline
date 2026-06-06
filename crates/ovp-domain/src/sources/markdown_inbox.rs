use std::path::{Path, PathBuf};

use ovp_core::{
    FilterError, Record, RecordId, RecordMeta, RunId, Source, SourceOutput, StepId,
};
use serde::Deserialize;

use crate::body::DomainBody;
use crate::source_doc::{PaperMeta, SourceDoc, SourceKind};

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
        read_source_doc(path)
    }
}

/// Read a single clipping file from disk and parse it into a `SourceDoc`.
/// Shared by `MarkdownInboxSource` (single file) and `InboxScanSource`
/// (directory sweep) so both agree on IO + parse error codes.
pub(crate) fn read_source_doc(path: &Path) -> Result<SourceDoc, FilterError> {
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

/// Stable record id derived from a clipping file's stem (`src-<stem>`).
/// Shared so single-file and directory-sweep sources produce matching ids.
pub(crate) fn record_id_for(path: &Path) -> String {
    source_doc_record_id(path)
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
    // --- source-kind classification fields ---
    /// `arxiv-paper`, `github-project`, ... Absent → article.
    #[serde(default)]
    source_type: Option<String>,
    #[serde(default)]
    arxiv_id: Option<String>,
    #[serde(default)]
    source_authors: Vec<String>,
    /// Legacy emits this as a comma-joined string (`cs.IR, cs.AI`).
    #[serde(default)]
    arxiv_categories: Option<String>,
    #[serde(default)]
    source_published_at: Option<String>,
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
    let source_url = strip_tracker_params(&fm.source.unwrap_or_default());
    let author = fm.author.map(|a| a.into_string());
    let kind = classify_kind(
        fm.source_type.as_deref(),
        fm.arxiv_id,
        fm.source_authors,
        fm.arxiv_categories,
        fm.source_published_at,
    );
    // Lines before the body begin (frontmatter + `---` delimiters). `body` is a
    // suffix slice of `raw`, so the prefix is everything up to its start.
    let body_line_offset = raw[..raw.len() - body.len()].bytes().filter(|&b| b == b'\n').count();
    Ok(SourceDoc {
        title,
        source_url,
        author,
        published: fm.published,
        tags: fm.tags,
        body_markdown: body.to_string(),
        body_line_offset,
        source_kind: kind,
    })
}

/// Classify a clipping's `SourceKind` from its frontmatter. `arxiv-paper`
/// → `Paper`; everything else (absent, github, website, ...) → `Article`.
/// GitHub is intentionally not yet its own variant (terminal-raw routing
/// is a later stage); it falls through to `Article` for now.
fn classify_kind(
    source_type: Option<&str>,
    arxiv_id: Option<String>,
    source_authors: Vec<String>,
    arxiv_categories: Option<String>,
    source_published_at: Option<String>,
) -> SourceKind {
    match source_type {
        Some("arxiv-paper") => SourceKind::Paper(PaperMeta {
            arxiv_id: arxiv_id.unwrap_or_default(),
            authors: source_authors,
            categories: split_categories(arxiv_categories.as_deref()),
            published: source_published_at,
        }),
        _ => SourceKind::Article,
    }
}

/// Split a comma-separated category string (`"cs.IR, cs.AI"`) into a
/// trimmed list. Empty/absent → empty vec.
fn split_categories(s: Option<&str>) -> Vec<String> {
    match s {
        None => Vec::new(),
        Some(raw) => raw
            .split(',')
            .map(|c| c.trim().to_string())
            .filter(|c| !c.is_empty())
            .collect(),
    }
}

/// Strip common tracker query params (`source`, `utm_*`, `ref`, `ref_src`,
/// `fbclid`, `gclid`, `mc_cid`, `mc_eid`) while preserving substantive
/// ones. If everything is stripped, also drop the trailing `?`.
///
/// Deliberately minimal: no full URL parser dep. Trackers in real-world
/// clippings overwhelmingly follow `key=value&key=value` query strings
/// after a single `?`, and that's what this handles. Fragments are kept.
pub(crate) fn strip_tracker_params(url: &str) -> String {
    let (prefix, query, fragment) = split_url(url);
    if query.is_empty() {
        return url.to_string();
    }
    let kept: Vec<&str> = query
        .split('&')
        .filter(|pair| !is_tracker_param(pair))
        .collect();
    let mut out = prefix.to_string();
    if !kept.is_empty() {
        out.push('?');
        out.push_str(&kept.join("&"));
    }
    if !fragment.is_empty() {
        out.push('#');
        out.push_str(fragment);
    }
    out
}

fn split_url(url: &str) -> (&str, &str, &str) {
    let (path_part, fragment) = match url.split_once('#') {
        Some((p, f)) => (p, f),
        None => (url, ""),
    };
    let (prefix, query) = match path_part.split_once('?') {
        Some((p, q)) => (p, q),
        None => (path_part, ""),
    };
    (prefix, query, fragment)
}

fn is_tracker_param(pair: &str) -> bool {
    let key = pair.split('=').next().unwrap_or("");
    matches!(
        key,
        "source" | "ref" | "ref_src" | "fbclid" | "gclid" | "mc_cid" | "mc_eid"
    ) || key.starts_with("utm_")
}

#[cfg(test)]
mod clipping_tests {
    use super::parse_clipping;

    #[test]
    fn body_line_offset_counts_frontmatter_lines() {
        // 3 frontmatter lines + 2 `---` delimiters = body starts at file line 6
        // → 5 lines precede the body.
        let raw = "---\ntitle: T\nsource: https://e/x\nauthor: A\n---\nFirst body line.\n\nSecond.";
        let doc = parse_clipping(raw).unwrap();
        assert_eq!(doc.body_markdown, "First body line.\n\nSecond.");
        assert_eq!(doc.body_line_offset, 5);
    }

    #[test]
    fn body_line_offset_zero_without_frontmatter() {
        let doc = parse_clipping("Just a body, no frontmatter.\n\nMore.").unwrap();
        assert_eq!(doc.body_line_offset, 0);
    }
}

#[cfg(test)]
mod tracker_tests {
    use super::strip_tracker_params;

    #[test]
    fn strips_source_param() {
        assert_eq!(
            strip_tracker_params("https://every.to/guides/x?source=post_button"),
            "https://every.to/guides/x"
        );
    }

    #[test]
    fn strips_utm_params() {
        assert_eq!(
            strip_tracker_params("https://example.com/?utm_source=newsletter&utm_medium=email"),
            "https://example.com/"
        );
    }

    #[test]
    fn preserves_substantive_params() {
        assert_eq!(
            strip_tracker_params("https://example.com/?id=123&utm_source=x"),
            "https://example.com/?id=123"
        );
    }

    #[test]
    fn preserves_fragment() {
        assert_eq!(
            strip_tracker_params("https://example.com/page?source=x#section"),
            "https://example.com/page#section"
        );
    }

    #[test]
    fn idempotent_on_clean_url() {
        let clean = "https://example.com/path";
        assert_eq!(strip_tracker_params(clean), clean);
    }
}

