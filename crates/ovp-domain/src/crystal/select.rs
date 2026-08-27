//! `cluster_select/v1` — L3 LLM-shaped synthesis clusters (the model stage of
//! `crystal-synth --cluster-mode llm`). One call per uncovered seed pack: the
//! model reads the seed's digest plus its kNN neighborhood digests and either
//! picks 3..cap case ids that form ONE claim-worthy cross-source cluster, or
//! REFUSES ("no opportunity" is a first-class answer).
//!
//! Everything downstream of the selection is the EXISTING, unchanged pipeline:
//! `crystal_synth/v1` + strength + gates + idempotent durable write. This
//! module only proposes groupings; it can never touch grounding or the ledger.
//! Selections are validated MECHANICALLY ([`validate_selection`]): ids must
//! come from the offered set, ≥ [`MIN_CLUSTER_CASES`], ≤ cap — a violation
//! fails that seed loudly (recorded) and the sweep continues.

use std::collections::BTreeSet;

use ovp_llm::{ModelMessage, ModelRequest};

use crate::crystal::alias::{Aliases, alias_for};
use serde::{Deserialize, Serialize};

const SELECT_TEMPLATE: &str = include_str!("../../prompts/cluster_select.md");
/// Cassette namespace + version marker for the cluster-selection stage.
// v2: the digests carry SHORT positional `case_id`s (`c1`, `c2`, …) instead of
// the long `<hash8>-<date>_<title>` form the model kept returning wrong. New
// namespace because the request text changed — v1 cassettes must not replay.
pub const CLUSTER_SELECT_PROMPT_ID: &str = "cluster_select/v2";
const DEFAULT_MODEL: &str = "claude-sonnet-4-6";
/// Selection replies are tiny (a handful of ids + one sentence).
const SELECT_MAX_TOKENS: u32 = 1024;

/// A durable cross-source claim needs at least this many distinct cases —
/// the selection floor mirrors the synthesis goal, not the provenance gate
/// (which needs ≥2): asking for 3+ gives the synth call room to keep a
/// 2-source claim after one case contributes nothing.
pub const MIN_CLUSTER_CASES: usize = 3;

/// Compact digest of one reader pack as shown to the selector: id + title +
/// card titles. Deliberately NO quotes/units — the selector shapes groups,
/// the synth stage sees the evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CaseDigest {
    pub case_id: String,
    pub title: String,
    pub card_titles: Vec<String>,
}

impl CaseDigest {
    /// The same digest under a short handle. Only the id the model has to copy
    /// changes; the title and card titles it reasons over are untouched.
    pub fn with_alias(&self, alias: String) -> CaseDigest {
        CaseDigest {
            case_id: alias,
            title: self.title.clone(),
            card_titles: self.card_titles.clone(),
        }
    }
}

/// The alias table a `cluster_select` reply must be resolved against: the seed
/// is `c1`, then the neighbors in order.
pub fn selection_aliases(seed: &CaseDigest, neighbors: &[CaseDigest]) -> Aliases {
    Aliases::new(
        std::iter::once(seed.case_id.as_str()).chain(neighbors.iter().map(|n| n.case_id.as_str())),
    )
}

/// Drop a trailing `_tag_` token (the reader card-kind italics:
/// `_definition_`, `_fact_`, …) if present.
fn strip_trailing_em_tag(s: &str) -> &str {
    let trimmed = s.trim_end();
    if let Some(last) = trimmed.rsplit_once(char::is_whitespace).map(|(_, l)| l)
        && last.len() > 2
        && last.starts_with('_')
        && last.ends_with('_')
    {
        return trimmed[..trimmed.len() - last.len()].trim_end();
    }
    trimmed
}

