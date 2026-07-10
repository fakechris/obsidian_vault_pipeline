//! The structured form of a minted evergreen note, plus the deterministic
//! parse / merge / render and the **same-slug reconcile** decision (M12b).
//!
//! M12a made minting write a grounded body. That body is per-document, so two
//! distinct articles surfacing the same slug render different bodies. M12b
//! closes the loop: before applying, the run-cycle reconciles each minted
//! evergreen `VaultCreate` against what is already on disk —
//!
//! - nothing on disk        → MintNew (keep the `VaultCreate`);
//! - on disk, same content  → keep the `VaultCreate` (the applier idempotent-skips);
//! - on disk, different      → EnrichExisting: parse both, union the
//!   source-backed claims / sources / related links (keeping the first note's
//!   definition), and emit a `VaultUpdate` guarded by the on-disk `before_hash`;
//! - on disk, nothing new to add → skip (idempotent enrich).
//!
//! All of this is a pure function of the two note bodies, so a second run over
//! the same inputs is a no-op. Cross-document *semantic* merge (deduping
//! near-duplicate claims, concept-specific definitions) is still future; this
//! is a structural union that keeps the main flow from failing on a repeat slug.

use ovp_core::{ContentHash, VaultCreateOp, VaultUpdateOp, WriteOp};
use sha2::{Digest, Sha256};

use crate::evergreen::EvergreenConcept;

/// Caps so a heavily-cross-referenced concept's note can't grow without bound
/// across many documents. Applied after the union, so the merge stays
/// idempotent once a section is full.
const MAX_CLAIMS: usize = 8;
const MAX_SOURCES: usize = 8;
const MAX_RELATED: usize = 12;

/// The structured content of an evergreen note body, in our rendered format.
/// `claims` / `sources` / `related` hold the bullet inner-text verbatim (e.g.
/// a source is `"[Title](url)"`, a related is `"[[slug]]"`), so a parse →
/// merge → render round-trip preserves them exactly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvergreenNote {
    pub title: String,
    pub slug: String,
    pub definition: String,
    pub claims: Vec<String>,
    pub sources: Vec<String>,
    pub related: Vec<String>,
}

impl EvergreenNote {
    /// The note a freshly-minted concept renders to. `render()` of this is
    /// byte-identical to the legacy `render_rich` for every body the production
    /// mint path produces (the writer's claim selection already trims + drops
    /// empties, so `claims` never carries blanks).
    pub fn from_concept(c: &EvergreenConcept) -> Self {
        Self {
            title: c.title.clone(),
            slug: c.slug.clone(),
            definition: c.definition.trim().to_string(),
            // Trim + drop empty claims. Deliberate hardening: the production
            // writer never emits an empty claim, so this matches `render_rich`
            // for all reachable inputs; it only diverges (favorably) for a
            // hand-constructed concept carrying a blank claim string.
            claims: c
                .source_claims
                .iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
            sources: source_bullets(c),
            related: c.related.iter().map(|r| format!("[[{}]]", r.trim())).collect(),
        }
    }

    /// Parse a rendered evergreen note back into structure. Returns `None` if it
    /// is not our format (no `type: evergreen` frontmatter, or missing
    /// title/slug) — the reconcile then declines to merge rather than risk
    /// clobbering an unknown note.
    pub fn parse(md: &str) -> Option<Self> {
        let mut title = String::new();
        let mut slug = String::new();
        let mut is_evergreen = false;

        let mut lines = md.lines();
        if lines.next() != Some("---") {
            return None;
        }
        for line in lines.by_ref() {
            if line == "---" {
                break;
            }
            if let Some(v) = line.strip_prefix("title:") {
                title = unquote(v.trim());
            } else if let Some(v) = line.strip_prefix("slug:") {
                slug = v.trim().to_string();
            } else if line.trim() == "type: evergreen" {
                is_evergreen = true;
            }
        }
        if !is_evergreen || title.is_empty() || slug.is_empty() {
            return None;
        }

        #[derive(PartialEq)]
        enum Sec {
            None,
            Claims,
            Source,
            Related,
        }
        let mut sec = Sec::None;
        let mut definition = String::new();
        let mut claims = Vec::new();
        let mut sources = Vec::new();
        let mut related = Vec::new();
        for line in lines {
            if let Some(h) = line.strip_prefix("## ") {
                sec = match h.trim() {
                    "Source-backed claims" => Sec::Claims,
                    "Source" => Sec::Source,
                    "Related" => Sec::Related,
                    _ => Sec::None,
                };
                continue;
            }
            if sec == Sec::None {
                if let Some(d) = line.strip_prefix("> ")
                    && definition.is_empty() {
                        definition = d.trim().to_string();
                    }
                continue;
            }
            if let Some(item) = line.strip_prefix("- ") {
                let item = item.trim().to_string();
                match sec {
                    Sec::Claims => claims.push(item),
                    Sec::Source => sources.push(item),
                    Sec::Related => related.push(item),
                    Sec::None => {}
                }
            }
        }
        Some(Self { title, slug, definition, claims, sources, related })
    }

