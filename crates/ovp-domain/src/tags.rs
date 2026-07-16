//! Tag vocabulary layer: deterministic normalization + the operator-owned
//! alias table (`.ovp/tags/aliases.toml`).
//!
//! Ownership contract (docs/stage-tags): raw tags live in note frontmatter
//! and are NEVER rewritten by the pipeline — canonicalization happens only
//! when a projection is built. The alias table maps variant spellings to a
//! canonical tag, so both already-ingested notes and future captures with
//! any variant spelling converge without touching the source files.

use std::collections::BTreeMap;
use std::path::Path;

/// Tags the intake renderer stamps on every note it writes. They mark the
/// capture mechanism, not the content, so no projection surfaces them.
pub const BOILERPLATE_TAGS: &[&str] = &["clippings", "pinboard"];

/// Deterministic tag normalization: trim, strip a leading `#`, lowercase,
/// fold separators (whitespace/underscore/`/`) to `-`, collapse runs of `-`,
/// strip leading/trailing `-`. Returns `None` when nothing survives.
///
/// Deliberately NOT here: plural folding, abbreviation expansion, semantic
/// merges — those are corpus-dependent judgments and belong in the alias
/// table, where the operator approves them.
pub fn normalize_tag(raw: &str) -> Option<String> {
    let mut out = String::with_capacity(raw.len());
    let mut prev_dash = true; // suppress leading dashes
    for c in raw.trim().trim_start_matches('#').chars() {
        let folded: Option<char> = if c.is_whitespace() || c == '_' || c == '/' || c == '-' {
            None
        } else {
            Some(c)
        };
        match folded {
            Some(c) => {
                for lc in c.to_lowercase() {
                    out.push(lc);
                }
                prev_dash = false;
            }
            None => {
                if !prev_dash {
                    out.push('-');
                    prev_dash = true;
                }
            }
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    if out.is_empty() { None } else { Some(out) }
}

/// The operator-approved alias table: normalized alias → normalized
/// canonical, plus a `drop` list for capture-channel tags (e.g. a clipper's
/// per-site tag) that should never surface as content facets. Loaded from
/// `.ovp/tags/aliases.toml`; a missing file is an empty table (the mechanism
/// is optional until the operator seeds it).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TagAliases {
    map: BTreeMap<String, String>,
    drop: std::collections::BTreeSet<String>,
}

impl TagAliases {
    /// Parse the alias table from TOML text. Fail-loud on structural rot:
    /// unparseable TOML, an alias that normalizes to nothing, an alias equal
    /// to its canonical, or a transitive chain (a canonical that is itself
    /// an alias) — chains would make resolution order-dependent.
    pub fn parse(text: &str) -> Result<Self, String> {
        // deny_unknown_fields: a typo like `[alias]` must fail loud, not
        // parse as an empty table that silently un-merges the vocabulary.
        #[derive(serde::Deserialize)]
        #[serde(deny_unknown_fields)]
        struct File {
            #[serde(default)]
            aliases: BTreeMap<String, String>,
            #[serde(default)]
            drop: Vec<String>,
        }
        let file: File =
            toml::from_str(text).map_err(|e| format!("tag aliases: invalid TOML: {e}"))?;
        let mut drop = std::collections::BTreeSet::new();
        for raw in &file.drop {
            let d = normalize_tag(raw)
                .ok_or_else(|| format!("tag aliases: drop entry {raw:?} normalizes to nothing"))?;
            drop.insert(d);
        }
        let mut map = BTreeMap::new();
        for (alias, canonical) in &file.aliases {
            let a = normalize_tag(alias)
                .ok_or_else(|| format!("tag aliases: alias {alias:?} normalizes to nothing"))?;
            let c = normalize_tag(canonical).ok_or_else(|| {
                format!("tag aliases: canonical {canonical:?} (for {alias:?}) normalizes to nothing")
            })?;
            if a == c {
                return Err(format!("tag aliases: {alias:?} maps to itself"));
            }
            if map.insert(a.clone(), c).is_some() {
                return Err(format!(
                    "tag aliases: duplicate alias {a:?} after normalization"
                ));
            }
        }
        for (alias, canonical) in &map {
            if map.contains_key(canonical) {
                return Err(format!(
                    "tag aliases: {canonical:?} is both a canonical and an alias (chain); \
                     point every alias directly at the final canonical"
                ));
            }
            if drop.contains(canonical) {
                return Err(format!(
                    "tag aliases: {canonical:?} is both a canonical and dropped; \
                     alias the variants to a kept tag or drop them directly"
                ));
            }
            if drop.contains(alias) {
                return Err(format!(
                    "tag aliases: {alias:?} is both an alias and dropped; \
                     pick one — drop wins would silently disable the merge"
                ));
            }
            if BOILERPLATE_TAGS.contains(&canonical.as_str()) {
                return Err(format!(
                    "tag aliases: {alias:?} maps to boilerplate {canonical:?}; \
                     capture-mechanism tags never surface as content facets"
                ));
            }
        }
        Ok(Self { map, drop })
    }

    /// Load the table from `<vault_root>/.ovp/tags/aliases.toml`. Missing
    /// file → empty table; unreadable or invalid file → error (a present but
    /// broken table must not silently un-merge the vocabulary).
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        let path = vault_root.join(crate::vault_layout::VaultLayout.tag_aliases_file());
        match std::fs::read_to_string(&path) {
            Ok(text) => Self::parse(&text),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(format!("reading {}: {e}", path.display())),
        }
    }

    /// Canonical form of an already-normalized tag.
    pub fn resolve<'a>(&'a self, normalized: &'a str) -> &'a str {
        self.map.get(normalized).map(String::as_str).unwrap_or(normalized)
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }
}

