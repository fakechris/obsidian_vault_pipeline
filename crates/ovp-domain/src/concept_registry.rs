use std::collections::{HashMap, HashSet};
use std::path::Path;

use serde::Deserialize;

/// The focused canonical-concept authority `ConceptResolver` consumes.
/// Holds the set of canonical evergreen slugs plus an alias map
/// (`alias → canonical`). This is the v1 slice of the legacy
/// `ConceptRegistry` — enough to resolve a candidate slug to its
/// canonical form. The legacy surface/trigram indexes are not ported
/// yet; they belong to the absorb/dedup stages.
///
/// Loaders do I/O at construction (app-layer), never inside a transform.
#[derive(Debug, Clone, Default)]
pub struct ConceptRegistry {
    canonical: HashSet<String>,
    aliases: HashMap<String, String>,
}

impl ConceptRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Build a canonical-only registry (no aliases) from a slug slice.
    pub fn from_slugs(slugs: &[&str]) -> Self {
        Self {
            canonical: slugs.iter().map(|s| s.to_string()).collect(),
            aliases: HashMap::new(),
        }
    }

    pub fn insert_canonical(&mut self, slug: impl Into<String>) {
        self.canonical.insert(slug.into());
    }

    /// Register `alias` as another name for `canonical`. The canonical
    /// slug is also inserted into the canonical set.
    pub fn insert_alias(&mut self, alias: impl Into<String>, canonical: impl Into<String>) {
        let canonical = canonical.into();
        self.canonical.insert(canonical.clone());
        self.aliases.insert(alias.into(), canonical);
    }

    pub fn canonical_count(&self) -> usize {
        self.canonical.len()
    }

    pub fn alias_count(&self) -> usize {
        self.aliases.len()
    }

    /// Resolve a candidate slug to its canonical form, if known. Returns
    /// the *stored* canonical slug when the input is canonical or an
    /// alias of a canonical; `None` if unknown (the candidate stays a
    /// candidate). Borrow ties to `self`, so the result is the canonical
    /// spelling, not the input alias.
    pub fn resolve<'a>(&'a self, slug: &str) -> Option<&'a str> {
        if let Some(c) = self.canonical.get(slug) {
            return Some(c.as_str());
        }
        if let Some(canon) = self.aliases.get(slug) {
            if let Some(c) = self.canonical.get(canon) {
                return Some(c.as_str());
            }
        }
        None
    }

    /// Load from a JSON registry file:
    /// `{ "canonical": ["slug", ...], "aliases": { "alias": "canonical" } }`.
    pub fn load_from_file(path: &Path) -> Result<Self, RegistryError> {
        let raw = std::fs::read_to_string(path)
            .map_err(|e| RegistryError(format!("read {}: {e}", path.display())))?;
        let parsed: RegistryFile = serde_json::from_str(&raw)
            .map_err(|e| RegistryError(format!("parse {}: {e}", path.display())))?;
        let mut reg = ConceptRegistry::new();
        for c in parsed.canonical {
            reg.insert_canonical(c);
        }
        for (alias, canonical) in parsed.aliases {
            reg.insert_alias(alias, canonical);
        }
        Ok(reg)
    }

    /// Load by scanning an evergreen directory: each `<slug>.md` file
    /// name becomes a canonical slug. No aliases. Missing directory is an
    /// error; a present-but-empty directory yields an empty registry.
    pub fn load_from_evergreen_dir(dir: &Path) -> Result<Self, RegistryError> {
        let entries = std::fs::read_dir(dir)
            .map_err(|e| RegistryError(format!("read_dir {}: {e}", dir.display())))?;
        let mut reg = ConceptRegistry::new();
        for entry in entries {
            let entry = entry.map_err(|e| RegistryError(format!("dir entry: {e}")))?;
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("md") {
                if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                    reg.insert_canonical(stem.to_string());
                }
            }
        }
        Ok(reg)
    }
}

#[derive(Debug, Deserialize)]
struct RegistryFile {
    #[serde(default)]
    canonical: Vec<String>,
    #[serde(default)]
    aliases: HashMap<String, String>,
}

#[derive(Debug)]
pub struct RegistryError(pub String);

impl std::fmt::Display for RegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "concept registry error: {}", self.0)
    }
}

impl std::error::Error for RegistryError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_canonical_directly() {
        let r = ConceptRegistry::from_slugs(&["ai-agent", "rag"]);
        assert_eq!(r.resolve("ai-agent"), Some("ai-agent"));
        assert_eq!(r.resolve("rag"), Some("rag"));
        assert_eq!(r.resolve("unknown"), None);
    }

    #[test]
    fn resolves_alias_to_canonical() {
        let mut r = ConceptRegistry::new();
        r.insert_canonical("ai-agent");
        r.insert_alias("ai-agents", "ai-agent");
        // The alias resolves to the canonical spelling, not the alias.
        assert_eq!(r.resolve("ai-agents"), Some("ai-agent"));
        assert_eq!(r.resolve("ai-agent"), Some("ai-agent"));
        assert_eq!(r.alias_count(), 1);
    }

    #[test]
    fn load_from_file_round_trip() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("registry.json");
        std::fs::write(
            &path,
            r#"{"canonical":["ai-agent","competitive-advantage"],"aliases":{"agents":"ai-agent"}}"#,
        )
        .unwrap();
        let r = ConceptRegistry::load_from_file(&path).unwrap();
        assert_eq!(r.canonical_count(), 2);
        assert_eq!(r.resolve("agents"), Some("ai-agent"));
        assert_eq!(r.resolve("competitive-advantage"), Some("competitive-advantage"));
    }

    #[test]
    fn load_from_evergreen_dir_uses_filenames() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("ai-agent.md"), "# AI Agent").unwrap();
        std::fs::write(tmp.path().join("rag.md"), "# RAG").unwrap();
        std::fs::write(tmp.path().join("notes.txt"), "ignore").unwrap();
        let r = ConceptRegistry::load_from_evergreen_dir(tmp.path()).unwrap();
        assert_eq!(r.canonical_count(), 2);
        assert_eq!(r.resolve("ai-agent"), Some("ai-agent"));
        assert_eq!(r.resolve("rag"), Some("rag"));
    }

    #[test]
    fn missing_dir_errors() {
        let r = ConceptRegistry::load_from_evergreen_dir(Path::new("/no/such/dir"));
        assert!(r.is_err());
    }
}
