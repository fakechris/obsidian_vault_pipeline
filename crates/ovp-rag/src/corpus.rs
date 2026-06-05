//! The read-only retrieval corpus, loaded from a `KnowledgeView`.

use std::io::ErrorKind;
use std::path::Path;

use ovp_query::KnowledgeView;

use crate::RagError;

/// One retrievable unit: a canonical concept plus the notes that backlink it and
/// (when the evergreen note exists on disk) its body text. A read-only snapshot,
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
            docs.push(ConceptDoc {
                slug: c.slug.clone(),
                title: c.title.clone(),
                evergreen_path: c.evergreen_path.clone(),
                provenance_source_url: c.provenance_source_url.clone(),
                backlinks: view.backlinks(&c.slug).to_vec(),
                body: read_body(vault_root, &c.evergreen_path)?,
            });
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
        ConceptDoc {
            slug: slug.into(),
            title: slug.to_uppercase(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "https://example.com".into(),
            backlinks: vec![],
            body: None,
        }
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
