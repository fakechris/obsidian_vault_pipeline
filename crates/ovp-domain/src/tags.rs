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
            // Boilerplate is filtered BEFORE alias resolution, so an alias
            // keyed on it could never fire — dead config fails loud like
            // every other structural-rot case here.
            if BOILERPLATE_TAGS.contains(&alias.as_str()) {
                return Err(format!(
                    "tag aliases: {alias:?} is a boilerplate capture tag; it is \
                     filtered before alias resolution, so this entry can never fire"
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

    /// Resolve a RAW user-supplied tag (query params, CLI flags): normalize,
    /// then alias-resolve. `None` when nothing survives normalization — the
    /// caller decides whether that is an error (CLI) or a match-nothing
    /// passthrough (read endpoints). The one helper every query surface uses.
    pub fn resolve_raw(&self, raw: &str) -> Option<String> {
        normalize_tag(raw).map(|n| self.resolve(&n).to_string())
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
    /// Coarse confidence: kNN = neighbor-weight share (0..1); bootstrap
    /// methods use fixed bands (llm 0.8, community floor 0.4).
    pub score: f64,
    /// kNN: number of neighbors carrying the tag; community floor: the
    /// community size; llm: 0 (a judgment, not a vote).
    pub support: usize,
    /// How this tag was inferred: `knn` | `community` | `llm`.
    /// Serde-additive: pre-bootstrap files deserialize to `""` (= knn era).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub method: String,
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

pub const TAGS_VOCABULARY_SCHEMA: &str = "ovp.tags-vocabulary/v1";

/// Where a vocabulary entry came from. `User` and `Community` entries are
/// re-derived on every bootstrap run; `Llm` entries are the one thing worth
/// persisting (a reviewed-and-survived model proposal is not re-derivable).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TagOrigin {
    /// Observed as an operator tag in the index.
    User,
    /// A theme community's c-TF-IDF keyword (the deterministic seed).
    Community,
    /// Survived the classifier's capped new-name budget + embedding dedup.
    Llm,
}

/// `.ovp/tags/vocabulary.toml` — the CLOSED list the tag classifier may pick
/// from: user tags ∪ community keywords ∪ surviving LLM proposals. A
/// projection: `tags-bootstrap` rebuilds the user/community entries each run
/// and carries the llm entries forward. Curation: deleting an `llm` line
/// removes it permanently (not re-derivable); user/community entries would
/// be re-derived on the next run, so banning those goes through the
/// persistent `banned` list, which every insert respects.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TagVocabulary {
    entries: BTreeMap<String, TagOrigin>,
    banned: std::collections::BTreeSet<String>,
}

impl TagVocabulary {
    /// Insert a normalized name; an existing entry keeps its origin (user
    /// beats community beats llm because bootstrap inserts in that order).
    /// Banned names never enter.
    pub fn insert(&mut self, name: String, origin: TagOrigin) {
        if self.banned.contains(&name) {
            return;
        }
        self.entries.entry(name).or_insert(origin);
    }

    /// Ban a name from the vocabulary (persists across bootstrap runs).
    pub fn ban(&mut self, name: String) {
        self.entries.remove(&name);
        self.banned.insert(name);
    }

    pub fn banned(&self) -> impl Iterator<Item = &str> {
        self.banned.iter().map(String::as_str)
    }

    pub fn contains(&self, name: &str) -> bool {
        self.entries.contains_key(name)
    }

    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.entries.keys().map(String::as_str)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, TagOrigin)> {
        self.entries.iter().map(|(k, v)| (k.as_str(), *v))
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn parse(text: &str) -> Result<Self, String> {
        #[derive(serde::Deserialize)]
        #[serde(deny_unknown_fields)]
        struct File {
            schema: String,
            #[serde(default)]
            banned: Vec<String>,
            #[serde(default)]
            tags: BTreeMap<String, TagOrigin>,
        }
        let file: File =
            toml::from_str(text).map_err(|e| format!("tag vocabulary: invalid TOML: {e}"))?;
        if file.schema != TAGS_VOCABULARY_SCHEMA {
            return Err(format!(
                "tag vocabulary: unknown schema {:?} (expected {TAGS_VOCABULARY_SCHEMA:?})",
                file.schema
            ));
        }
        let mut banned = std::collections::BTreeSet::new();
        for name in file.banned {
            let n = normalize_tag(&name)
                .ok_or_else(|| format!("tag vocabulary: banned {name:?} normalizes to nothing"))?;
            banned.insert(n);
        }
        let mut entries = BTreeMap::new();
        for (name, origin) in file.tags {
            let n = normalize_tag(&name)
                .ok_or_else(|| format!("tag vocabulary: {name:?} normalizes to nothing"))?;
            if !banned.contains(&n) {
                entries.insert(n, origin);
            }
        }
        Ok(Self { entries, banned })
    }

    /// Missing file → empty (bootstrap seeds it); broken file → error.
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        let path = vault_root.join(crate::vault_layout::VaultLayout.tags_vocabulary_file());
        match std::fs::read_to_string(&path) {
            Ok(text) => Self::parse(&text),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(format!("reading {}: {e}", path.display())),
        }
    }

    pub fn save(&self, vault_root: &Path) -> Result<String, String> {
        let path = vault_root.join(crate::vault_layout::VaultLayout.tags_vocabulary_file());
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("creating {}: {e}", parent.display()))?;
        }
        let mut body = String::from(
            "# Closed tag vocabulary — the classifier may only pick from this list.\n\
             # Rebuilt by `ovp2 tags-bootstrap`: user/community entries are re-derived\n\
             # each run (deleting them does NOT stick — add the name to `banned`);\n\
             # llm entries persist, so deleting an llm line removes it for good.\n\
             schema = \"ovp.tags-vocabulary/v1\"\n",
        );
        if self.banned.is_empty() {
            body.push_str("banned = []\n\n[tags]\n");
        } else {
            body.push_str("banned = [\n");
            for name in &self.banned {
                body.push_str(&format!("  {},\n", toml_basic_string(name)));
            }
            body.push_str("]\n\n[tags]\n");
        }
        for (name, origin) in &self.entries {
            let origin = match origin {
                TagOrigin::User => "user",
                TagOrigin::Community => "community",
                TagOrigin::Llm => "llm",
            };
            body.push_str(&format!("{} = \"{origin}\"\n", toml_basic_string(name)));
        }
        std::fs::write(&path, body).map_err(|e| format!("writing {}: {e}", path.display()))?;
        Ok(path.display().to_string())
    }
}

