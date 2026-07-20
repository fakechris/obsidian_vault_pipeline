//! Grounded theme pages — the `theme_pages.json` PROJECTION over durable claims.
//!
//! `ovp2 crystal-theme-pages` groups the ACTIVE durable claims by their
//! majority theme community (the same vote `ovp-index` uses for
//! `ClaimRow.theme`), asks the model to weave each group into a short wiki
//! page (`theme_page/v1`), and gates the reply through a DETERMINISTIC
//! verifier: every paragraph must cite at least one `[claim:<claim_key>]`
//! and every cited key must be one of the claims supplied for that theme.
//! A page that fails the verifier is never written.
//!
//! `.ovp/crystal/theme_pages.json` is a REBUILDABLE projection: it is never
//! baked into the crystal ledger, and pages are regenerated only when a
//! theme's claim set changes (`ThemePage.claim_keys` is the staleness
//! marker). Display labels are refreshed from `themes.json` at write time and
//! never enter the synthesis request — a relabel must not move a cassette key
//! (same contract as `ThemeCommunity::synth_theme`).

use std::collections::BTreeSet;
use std::path::Path;

use ovp_llm::{ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};

/// Schema marker for `theme_pages.json`.
pub const THEME_PAGES_SCHEMA: &str = "ovp.theme_pages/v1";
/// Cassette namespace + version marker for the page-synthesis stage.
pub const THEME_PAGE_PROMPT_ID: &str = "theme_page/v1";
/// Fewer durable claims than this and a theme gets no page — one claim is a
/// claim card, not a topic page.
pub const MIN_PAGE_CLAIMS: usize = 2;

const THEME_PAGE_TEMPLATE: &str = include_str!("../../prompts/theme_page.md");
const PAGE_MODEL: &str = "claude-sonnet-4-6";
/// Pages weave dozens of claims into multi-section prose — far above the
/// label stage's budget, still bounded.
const PAGE_MAX_TOKENS: u32 = 4000;

/// One claim as fed to the page synthesis: its durable ledger identity plus
/// the text the narrative may use. `claim_key` (not `claim_id`) because the
/// key is the unique, deterministic address every surface resolves
/// (`claim/<key>.json` in the publish tree; claim_ids can collide across
/// runs).
#[derive(Debug, Clone, PartialEq)]
pub struct PageClaim {
    pub claim_key: String,
    pub claim: String,
    /// Distinct cited source cases — shown to the model so breadth is
    /// visible ("supported by 4 sources"), never invented.
    pub source_count: usize,
}

/// One rendered section of a theme page.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PageSection {
    pub heading: String,
    /// Paragraphs separated by blank lines; `[claim:<key>]` citations inline.
    pub body: String,
}

/// One grounded topic page.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemePage {
    /// Stable grouping identity: the `themes.json` community id.
    pub community_id: i64,
    /// Display-only, refreshed from `themes.json` on every write; never part
    /// of the synthesis request.
    pub label: String,
    pub label_zh: String,
    /// SORTED active-claim keys this page was woven from — the staleness
    /// marker (same set ⇒ page is up to date) and the verifier's universe.
    pub claim_keys: Vec<String>,
    pub sections: Vec<PageSection>,
}

/// The whole projection file.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemePagesFile {
    pub schema: String,
    /// Pages sorted by `community_id`.
    pub pages: Vec<ThemePage>,
}

impl ThemePagesFile {
    /// Read `theme_pages.json`. Missing file → `Ok(None)` (pages are
    /// optional); unparseable or wrong schema → `Err` (a corrupt projection
    /// should be regenerated, not silently ignored).
    pub fn load(path: &Path) -> Result<Option<ThemePagesFile>, String> {
        if !path.exists() {
            return Ok(None);
        }
        let raw = std::fs::read_to_string(path)
            .map_err(|e| format!("reading {}: {e}", path.display()))?;
        let file: ThemePagesFile = serde_json::from_str(&raw).map_err(|e| {
            format!(
                "parsing {}: {e} (regenerate with `ovp2 crystal-theme-pages --refresh`)",
                path.display()
            )
        })?;
        if file.schema != THEME_PAGES_SCHEMA {
            return Err(format!(
                "{}: unsupported schema `{}` (expected `{THEME_PAGES_SCHEMA}`)",
                path.display(),
                file.schema
            ));
        }
        Ok(Some(file))
    }

    /// The page for a community, if any.
    pub fn page(&self, community_id: i64) -> Option<&ThemePage> {
        self.pages.iter().find(|p| p.community_id == community_id)
    }
}

// ---- Synthesis request (`theme_page/v1`) ----