pub const TAGS_INFERRED_SCHEMA: &str = "ovp.tags-inferred/v1";

/// One machine-inferred tag on one source, with its evidence.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct InferredTag {
    pub tag: String,
    /// Share of neighbor similarity weight that voted for this tag (0..1).
    pub score: f64,
    /// Number of neighbors carrying the tag.
    pub support: usize,
}

/// `.ovp/tags/inferred.json` — kNN-voted tags for sources that had NO
/// operator tags at generation time. A rebuildable projection: regenerate
/// with `tags-suggest`, delete freely. The index attaches these as
/// `tags_inferred`, never mixing them into operator tags, and drops them for
/// any source that has since gained real tags (self-healing staleness).
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TagsInferredFile {
    pub schema: String,
    /// Embedding model the neighbor graph was built with.
    pub model: String,
    /// Generation parameters, recorded for auditability (k, thresholds…).
    #[serde(default)]
    pub params: BTreeMap<String, f64>,
    /// source sha256 → inferred tags (score descending).
    #[serde(default)]
    pub entries: BTreeMap<String, Vec<InferredTag>>,
}

impl TagsInferredFile {
    /// Load from the vault. Missing file → `None` (feature unused); a present
    /// but unparseable file is an error — silently dropping every inferred
    /// tag would read as "backfill vanished".
    pub fn load(vault_root: &Path) -> Result<Option<Self>, String> {
        let path = vault_root.join(crate::vault_layout::VaultLayout.tags_inferred_file());
        let raw = match std::fs::read_to_string(&path) {
            Ok(raw) => raw,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(format!("reading {}: {e}", path.display())),
        };
        let file: Self = serde_json::from_str(&raw)
            .map_err(|e| format!("parsing {}: {e}", path.display()))?;
        if file.schema != TAGS_INFERRED_SCHEMA {
            return Err(format!(
                "{}: unknown schema {:?} (expected {TAGS_INFERRED_SCHEMA:?})",
                path.display(),
                file.schema
            ));
        }
        Ok(Some(file))
    }

    pub fn save(&self, vault_root: &Path) -> Result<String, String> {
        let path = vault_root.join(crate::vault_layout::VaultLayout.tags_inferred_file());
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("creating {}: {e}", parent.display()))?;
        }
        let body = serde_json::to_string_pretty(self)
            .map_err(|e| format!("serializing inferred tags: {e}"))?;
        std::fs::write(&path, format!("{body}\n"))
            .map_err(|e| format!("writing {}: {e}", path.display()))?;
        Ok(path.display().to_string())
    }
}

