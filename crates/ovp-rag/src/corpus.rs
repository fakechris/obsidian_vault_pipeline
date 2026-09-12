//! The read-only retrieval corpus, loaded from a `KnowledgeView`.

use std::io::ErrorKind;
use std::path::Path;

use ovp_query::KnowledgeView;
use serde::{Deserialize, Serialize};

use crate::RagError;

/// Granular child unit sliced from an evergreen note body for high-precision
/// lexical and semantic retrieval. Mapped back to its parent [`ConceptDoc`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChildUnit {
    /// Deterministic unit identifier: `{parent_slug}#u{index}`
    pub unit_id: String,
    /// Slug of the parent ConceptDoc
    pub parent_slug: String,
    /// 0-indexed position within the parent doc
    pub index: usize,
    /// Nearest enclosing section heading, if any (e.g. "Architecture Overview")
    pub heading: Option<String>,
    /// Slice of content text for this unit
    pub content: String,
    /// Character start offset in parent note body
    pub start_char: usize,
    /// Character end offset in parent note body
    pub end_char: usize,
}

/// One retrievable unit: a canonical concept plus the notes that backlink it,
/// its body text, and its sliced child units. A read-only snapshot,
/// rebuildable from the `KnowledgeView` it came from — it holds no authority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConceptDoc {
    pub slug: String,
    pub title: String,
    pub evergreen_path: String,
    pub provenance_source_url: String,
    /// Vault-relative paths of notes that wikilink this concept (from the index).
    pub backlinks: Vec<String>,
    /// The evergreen note's text, if the file exists on disk. `None` when the
    /// note is absent (a concept can legitimately precede its note).
    pub body: Option<String>,
    /// Granular child units sliced from `body` for precision retrieval and citation.
    pub child_units: Vec<ChildUnit>,
}

impl ConceptDoc {
    pub fn new(
        slug: impl Into<String>,
        title: impl Into<String>,
        evergreen_path: impl Into<String>,
        provenance_source_url: impl Into<String>,
        backlinks: Vec<String>,
        body: Option<String>,
    ) -> Self {
        let slug = slug.into();
        let child_units = match body.as_deref() {
            Some(b) if !b.trim().is_empty() => chunk_note_body(&slug, b),
            _ => Vec::new(),
        };
        Self {
            slug,
            title: title.into(),
            evergreen_path: evergreen_path.into(),
            provenance_source_url: provenance_source_url.into(),
            backlinks,
            body,
            child_units,
        }
    }
}