/// Build the page-synthesis `ModelRequest` for one theme. `synth_theme` is
/// the community's DETERMINISTIC keyword identity
/// (`ThemeCommunity::synth_theme`) — never the display label, so a
/// presentation relabel cannot move the cassette key or change a prompt
/// byte. Claims are pre-sorted by the caller (sorted claim_key order) so the
/// same claim set always builds the same request.
pub fn theme_page_request(synth_theme: &str, claims: &[PageClaim]) -> ModelRequest {
    let marker = "## Topic";
    let (system, _) = THEME_PAGE_TEMPLATE
        .split_once(marker)
        .unwrap_or((THEME_PAGE_TEMPLATE, ""));
    let mut user = format!("{marker}\n\nKeywords: {synth_theme}\n\nClaims:\n");
    for c in claims {
        user.push_str(&format!(
            "- [claim:{}] ({} source(s)) {}\n",
            c.claim_key, c.source_count, c.claim
        ));
    }
    ModelRequest {
        model: PAGE_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: PAGE_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(THEME_PAGE_PROMPT_ID.to_string()),
    }
}

/// Parse a page reply: `{"sections": [{"heading": "...", "body": "..."}]}`.
/// Structural checks only — grounding is the verifier's job.
pub fn parse_theme_page(reply_text: &str) -> Result<Vec<PageSection>, String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    let sections = value
        .get("sections")
        .and_then(|v| v.as_array())
        .ok_or("missing `sections` array")?;
    let mut out = Vec::with_capacity(sections.len());
    for (i, s) in sections.iter().enumerate() {
        let heading = s
            .get("heading")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("section {i}: missing `heading`"))?;
        let body = s
            .get("body")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("section {i}: missing `body`"))?;
        out.push(PageSection {
            heading: heading.to_string(),
            body: body.to_string(),
        });
    }
    Ok(out)
}

// ---- Deterministic verifier (the page gate) ----

/// One grounding defect found in a synthesized page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PageDefect {
    /// The model produced no sections at all.
    NoSections,
    /// A cited key is not in this theme's claim set.
    UnknownClaim { section: usize, citation: String },
    /// A paragraph carries no `[claim:…]` citation.
    UncitedParagraph { section: usize, paragraph: usize },
}

impl std::fmt::Display for PageDefect {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PageDefect::NoSections => write!(f, "no sections"),
            PageDefect::UnknownClaim { section, citation } => {
                write!(f, "section {section}: cites unknown claim `{citation}`")
            }
            PageDefect::UncitedParagraph { section, paragraph } => {
                write!(
                    f,
                    "section {section} paragraph {paragraph}: no [claim:…] citation"
                )
            }
        }
    }
}

/// Extract the `claim:<key>` citations from a body, in order of appearance
/// (with duplicates — the verifier counts per-paragraph presence; callers
/// wanting a set can collect one). Same `[…]` tokenizer semantics as
/// `ovp-memory`'s answer verifier, restricted to the `claim:` kind — the two
/// must not disagree on what counts as a citation.
pub fn extract_claim_citations(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find('[') {
        let after = &rest[start + 1..];
        let Some(end) = after.find(']') else { break };
        let candidate = after[..end].trim();
        if let Some(key) = candidate.strip_prefix("claim:") {
            let key = key.trim();
            if !key.is_empty() {
                out.push(key.to_string());
            }
        }
        rest = &after[end + 1..];
    }
    out
}

/// Verify a synthesized page against the claim set it was given. Deterministic
/// and total: returns EVERY defect (the command fails loud and reports all of
/// them, so a rerun's repair pass has the full picture).
pub fn verify_page(sections: &[PageSection], known_keys: &BTreeSet<String>) -> Vec<PageDefect> {
    let mut defects = Vec::new();
    if sections.is_empty() {
        defects.push(PageDefect::NoSections);
        return defects;
    }
    for (si, section) in sections.iter().enumerate() {
        for (pi, para) in paragraphs(&section.body).iter().enumerate() {
            let cited = extract_claim_citations(para);
            if cited.is_empty() {
                defects.push(PageDefect::UncitedParagraph {
                    section: si,
                    paragraph: pi,
                });
            }
            for key in cited {
                if !known_keys.contains(&key) {
                    defects.push(PageDefect::UnknownClaim {
                        section: si,
                        citation: key,
                    });
                }
            }
        }
    }
    defects
}

/// Claim keys the page never cites — reported as coverage, never a defect
/// (the prompt allows dropping claims that do not fit the narrative).
pub fn uncited_keys(sections: &[PageSection], known_keys: &BTreeSet<String>) -> Vec<String> {
    let cited: BTreeSet<String> = sections
        .iter()
        .flat_map(|s| extract_claim_citations(&s.body))
        .collect();
    known_keys
        .iter()
        .filter(|k| !cited.contains(*k))
        .cloned()
        .collect()
}

