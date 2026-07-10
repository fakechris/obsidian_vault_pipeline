//! Semantic display themes — the `themes.json` PROJECTION over reader packs.
//!
//! `ovp2 crystal-themes` embeds every reader pack (title + reader.md head),
//! clusters the corpus (Louvain over a non-mutual kNN graph — see `ovp-embed`)
//! and writes `.ovp/crystal/themes.json`. That file is a REBUILDABLE
//! projection: it is never baked into the crystal ledger, and claims are never
//! re-synthesized to re-theme. Consumers:
//!
//! - `ovp-index::build` projects `ClaimRow.theme` = majority community label
//!   among a claim's cited packs (Unclassified when unmapped).
//! - `crystal-synth` groups synthesis batches by community when the file
//!   exists (replacing the retired hardcoded keyword buckets).
//!
//! Labels are presentation-only: deterministic c-TF-IDF keywords are the
//! stable auditable layer, the optional bilingual LLM name (`theme_label/v1`)
//! sits on top.

use std::collections::BTreeMap;
use std::path::Path;

use ovp_llm::{ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::synth::{Cluster, UnitsCatalog};

/// Schema marker for `themes.json`.
pub const THEMES_SCHEMA: &str = "ovp.themes/v1";
/// Community id for packs the clustering left unassigned (noise/singletons).
pub const UNCLASSIFIED_ID: i64 = -1;
/// Display theme for claims with no mapped cited pack.
pub const UNCLASSIFIED_THEME: &str = "Unclassified";

const THEME_LABEL_TEMPLATE: &str = include_str!("../../prompts/theme_label.md");
/// Cassette namespace + version marker for the bilingual labeling stage.
pub const THEME_LABEL_PROMPT_ID: &str = "theme_label/v1";
const LABEL_MODEL: &str = "claude-sonnet-4-6";
const LABEL_MAX_TOKENS: u32 = 300;

/// Clustering parameters, recorded for reproducibility.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemeParams {
    pub k: usize,
    pub cosine_threshold: f64,
    pub resolution: f64,
    pub seed: u64,
    pub text_prefix: String,
    pub head_chars: usize,
}

/// How the community labels were produced. ADDITIVE to `ovp.themes/v1`:
/// files written before this field existed deserialize as [`Keyword`]
/// (`Keyword` was the only offline writer back then), so a later
/// `--client live` run knows it still owes the bilingual naming pass.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LabelsProvenance {
    /// Deterministic c-TF-IDF keyword labels (the offline default).
    #[default]
    Keyword,
    /// Bilingual model-named labels (`--client live`, `theme_label/v1`).
    Llm,
}

/// One discovered community.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemeCommunity {
    pub id: i64,
    pub label: String,
    pub label_zh: String,
    pub keywords: Vec<String>,
    pub size: usize,
}

/// The whole projection file.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemesFile {
    pub schema: String,
    pub model: String,
    pub params: ThemeParams,
    /// sha256 over the sorted `(case_id, text_sha256)` input set — staleness
    /// marker (`input_hash` computes it).
    pub generated_from: String,
    /// EVERY input pack → community id ([`UNCLASSIFIED_ID`] for noise), so a
    /// pack missing from this map is by definition NEW (staleness check).
    pub packs: BTreeMap<String, i64>,
    pub communities: Vec<ThemeCommunity>,
    /// How `communities[].label` was produced (additive; absent in older
    /// files → [`LabelsProvenance::Keyword`]). Lets `crystal-themes` decide
    /// whether a `--client live` run still needs to relabel an otherwise
    /// up-to-date projection.
    #[serde(default)]
    pub labels_provenance: LabelsProvenance,
}

impl ThemesFile {
    /// Read `themes.json`. Missing file → `Ok(None)` (themes are optional);
    /// unparseable or wrong schema → `Err` (a corrupt projection should be
    /// regenerated, not silently ignored).
    pub fn load(path: &Path) -> Result<Option<ThemesFile>, String> {
        if !path.exists() {
            return Ok(None);
        }
        let raw = std::fs::read_to_string(path)
            .map_err(|e| format!("reading {}: {e}", path.display()))?;
        let file: ThemesFile = serde_json::from_str(&raw).map_err(|e| {
            format!(
                "parsing {}: {e} (regenerate with `ovp2 crystal-themes --refresh`)",
                path.display()
            )
        })?;
        if file.schema != THEMES_SCHEMA {
            return Err(format!(
                "{}: unsupported schema `{}` (expected `{THEMES_SCHEMA}`)",
                path.display(),
                file.schema
            ));
        }
        Ok(Some(file))
    }