/// Raw frontmatter tags → sorted, deduped canonical tags: normalize, drop
/// boilerplate, resolve aliases, apply the operator drop list. The one entry
/// point projections use.
pub fn canonical_tags<S: AsRef<str>>(raw: &[S], aliases: &TagAliases) -> Vec<String> {
    let mut out: Vec<String> = raw
        .iter()
        .filter_map(|t| normalize_tag(t.as_ref()))
        .filter(|t| !BOILERPLATE_TAGS.contains(&t.as_str()))
        .filter(|t| !aliases.drop.contains(t))
        .map(|t| aliases.resolve(&t).to_string())
        .collect();
    out.sort();
    out.dedup();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_folds_case_separators_and_hash() {
        assert_eq!(normalize_tag("Claude_Code"), Some("claude-code".into()));
        assert_eq!(normalize_tag("#AI  Agent"), Some("ai-agent".into()));
        assert_eq!(normalize_tag("ai/agent"), Some("ai-agent".into()));
        assert_eq!(normalize_tag("--open--source--"), Some("open-source".into()));
        assert_eq!(normalize_tag("大模型"), Some("大模型".into()));
    }

    #[test]
    fn normalize_rejects_empty() {
        assert_eq!(normalize_tag(""), None);
        assert_eq!(normalize_tag("  #- _ "), None);
    }

    #[test]
    fn canonical_tags_drop_boilerplate_resolve_aliases_and_dedup() {
        let aliases = TagAliases::parse("[aliases]\n\"ai-agents\" = \"agent\"\n").unwrap();
        let got = canonical_tags(
            &["clippings", "pinboard", "AI Agents", "agent", "Agent"],
            &aliases,
        );
        assert_eq!(got, vec!["agent".to_string()]);
    }

    #[test]
    fn drop_list_removes_channel_tags_and_rejects_dropped_canonicals() {
        let t = TagAliases::parse("drop = [\"Twitter\"]\n[aliases]\n\"tweets\" = \"tweet\"\n")
            .unwrap();
        assert_eq!(canonical_tags(&["twitter", "tweets"], &t), vec!["tweet".to_string()]);
        let err = TagAliases::parse("drop = [\"tweet\"]\n[aliases]\n\"tweets\" = \"tweet\"\n")
            .unwrap_err();
        assert!(err.contains("dropped"), "{err}");
        // An alias key in the drop list is ambiguous config — fail loud.
        let err = TagAliases::parse("drop = [\"tweets\"]\n[aliases]\n\"tweets\" = \"tweet\"\n")
            .unwrap_err();
        assert!(err.contains("both an alias and dropped"), "{err}");
    }

    #[test]
    fn parse_rejects_typo_sections_and_boilerplate_canonicals() {
        // `[alias]` (typo) must not parse as an empty table.
        assert!(TagAliases::parse("[alias]\n\"a\" = \"b\"\n").is_err());
        // Aliasing onto a boilerplate capture tag can never surface.
        let err =
            TagAliases::parse("[aliases]\n\"clipping\" = \"clippings\"\n").unwrap_err();
        assert!(err.contains("boilerplate"), "{err}");
    }

    #[test]
    fn parse_rejects_chains_self_maps_and_bad_toml() {
        assert!(TagAliases::parse("aliases = 3").is_err());
        assert!(TagAliases::parse("[aliases]\n\"agent\" = \"Agent\"\n").is_err());
        let chain = "[aliases]\n\"a\" = \"b\"\n\"b\" = \"c\"\n";
        let err = TagAliases::parse(chain).unwrap_err();
        assert!(err.contains("chain"), "{err}");
    }

    #[test]
    fn parse_normalizes_both_sides_and_missing_section_is_empty() {
        let t = TagAliases::parse("[aliases]\n\"AI_Agents\" = \"Agent\"\n").unwrap();
        assert_eq!(t.resolve("ai-agents"), "agent");
        assert!(TagAliases::parse("").unwrap().is_empty());
    }
}