/// Slices a markdown note body into bounded child units along headings (`# Heading`)
/// and paragraph boundaries (`\n\n`), tracking section headings and character offsets.
pub fn chunk_note_body(parent_slug: &str, body: &str) -> Vec<ChildUnit> {
    if body.trim().is_empty() {
        return Vec::new();
    }

    let mut units = Vec::new();
    let mut current_heading: Option<String> = None;
    let mut current_buf = String::new();
    let mut chunk_start_char = 0;
    let mut char_pos = 0;

    for line in body.split_inclusive('\n') {
        let trimmed_line = line.trim();
        let is_heading = trimmed_line.starts_with('#') && {
            let hashes = trimmed_line.chars().take_while(|&c| c == '#').count();
            (1..=6).contains(&hashes) && trimmed_line[hashes..].starts_with(' ')
        };

        if is_heading {
            let trimmed_buf = current_buf.trim();
            if !trimmed_buf.is_empty() {
                let unit_id = format!("{}#u{}", parent_slug, units.len());
                units.push(ChildUnit {
                    unit_id,
                    parent_slug: parent_slug.to_string(),
                    index: units.len(),
                    heading: current_heading.clone(),
                    content: trimmed_buf.to_string(),
                    start_char: chunk_start_char,
                    end_char: chunk_start_char + current_buf.chars().count(),
                });
                current_buf.clear();
            }

            let hashes = trimmed_line.chars().take_while(|&c| c == '#').count();
            let heading_text = trimmed_line[hashes..].trim().to_string();
            current_heading = Some(heading_text);
            chunk_start_char = char_pos + line.chars().count();
        } else if trimmed_line.is_empty() {
            let trimmed_buf = current_buf.trim();
            if trimmed_buf.chars().count() >= 80 {
                let unit_id = format!("{}#u{}", parent_slug, units.len());
                units.push(ChildUnit {
                    unit_id,
                    parent_slug: parent_slug.to_string(),
                    index: units.len(),
                    heading: current_heading.clone(),
                    content: trimmed_buf.to_string(),
                    start_char: chunk_start_char,
                    end_char: chunk_start_char + current_buf.chars().count(),
                });
                current_buf.clear();
                chunk_start_char = char_pos + line.chars().count();
            } else if !current_buf.is_empty() {
                current_buf.push_str(line);
            } else {
                chunk_start_char = char_pos + line.chars().count();
            }
        } else {
            if current_buf.is_empty() {
                chunk_start_char = char_pos;
            }
            current_buf.push_str(line);
        }

        char_pos += line.chars().count();
    }

    let trimmed_buf = current_buf.trim();
    if !trimmed_buf.is_empty() {
        let unit_id = format!("{}#u{}", parent_slug, units.len());
        units.push(ChildUnit {
            unit_id,
            parent_slug: parent_slug.to_string(),
            index: units.len(),
            heading: current_heading,
            content: trimmed_buf.to_string(),
            start_char: chunk_start_char,
            end_char: chunk_start_char + current_buf.chars().count(),
        });
    }

    if units.is_empty() && !body.trim().is_empty() {
        units.push(ChildUnit {
            unit_id: format!("{}#u0", parent_slug),
            parent_slug: parent_slug.to_string(),
            index: 0,
            heading: None,
            content: body.trim().to_string(),
            start_char: 0,
            end_char: body.chars().count(),
        });
    }

    units
}

/// The read-only retrieval corpus: one [`ConceptDoc`] per canonical concept,
/// slug-sorted (the `KnowledgeView` is already sorted). Built from the L5 read
/// model — it never assembles, runs, applies, or writes.
#[derive(Debug, Clone)]
pub struct RagCorpus {
    docs: Vec<ConceptDoc>,
}

impl RagCorpus {
    /// Load the L5 read model from disk and build the corpus in one step.
    /// Fail-loud: a corrupt/unreadable read model is [`RagError::Load`].
    pub fn load(vault_root: &Path, canonical_root: &Path) -> Result<Self, RagError> {
        let view = KnowledgeView::load(vault_root, canonical_root).map_err(RagError::Load)?;
        Self::from_view(&view)
    }

    /// Build from an already-loaded `KnowledgeView`. Reads each concept's
    /// evergreen note body off `vault_root().join(evergreen_path)` (read-only —
    /// the same pattern `ovp-lint` uses to stat evergreen files). An *absent*
    /// note → `body: None`; an *unreadable* note (a non-`NotFound` I/O error) →
    /// [`RagError::Body`] — loud, never a silently empty corpus.
    pub fn from_view(view: &KnowledgeView) -> Result<Self, RagError> {
        let vault_root = view.vault_root();
        let mut docs = Vec::with_capacity(view.concepts().len());
        for c in view.concepts() {
            let body = read_body(vault_root, &c.evergreen_path)?;
            docs.push(ConceptDoc::new(
                c.slug.clone(),
                c.title.clone(),
                c.evergreen_path.clone(),
                c.provenance_source_url.clone(),
                view.backlinks(&c.slug).to_vec(),
                body,
            ));
        }
        Ok(Self { docs })
    }

    /// All concept docs, slug-sorted.
    pub fn docs(&self) -> &[ConceptDoc] {
        &self.docs
    }

    pub fn len(&self) -> usize {
        self.docs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.docs.is_empty()
    }

    /// Look up a doc by exact slug.
    pub fn get(&self, slug: &str) -> Option<&ConceptDoc> {
        self.docs.iter().find(|d| d.slug == slug)
    }

    /// Build a corpus directly from docs (test helper — production builds go
    /// through [`RagCorpus::from_view`]).
    #[cfg(test)]
    pub(crate) fn from_docs(docs: Vec<ConceptDoc>) -> Self {
        Self { docs }
    }
}

