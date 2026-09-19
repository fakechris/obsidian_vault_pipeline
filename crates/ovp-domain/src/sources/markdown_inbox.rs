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
    /// The reader's own words about the source (a Pinboard `extended`
    /// note, a clipper comment). Reserved key: it is NOT source text and
    /// never reaches unit extraction. `note:` is accepted as an alias.
    #[serde(default, alias = "note")]
    annotation: Option<String>,
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

/// The reserved frontmatter keys holding the READER's own words (never the
/// source's). `annotation` is canonical; `note` is the accepted alias.
pub const ANNOTATION_KEYS: [&str; 2] = ["annotation", "note"];

/// Byte span of the reserved annotation entry — its `key:` line plus every
/// block-scalar continuation line — inside a note's LEADING frontmatter.
/// `None` when the note has no frontmatter or carries no annotation.
///
/// This exists because the annotation contract is not satisfied by keeping
/// the text out of the reader's prompt alone: any tool that hands a model the
/// RAW file (the agent's source-read and chunk-search tools do) would put the
/// reader's own opinion in front of the model as if it were source text, and
/// it could then be quoted back as evidence. Model-facing readers cut this
/// span; the parser, the index and the portal keep reading the key normally.
///
/// Continuation lines are recognized structurally, not by re-parsing YAML: a
/// key sits at column 0 inside this dialect, so any indented or empty line
/// after it belongs to its value. That keeps the cut correct for a multi-line
/// `|-` block without pulling a YAML parser into a byte-level scan.
pub fn annotation_span(raw: &str) -> Option<std::ops::Range<usize>> {
    let (fm_start, fm_len) = frontmatter_region(raw)?;
    let fm = &raw[fm_start..fm_start + fm_len];

    let mut offset = 0usize;
    let mut span: Option<std::ops::Range<usize>> = None;
    for line in fm.split_inclusive('\n') {
        let starts_at_col0 = !line.starts_with([' ', '\t']) && !line.trim().is_empty();
        if let Some(open) = &span {
            if starts_at_col0 {
                return Some(open.start..fm_start + offset);
            }
        } else if starts_at_col0 && is_annotation_key_line(line) {
            span = Some(fm_start + offset..fm_start + offset);
        }
        offset += line.len();
    }
    span.map(|open| open.start..fm_start + offset)
}

/// Does this frontmatter line open the reserved annotation key? Accepts the
/// plain and simply-quoted spellings (`annotation:`, `"annotation":`,
/// `'note':`).
///
/// KNOWN LIMIT: this is a scanner for the dialect this module WRITES —
/// block-style mapping, keys at column 0. serde_yaml accepts more than that
/// (a fully indented root mapping, a flow mapping `{annotation: x}`, a key
/// escaped as `"annotation"`), and those forms parse as an annotation
/// but are NOT matched here, so they would not be cut. Nothing in this repo
/// writes them; a hand-edited note could. Widening the scanner means running
/// a YAML parse inside a byte-level scan, which is a change worth making on
/// its own rather than smuggling into the key's introduction.
fn is_annotation_key_line(line: &str) -> bool {
    let Some((key, _)) = line.split_once(':') else {
        return false;
    };
    let key = key.trim();
    let key = key
        .strip_prefix('"')
        .and_then(|k| k.strip_suffix('"'))
        .or_else(|| key.strip_prefix('\'').and_then(|k| k.strip_suffix('\'')))
        .unwrap_or(key);
    ANNOTATION_KEYS.contains(&key)
}

/// `(start, len)` of the frontmatter YAML inside `raw` — the bytes between the
/// opening fence and the closing one, excluding both. Tolerates a BOM and
/// CRLF line endings. `None` when there is no leading fence.
///
/// A missing CLOSING fence still yields a region (everything after the opening
/// fence): the redaction contract must fail CLOSED, so a truncated or
/// malformed note hides its annotation rather than exposing it.
pub fn frontmatter_region(raw: &str) -> Option<(usize, usize)> {
    // Every offset this returns is a line boundary, so it is always a char
    // boundary too — safe to slice the original `&str` with.
    frontmatter_region_bytes(raw.as_bytes())
}