/// Build a digest from a pack's resolved title + its `reader.md` body: the
/// card titles are the `## ` headings (leading ordinal and trailing card-kind
/// tag stripped). Deterministic, line-based; an empty/missing reader body
/// yields an empty card list (the title still carries signal).
pub fn digest_from_reader_md(case_id: &str, title: &str, reader_md: &str) -> CaseDigest {
    let mut card_titles = Vec::new();
    for line in reader_md.lines() {
        let t = line.trim_start();
        let Some(rest) = t.strip_prefix("## ") else {
            continue;
        };
        // "## 3. Card heading  _definition_" → "Card heading".
        let mut h = rest.trim();
        if let Some((num, tail)) = h.split_once('.')
            && !num.is_empty()
            && num.bytes().all(|b| b.is_ascii_digit())
        {
            h = tail.trim_start();
        }
        let h = strip_trailing_em_tag(h);
        if !h.is_empty() {
            card_titles.push(h.to_string());
        }
    }
    CaseDigest {
        case_id: case_id.to_string(),
        title: title.to_string(),
        card_titles,
    }
}

/// The model-input payload: seed + numbered neighbors + the size bounds.
/// Serialized as pretty JSON into the user message so the request (and thus
/// the cassette key) is a pure function of the digests and caps.
#[derive(Debug, Clone, Serialize)]
struct SelectInput<'a> {
    min_cases: usize,
    max_cases: usize,
    seed: &'a CaseDigest,
    neighbors: &'a [CaseDigest],
}

/// Build the selection `ModelRequest` for one seed (namespace =
/// cluster_select/v1). `max_cases` is the synthesis cluster cap.
pub fn cluster_select_request(
    seed: &CaseDigest,
    neighbors: &[CaseDigest],
    max_cases: usize,
) -> ModelRequest {
    let marker = "## Corpus";
    let (system, _) = SELECT_TEMPLATE
        .split_once(marker)
        .unwrap_or((SELECT_TEMPLATE, ""));
    // The model sees SHORT handles, never the real ids. The offered ids run to
    // ~60 characters of hash + date + title; a fresh live sweep had the model
    // return one that had never been offered, which `validate_selection`
    // correctly refused — killing that seed. See crystal::alias.
    let aliased_seed = seed.with_alias(alias_for(0));
    let aliased_neighbors: Vec<CaseDigest> = neighbors
        .iter()
        .enumerate()
        .map(|(i, n)| n.with_alias(alias_for(i + 1)))
        .collect();
    let input = SelectInput {
        min_cases: MIN_CLUSTER_CASES,
        max_cases,
        seed: &aliased_seed,
        neighbors: &aliased_neighbors,
    };
    let user = format!(
        "{marker}\n\n{}\n",
        serde_json::to_string_pretty(&input).unwrap_or_else(|_| "{}".to_string())
    );
    ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: SELECT_MAX_TOKENS,
        temperature: None,
        tools: None,
        cache_namespace: Some(CLUSTER_SELECT_PROMPT_ID.to_string()),
    }
}

/// The parsed selection reply. Refusal is a first-class outcome, not an error.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum ClusterSelection {
    Selected {
        case_ids: Vec<String>,
        rationale: String,
    },
    Refused {
        reason: String,
    },
}

/// Parse a `cluster_select/v1` reply: either `{"selected_case_ids": [...],
/// "rationale": "..."}` or `{"refuse": true, "reason": "..."}`. Ids are
/// trimmed; empty ids are dropped here (mechanical set/size checks are
/// [`validate_selection`]'s job). `Err(detail)` when neither shape is found.
pub fn parse_cluster_selection(reply_text: &str) -> Result<ClusterSelection, String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    if value.get("refuse").and_then(|v| v.as_bool()) == Some(true) {
        let reason = value
            .get("reason")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("(no reason given)")
            .to_string();
        return Ok(ClusterSelection::Refused { reason });
    }
    let arr = value
        .get("selected_case_ids")
        .and_then(|v| v.as_array())
        .ok_or("missing `selected_case_ids` array (and no `refuse: true`)")?;
    let mut case_ids = Vec::with_capacity(arr.len());
    for item in arr {
        let Some(s) = item.as_str() else {
            return Err("`selected_case_ids` must be an array of strings".to_string());
        };
        let s = s.trim();
        if !s.is_empty() {
            case_ids.push(s.to_string());
        }
    }
    let rationale = value
        .get("rationale")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .unwrap_or_default()
        .to_string();
    Ok(ClusterSelection::Selected {
        case_ids,
        rationale,
    })
}