// ---- Classification model stage (`tag_classify/v1`) ----

const TAG_CLASSIFY_TEMPLATE: &str = include_str!("../prompts/tag_classify.md");
pub const TAG_CLASSIFY_PROMPT_ID: &str = "tag_classify/v1";
const TAG_CLASSIFY_MODEL: &str = "claude-sonnet-4-6";
const TAG_CLASSIFY_MAX_TOKENS: u32 = 2000;

/// One source in a classification batch.
pub struct ClassifyInput {
    /// Batch-local id echoed back by the model.
    pub id: usize,
    pub title: String,
    /// Card titles (the pack's synthesized surface) — the content signal.
    pub card_titles: Vec<String>,
}

/// Build the batched classification request: the closed vocabulary + the
/// batch's sources. Deterministic text → stable cassette keys.
pub fn tag_classify_request(
    vocabulary: &[&str],
    sources: &[ClassifyInput],
    max_new: usize,
) -> ovp_llm::ModelRequest {
    let marker = "## Batch";
    let (system, _) = TAG_CLASSIFY_TEMPLATE
        .split_once(marker)
        .unwrap_or((TAG_CLASSIFY_TEMPLATE, ""));
    let mut user = format!(
        "{marker}\n\nAt most {max_new} `new_tags` for this whole batch.\n\nVocabulary: {}\n\nSources:\n",
        vocabulary.join(", ")
    );
    for s in sources {
        user.push_str(&format!("{}. {}", s.id, s.title));
        if !s.card_titles.is_empty() {
            user.push_str(&format!(" — {}", s.card_titles.join(" | ")));
        }
        user.push('\n');
    }
    ovp_llm::ModelRequest {
        model: TAG_CLASSIFY_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ovp_llm::ModelMessage::User { content: user }],
        max_tokens: TAG_CLASSIFY_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(TAG_CLASSIFY_PROMPT_ID.to_string()),
    }
}

/// Parsed classification reply.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ClassifyReply {
    /// Batch-local id → picked tag names (raw; caller validates vs vocab).
    pub picks: BTreeMap<usize, Vec<String>>,
    pub new_tags: Vec<String>,
}

/// Parse `{"sources":[{"id":0,"tags":[...]},…],"new_tags":[...]}` for a
/// batch of ids `0..expected`. A missing, duplicate, or out-of-range id is a
/// parse ERROR (not a silent gap) so `call_and_parse` invalidates the
/// cassette entry and the retry re-asks the model.
pub fn parse_tag_classify(reply_text: &str, expected: usize) -> Result<ClassifyReply, String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    let sources = value
        .get("sources")
        .and_then(|v| v.as_array())
        .ok_or("missing `sources` array")?;
    let mut picks = BTreeMap::new();
    for s in sources {
        let id = s
            .get("id")
            .and_then(|v| v.as_u64())
            .ok_or("source missing numeric `id`")? as usize;
        if id >= expected {
            return Err(format!("source id {id} out of range (batch of {expected})"));
        }
        let tags: Vec<String> = s
            .get("tags")
            .and_then(|v| v.as_array())
            .ok_or("source missing `tags` array")?
            .iter()
            .filter_map(|t| t.as_str())
            .map(|t| t.trim().to_string())
            .filter(|t| !t.is_empty())
            .collect();
        if picks.insert(id, tags).is_some() {
            return Err(format!("duplicate source id {id}"));
        }
    }
    if picks.len() != expected {
        return Err(format!(
            "reply covers {}/{expected} batch ids — every input id must appear exactly once",
            picks.len()
        ));
    }
    let new_tags: Vec<String> = value
        .get("new_tags")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|t| t.as_str())
                .map(|t| t.trim().to_string())
                .filter(|t| !t.is_empty())
                .collect()
        })
        .unwrap_or_default();
    Ok(ClassifyReply { picks, new_tags })
}

