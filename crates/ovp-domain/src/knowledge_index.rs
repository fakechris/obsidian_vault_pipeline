use std::collections::BTreeMap;

use ovp_core::{ContentHash, OpId, RecordId, RunId, VaultCreateOp, VaultUpdateOp, WriteOp, WritePlan};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::canonical::CanonicalConcept;
use crate::vault_layout::VaultLayout;

/// Extract `[[wikilink]]` targets from markdown. Returns the inner slug
/// for `[[slug]]` and `[[slug|alias]]` (the part before `|`). Image
/// embeds (`![[...]]`) are skipped — those are attachments, not concept
/// references. Pure.
pub fn extract_wikilinks(md: &str) -> Vec<String> {
    let bytes = md.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i + 1 < bytes.len() {
        if bytes[i] == b'[' && bytes[i + 1] == b'[' {
            // Skip image embeds: preceding '!'.
            if i > 0 && bytes[i - 1] == b'!' {
                i += 2;
                continue;
            }
            if let Some(end) = md[i + 2..].find("]]") {
                let inner = &md[i + 2..i + 2 + end];
                let target = inner.split('|').next().unwrap_or("").trim();
                if !target.is_empty() {
                    out.push(target.to_string());
                }
                i = i + 2 + end + 2;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// A derived knowledge-index entry: a canonical concept plus the vault
/// notes that reference it (`[[slug]]` backlinks).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeIndexEntry {
    pub slug: String,
    pub title: String,
    pub evergreen_path: String,
    /// Vault-relative paths of notes that wikilink this concept, sorted.
    pub backlinks: Vec<String>,
}

/// A derived index over the canonical concepts + vault backlinks. Fully
/// rebuildable from (canonical store + vault), holds no authority
/// (invariant #11). Serialized to a single JSON artifact.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeIndex {
    pub entries: Vec<KnowledgeIndexEntry>,
}

impl KnowledgeIndex {
    /// Build from canonical concepts + a `slug → [note paths]` backlink
    /// map (produced by scanning the vault for `[[slug]]`). Entries are
    /// sorted by slug and backlinks sorted/deduped, so the same inputs
    /// always yield byte-identical JSON (idempotent rebuilds).
    pub fn build(
        concepts: &[CanonicalConcept],
        backlinks: &BTreeMap<String, Vec<String>>,
    ) -> Self {
        let mut entries: Vec<KnowledgeIndexEntry> = concepts
            .iter()
            .map(|c| {
                let mut links = backlinks.get(&c.slug).cloned().unwrap_or_default();
                links.sort();
                links.dedup();
                KnowledgeIndexEntry {
                    slug: c.slug.clone(),
                    title: c.title.clone(),
                    evergreen_path: c.evergreen_path.clone(),
                    backlinks: links,
                }
            })
            .collect();
        entries.sort_by(|a, b| a.slug.cmp(&b.slug));
        Self { entries }
    }

    /// Deterministic pretty JSON.
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).expect("KnowledgeIndex serializes")
    }
}

/// Plans the derived knowledge-index rebuild, same shape as `MocBuilder`:
/// compares the freshly-built index against the current on-disk artifact
/// and emits VaultCreate / VaultUpdate / nothing.
pub struct KnowledgeIndexBuilder {
    layout: VaultLayout,
}

impl KnowledgeIndexBuilder {
    pub fn new() -> Self {
        Self { layout: VaultLayout::new() }
    }

    pub fn index_path(&self) -> ovp_core::VaultPath {
        self.layout.knowledge_index()
    }

