//! OVP Next L5 — the read layer (`KnowledgeView` + queries).
//!
//! A **read-only** surface over the canonical store (authority) + the derived
//! knowledge index (backlinks). It never mutates, never assembles a pipeline,
//! never runs one. `ovp-lint` builds its health checks on the same `KnowledgeView`.
//! See `docs/stage-read-health.md`.

use std::path::{Path, PathBuf};

use ovp_domain::{CanonicalConcept, CanonicalParseError, KnowledgeIndex, VaultLayout};
use ovp_stores::CanonicalFsStoreApplier;
use serde::Serialize;

/// Why loading a `KnowledgeView` failed. Fail-loud: a query over a corrupt store
/// is an error, never silently-empty results.
#[derive(Debug)]
pub enum QueryError {
    /// Reading the canonical store directory failed.
    CanonicalRead(String),
    /// A canonical record failed strict parse (bad payload / key≠slug / invalid
    /// slug / wrong evergreen_path).
    CanonicalParse(CanonicalParseError),
    /// The persisted knowledge index exists but could not be read (a non-
    /// `NotFound` I/O error — permission denied, transient failure, …). Distinct
    /// from "absent" (a missing index is fine; an unreadable one is not).
    IndexRead(String),
    /// The persisted knowledge index was read but did not parse.
    IndexParse(String),
}

impl std::fmt::Display for QueryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            QueryError::CanonicalRead(e) => write!(f, "reading canonical store: {e}"),
            QueryError::CanonicalParse(e) => write!(f, "canonical store: {e}"),
            QueryError::IndexRead(e) => write!(f, "reading knowledge index: {e}"),
            QueryError::IndexParse(e) => write!(f, "parsing knowledge index: {e}"),
        }
    }
}

impl std::error::Error for QueryError {}

/// A loaded, read-only snapshot of the knowledge base: canonical concepts
/// (authority) + the derived knowledge index (backlinks) + the vault root (for
/// on-disk checks, used by `ovp-lint`). Construct with [`KnowledgeView::load`].
#[derive(Debug, Clone)]
pub struct KnowledgeView {
    concepts: Vec<CanonicalConcept>,
    index: Option<KnowledgeIndex>,
    vault_root: PathBuf,
}

impl KnowledgeView {
    /// Load from a vault root + canonical store root. The canonical store is the
    /// authority (strict parse — fail-loud on corruption); the knowledge index
    /// is read if present (absent → backlink-less but still queryable).
    pub fn load(vault_root: &Path, canonical_root: &Path) -> Result<Self, QueryError> {
        let store = CanonicalFsStoreApplier::new(canonical_root);
        let pairs = store.read_all().map_err(|e| QueryError::CanonicalRead(e.to_string()))?;
        let mut concepts =
            CanonicalConcept::try_parse_pairs(pairs).map_err(QueryError::CanonicalParse)?;
        concepts.sort_by(|a, b| a.slug.cmp(&b.slug));

        let index_path = vault_root.join(VaultLayout::new().knowledge_index().as_str());
        let index = match std::fs::read_to_string(&index_path) {
            Ok(raw) => Some(
                serde_json::from_str::<KnowledgeIndex>(&raw)
                    .map_err(|e| QueryError::IndexParse(e.to_string()))?,
            ),
            // Absent is fine (not rebuilt yet → backlink-less but queryable);
            // any OTHER I/O error (permission, transient) is loud, not "absent".
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(QueryError::IndexRead(e.to_string())),
        };

        Ok(Self { concepts, index, vault_root: vault_root.to_path_buf() })
    }

    /// All canonical concepts, slug-sorted.
    pub fn concepts(&self) -> &[CanonicalConcept] {
        &self.concepts
    }

    /// The vault root this view was loaded from (used by `ovp-lint`).
    pub fn vault_root(&self) -> &Path {
        &self.vault_root
    }

    /// The persisted knowledge index, if one was present at load.
    pub fn index(&self) -> Option<&KnowledgeIndex> {
        self.index.as_ref()
    }

    /// Look up a concept by exact slug.
    pub fn get(&self, slug: &str) -> Option<&CanonicalConcept> {
        self.concepts.iter().find(|c| c.slug == slug)
    }

    /// Concepts whose slug or title contains `needle` (case-insensitive),
    /// slug-sorted (the underlying list is already sorted).
    pub fn search(&self, needle: &str) -> Vec<&CanonicalConcept> {
        let needle = needle.to_lowercase();
        self.concepts
            .iter()
            .filter(|c| {
                c.slug.to_lowercase().contains(&needle) || c.title.to_lowercase().contains(&needle)
            })
            .collect()
    }

    /// Vault-relative note paths that reference `slug` (from the knowledge
    /// index). Empty if there is no index or no entry for the slug.
    pub fn backlinks(&self, slug: &str) -> &[String] {
        self.index
            .as_ref()
            .and_then(|i| i.entries.iter().find(|e| e.slug == slug))
            .map(|e| e.backlinks.as_slice())
            .unwrap_or(&[])
    }

    /// Summary counts over the view.
    pub fn stats(&self) -> ViewStats {
        let index_present = self.index.is_some();
        let total_backlinks = self
            .index
            .as_ref()
            .map(|i| i.entries.iter().map(|e| e.backlinks.len()).sum())
            .unwrap_or(0);
        // Concepts with no backlink entry in the index (orphans, when an index
        // exists). Without an index we can't tell, so report 0.
        let concepts_without_backlinks = self
            .index
            .as_ref()
            .map(|_| self.concepts.iter().filter(|c| self.backlinks(&c.slug).is_empty()).count())
            .unwrap_or(0);
        ViewStats {
            concept_count: self.concepts.len(),
            index_present,
            total_backlinks,
            concepts_without_backlinks,
        }
    }
}

