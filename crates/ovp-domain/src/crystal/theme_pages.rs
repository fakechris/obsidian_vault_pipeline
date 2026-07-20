//! Grounded theme pages — the `theme_pages.json` PROJECTION over durable claims.
//!
//! `ovp2 crystal-theme-pages` groups the ACTIVE durable claims by their
//! majority theme community (the same vote `ovp-index` uses for
//! `ClaimRow.theme`), asks the model to weave each group into a short wiki
//! page (`theme_page/v1`), and gates the reply through a DETERMINISTIC
//! verifier: every SENTENCE must cite at least one `[claim:<claim_key>]`
//! and every cited key must be one of the claims supplied for that theme.
//! A failing draft gets ONE bounded repair call; a page that still fails
//! the verifier is never written.
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

/// The positional citation handle the MODEL uses for claim `index` (0-based
/// caller side, 1-based in the prompt): `c1`, `c2`, …. Real `claim_key`s are
/// 16-hex strings the model cannot reliably copy dozens of times — the first
/// live run on the real vault degraded into pattern-continued fake keys after
/// ~4 sections (t003, 35 defects). Small handles are copyable; the code, not
/// the model, owns the handle → claim_key substitution.
pub fn claim_handle(index: usize) -> String {
    format!("c{}", index + 1)
}