/// Mechanically validate a selection against the offered set and size bounds.
/// Returns the sorted, deduplicated case ids on success. A violation is a
/// per-seed failure (the caller records it and continues the sweep — it never
/// aborts the run). Deterministic + total.
pub fn validate_selection(
    offered: &BTreeSet<String>,
    selected: &[String],
    max_cases: usize,
) -> Result<Vec<String>, String> {
    let mut ids: Vec<String> = selected.to_vec();
    ids.sort();
    ids.dedup();
    let outside: Vec<&String> = ids.iter().filter(|id| !offered.contains(*id)).collect();
    if !outside.is_empty() {
        return Err(format!(
            "selected id(s) not in the offered set: {}",
            outside
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if ids.len() < MIN_CLUSTER_CASES {
        return Err(format!(
            "selected {} distinct case(s); a cluster needs at least {MIN_CLUSTER_CASES}",
            ids.len()
        ));
    }
    if ids.len() > max_cases {
        return Err(format!(
            "selected {} distinct case(s); the cluster cap is {max_cases}",
            ids.len()
        ));
    }
    Ok(ids)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(id: &str) -> CaseDigest {
        CaseDigest {
            case_id: id.to_string(),
            title: format!("Title {id}"),
            card_titles: vec![format!("Card of {id}")],
        }
    }

    #[test]
    fn digest_from_reader_md_extracts_card_titles() {
        let md = "# Claude Code 源码解读\n\n\
            > 12 cards · 26 grounded units\n\n\
            ## 1. Agent Loop 是心脏  _definition_\n\nbody\n\n\
            ## 2. 分层解耦\n\nbody\n\n\
            ### not a card heading\n";
        let d = digest_from_reader_md("c1", "Claude Code 源码解读", md);
        assert_eq!(d.case_id, "c1");
        assert_eq!(d.card_titles, vec!["Agent Loop 是心脏", "分层解耦"]);
    }

    #[test]
    fn digest_survives_empty_reader_md() {
        let d = digest_from_reader_md("c1", "T", "");
        assert!(d.card_titles.is_empty());
        assert_eq!(d.title, "T");
    }

    #[test]
    fn request_carries_namespace_seed_and_neighbors() {
        let seed = digest("seed-1");
        let neighbors = vec![digest("n-1"), digest("n-2")];
        let req = cluster_select_request(&seed, &neighbors, 16);
        assert_eq!(req.cache_namespace.as_deref(), Some("cluster_select/v2"));
        assert!(req.system.as_deref().unwrap().contains("cluster_select/v2"));
        let ModelMessage::User { content } = &req.messages[0] else {
            panic!()
        };
        // Assert on the case_id FIELDS, not on the raw text: the titles
        // legitimately contain the id here (`Title seed-1`) and the model is
        // supposed to read those. What must not reach it is the long id as
        // the thing it has to copy back.
        let json: serde_json::Value =
            serde_json::from_str(content.split_once("\n\n").unwrap().1.trim()).unwrap();
        assert_eq!(json["seed"]["case_id"], "c1");
        assert_eq!(json["neighbors"][0]["case_id"], "c2");
        assert_eq!(json["neighbors"][1]["case_id"], "c3");
        // Titles and card titles are untouched — only the id is aliased.
        assert_eq!(json["seed"]["title"], "Title seed-1");
        assert_eq!(json["neighbors"][1]["card_titles"][0], "Card of n-2");
        assert!(content.contains("\"max_cases\": 16"));
        assert!(content.contains("\"min_cases\": 3"));
    }

    #[test]
    fn a_selection_of_handles_resolves_back_to_the_real_case_ids() {
        let seed = digest("seed-1");
        let neighbors = vec![digest("n-1"), digest("n-2")];
        let table = selection_aliases(&seed, &neighbors);
        assert_eq!(
            table.resolve_all(["c1", "c3"].into_iter()),
            vec!["seed-1".to_string(), "n-2".to_string()]
        );
    }

    #[test]
    fn a_handle_that_was_never_offered_still_fails_validation() {
        // The bug being fixed was an id outside the offered set. Shortening
        // the handle must not make an out-of-range one resolve to something.
        let seed = digest("seed-1");
        let neighbors = vec![digest("n-1")];
        let table = selection_aliases(&seed, &neighbors);
        let resolved = table.resolve_all(["c1", "c9"].into_iter());
        assert_eq!(resolved[1], "c9", "kept verbatim so the error names it");
        let offered: BTreeSet<String> =
            ["seed-1".to_string(), "n-1".to_string()].into_iter().collect();
        assert!(validate_selection(&offered, &resolved, 16).is_err());
    }

    #[test]
    fn request_is_a_pure_function_of_digests() {
        let seed = digest("seed-1");
        let n = vec![digest("n-1")];
        let a = ovp_llm::request_key(&cluster_select_request(&seed, &n, 16));
        let b = ovp_llm::request_key(&cluster_select_request(&seed, &n, 16));
        assert_eq!(a, b);
        let c = ovp_llm::request_key(&cluster_select_request(&seed, &n, 8));
        assert_ne!(a, c, "cap is part of the request identity");
    }

    #[test]
    fn parse_selected_and_refused_and_garbage() {
        let sel = parse_cluster_selection(
            r#"{"selected_case_ids":[" a ","b","c"],"rationale":"shared topic"}"#,
        )
        .unwrap();
        assert_eq!(
            sel,
            ClusterSelection::Selected {
                case_ids: vec!["a".into(), "b".into(), "c".into()],
                rationale: "shared topic".into()
            }
        );
        let refused = parse_cluster_selection(r#"{"refuse":true,"reason":"scattered"}"#).unwrap();
        assert_eq!(
            refused,
            ClusterSelection::Refused {
                reason: "scattered".into()
            }
        );
        // refuse:true with no reason still parses (reason placeholder).
        let bare = parse_cluster_selection(r#"{"refuse":true}"#).unwrap();
        assert!(matches!(bare, ClusterSelection::Refused { .. }));
        // Neither shape → Err.
        assert!(parse_cluster_selection(r#"{"claims":[]}"#).is_err());
        // Non-string ids → Err.
        assert!(parse_cluster_selection(r#"{"selected_case_ids":[1,2,3]}"#).is_err());
    }

    #[test]
    fn validate_selection_enforces_set_and_bounds() {
        let offered: BTreeSet<String> =
            ["a", "b", "c", "d"].iter().map(|s| s.to_string()).collect();
        // Happy path: sorted + deduped.
        let ok = validate_selection(&offered, &["c".into(), "a".into(), "b".into(), "a".into()], 4)
            .unwrap();
        assert_eq!(ok, vec!["a", "b", "c"]);
        // Outside the offered set.
        let err = validate_selection(&offered, &["a".into(), "b".into(), "zzz".into()], 4)
            .unwrap_err();
        assert!(err.contains("zzz"), "{err}");
        // Too few (dedup counts distinct ids).
        let err =
            validate_selection(&offered, &["a".into(), "a".into(), "b".into()], 4).unwrap_err();
        assert!(err.contains("at least 3"), "{err}");
        // Over the cap.
        let err = validate_selection(
            &offered,
            &["a".into(), "b".into(), "c".into(), "d".into()],
            3,
        )
        .unwrap_err();
        assert!(err.contains("cap is 3"), "{err}");
    }
}