    /// Merge `other` into `self` (self = the existing on-disk note, other = the
    /// new mint). Keeps the existing title/slug/definition (first-writer-wins
    /// for the headline) and unions the lists, preserving existing order then
    /// appending the new items not already present, capped. Deterministic and
    /// idempotent: if `other`'s items are already present, `merge == self`.
    pub fn merge(&self, other: &Self) -> Self {
        Self {
            title: self.title.clone(),
            slug: self.slug.clone(),
            definition: self.definition.clone(),
            claims: union(&self.claims, &other.claims, MAX_CLAIMS),
            sources: union(&self.sources, &other.sources, MAX_SOURCES),
            related: union(&self.related, &other.related, MAX_RELATED),
        }
    }

    /// Render to the canonical evergreen note body. Byte-identical to the
    /// legacy `render_rich` for a freshly-minted note.
    pub fn render(&self) -> String {
        let mut s = frontmatter(&self.title, &self.slug, "minted");
        s.push_str(&format!("# {}\n\n", self.title));
        if !self.definition.trim().is_empty() {
            s.push_str(&format!("> {}\n", self.definition.trim()));
        }
        if !self.claims.is_empty() {
            s.push_str("\n## Source-backed claims\n\n");
            for c in &self.claims {
                s.push_str(&format!("- {}\n", c.trim()));
            }
        }
        if !self.sources.is_empty() {
            s.push_str("\n## Source\n\n");
            for src in &self.sources {
                s.push_str(&format!("- {}\n", src.trim()));
            }
        }
        if !self.related.is_empty() {
            s.push_str("\n## Related\n\n");
            for r in &self.related {
                s.push_str(&format!("- {}\n", r.trim()));
            }
        }
        s
    }
}

/// The 0-or-1 source bullet a freshly-minted concept renders (mirrors the
/// legacy `render_rich` source line): `[title](url)`, bare url, or bare title.
fn source_bullets(c: &EvergreenConcept) -> Vec<String> {
    let url = c.provenance_source_url.trim();
    let title = c.source_title.trim();
    if !url.is_empty() {
        let label = if title.is_empty() { url } else { title };
        vec![format!("[{label}]({url})")]
    } else if !title.is_empty() {
        vec![title.to_string()]
    } else {
        vec![]
    }
}

/// Existing items in order, then `extra` items not already present. The cap
/// bounds *growth* but never drops content that was already in `existing` (a
/// hand-edited note above the cap is preserved, not silently shrunk), so a
/// re-mint of an over-cap note adds nothing → `merge == existing` → skip.
fn union(existing: &[String], extra: &[String], cap: usize) -> Vec<String> {
    let mut out = existing.to_vec();
    for x in extra {
        if !out.iter().any(|e| e == x) {
            out.push(x.clone());
        }
    }
    out.truncate(cap.max(existing.len()));
    out
}