/// Build the page-synthesis `ModelRequest` for one theme. `synth_theme` is
/// the community's DETERMINISTIC keyword identity
/// (`ThemeCommunity::synth_theme`) — never the display label, so a
/// presentation relabel cannot move the cassette key or change a prompt
/// byte. Claims are pre-sorted by the caller (sorted claim_key order) so the
/// same claim set always builds the same request; the prompt shows each
/// claim under its positional handle (`claim_handle`), never the raw key.
pub fn theme_page_request(synth_theme: &str, claims: &[PageClaim]) -> ModelRequest {
    let marker = "## Topic";
    let (system, _) = THEME_PAGE_TEMPLATE
        .split_once(marker)
        .unwrap_or((THEME_PAGE_TEMPLATE, ""));
    let mut user = format!("{marker}\n\nKeywords: {synth_theme}\n\nClaims:\n");
    for (i, c) in claims.iter().enumerate() {
        user.push_str(&format!(
            "- [claim:{}] ({} source(s)) {}\n",
            claim_handle(i),
            c.source_count,
            c.claim
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

/// Build the ONE bounded repair request for a draft that failed the page
/// gate: the model gets its own previous JSON (still handle-cited), the
/// verifier's defect list, and the claim list again — and must either cite
/// or delete each offending sentence, never add content. Same cassette
/// namespace as the synthesis stage; the request is deterministic given the
/// draft + defects, so replay works. If the repaired draft still fails the
/// gate, the command fails loud — there is no second repair (same bounded
/// contract as the JSON-repair pass in `call_and_parse`).
pub fn theme_page_repair_request(
    synth_theme: &str,
    claims: &[PageClaim],
    previous_sections: &[PageSection],
    defects: &[String],
) -> ModelRequest {
    let marker = "## Topic";
    let (system, _) = THEME_PAGE_TEMPLATE
        .split_once(marker)
        .unwrap_or((THEME_PAGE_TEMPLATE, ""));
    let mut user = format!("{marker}\n\nKeywords: {synth_theme}\n\nClaims:\n");
    for (i, c) in claims.iter().enumerate() {
        user.push_str(&format!(
            "- [claim:{}] ({} source(s)) {}\n",
            claim_handle(i),
            c.source_count,
            c.claim
        ));
    }
    let draft = serde_json::json!({ "sections": previous_sections });
    user.push_str("\n## Previous draft (failed the citation gate)\n\n");
    user.push_str(&draft.to_string());
    user.push_str("\n\n## Verifier defects\n\n");
    for d in defects {
        user.push_str(&format!("- {d}\n"));
    }
    user.push_str(
        "\nRepair the draft: for each uncited sentence, either add the \
         [claim:cN] citation(s) whose claims genuinely support it, or delete \
         the sentence. Replace any unknown citation with a valid handle or \
         remove it. Do NOT add new sentences, sections, or content. Output \
         only the corrected JSON in the same format.",
    );
    ModelRequest {
        model: PAGE_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: PAGE_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(THEME_PAGE_PROMPT_ID.to_string()),
    }
}

/// Replace `[claim:cN]` handles with the real claim keys, in the same sorted
/// claim order the request was built from. Unknown or out-of-range handles
/// are left untouched — the verifier then reports them as `UnknownClaim`
/// (fail loud, never silently drop a citation). Bodies that already carry a
/// full `[claim:ck-…]` key pass through unchanged.
pub fn resolve_handles(sections: &mut [PageSection], claim_keys: &[String]) {
    for section in sections.iter_mut() {
        let mut out = String::with_capacity(section.body.len());
        let mut rest = section.body.as_str();
        while let Some(start) = rest.find("[claim:") {
            let (head, tail) = rest.split_at(start);
            out.push_str(head);
            let Some(end) = tail.find(']') else {
                out.push_str(tail);
                rest = "";
                break;
            };
            let inner = tail["[claim:".len()..end].trim();
            let resolved = inner
                .strip_prefix('c')
                .and_then(|n| n.parse::<usize>().ok())
                .and_then(|n| n.checked_sub(1))
                .and_then(|i| claim_keys.get(i));
            match resolved {
                Some(key) => out.push_str(&format!("[claim:{key}]")),
                None => out.push_str(&tail[..=end]),
            }
            rest = &tail[end + 1..];
        }
        out.push_str(rest);
        section.body = out;
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
    /// A sentence carries no `[claim:…]` citation. Sentence-level, not
    /// paragraph-level: one citation must not launder the uncited sentences
    /// around it into the grounded projection (codex review P1).
    UncitedSentence { section: usize, snippet: String },
    /// A section whose body contains no semantic sentence at all (only
    /// punctuation or bare citations) — a "grounded" page must contain
    /// grounded prose, not citation confetti (codex review round-2 P2).
    EmptySection { section: usize },
}

impl std::fmt::Display for PageDefect {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PageDefect::NoSections => write!(f, "no sections"),
            PageDefect::UnknownClaim { section, citation } => {
                write!(f, "section {section}: cites unknown claim `{citation}`")
            }
            PageDefect::UncitedSentence { section, snippet } => {
                write!(f, "section {section}: uncited sentence `{snippet}`")
            }
            PageDefect::EmptySection { section } => {
                write!(f, "section {section}: no semantic sentences")
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
/// them, so a rerun's repair pass has the full picture). Grounding is checked
/// per SENTENCE — `Unsupported claim. Supported claim [claim:x].` must fail on
/// the first sentence, not ride on the second's citation.
pub fn verify_page(sections: &[PageSection], known_keys: &BTreeSet<String>) -> Vec<PageDefect> {
    let mut defects = Vec::new();
    if sections.is_empty() {
        defects.push(PageDefect::NoSections);
        return defects;
    }
    for (si, section) in sections.iter().enumerate() {
        let mut semantic_sentences = 0usize;
        for para in paragraphs(&section.body) {
            for sentence in sentences(&para) {
                semantic_sentences += 1;
                if extract_claim_citations(&sentence).is_empty() {
                    defects.push(PageDefect::UncitedSentence {
                        section: si,
                        snippet: snippet_of(&sentence),
                    });
                }
            }
            for key in extract_claim_citations(&para) {
                if !known_keys.contains(&key) {
                    defects.push(PageDefect::UnknownClaim {
                        section: si,
                        citation: key,
                    });
                }
            }
        }
        if semantic_sentences == 0 {
            defects.push(PageDefect::EmptySection { section: si });
        }
    }
    defects
}

/// First ~40 chars of a sentence, for actionable defect messages.
fn snippet_of(sentence: &str) -> String {
    let t = sentence.trim();
    let cut: String = t.chars().take(40).collect();
    if cut.chars().count() < t.chars().count() {
        format!("{cut}…")
    } else {
        cut
    }
}

/// Split a paragraph into sentences for the citation gate.
///
/// One rule, ZERO merge heuristics: a terminator (`.!?。！？`) ends a
/// sentence whenever it is followed — after closing quotes/brackets — by
/// whitespace, a `[claim:` citation, or end of text. Codex review rounds
/// 2–4 demonstrated that every "don't split here" heuristic (uppercase
/// look-ahead, abbreviation word lists, dotted segments like `U.S.`) is a
/// laundering vector that merges an uncited sentence into a cited one. A
/// false SPLIT costs one spurious defect that the bounded repair pass
/// absorbs; a false MERGE breaks the grounding guarantee. Split wins,
/// always. (`v2.0` still holds together — the dot is not followed by
/// whitespace.)
///
/// - A fragment's LEADING `[claim:…]` cluster re-attaches to the previous
///   sentence (`Text. [claim:c1] Next…` cites "Text." with c1).
/// - Fragments with no alphanumeric content (stray punctuation, bare
///   citations) are never semantic sentences.
fn sentences(paragraph: &str) -> Vec<String> {
    let chars: Vec<char> = paragraph.chars().collect();
    let mut fragments: Vec<String> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < chars.len() {
        let c = chars[i];
        let hard = matches!(c, '。' | '！' | '？');
        let soft = matches!(c, '.' | '!' | '?');
        let mut end = i + 1;
        if hard || soft {
            // Closing quotes/brackets belong to the sentence they end.
            while end < chars.len() && matches!(chars[end], '"' | '”' | '\'' | ')' | '）' | ']')
            {
                end += 1;
            }
        }
        let splits = if hard {
            true
        } else if soft {
            end >= chars.len() || chars[end].is_whitespace() || chars[end] == '['
        } else {
            false
        };
        if splits {
            fragments.push(chars[start..end].iter().collect());
            start = end;
            i = end;
        } else {
            i += 1;
        }
    }
    if start < chars.len() {
        fragments.push(chars[start..].iter().collect());
    }

    let mut out: Vec<String> = Vec::new();
    for fragment in fragments {
        let (leading, rest) = split_leading_citations(&fragment);
        if !leading.is_empty()
            && let Some(prev) = out.last_mut()
        {
            prev.push(' ');
            prev.push_str(&leading);
        }
        let rest = rest.trim();
        // `is_alphanumeric` covers CJK ideographs (Unicode Letter) while
        // excluding CJK punctuation like `。` — a section of `。[claim:x]`
        // confetti must not count as a semantic sentence (codex round-4 P2).
        if rest.chars().any(char::is_alphanumeric) {
            out.push(rest.to_string());
        } else if !rest.is_empty()
            && let Some(prev) = out.last_mut()
        {
            // Citation-less punctuation tail (e.g. a stray `.`) — keep it
            // attached rather than inventing an empty sentence.
            prev.push(' ');
            prev.push_str(rest);
        }
    }
    out
}

/// Split a fragment's leading run of `[claim:…]` citations (with surrounding
/// whitespace) from the rest.
fn split_leading_citations(fragment: &str) -> (String, String) {
    let mut rest = fragment;
    let mut leading = String::new();
    loop {
        let trimmed = rest.trim_start();
        if let Some(after) = trimmed.strip_prefix("[claim:")
            && let Some(end) = after.find(']')
        {
            if !leading.is_empty() {
                leading.push(' ');
            }
            leading.push_str(&trimmed[..("[claim:".len() + end + 1)]);
            rest = &after[end + 1..];
        } else {
            return (leading, rest.trim_start().to_string());
        }
    }
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
    fn request_carries_handles_and_claims_and_namespace() {
        let req = theme_page_request("memory · context · agent", &claims_fixture());
        assert_eq!(req.cache_namespace.as_deref(), Some("theme_page/v1"));
        assert!(req.system.as_deref().unwrap().contains("theme_page/v1"));
        let ModelMessage::User { content } = &req.messages[0] else {
            panic!()
        };
        assert!(content.contains("memory · context · agent"));
        assert!(content.contains("[claim:c1] (3 source(s))"));
        assert!(content.contains("[claim:c2] (2 source(s))"));
        assert!(content.contains("Retrieval quality beats graph rendering."));
        assert!(
            !content.contains("ck-aaaa111122223333"),
            "raw keys never reach the model — handles only"
        );
    }

    #[test]
    fn resolve_handles_substitutes_and_leaves_the_unresolvable_loud() {
        let keys_vec = vec!["ck-aaaa".to_string(), "ck-bbbb".to_string()];
        let mut sections = vec![PageSection {
            heading: "H".into(),
            body: "First [claim:c1] and second [claim: c2 ].\n\n\
                   Out of range [claim:c9], not a handle [claim:ck-aaaa], \
                   garbage [claim:cx], unterminated [claim:c1"
                .into(),
        }];
        resolve_handles(&mut sections, &keys_vec);
        assert_eq!(
            sections[0].body,
            "First [claim:ck-aaaa] and second [claim:ck-bbbb].\n\n\
             Out of range [claim:c9], not a handle [claim:ck-aaaa], \
             garbage [claim:cx], unterminated [claim:c1"
        );
        // The survivors are exactly what the verifier then flags.
        let defects = verify_page(&sections, &keys(&["ck-aaaa", "ck-bbbb"]));
        assert_eq!(
            defects,
            vec![
                PageDefect::UnknownClaim {
                    section: 0,
                    citation: "c9".into()
                },
                PageDefect::UnknownClaim {
                    section: 0,
                    citation: "cx".into()
                },
            ]
        );
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
                PageDefect::UncitedSentence {
                    section: 0,
                    snippet: "Uncited paragraph.".into()
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
    fn one_citation_does_not_launder_the_uncited_sentence_beside_it() {
        // The codex-review P1 example: paragraph-level checking would pass
        // this; sentence-level must not.
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "Unsupported claim. Supported claim [claim:ck-a].".into(),
        }];
        assert_eq!(
            verify_page(&sections, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "Unsupported claim.".into()
            }]
        );
    }

    #[test]
    fn lowercase_continuation_still_splits_and_fails_uncited() {
        // Codex round-2 P1: sentence boundaries must not depend on the next
        // sentence starting uppercase.
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "Unsupported claim. however supported [claim:ck-a].".into(),
        }];
        assert_eq!(
            verify_page(&sections, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "Unsupported claim.".into()
            }]
        );
    }

    #[test]
    fn word_abbreviations_split_rather_than_launder() {
        // Codex round-3 P1: `Inc.`-style tokens must not merge an uncited
        // sentence into the cited one after it. The false-split cost lands
        // on the repair pass, never on the grounding guarantee.
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "Acme Inc. A supported sentence [claim:ck-a].".into(),
        }];
        assert_eq!(
            verify_page(&sections, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "Acme Inc.".into()
            }]
        );
    }

    #[test]
    fn content_free_sections_are_rejected() {
        // Codex round-2 P2: a section of citation confetti with no prose
        // must not count as grounded content.
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "[claim:ck-a]".into(),
        }];
        assert_eq!(
            verify_page(&sections, &keys(&["ck-a"])),
            vec![PageDefect::EmptySection { section: 0 }]
        );
    }

    #[test]
    fn sentence_splitting_handles_trailing_citations_numbers_and_cjk() {
        // Citation AFTER the period attaches to the sentence it follows.
        let ok = vec![PageSection {
            heading: "H".into(),
            body: "Text. [claim:ck-a] Next sentence [claim:ck-b].".into(),
        }];
        assert!(verify_page(&ok, &keys(&["ck-a", "ck-b"])).is_empty());
        // Version numbers hold together (dot not followed by whitespace).
        let version = vec![PageSection {
            heading: "H".into(),
            body: "Mem0 v2.0 persists state [claim:ck-a].".into(),
        }];
        assert!(verify_page(&version, &keys(&["ck-a"])).is_empty());
        // CJK terminators split; the uncited CJK sentence fails.
        let cjk = vec![PageSection {
            heading: "H".into(),
            body: "记忆需要治理 [claim:ck-a]。存储不是难点。".into(),
        }];
        assert_eq!(
            verify_page(&cjk, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "存储不是难点。".into()
            }]
        );
    }

    #[test]
    fn split_over_launder_has_no_abbreviation_escapes() {
        // Codex rounds 3-4: every abbreviation escape was a laundering
        // vector. `e.g.` now splits — the false positive is the accepted
        // cost (the repair pass absorbs it), the merge would be a hole.
        let eg = vec![PageSection {
            heading: "H".into(),
            body: "Systems like e.g. Mem0 persist state [claim:ck-a].".into(),
        }];
        assert_eq!(
            verify_page(&eg, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "Systems like e.g.".into()
            }]
        );
        // The round-4 U.S. laundering example must produce a defect.
        let us = vec![PageSection {
            heading: "H".into(),
            body: "Unsupported assertion about the U.S. Supported assertion [claim:ck-a].".into(),
        }];
        assert_eq!(
            verify_page(&us, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "Unsupported assertion about the U.S.".into()
            }]
        );
    }

    #[test]
    fn citation_directly_after_period_still_splits() {
        // Codex round-4 P1: `Supported fact.[claim:ck-a] Unsupported.` must
        // not verify as one cited fragment.
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "Supported fact.[claim:ck-a] Unsupported follow-up.".into(),
        }];
        assert_eq!(
            verify_page(&sections, &keys(&["ck-a"])),
            vec![PageDefect::UncitedSentence {
                section: 0,
                snippet: "Unsupported follow-up.".into()
            }]
        );
    }

    #[test]
    fn cjk_punctuation_confetti_is_not_semantic_content() {
        // Codex round-4 P2: `。[claim:ck-a]` must be an EmptySection, not a
        // "sentence".
        let sections = vec![PageSection {
            heading: "H".into(),
            body: "。[claim:ck-a]".into(),
        }];
        assert_eq!(
            verify_page(&sections, &keys(&["ck-a"])),
            vec![PageDefect::EmptySection { section: 0 }]
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