/// Serializable summary of a `KnowledgeView` (for `query stats --json`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ViewStats {
    pub concept_count: usize,
    pub index_present: bool,
    pub total_backlinks: usize,
    pub concepts_without_backlinks: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_core::{ApplyMode, CanonicalKey, CanonicalUpsertOp, ContentHash, OpId, PlanApplier, RecordId, RunId, WriteOp, WritePlan};
    use sha2::{Digest, Sha256};
    use std::collections::BTreeMap;

    fn sha(b: &[u8]) -> String {
        let h = Sha256::digest(b);
        let mut s = String::new();
        use std::fmt::Write;
        for x in h.iter() {
            write!(s, "{:02x}", x).unwrap();
        }
        s
    }

    fn concept(slug: &str, title: &str) -> CanonicalConcept {
        CanonicalConcept {
            slug: slug.into(),
            title: title.into(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "https://example.com/x".into(),
        }
    }

    fn seed_canonical(root: &Path, concepts: &[CanonicalConcept]) {
        let mut store = CanonicalFsStoreApplier::new(root);
        let mut plan = WritePlan::new(RunId::new("seed"));
        for c in concepts {
            let payload = c.to_payload();
            plan.push(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
                op_id: OpId::new(format!("op-{}", c.slug)),
                key: CanonicalKey::new(c.slug.clone()),
                before_hash: None,
                after_hash: ContentHash::new(sha(payload.as_bytes())),
                payload,
                reason: "seed".into(),
                originating_record: RecordId::new("r"),
            }));
        }
        store.apply(&plan, ApplyMode::Apply);
    }

    fn write_index(vault: &Path, concepts: &[CanonicalConcept], backlinks: BTreeMap<String, Vec<String>>) {
        let index = KnowledgeIndex::build(concepts, &backlinks);
        let path = vault.join("60-Logs/knowledge-index.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, index.to_json()).unwrap();
    }

    #[test]
    fn loads_and_queries_seeded_state() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        let concepts = [concept("ai-agent", "Ai Agent"), concept("rag", "Rag")];
        seed_canonical(canon.path(), &concepts);
        let mut bl = BTreeMap::new();
        bl.insert("ai-agent".to_string(), vec!["20-Areas/x.md".to_string()]);
        write_index(vault.path(), &concepts, bl);

        let view = KnowledgeView::load(vault.path(), canon.path()).unwrap();
        assert_eq!(view.concepts().len(), 2);
        assert_eq!(view.get("ai-agent").unwrap().title, "Ai Agent");
        assert!(view.get("missing").is_none());
        // search is case-insensitive over slug + title.
        assert_eq!(view.search("AGENT").len(), 1);
        assert_eq!(view.search("ra").iter().map(|c| c.slug.as_str()).collect::<Vec<_>>(), vec!["rag"]);
        assert_eq!(view.backlinks("ai-agent"), &["20-Areas/x.md".to_string()]);
        assert!(view.backlinks("rag").is_empty());

        let stats = view.stats();
        assert_eq!(stats.concept_count, 2);
        assert!(stats.index_present);
        assert_eq!(stats.total_backlinks, 1);
        assert_eq!(stats.concepts_without_backlinks, 1); // rag has none
    }

    #[test]
    fn loads_without_index() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        seed_canonical(canon.path(), &[concept("ai-agent", "Ai Agent")]);

        let view = KnowledgeView::load(vault.path(), canon.path()).unwrap();
        assert_eq!(view.concepts().len(), 1);
        assert!(view.index().is_none());
        assert!(view.backlinks("ai-agent").is_empty());
        let stats = view.stats();
        assert!(!stats.index_present);
        assert_eq!(stats.concepts_without_backlinks, 0); // unknown without an index
    }

    #[test]
    fn corrupt_canonical_is_loud() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        seed_canonical(canon.path(), &[concept("ai-agent", "Ai Agent")]);
        std::fs::write(canon.path().join("broken.json"), "not json").unwrap();

        let err = KnowledgeView::load(vault.path(), canon.path()).unwrap_err();
        assert!(matches!(err, QueryError::CanonicalParse(_)), "got {err:?}");
    }

    #[test]
    fn corrupt_index_is_loud() {
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        seed_canonical(canon.path(), &[concept("ai-agent", "Ai Agent")]);
        let path = vault.path().join("60-Logs/knowledge-index.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, "{ not valid json").unwrap();

        let err = KnowledgeView::load(vault.path(), canon.path()).unwrap_err();
        assert!(matches!(err, QueryError::IndexParse(_)), "got {err:?}");
    }

    #[test]
    fn unreadable_index_is_loud_not_absent() {
        // The index *path* exists but is not a readable file (here: a directory),
        // so read_to_string errors with a non-NotFound kind. That must be loud
        // (IndexRead), not silently treated as "no index yet".
        let vault = tempfile::tempdir().unwrap();
        let canon = tempfile::tempdir().unwrap();
        seed_canonical(canon.path(), &[concept("ai-agent", "Ai Agent")]);
        let path = vault.path().join("60-Logs/knowledge-index.json");
        std::fs::create_dir_all(&path).unwrap(); // a directory where the file should be

        let err = KnowledgeView::load(vault.path(), canon.path()).unwrap_err();
        assert!(matches!(err, QueryError::IndexRead(_)), "got {err:?}");
    }
}