/// Reconcile a minted evergreen `VaultCreate` against the note already on disk
/// (`existing` is its current body, or `None` if absent). Returns the write to
/// apply, or `None` to skip it. See the module docs for the decision table.
pub fn reconcile_evergreen_write(minted: &VaultCreateOp, existing: Option<&str>) -> Option<WriteOp> {
    let Some(existing) = existing else {
        return Some(WriteOp::VaultCreate(minted.clone())); // MintNew
    };
    if content_hash(existing.as_bytes()) == minted.after_hash.as_str() {
        // Identical to what we'd mint → keep the VaultCreate; the applier
        // idempotent-skips it (preserves the same-input re-run behavior).
        return Some(WriteOp::VaultCreate(minted.clone()));
    }
    // Different content under the same slug → enrich rather than fail.
    let (Some(existing_note), Some(new_note)) =
        (EvergreenNote::parse(existing), EvergreenNote::parse(&minted.body))
    else {
        // Not our format on one side → don't risk clobbering it; skip the write.
        return None;
    };
    let merged = existing_note.merge(&new_note);
    if merged == existing_note {
        return None; // nothing new to add — idempotent enrich, skip
    }
    let body = merged.render();
    Some(WriteOp::VaultUpdate(VaultUpdateOp {
        op_id: minted.op_id.clone(),
        path: minted.path.clone(),
        before_hash: ContentHash::new(content_hash(existing.as_bytes())),
        after_hash: ContentHash::new(content_hash(body.as_bytes())),
        body,
        reason: "enrich existing evergreen (same slug, later document)".into(),
        originating_record: minted.originating_record.clone(),
    }))
}

/// The shared YAML frontmatter for an evergreen note. `status` distinguishes a
/// grounded `minted` note from a bare `stub`.
pub(crate) fn frontmatter(title: &str, slug: &str, status: &str) -> String {
    format!(
        "---\ntitle: {}\ntype: evergreen\nslug: {}\nstatus: {}\n---\n\n",
        yaml_quote(title),
        slug,
        status
    )
}

pub(crate) fn yaml_quote(s: &str) -> String {
    let needs = s.is_empty()
        || s.starts_with([
            '-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%',
            '@', '`',
        ])
        || s.contains(": ")
        || s.contains(" #")
        || s.contains('\n');
    if needs {
        format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        s.to_string()
    }
}

/// Reverse [`yaml_quote`] for the common (rarely-quoted) title case.
fn unquote(s: &str) -> String {
    if s.len() >= 2 && s.starts_with('"') && s.ends_with('"') {
        s[1..s.len() - 1].replace("\\\"", "\"").replace("\\\\", "\\")
    } else {
        s.to_string()
    }
}