/// [`frontmatter_region`] over raw bytes, for callers that must locate the
/// frontmatter BEFORE deciding what is safe to decode as UTF-8 (a streaming
/// reader's buffer can end mid-character in the body, and decoding the whole
/// buffer just to find the fences would make that a redaction bypass).
pub fn frontmatter_region_bytes(raw: &[u8]) -> Option<(usize, usize)> {
    let bom = if raw.starts_with(b"\xef\xbb\xbf") { 3 } else { 0 };
    let after_bom = &raw[bom..];
    let open = if after_bom.starts_with(b"---\n") {
        4
    } else if after_bom.starts_with(b"---\r\n") {
        5
    } else {
        return None;
    };
    let start = bom + open;

    // The close must be a line that is EXACTLY `---`. Matching a bare `---`
    // prefix instead would let an ordinary key like `---metadata: value` end
    // the region early, and everything after it would escape the cut.
    let mut line_start = start;
    for i in start..raw.len() {
        if raw[i] != b'\n' {
            continue;
        }
        let mut line_end = i;
        if line_end > line_start && raw[line_end - 1] == b'\r' {
            line_end -= 1;
        }
        if &raw[line_start..line_end] == b"---" {
            return Some((start, line_start - start));
        }
        line_start = i + 1;
    }
    // A final line with no trailing newline can still be the closing fence.
    let mut line_end = raw.len();
    if line_end > line_start && raw[line_end - 1] == b'\r' {
        line_end -= 1;
    }
    if &raw[line_start..line_end] == b"---" {
        return Some((start, line_start - start));
    }
    // Never closed: fail CLOSED by treating the rest as frontmatter, so a
    // truncated note hides its annotation instead of exposing it.
    Some((start, raw.len() - start))
}