/// Split a body into paragraphs: blank-line separated runs of non-empty lines.
fn paragraphs(body: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current: Vec<&str> = Vec::new();
    for line in body.lines() {
        if line.trim().is_empty() {
            if !current.is_empty() {
                out.push(current.join("\n"));
                current.clear();
            }
        } else {
            current.push(line);
        }
    }
    if !current.is_empty() {
        out.push(current.join("\n"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(list: &[&str]) -> BTreeSet<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    fn claims_fixture() -> Vec<PageClaim> {
        vec![
            PageClaim {
                claim_key: "ck-aaaa111122223333".into(),
                claim: "Agent memory persists across sessions.".into(),
                source_count: 3,
            },
            PageClaim {
                claim_key: "ck-bbbb444455556666".into(),
                claim: "Retrieval quality beats graph rendering.".into(),
                source_count: 2,
            },
        ]
    }

    #[test]
    fn request_carries_keys_and_claims_and_namespace() {
        let req = theme_page_request("memory · context · agent", &claims_fixture());
        assert_eq!(req.cache_namespace.as_deref(), Some("theme_page/v1"));
        assert!(req.system.as_deref().unwrap().contains("theme_page/v1"));
        let ModelMessage::User { content } = &req.messages[0] else {
            panic!()
        };
        assert!(content.contains("memory · context · agent"));
        assert!(content.contains("[claim:ck-aaaa111122223333] (3 source(s))"));
        assert!(content.contains("Retrieval quality beats graph rendering."));
    }

    #[test]
    fn request_depends_on_keywords_never_display_labels() {
        // The caller passes synth_theme (deterministic keywords). A display
        // relabel changes nothing the request can see — same inputs, same
        // request key.
        let a = theme_page_request("memory · context", &claims_fixture());
        let b = theme_page_request("memory · context", &claims_fixture());
        assert_eq!(ovp_llm::request_key(&a), ovp_llm::request_key(&b));
    }

    #[test]
    fn parse_theme_page_roundtrip_and_defects() {
        let sections = parse_theme_page(
            r#"{"sections":[{"heading":"Memory","body":"Persists [claim:ck-a]."}]}"#,
        )
        .unwrap();
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].heading, "Memory");
        assert!(parse_theme_page(r#"{"nope":1}"#).is_err());
        assert!(parse_theme_page(r#"{"sections":[{"heading":"","body":"x"}]}"#).is_err());
        assert!(parse_theme_page(r#"{"sections":[{"heading":"H"}]}"#).is_err());
    }

    #[test]
    fn verifier_accepts_a_grounded_page() {
        let sections = vec![PageSection {
            heading: "Memory".into(),
            body: "Agent memory persists [claim:ck-a].\n\n\
                   Retrieval beats rendering [claim:ck-b], and both matter [claim:ck-a]."
                .into(),
        }];
        assert!(verify_page(&sections, &keys(&["ck-a", "ck-b"])).is_empty());
    }

    #[test]
    fn verifier_reports_every_defect() {
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "Uncited paragraph.\n\nCites a ghost [claim:ck-ghost].".into(),
        }];
        let defects = verify_page(&sections, &keys(&["ck-a"]));
        assert_eq!(
            defects,
            vec![
                PageDefect::UncitedParagraph {
                    section: 0,
                    paragraph: 0
                },
                PageDefect::UnknownClaim {
                    section: 0,
                    citation: "ck-ghost".into()
                },
            ]
        );
        assert_eq!(
            verify_page(&[], &keys(&["ck-a"])),
            vec![PageDefect::NoSections]
        );
    }

    #[test]
    fn citation_extraction_matches_the_answer_verifier_tokenizer() {
        let text = "A [claim:ck-a], b [claim: ck-b ], skip [unit:u-1] and [see note], \
                    unterminated [claim:ck-x";
        assert_eq!(extract_claim_citations(text), vec!["ck-a", "ck-b"]);
    }

    #[test]
    fn uncited_keys_reports_coverage_not_defects() {
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "Only a [claim:ck-a].".into(),
        }];
        assert_eq!(
            uncited_keys(&sections, &keys(&["ck-a", "ck-b"])),
            vec!["ck-b"]
        );
    }

    #[test]
    fn pages_file_load_missing_corrupt_and_schema() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("theme_pages.json");
        assert_eq!(ThemePagesFile::load(&path).unwrap(), None);
        std::fs::write(&path, "not json").unwrap();
        assert!(ThemePagesFile::load(&path).is_err());
        let mut good = ThemePagesFile {
            schema: THEME_PAGES_SCHEMA.into(),
            pages: vec![ThemePage {
                community_id: 0,
                label: "Agent memory".into(),
                label_zh: "智能体记忆".into(),
                claim_keys: vec!["ck-a".into(), "ck-b".into()],
                sections: vec![PageSection {
                    heading: "H".into(),
                    body: "B [claim:ck-a].".into(),
                }],
            }],
        };
        std::fs::write(&path, serde_json::to_string(&good).unwrap()).unwrap();
        let loaded = ThemePagesFile::load(&path).unwrap().unwrap();
        assert_eq!(loaded, good);
        assert!(loaded.page(0).is_some());
        assert!(loaded.page(9).is_none());
        good.schema = "ovp.theme_pages/v999".into();
        std::fs::write(&path, serde_json::to_string(&good).unwrap()).unwrap();
        assert!(
            ThemePagesFile::load(&path).is_err(),
            "wrong schema fails loud"
        );
    }
}