/// Read a vault-relative note body: `Some` if present, `None` if absent
/// (`NotFound`), and a loud [`RagError::Body`] on any other I/O error.
fn read_body(vault_root: &Path, rel: &str) -> Result<Option<String>, RagError> {
    match std::fs::read_to_string(vault_root.join(rel)) {
        Ok(s) => Ok(Some(s)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(RagError::Body(format!("evergreen note `{rel}`: {e}"))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(slug: &str) -> ConceptDoc {
        ConceptDoc::new(
            slug,
            slug.to_uppercase(),
            format!("10-Knowledge/Evergreen/{slug}.md"),
            "https://example.com",
            vec![],
            None,
        )
    }

    #[test]
    fn get_len_is_empty() {
        let corpus = RagCorpus::from_docs(vec![doc("ai-agent"), doc("rag")]);
        assert_eq!(corpus.len(), 2);
        assert!(!corpus.is_empty());
        assert_eq!(corpus.get("rag").unwrap().slug, "rag");
        assert!(corpus.get("missing").is_none());
        assert!(RagCorpus::from_docs(vec![]).is_empty());
    }

    #[test]
    fn chunk_note_body_slices_headings_and_paragraphs() {
        let markdown = r#"# Retrieval Architecture
RAG combines external non-parametric retrieval with parametric language model generation.

## Slicing Strategy
Parent-child units index small chunks for scoring and map to parent concepts for context.

## Evaluation
Offline evaluation measures recall at k across canonical knowledge benchmarks.
"#;
        let units = chunk_note_body("rag-arch", markdown);
        assert_eq!(units.len(), 3);
        assert_eq!(units[0].unit_id, "rag-arch#u0");
        assert_eq!(units[0].parent_slug, "rag-arch");
        assert_eq!(units[0].heading.as_deref(), Some("Retrieval Architecture"));
        assert!(units[0].content.contains("non-parametric"));

        assert_eq!(units[1].unit_id, "rag-arch#u1");
        assert_eq!(units[1].heading.as_deref(), Some("Slicing Strategy"));
        assert!(units[1].content.contains("Parent-child"));

        assert_eq!(units[2].unit_id, "rag-arch#u2");
        assert_eq!(units[2].heading.as_deref(), Some("Evaluation"));
        assert!(units[2].content.contains("Offline evaluation"));
    }

    #[test]
    fn chunk_note_body_empty_returns_empty() {
        assert!(chunk_note_body("slug", "").is_empty());
        assert!(chunk_note_body("slug", "   \n\n  ").is_empty());
    }

    #[test]
    fn chunk_note_body_single_paragraph() {
        let text = "Simple single paragraph note without any markdown headings.";
        let units = chunk_note_body("simple", text);
        assert_eq!(units.len(), 1);
        assert_eq!(units[0].unit_id, "simple#u0");
        assert_eq!(units[0].heading, None);
        assert_eq!(units[0].content, text);
    }

    #[test]
    fn read_body_absent_is_none_not_error() {
        let tmp = tempfile::tempdir().unwrap();
        // The note file does not exist → None, not an error.
        let body = read_body(tmp.path(), "10-Knowledge/Evergreen/ghost.md").unwrap();
        assert!(body.is_none());
    }

    #[test]
    fn read_body_present_returns_text() {
        let tmp = tempfile::tempdir().unwrap();
        let rel = "note.md";
        std::fs::write(tmp.path().join(rel), "hello body").unwrap();
        assert_eq!(read_body(tmp.path(), rel).unwrap().as_deref(), Some("hello body"));
    }

    #[test]
    fn read_body_unreadable_is_loud() {
        // The path exists but is a directory, so read_to_string errors with a
        // non-NotFound kind → loud RagError::Body, never silent None.
        let tmp = tempfile::tempdir().unwrap();
        let rel = "is-a-dir.md";
        std::fs::create_dir(tmp.path().join(rel)).unwrap();
        let err = read_body(tmp.path(), rel).unwrap_err();
        assert!(matches!(err, RagError::Body(_)), "got {err:?}");
    }
}