/// A tag name as a valid TOML basic string — normalized tags can still carry
/// quotes/backslashes/control characters, which unescaped would break the
/// vocabulary file and the paste-ready proposals block. One implementation
/// for every writer.
pub fn toml_basic_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 || c == '\u{7f}' => {
                out.push_str(&format!("\\u{:04X}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
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
    fn vocabulary_round_trips_and_first_origin_wins() {
        let mut v = TagVocabulary::default();
        v.insert("agent".into(), TagOrigin::User);
        v.insert("agent".into(), TagOrigin::Llm); // ignored — user wins
        v.insert("向量检索".into(), TagOrigin::Community);
        let dir = tempfile::tempdir().unwrap();
        v.save(dir.path()).unwrap();
        let loaded = TagVocabulary::load(dir.path()).unwrap();
        assert_eq!(loaded, v);
        assert_eq!(loaded.iter().next(), Some(("agent", TagOrigin::User)));
        // Missing file → empty; typo section → error.
        assert!(TagVocabulary::load(tempfile::tempdir().unwrap().path()).unwrap().is_empty());
        assert!(TagVocabulary::parse("schema = \"ovp.tags-vocabulary/v1\"\n[tag]\n").is_err());
    }

    #[test]
    fn classify_reply_parses_and_rejects_duplicates_gaps_and_strays() {
        let ok = parse_tag_classify(
            r#"{"sources":[{"id":0,"tags":["agent"," memory "]},{"id":1,"tags":[]}],"new_tags":["kv-cache"]}"#,
            2,
        )
        .unwrap();
        assert_eq!(ok.picks[&0], vec!["agent", "memory"]);
        assert!(ok.picks[&1].is_empty());
        assert_eq!(ok.new_tags, vec!["kv-cache"]);
        // Duplicate id, missing id, and out-of-range id are all parse errors.
        assert!(
            parse_tag_classify(r#"{"sources":[{"id":0,"tags":[]},{"id":0,"tags":[]}]}"#, 2)
                .is_err()
        );
        assert!(parse_tag_classify(r#"{"sources":[{"id":0,"tags":[]}]}"#, 2).is_err());
        assert!(parse_tag_classify(r#"{"sources":[{"id":5,"tags":[]}]}"#, 1).is_err());
        assert!(parse_tag_classify(r#"{"new_tags":[]}"#, 0).is_err());
    }

    #[test]
    fn vocabulary_bans_persist_and_block_reinsertion() {
        let mut v = TagVocabulary::default();
        v.insert("twitter".into(), TagOrigin::User);
        v.ban("twitter".into());
        v.insert("twitter".into(), TagOrigin::Community); // blocked
        assert!(!v.contains("twitter"));
        let dir = tempfile::tempdir().unwrap();
        v.save(dir.path()).unwrap();
        let loaded = TagVocabulary::load(dir.path()).unwrap();
        assert!(loaded.banned().any(|b| b == "twitter"));
        assert!(!loaded.contains("twitter"));
    }

    #[test]
    fn classify_request_is_deterministic_and_carries_the_cap() {
        let sources = vec![ClassifyInput {
            id: 0,
            title: "T".into(),
            card_titles: vec!["c1".into(), "c2".into()],
        }];
        let a = tag_classify_request(&["agent", "memory"], &sources, 2);
        let b = tag_classify_request(&["agent", "memory"], &sources, 2);
        assert_eq!(a.messages, b.messages);
        let ovp_llm::ModelMessage::User { content } = &a.messages[0] else {
            panic!("expected a user message");
        };
        assert!(content.contains("At most 2 `new_tags`"), "{content}");
        assert!(content.contains("0. T — c1 | c2"), "{content}");
    }

    #[test]
    fn parse_normalizes_both_sides_and_missing_section_is_empty() {
        let t = TagAliases::parse("[aliases]\n\"AI_Agents\" = \"Agent\"\n").unwrap();
        assert_eq!(t.resolve("ai-agents"), "agent");
        assert!(TagAliases::parse("").unwrap().is_empty());
    }
}
