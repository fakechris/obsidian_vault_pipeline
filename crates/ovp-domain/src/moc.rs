use ovp_core::{
    ContentHash, OpId, RecordId, VaultCreateOp, VaultUpdateOp, WriteOp, WritePlan,
};
use ovp_core::RunId;
use sha2::{Digest, Sha256};

use crate::canonical::CanonicalConcept;
use crate::vault_layout::VaultLayout;

/// Builds the Atlas MOC (Map of Content) — a derived index of canonical
/// concepts. Pure: a function of the canonical concept set. Per invariant
/// #11 it is fully rebuildable from the canonical store; it holds no
/// authority of its own.
///
/// v1 renders a single flat `MOC-Index.md` listing every canonical
/// concept as a wikilink. Domain-grouped MOCs (the legacy `MOC-*.md`
/// split) are a later refinement.
pub struct MocBuilder {
    layout: VaultLayout,
    moc_name: String,
}

impl MocBuilder {
    pub fn new() -> Self {
        Self { layout: VaultLayout::new(), moc_name: "MOC-Index".to_string() }
    }

    pub fn with_name(mut self, name: impl Into<String>) -> Self {
        self.moc_name = name.into();
        self
    }

    /// Render the MOC markdown for a set of canonical concepts.
    /// Deterministic: concepts are sorted by slug, so the same set always
    /// yields byte-identical output (→ idempotent rebuilds).
    pub fn render(&self, concepts: &[CanonicalConcept]) -> String {
        let mut sorted: Vec<&CanonicalConcept> = concepts.iter().collect();
        sorted.sort_by(|a, b| a.slug.cmp(&b.slug));

        let mut s = String::new();
        s.push_str("---\n");
        s.push_str("title: MOC Index\n");
        s.push_str("type: moc\n");
        s.push_str(&format!("concept_count: {}\n", sorted.len()));
        s.push_str("---\n\n");
        s.push_str("# MOC Index\n\n");
        s.push_str("> Derived index of canonical concepts. Rebuilt from the canonical store.\n\n");
        if sorted.is_empty() {
            s.push_str("_No canonical concepts yet._\n");
        } else {
            for c in sorted {
                s.push_str(&format!("- [[{}]] — {}\n", c.slug, c.title));
            }
        }
        s
    }

    /// Plan a MOC rebuild. Pure: compares the freshly-rendered MOC against
    /// the current on-disk content (read by the caller, passed as
    /// `current`) and emits the right op — `VaultCreate` if absent,
    /// `VaultUpdate` (with `before_hash`) if changed, or an empty plan if
    /// unchanged. Keeps I/O at the boundary; the applier does the write.
    pub fn plan_rebuild(
        &self,
        run_id: RunId,
        concepts: &[CanonicalConcept],
        current: Option<&str>,
    ) -> WritePlan {
        let desired = self.render(concepts);
        let path = self.layout.atlas_moc(&self.moc_name);
        let after = hex_sha256(desired.as_bytes());
        let mut plan = WritePlan::new(run_id);

        match current {
            None => {
                plan.push(WriteOp::VaultCreate(VaultCreateOp {
                    op_id: OpId::new(format!("op-moc-{}", self.moc_name)),
                    path,
                    after_hash: ContentHash::new(after),
                    body: desired,
                    reason: "rebuild MOC index".into(),
                    originating_record: RecordId::new("moc-rebuild"),
                }));
            }
            Some(cur) => {
                let before = hex_sha256(cur.as_bytes());
                if before == after {
                    // Unchanged — empty plan (idempotent rebuild).
                } else {
                    plan.push(WriteOp::VaultUpdate(VaultUpdateOp {
                        op_id: OpId::new(format!("op-moc-{}", self.moc_name)),
                        path,
                        before_hash: ContentHash::new(before),
                        after_hash: ContentHash::new(after),
                        body: desired,
                        reason: "rebuild MOC index (changed)".into(),
                        originating_record: RecordId::new("moc-rebuild"),
                    }));
                }
            }
        }
        plan
    }

    pub fn moc_path(&self) -> ovp_core::VaultPath {
        self.layout.atlas_moc(&self.moc_name)
    }
}

impl Default for MocBuilder {
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

    fn concept(slug: &str, title: &str) -> CanonicalConcept {
        CanonicalConcept {
            slug: slug.into(),
            title: title.into(),
            evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
            provenance_source_url: "https://example.com/x".into(),
        }
    }

    #[test]
    fn renders_sorted_wikilinks() {
        let b = MocBuilder::new();
        let md = b.render(&[concept("rag", "Rag"), concept("ai-agent", "Ai Agent")]);
        assert!(md.contains("type: moc"));
        assert!(md.contains("concept_count: 2"));
        // Sorted by slug: ai-agent before rag.
        let ai = md.find("[[ai-agent]]").unwrap();
        let rag = md.find("[[rag]]").unwrap();
        assert!(ai < rag, "concepts must be sorted by slug");
        assert!(md.contains("- [[ai-agent]] — Ai Agent"));
    }

    #[test]
    fn render_is_deterministic_regardless_of_input_order() {
        let b = MocBuilder::new();
        let a = b.render(&[concept("b", "B"), concept("a", "A")]);
        let c = b.render(&[concept("a", "A"), concept("b", "B")]);
        assert_eq!(a, c);
    }

    #[test]
    fn empty_concepts_renders_placeholder() {
        let b = MocBuilder::new();
        let md = b.render(&[]);
        assert!(md.contains("concept_count: 0"));
        assert!(md.contains("_No canonical concepts yet._"));
    }

    #[test]
    fn plan_create_when_absent() {
        let b = MocBuilder::new();
        let plan = b.plan_rebuild(RunId::new("r"), &[concept("x", "X")], None);
        assert_eq!(plan.len(), 1);
        match &plan.ops[0] {
            WriteOp::VaultCreate(o) => {
                assert_eq!(o.path.as_str(), "10-Knowledge/Atlas/MOC-Index.md");
                assert!(o.body.contains("[[x]]"));
            }
            other => panic!("expected VaultCreate, got {other:?}"),
        }
    }

    #[test]
    fn plan_empty_when_unchanged() {
        let b = MocBuilder::new();
        let concepts = [concept("x", "X")];
        let current = b.render(&concepts);
        let plan = b.plan_rebuild(RunId::new("r"), &concepts, Some(&current));
        assert!(plan.is_empty(), "unchanged MOC → no op");
    }

    #[test]
    fn plan_update_when_changed() {
        let b = MocBuilder::new();
        let old = b.render(&[concept("x", "X")]);
        let plan = b.plan_rebuild(
            RunId::new("r"),
            &[concept("x", "X"), concept("y", "Y")],
            Some(&old),
        );
        assert_eq!(plan.len(), 1);
        match &plan.ops[0] {
            WriteOp::VaultUpdate(o) => {
                assert_eq!(o.before_hash.as_str(), hex_sha256(old.as_bytes()));
                assert!(o.body.contains("[[y]]"));
            }
            other => panic!("expected VaultUpdate, got {other:?}"),
        }
    }
}