    fn community(&self, id: i64) -> Option<&ThemeCommunity> {
        self.communities.iter().find(|c| c.id == id)
    }

    /// Display label of the community a pack belongs to (None for noise or
    /// unknown packs).
    pub fn label_of(&self, case_id: &str) -> Option<&str> {
        let id = *self.packs.get(case_id)?;
        if id == UNCLASSIFIED_ID {
            return None;
        }
        self.community(id).map(|c| c.label.as_str())
    }

    /// Majority community label among `case_ids` (ties → lexicographically
    /// first label). `None` when no case maps to a community.
    pub fn majority_label(&self, case_ids: &[String]) -> Option<String> {
        let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
        for id in case_ids {
            if let Some(label) = self.label_of(id) {
                *counts.entry(label).or_insert(0) += 1;
            }
        }
        counts
            .into_iter()
            .max_by(|(la, ca), (lb, cb)| ca.cmp(cb).then(lb.cmp(la)))
            .map(|(label, _)| label.to_string())
    }
}

/// Staleness/identity hash over the input set: sha256 of the sorted
/// `case_id\ttext_sha\n` lines.
pub fn input_hash(inputs: &[(String, String)]) -> String {
    let mut lines: Vec<String> = inputs
        .iter()
        .map(|(case_id, sha)| format!("{case_id}\t{sha}\n"))
        .collect();
    lines.sort();
    let mut hasher = Sha256::new();
    for line in &lines {
        hasher.update(line.as_bytes());
    }
    let digest = hasher.finalize();
    let mut out = String::with_capacity(64);
    for b in digest {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

// ---- Synthesis grouping (replaces the retired keyword buckets) ----

/// Group catalog cases into synthesis clusters by their `themes.json`
/// community. Cases missing from the projection or mapped to noise fall into
/// a trailing `unclassified` cluster. Deterministic: communities in file
/// order, cases sorted within each cluster; empty clusters are dropped.
pub fn clusters_from_themes(catalog: &UnitsCatalog, themes: &ThemesFile) -> Vec<Cluster> {
    let mut by_id: BTreeMap<i64, Vec<String>> = BTreeMap::new();
    let mut unclassified: Vec<String> = Vec::new();
    for case_id in catalog.cases.keys() {
        match themes.packs.get(case_id) {
            Some(&id) if id != UNCLASSIFIED_ID && themes.community(id).is_some() => {
                by_id.entry(id).or_default().push(case_id.clone());
            }
            _ => unclassified.push(case_id.clone()),
        }
    }
    let mut clusters = Vec::new();
    for community in &themes.communities {
        if let Some(mut cases) = by_id.remove(&community.id) {
            cases.sort();
            clusters.push(Cluster {
                key: format!("t{:03}", community.id),
                theme: community.label.clone(),
                cases,
            });
        }
    }
    if !unclassified.is_empty() {
        unclassified.sort();
        clusters.push(Cluster {
            key: "unclassified".to_string(),
            theme: UNCLASSIFIED_THEME.to_string(),
            cases: unclassified,
        });
    }
    clusters
}

/// Date segment of a reader-pack case id, for both dir-name shapes:
/// corpus `<hash8>-<YYYY-MM-DD>_title` and modern `<YYYY-MM-DD>_title-<hash8>`.
fn case_date(case_id: &str) -> Option<&str> {
    let is_date = |s: &str| {
        s.len() == 10 && s.bytes().all(|b| b.is_ascii_digit() || b == b'-') && s.as_bytes()[4] == b'-'
    };
    if let Some(d) = case_id.get(..10)
        && is_date(d)
    {
        return Some(d);
    }
    if let Some(d) = case_id.get(9..19)
        && is_date(d)
    {
        return Some(d);
    }
    None
}

/// Fallback grouping when no semantic themes exist (embed feature off, no
/// model, fresh vault): deterministic date-ordered batches of at most `cap`
/// cases. Cases sort by (date, case_id); undated cases sort last.
pub fn clusters_date_ordered(catalog: &UnitsCatalog, cap: usize) -> Vec<Cluster> {
    if cap == 0 {
        return Vec::new();
    }
    let mut ordered: Vec<&String> = catalog.cases.keys().collect();
    ordered.sort_by_key(|id| (case_date(id).unwrap_or("9999-99-99").to_string(), (*id).clone()));
    ordered
        .chunks(cap)
        .enumerate()
        .map(|(i, chunk)| Cluster {
            key: format!("batch-{:03}", i + 1),
            theme: format!("Unthemed batch {}", i + 1),
            cases: chunk.iter().map(|s| (*s).clone()).collect(),
        })
        .collect()
}

// ---- Bilingual label model stage (`theme_label/v1`) ----

/// Build the labeling `ModelRequest` for one community: its c-TF-IDF keywords
/// plus a few representative titles → a short bilingual name.
pub fn theme_label_request(keywords: &[String], sample_titles: &[String]) -> ModelRequest {
    let marker = "## Community";
    let (system, _) = THEME_LABEL_TEMPLATE
        .split_once(marker)
        .unwrap_or((THEME_LABEL_TEMPLATE, ""));
    let mut user = format!("{marker}\n\nKeywords: {}\n\nRepresentative titles:\n", keywords.join(", "));
    for t in sample_titles {
        user.push_str(&format!("- {t}\n"));
    }
    ModelRequest {
        model: LABEL_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: LABEL_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(THEME_LABEL_PROMPT_ID.to_string()),
    }
}

/// Parse a labeling reply: `{"label": "...", "label_zh": "..."}`.
pub fn parse_theme_label(reply_text: &str) -> Result<(String, String), String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    let label = value
        .get("label")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or("missing `label`")?;
    let label_zh = value
        .get("label_zh")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(label);
    Ok((label.to_string(), label_zh.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crystal::synth::CatalogCase;

    fn themes_fixture() -> ThemesFile {
        ThemesFile {
            schema: THEMES_SCHEMA.into(),
            model: "test-model".into(),
            params: ThemeParams {
                k: 10,
                cosine_threshold: 0.5,
                resolution: 1.5,
                seed: 42,
                text_prefix: "passage: ".into(),
                head_chars: 1500,
            },
            generated_from: "abc".into(),
            packs: BTreeMap::from([
                ("case-a".to_string(), 0),
                ("case-b".to_string(), 0),
                ("case-c".to_string(), 1),
                ("case-noise".to_string(), UNCLASSIFIED_ID),
            ]),
            communities: vec![
                ThemeCommunity {
                    id: 0,
                    label: "Agent memory".into(),
                    label_zh: "智能体记忆".into(),
                    keywords: vec!["memory".into()],
                    size: 2,
                },
                ThemeCommunity {
                    id: 1,
                    label: "Quant markets".into(),
                    label_zh: "量化市场".into(),
                    keywords: vec!["quant".into()],
                    size: 1,
                },
            ],
            labels_provenance: LabelsProvenance::Keyword,
        }
    }

    #[test]
    fn labels_provenance_is_additive_older_files_default_to_keyword() {
        // A pre-provenance v1 file (no `labels_provenance` key) must load,
        // and default to Keyword.
        let raw = serde_json::json!({
            "schema": THEMES_SCHEMA,
            "model": "test-model",
            "params": {
                "k": 10, "cosine_threshold": 0.5, "resolution": 1.5,
                "seed": 42, "text_prefix": "passage: ", "head_chars": 1500
            },
            "generated_from": "abc",
            "packs": {},
            "communities": []
        });
        let file: ThemesFile = serde_json::from_value(raw).unwrap();
        assert_eq!(file.labels_provenance, LabelsProvenance::Keyword);
        // And the field round-trips snake_case.
        let s = serde_json::to_string(&ThemesFile { labels_provenance: LabelsProvenance::Llm, ..file }).unwrap();
        assert!(s.contains(r#""labels_provenance":"llm""#), "{s}");
    }

    fn catalog_of(ids: &[&str]) -> UnitsCatalog {
        let mut cat = UnitsCatalog::default();
        for id in ids {
            cat.cases.insert(
                (*id).to_string(),
                CatalogCase {
                    title: format!("Title {id}"),
                    units: vec![],
                },
            );
        }
        cat
    }

    #[test]
    fn label_of_maps_and_noise_is_none() {
        let t = themes_fixture();
        assert_eq!(t.label_of("case-a"), Some("Agent memory"));
        assert_eq!(t.label_of("case-noise"), None);
        assert_eq!(t.label_of("unknown"), None);
    }

    #[test]
    fn majority_label_counts_and_breaks_ties_lexicographically() {
        let t = themes_fixture();
        // Two in community 0, one in community 1 → majority Agent memory.
        let major = t.majority_label(&[
            "case-a".into(),
            "case-b".into(),
            "case-c".into(),
        ]);
        assert_eq!(major.as_deref(), Some("Agent memory"));
        // 1:1 tie → lexicographically first label.
        let tie = t.majority_label(&["case-a".into(), "case-c".into()]);
        assert_eq!(tie.as_deref(), Some("Agent memory"));
        // No mapped case → None.
        assert_eq!(t.majority_label(&["case-noise".into(), "x".into()]), None);
    }

    #[test]
    fn input_hash_is_order_independent() {
        let a = input_hash(&[("a".into(), "1".into()), ("b".into(), "2".into())]);
        let b = input_hash(&[("b".into(), "2".into()), ("a".into(), "1".into())]);
        assert_eq!(a, b);
        let c = input_hash(&[("a".into(), "CHANGED".into()), ("b".into(), "2".into())]);
        assert_ne!(a, c);
    }

    #[test]
    fn clusters_from_themes_groups_and_collects_unclassified() {
        let t = themes_fixture();
        let cat = catalog_of(&["case-a", "case-b", "case-c", "case-noise", "case-new"]);
        let clusters = clusters_from_themes(&cat, &t);
        assert_eq!(clusters.len(), 3);
        assert_eq!(clusters[0].key, "t000");
        assert_eq!(clusters[0].theme, "Agent memory");
        assert_eq!(clusters[0].cases, vec!["case-a", "case-b"]);
        assert_eq!(clusters[1].key, "t001");
        assert_eq!(clusters[2].key, "unclassified");
        assert_eq!(clusters[2].cases, vec!["case-new", "case-noise"]);
    }

    #[test]
    fn clusters_date_ordered_chunks_by_date() {
        let cat = catalog_of(&[
            "2026-06-01_newer-aaaa1111",
            "00044cfd-2026-05-07_corpus",
            "zzz-undated",
        ]);
        let clusters = clusters_date_ordered(&cat, 2);
        assert_eq!(clusters.len(), 2);
        assert_eq!(clusters[0].key, "batch-001");
        assert_eq!(
            clusters[0].cases,
            vec!["00044cfd-2026-05-07_corpus", "2026-06-01_newer-aaaa1111"],
            "corpus date sorts before modern date"
        );
        assert_eq!(clusters[1].cases, vec!["zzz-undated"], "undated last");
        assert!(clusters_date_ordered(&cat, 0).is_empty());
    }

    #[test]
    fn theme_label_request_and_parse_roundtrip() {
        let req = theme_label_request(
            &["memory".into(), "agent".into()],
            &["Agent memory systems".into()],
        );
        assert_eq!(req.cache_namespace.as_deref(), Some("theme_label/v1"));
        assert!(req.system.as_deref().unwrap().contains("theme_label/v1"));
        let ModelMessage::User { content } = &req.messages[0] else {
            panic!()
        };
        assert!(content.contains("memory, agent"));
        assert!(content.contains("Agent memory systems"));

        let (label, zh) =
            parse_theme_label(r#"{"label":"Agent memory","label_zh":"智能体记忆"}"#).unwrap();
        assert_eq!(label, "Agent memory");
        assert_eq!(zh, "智能体记忆");
        // Missing zh falls back to the English label.
        let (l2, zh2) = parse_theme_label(r#"{"label":"Solo"}"#).unwrap();
        assert_eq!((l2.as_str(), zh2.as_str()), ("Solo", "Solo"));
        assert!(parse_theme_label(r#"{"nope":1}"#).is_err());
    }

    #[test]
    fn themes_file_load_missing_and_corrupt() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("themes.json");
        assert_eq!(ThemesFile::load(&path).unwrap(), None);
        std::fs::write(&path, "not json").unwrap();
        assert!(ThemesFile::load(&path).is_err());
        let mut good = themes_fixture();
        good.schema = "ovp.themes/v999".into();
        std::fs::write(&path, serde_json::to_string(&good).unwrap()).unwrap();
        assert!(ThemesFile::load(&path).is_err(), "wrong schema fails loud");
        good.schema = THEMES_SCHEMA.into();
        std::fs::write(&path, serde_json::to_string(&good).unwrap()).unwrap();
        let loaded = ThemesFile::load(&path).unwrap().unwrap();
        assert_eq!(loaded, good);
    }
}