/// Hex SHA-256 of bytes — the content hash the vault applier validates against.
pub fn content_hash(bytes: &[u8]) -> String {
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
    use ovp_core::{OpId, RecordId, VaultPath};

    fn concept(slug: &str, def: &str, claims: &[&str], src_title: &str, src_url: &str, related: &[&str]) -> EvergreenConcept {
        let mut c = EvergreenConcept::from_candidate(slug, src_url);
        c.definition = def.into();
        c.source_claims = claims.iter().map(|s| s.to_string()).collect();
        c.source_title = src_title.into();
        c.related = related.iter().map(|s| s.to_string()).collect();
        c
    }

    fn vault_create(c: &EvergreenConcept) -> VaultCreateOp {
        let note = EvergreenNote::from_concept(c);
        let body = note.render();
        VaultCreateOp {
            op_id: OpId::new(format!("op-evergreen-{}", c.slug)),
            path: VaultPath::new(format!("10-Knowledge/Evergreen/{}.md", c.slug)),
            after_hash: ContentHash::new(content_hash(body.as_bytes())),
            body,
            reason: "mint".into(),
            originating_record: RecordId::new("r"),
        }
    }

    #[test]
    fn parse_round_trips_a_rendered_note() {
        let c = concept("rag", "RAG augments generation.", &["Claim one.", "Claim two."], "Doc A", "https://a/x", &["vector-db"]);
        let note = EvergreenNote::from_concept(&c);
        let parsed = EvergreenNote::parse(&note.render()).unwrap();
        assert_eq!(parsed, note, "render -> parse preserves structure");
    }

    #[test]
    fn parse_rejects_non_evergreen() {
        assert!(EvergreenNote::parse("not a note").is_none());
        assert!(EvergreenNote::parse("---\ntitle: X\n---\n\n# X\n").is_none(), "no type: evergreen");
    }

    #[test]
    fn merge_unions_lists_and_keeps_first_definition() {
        let a = EvergreenNote::from_concept(&concept(
            "rag", "Definition A.", &["A claim."], "Doc A", "https://a/x", &["vector-db"],
        ));
        let b = EvergreenNote::from_concept(&concept(
            "rag", "Definition B.", &["B claim."], "Doc B", "https://b/y", &["embeddings"],
        ));
        let m = a.merge(&b);
        assert_eq!(m.definition, "Definition A.", "first-writer-wins definition");
        assert!(m.claims.contains(&"A claim.".to_string()) && m.claims.contains(&"B claim.".to_string()));
        assert!(m.sources.iter().any(|s| s.contains("https://a/x")) && m.sources.iter().any(|s| s.contains("https://b/y")));
        assert!(m.related.contains(&"[[vector-db]]".to_string()) && m.related.contains(&"[[embeddings]]".to_string()));
    }

    #[test]
    fn merge_is_idempotent_when_other_is_subset() {
        let a = EvergreenNote::from_concept(&concept("rag", "D.", &["A."], "Doc", "https://a/x", &["k"]));
        let b = EvergreenNote::from_concept(&concept("rag", "D.", &["A."], "Doc", "https://a/x", &["k"]));
        assert_eq!(a.merge(&b), a, "merging an equivalent note adds nothing");
    }

    #[test]
    fn reconcile_mints_new_when_absent() {
        let c = concept("rag", "D.", &["A."], "Doc", "https://a/x", &[]);
        let op = vault_create(&c);
        match reconcile_evergreen_write(&op, None) {
            Some(WriteOp::VaultCreate(o)) => assert_eq!(o.path.as_str(), "10-Knowledge/Evergreen/rag.md"),
            other => panic!("expected VaultCreate, got {other:?}"),
        }
    }

    #[test]
    fn reconcile_keeps_create_when_identical() {
        let c = concept("rag", "D.", &["A."], "Doc", "https://a/x", &[]);
        let op = vault_create(&c);
        // Existing on disk == what we'd mint → keep VaultCreate (applier skips).
        match reconcile_evergreen_write(&op, Some(&op.body)) {
            Some(WriteOp::VaultCreate(_)) => {}
            other => panic!("expected VaultCreate, got {other:?}"),
        }
    }

    #[test]
    fn reconcile_enriches_on_different_same_slug() {
        let a = concept("rag", "Definition A.", &["A claim."], "Doc A", "https://a/x", &["vector-db"]);
        let existing = EvergreenNote::from_concept(&a).render();
        let b = concept("rag", "Definition B.", &["B claim."], "Doc B", "https://b/y", &["embeddings"]);
        let op_b = vault_create(&b);
        match reconcile_evergreen_write(&op_b, Some(&existing)) {
            Some(WriteOp::VaultUpdate(u)) => {
                assert_eq!(u.before_hash.as_str(), content_hash(existing.as_bytes()));
                assert_eq!(u.after_hash.as_str(), content_hash(u.body.as_bytes()));
                assert!(u.body.contains("A claim.") && u.body.contains("B claim."), "claims merged");
                assert!(u.body.contains("https://a/x") && u.body.contains("https://b/y"), "both sources present");
                assert!(u.body.contains("Definition A."), "keeps first definition");
            }
            other => panic!("expected VaultUpdate, got {other:?}"),
        }
    }

    #[test]
    fn reconcile_skips_when_nothing_new() {
        // A note that already contains B's grounding; re-minting B adds nothing.
        let b = concept("rag", "Definition A.", &["B claim."], "Doc B", "https://b/y", &["embeddings"]);
        let existing = EvergreenNote::from_concept(&b).render();
        let op_b = vault_create(&b);
        // existing is exactly op_b.body here, so it's the identical branch; make a
        // superset existing instead.
        let merged = EvergreenNote::parse(&existing)
            .unwrap()
            .merge(&EvergreenNote::from_concept(&concept("rag", "Definition A.", &["extra."], "Doc A", "https://a/x", &["k"])));
        let superset = merged.render();
        assert!(reconcile_evergreen_write(&op_b, Some(&superset)).is_none(), "no new info -> skip");
    }

    #[test]
    fn reconcile_skips_unparseable_existing() {
        let c = concept("rag", "D.", &["A."], "Doc", "https://a/x", &[]);
        let op = vault_create(&c);
        assert!(reconcile_evergreen_write(&op, Some("garbage not a note")).is_none());
    }
}