    pub fn plan_rebuild(
        &self,
        run_id: RunId,
        index: &KnowledgeIndex,
        current: Option<&str>,
    ) -> WritePlan {
        let desired = index.to_json();
        let path = self.layout.knowledge_index();
        let after = hex_sha256(desired.as_bytes());
        let mut plan = WritePlan::new(run_id);
        match current {
            None => plan.push(WriteOp::VaultCreate(VaultCreateOp {
                op_id: OpId::new("op-knowledge-index"),
                path,
                after_hash: ContentHash::new(after),
                body: desired,
                reason: "rebuild knowledge index".into(),
                originating_record: RecordId::new("index-rebuild"),
            })),
            Some(cur) => {
                let before = hex_sha256(cur.as_bytes());
                if before != after {
                    plan.push(WriteOp::VaultUpdate(VaultUpdateOp {
                        op_id: OpId::new("op-knowledge-index"),
                        path,
                        before_hash: ContentHash::new(before),
                        after_hash: ContentHash::new(after),
                        body: desired,
                        reason: "rebuild knowledge index (changed)".into(),
                        originating_record: RecordId::new("index-rebuild"),
                    }));
                }
            }
        }
        plan
    }
}

impl Default for KnowledgeIndexBuilder {
    fn default() -> Self {
        Self::new()
    }
}

fn hex_sha256(bytes: &[u8]) -> String {
    let hash = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    use std::fmt::Write;
    for b in hash.iter() {
        write!(s, "{:02x}", b).expect("infallible");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    fn concept(slug: &str) -> CanonicalConcept {
        CanonicalConcept {
            slug: slug.into(),
            title: slug.to_uppercase(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "u".into(),
        }
    }

    #[test]
    fn extract_basic_and_aliased_wikilinks() {
        let md = "see [[ai-agent]] and [[rag|Retrieval]] but not ![[img.png]]";
        let links = extract_wikilinks(md);
        assert_eq!(links, vec!["ai-agent", "rag"]);
    }

    #[test]
    fn extract_ignores_unclosed() {
        assert!(extract_wikilinks("[[oops").is_empty());
    }

    #[test]
    fn build_attaches_sorted_backlinks() {
        let mut bl = BTreeMap::new();
        bl.insert("ai-agent".to_string(), vec!["b.md".into(), "a.md".into(), "a.md".into()]);
        let idx = KnowledgeIndex::build(&[concept("ai-agent"), concept("rag")], &bl);
        assert_eq!(idx.entries.len(), 2);
        // sorted by slug
        assert_eq!(idx.entries[0].slug, "ai-agent");
        // backlinks sorted + deduped
        assert_eq!(idx.entries[0].backlinks, vec!["a.md", "b.md"]);
        assert!(idx.entries[1].backlinks.is_empty());
    }

    #[test]
    fn build_is_deterministic() {
        let bl = BTreeMap::new();
        let a = KnowledgeIndex::build(&[concept("b"), concept("a")], &bl);
        let c = KnowledgeIndex::build(&[concept("a"), concept("b")], &bl);
        assert_eq!(a.to_json(), c.to_json());
    }

    #[test]
    fn plan_create_then_idempotent() {
        let b = KnowledgeIndexBuilder::new();
        let idx = KnowledgeIndex::build(&[concept("x")], &BTreeMap::new());
        let plan = b.plan_rebuild(RunId::new("r"), &idx, None);
        assert_eq!(plan.len(), 1);
        match &plan.ops[0] {
            WriteOp::VaultCreate(o) => assert_eq!(o.path.as_str(), "60-Logs/knowledge-index.json"),
            other => panic!("expected VaultCreate, got {other:?}"),
        }
        // Unchanged → empty.
        let current = idx.to_json();
        assert!(b.plan_rebuild(RunId::new("r"), &idx, Some(&current)).is_empty());
    }

    #[test]
    fn plan_update_when_changed() {
        let b = KnowledgeIndexBuilder::new();
        let old = KnowledgeIndex::build(&[concept("x")], &BTreeMap::new()).to_json();
        let new_idx = KnowledgeIndex::build(&[concept("x"), concept("y")], &BTreeMap::new());
        let plan = b.plan_rebuild(RunId::new("r"), &new_idx, Some(&old));
        assert_eq!(plan.len(), 1);
        assert!(matches!(plan.ops[0], WriteOp::VaultUpdate(_)));
    }
}