/// `raw` with the annotation entry removed, for the model-facing readers
/// described on [`annotation_span`]. Borrowed unchanged when there is none.
pub fn redact_annotation(raw: &str) -> std::borrow::Cow<'_, str> {
    match annotation_span(raw) {
        None => std::borrow::Cow::Borrowed(raw),
        Some(span) => {
            let mut out = String::with_capacity(raw.len() - span.len());
            out.push_str(&raw[..span.start]);
            out.push_str(&raw[span.end..]);
            std::borrow::Cow::Owned(out)
        }
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
    let annotation = fm
        .annotation
        .map(|a| a.trim().to_string())
        .filter(|a| !a.is_empty());
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
        annotation,
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
mod annotation_redaction_tests {
    use super::{parse_clipping, redact_annotation};

    fn note(annotation_block: &str) -> String {
        format!(
            "---\ntitle: \"T\"\nsource: \"https://e.x/p\"\n{annotation_block}tags:\n  - \"pinboard\"\n---\nThe source body.\n"
        )
    }

    #[test]
    fn cuts_a_multi_line_block_scalar_but_keeps_every_other_key() {
        let raw = note("annotation: |-\n  my own take\n\n  second para\n");
        assert_eq!(
            parse_clipping(&raw).unwrap().annotation.as_deref(),
            Some("my own take\n\nsecond para"),
            "the parser still reads it"
        );
        let cut = redact_annotation(&raw);
        assert!(!cut.contains("my own take"), "{cut}");
        assert!(!cut.contains("second para"), "{cut}");
        assert!(cut.contains("title: \"T\""));
        assert!(cut.contains("source: \"https://e.x/p\""));
        assert!(cut.contains("  - \"pinboard\""));
        assert!(cut.contains("The source body."));
        // Still a well-formed clipping after the cut, minus the annotation.
        let doc = parse_clipping(&cut).expect("redacted note still parses");
        assert_eq!(doc.annotation, None);
        assert_eq!(doc.title, "T");
        assert_eq!(doc.tags, vec!["pinboard".to_string()]);
        assert_eq!(doc.body_markdown.trim(), "The source body.");
    }

    #[test]
    fn cuts_the_note_alias_and_a_single_line_value() {
        let raw = note("note: a one-liner\n");
        assert_eq!(
            parse_clipping(&raw).unwrap().annotation.as_deref(),
            Some("a one-liner")
        );
        let cut = redact_annotation(&raw);
        assert!(!cut.contains("a one-liner"), "{cut}");
        assert_eq!(parse_clipping(&cut).unwrap().annotation, None);
    }

    #[test]
    fn leaves_notes_without_an_annotation_byte_identical() {
        let raw = note("");
        assert_eq!(redact_annotation(&raw), raw);
        assert_eq!(redact_annotation("no frontmatter at all\n"), "no frontmatter at all\n");
        // A key that merely starts with the reserved name is NOT the key.
        let other = note("annotation_source: pinboard\n");
        assert_eq!(redact_annotation(&other), other);
    }

    #[test]
    fn an_annotation_in_the_body_is_not_frontmatter_and_is_left_alone() {
        // Only the LEADING frontmatter is redacted; a line in the body that
        // happens to look like the key is source text and must survive.
        let raw = "---\ntitle: \"T\"\n---\nannotation: this is body prose\n";
        assert_eq!(redact_annotation(raw), raw);
    }

    /// serde_yaml accepts a quoted key, so the cut must too — otherwise the
    /// parser reads an annotation the redactor cannot see, and it leaks.
    #[test]
    fn cuts_quoted_annotation_keys() {
        for key in ["\"annotation\"", "'annotation'", "\"note\""] {
            let raw = note(&format!("{key}: SENTINEL-leak\n"));
            assert_eq!(
                parse_clipping(&raw).unwrap().annotation.as_deref(),
                Some("SENTINEL-leak"),
                "{key} parses as an annotation"
            );
            let cut = redact_annotation(&raw);
            assert!(!cut.contains("SENTINEL-leak"), "{key} leaked: {cut}");
        }
    }

    /// A CRLF note must not slip past the cut. (The clipping parser itself
    /// does not accept CRLF frontmatter at all — that gap is older and wider
    /// than this key; the redactor is deliberately the more permissive of the
    /// two, because the cost of over-cutting is nothing and the cost of
    /// under-cutting is the reader's own words quoted back as evidence.)
    #[test]
    fn cuts_an_annotation_in_crlf_frontmatter() {
        let raw = "---\r\ntitle: \"T\"\r\nannotation: SENTINEL-crlf\r\ntags:\r\n  - \"x\"\r\n---\r\nBody.\r\n";
        let cut = redact_annotation(raw);
        assert!(!cut.contains("SENTINEL-crlf"), "{cut}");
        assert!(cut.contains("title: \"T\""), "{cut}");
        assert!(cut.contains("Body."), "{cut}");
    }

    /// A key that merely STARTS with `---` is not the closing fence. Ending
    /// the region there would let everything after it escape the cut.
    #[test]
    fn a_key_starting_with_three_dashes_is_not_the_closing_fence() {
        let raw = "---\ntitle: \"T\"\n---metadata: value\nannotation: SENTINEL-after-false-fence\n---\nBody.\n";
        let cut = redact_annotation(raw);
        assert!(!cut.contains("SENTINEL-after-false-fence"), "{cut}");
        assert!(cut.contains("---metadata: value"), "{cut}");
        assert!(cut.contains("Body."), "{cut}");
    }

    /// Fail closed: an unterminated fence must hide the annotation, not
    /// expose it because the closing `---` never arrived.
    #[test]
    fn cuts_an_annotation_when_the_fence_never_closes() {
        let raw = "---\ntitle: \"T\"\nannotation: |-\n  SENTINEL-unterminated\n";
        let cut = redact_annotation(raw);
        assert!(!cut.contains("SENTINEL-unterminated"), "{cut}");
    }

    #[test]
    fn cuts_an_annotation_that_is_the_last_frontmatter_key() {
        let raw = "---\ntitle: \"T\"\nannotation: |-\n  trailing key\n---\nBody.\n";
        assert_eq!(
            parse_clipping(raw).unwrap().annotation.as_deref(),
            Some("trailing key")
        );
        let cut = redact_annotation(raw);
        assert!(!cut.contains("trailing key"), "{cut}");
        let doc = parse_clipping(&cut).expect("redacted note still parses");
        assert_eq!(doc.annotation, None);
        assert_eq!(doc.body_markdown.trim(), "Body.");
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

