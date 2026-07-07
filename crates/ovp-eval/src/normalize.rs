//! Normalize each system's output into a shared [`NormalizedSubject`] so the
//! comparator works on one shape regardless of which pipeline produced it.
//!
//! Everything here is **lexical and deterministic** — no model, no network, no
//! semantics. Concept keys are a punctuation-stripped lowercase slug; grounding
//! is token-overlap against the original input. The comparator is explicit that
//! these are lexical signals (they miss paraphrase), and that the two systems
//! extract *different unit types* (ovp canonical evergreen concepts vs Nowledge
//! atomic-memory titles) — the comparison is observational, not a parity test.

use ovp_review::CanonicalSummary;
use serde::{Deserialize, Serialize};

use crate::nowledge::SourceDetail;

/// A system's output, normalized for comparison.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormalizedSubject {
    /// `"ovp2"` or `"nowledge-mem"`.
    pub system: String,
    pub source: NormSource,
    pub concepts: Vec<NormConcept>,
    pub claims: Vec<NormClaim>,
    pub structure: NormStructure,
    pub retrieval: Vec<NormRetrievalHit>,
    /// Free-text caveats about what these fields mean for THIS system (e.g.
    /// "concepts are atomic-memory titles, not canonical nodes").
    pub notes: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct NormSource {
    pub url: String,
    pub title: String,
    pub text_len: usize,
}

/// A unit of "what this system extracted as a concept". Different systems mean
/// different things by this (see `kind`); the comparison is lexical.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormConcept {
    /// Lexical key for set ops: lowercase, punctuation→`-`, collapsed.
    pub key: String,
    /// Human label as the system emitted it.
    pub label: String,
    /// `"evergreen"` (ovp canonical node) | `"memory-title"` (nowledge fact).
    pub kind: String,
}

/// An atomic claim/statement, with where it came from and whether it is
/// lexically grounded in the original input.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormClaim {
    pub text: String,
    /// ovp: the note heading the claim sits under; nowledge: the memory `unit_type`.
    pub section: String,
    pub grounded: bool,
    /// Lexical token-overlap ratio against the input [0.0, 1.0].
    pub grounding_score: f64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct NormStructure {
    pub concept_count: usize,
    pub claim_count: usize,
    /// Distinct note sections (ovp) or distinct memory unit_types (nowledge).
    pub section_count: usize,
    /// Per-source memory count (nowledge); 0 for ovp.
    pub memory_count: usize,
    /// GLOBAL crystal count (nowledge, whole store — NOT scoped to this input).
    /// `None` = the crystal endpoint failed or was not queried (distinct from
    /// `Some(0)` = the store genuinely has no crystals). Never coerce a failure
    /// to 0.
    pub global_crystal_count: Option<usize>,
    pub grounded_claims: usize,
    pub ungrounded_claims: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormRetrievalHit {
    pub query: String,
    pub rank: usize,
    pub title: String,
    pub snippet: String,
    pub grounded: bool,
}

// --- ovp side -------------------------------------------------------------

/// Normalize the ovp side from the M7 review's canonical summary + the produced
/// note markdown (read from disk). `note_md` is `None` if no note was produced.
pub fn normalize_ovp(canonical: &CanonicalSummary, note_md: Option<&str>) -> NormalizedSubject {
    let concepts: Vec<NormConcept> = canonical
        .slugs
        .iter()
        .map(|slug| NormConcept {
            key: normalize_key(slug),
            label: humanize(slug),
            kind: "evergreen".to_string(),
        })
        .collect();

    let (frontmatter, body) = note_md.map(split_frontmatter).unwrap_or((None, String::new()));
    let title = frontmatter
        .as_deref()
        .and_then(|fm| frontmatter_field(fm, "title"))
        .unwrap_or_default();
    let url = frontmatter
        .as_deref()
        .and_then(|fm| frontmatter_field(fm, "source"))
        .unwrap_or_default();

    let claims = extract_markdown_claims(&body);
    let section_count = distinct_sections(&claims);

    NormalizedSubject {
        system: "ovp2".to_string(),
        source: NormSource { url, title, text_len: 0 },
        structure: NormStructure {
            concept_count: concepts.len(),
            claim_count: claims.len(),
            section_count,
            memory_count: 0,
            global_crystal_count: None,
            grounded_claims: 0,
            ungrounded_claims: 0,
        },
        concepts,
        claims,
        retrieval: Vec::new(),
        notes: vec![
            "concepts = canonical evergreen nodes (slugs minted by the cycle)".to_string(),
            "claims = statements parsed from the produced 深度解读 note body".to_string(),
        ],
    }
}

// --- Nowledge side --------------------------------------------------------

/// Normalize the Nowledge side from a source detail. `global_crystal_count` is
/// whole-store context: `None` if the crystal endpoint failed/was not queried,
/// `Some(n)` for a real count — never coerce a failure to 0.
pub fn normalize_nowledge(
    detail: &SourceDetail,
    global_crystal_count: Option<usize>,
) -> NormalizedSubject {
    let concepts: Vec<NormConcept> = detail
        .memories
        .iter()
        .filter(|m| !m.title.trim().is_empty())
        .map(|m| NormConcept {
            key: normalize_key(&m.title),
            label: m.title.clone(),
            kind: "memory-title".to_string(),
        })
        .collect();

    let claims: Vec<NormClaim> = detail
        .memories
        .iter()
        .filter(|m| !m.content.trim().is_empty())
        .map(|m| NormClaim {
            text: m.content.clone(),
            section: if m.unit_type.is_empty() { "memory".to_string() } else { m.unit_type.clone() },
            grounded: false,
            grounding_score: 0.0,
        })
        .collect();

    let title = source_title(detail);
    let section_count = distinct_sections(&claims);

    NormalizedSubject {
        system: "nowledge-mem".to_string(),
        source: NormSource { url: detail.source.source_url.clone(), title, text_len: 0 },
        structure: NormStructure {
            concept_count: concepts.len(),
            claim_count: claims.len(),
            section_count,
            memory_count: detail.memories.len(),
            global_crystal_count,
            grounded_claims: 0,
            ungrounded_claims: 0,
        },
        concepts,
        claims,
        retrieval: Vec::new(),
        notes: vec![
            "concepts = atomic-memory titles (NOT canonical nodes); different granularity than ovp".to_string(),
            "claims = memory contents extracted from this source".to_string(),
            "global_crystal_count is whole-store, NOT scoped to this input; None = endpoint failed (not zero)".to_string(),
        ],
    }
}

/// A Nowledge source carries no explicit title; derive one from its first
/// section, else the original filename.
fn source_title(detail: &SourceDetail) -> String {
    if let Some(tree) = &detail.source.section_tree {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(tree) {
            if let Some(first) = v.as_array().and_then(|a| a.first()) {
                if let Some(t) = first.get("title").and_then(|t| t.as_str()) {
                    if !t.trim().is_empty() {
                        return t.to_string();
                    }
                }
            }
        }
    }
    if !detail.source.original_name.trim().is_empty() {
        return detail.source.original_name.clone();
    }
    detail.source.id.clone()
}

// --- grounding ------------------------------------------------------------

/// Set each claim's `grounded` + `grounding_score` by lexical token overlap
/// against `reference` (the original input text). A claim is grounded when at
/// least `threshold` of its significant tokens appear in the reference. Lexical
/// only — it misses paraphrase and synthesis; the pack says so loudly. Also
/// fills the subject's grounded/ungrounded structure counts and `text_len`.
pub fn audit_grounding(subject: &mut NormalizedSubject, reference: &str, threshold: f64) {
    let ref_tokens: std::collections::HashSet<String> = tokenize(reference).into_iter().collect();
    subject.source.text_len = reference.len();
    let mut grounded = 0usize;
    for claim in &mut subject.claims {
        let toks = tokenize(&claim.text);
        let score = if toks.is_empty() {
            0.0
        } else {
            let hits = toks.iter().filter(|t| ref_tokens.contains(*t)).count();
            hits as f64 / toks.len() as f64
        };
        claim.grounding_score = (score * 1000.0).round() / 1000.0;
        claim.grounded = score >= threshold;
        if claim.grounded {
            grounded += 1;
        }
    }
    subject.structure.grounded_claims = grounded;
    subject.structure.ungrounded_claims = subject.claims.len() - grounded;
    // Stamp retrieval grounding too (reuses the same reference).
    for hit in &mut subject.retrieval {
        let toks = tokenize(&hit.snippet);
        let score = if toks.is_empty() {
            0.0
        } else {
            toks.iter().filter(|t| ref_tokens.contains(*t)).count() as f64 / toks.len() as f64
        };
        hit.grounded = score >= threshold;
    }
}

// --- shared lexical helpers ----------------------------------------------

/// Lowercase, map every non-alphanumeric char to a break, collapse runs, join
/// with `-`. ASCII-only stripping; non-ASCII alphanumerics (e.g. CJK) are kept.
pub fn normalize_key(s: &str) -> String {
    let lowered = s.to_lowercase();
    lowered
        .split(|c: char| !c.is_alphanumeric())
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("-")
}

fn humanize(slug: &str) -> String {
    slug.split(['-', '_']).filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" ")
}

/// Significant tokens for overlap: lowercased alphanumeric words ≥ 3 chars that
/// are not stopwords. CJK runs (no spaces) become one token each — coarse but
/// deterministic and symmetric across both sides.
pub fn tokenize(s: &str) -> Vec<String> {
    s.to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|w| w.chars().count() >= 3 && !is_stopword(w))
        .map(|w| w.to_string())
        .collect()
}

/// Lexical relevance of `text` to `query`: the count of DISTINCT significant
/// query tokens that appear in `text`. Deterministic; used for the source-
/// scoped Nowledge retrieval lane (lexically scoring the query against THIS
/// source's memories), so it is comparable to ovp-rag's lexical scoring rather
/// than to Nowledge's whole-store semantic search.
pub fn lexical_overlap_score(query: &str, text: &str) -> usize {
    let q: std::collections::BTreeSet<String> = tokenize(query).into_iter().collect();
    if q.is_empty() {
        return 0;
    }
    let t: std::collections::HashSet<String> = tokenize(text).into_iter().collect();
    q.iter().filter(|tok| t.contains(*tok)).count()
}

fn is_stopword(w: &str) -> bool {
    matches!(
        w,
        "the" | "and" | "for" | "are" | "but" | "not" | "you" | "all" | "can" | "her" | "was"
            | "one" | "our" | "out" | "his" | "has" | "had" | "how" | "its" | "who" | "did"
            | "that" | "this" | "with" | "from" | "they" | "have" | "what" | "when" | "your"
            | "which" | "their" | "there" | "these" | "those" | "about" | "into" | "than"
            | "then" | "them" | "been" | "more" | "such" | "also" | "will"
    )
}

/// Split `---\n…\n---\n` frontmatter off the front of a note. Returns
/// `(Some(frontmatter), body)`, or `(None, whole)` if there is no fence.
fn split_frontmatter(note: &str) -> (Option<String>, String) {
    let mut lines = note.lines();
    if lines.next().map(str::trim_end) != Some("---") {
        return (None, note.to_string());
    }
    let mut fm = Vec::new();
    let mut body = Vec::new();
    let mut in_fm = true;
    for line in lines {
        if in_fm && line.trim_end() == "---" {
            in_fm = false;
            continue;
        }
        if in_fm {
            fm.push(line);
        } else {
            body.push(line);
        }
    }
    if in_fm {
        return (None, note.to_string());
    }
    (Some(fm.join("\n")), body.join("\n"))
}

/// Read a top-level scalar frontmatter field (`key: value`), unquoting.
fn frontmatter_field(fm: &str, key: &str) -> Option<String> {
    let prefix = format!("{key}:");
    for line in fm.lines() {
        let trimmed = line.trim_start();
        if let Some(rest) = trimmed.strip_prefix(&prefix) {
            let v = rest.trim().trim_matches('"').trim();
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
    }
    None
}

/// Extract claim-bearing lines from a markdown body. Tracks the current heading
/// as the claim's `section`; a claim is a blockquote line, a list item, or a
/// substantial paragraph line (≥ 5 words after stripping markers). Deterministic
/// and heading-language-agnostic (works on the localized 深度解读 headings).
fn extract_markdown_claims(body: &str) -> Vec<NormClaim> {
    let mut claims = Vec::new();
    let mut section = "preamble".to_string();
    let mut in_code = false;
    for raw in body.lines() {
        let line = raw.trim();
        if line.starts_with("```") {
            in_code = !in_code;
            continue;
        }
        if in_code || line.is_empty() {
            continue;
        }
        if let Some(h) = line.strip_prefix('#') {
            section = h.trim_start_matches('#').trim().to_string();
            section = strip_markers(&section);
            continue;
        }
        // Skip table rules / pure separators.
        if line.chars().all(|c| matches!(c, '-' | '|' | ':' | ' ')) {
            continue;
        }
        let text = strip_markers(line);
        if word_count(&text) >= 5 {
            claims.push(NormClaim {
                text,
                section: section.clone(),
                grounded: false,
                grounding_score: 0.0,
            });
        }
    }
    claims
}

/// Strip leading list/quote markers and inline emphasis/backticks for cleaner
/// claim text.
fn strip_markers(line: &str) -> String {
    let mut s = line.trim();
    loop {
        let t = s
            .trim_start_matches("- ")
            .trim_start_matches("* ")
            .trim_start_matches("> ")
            .trim_start();
        // numbered list "1. "
        let t = strip_numbered(t);
        if t == s {
            break;
        }
        s = t;
    }
    s.replace("**", "").replace('`', "").trim().to_string()
}

fn strip_numbered(s: &str) -> &str {
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i > 0 && i + 1 < bytes.len() && bytes[i] == b'.' && bytes[i + 1] == b' ' {
        s[i + 2..].trim_start()
    } else {
        s
    }
}

/// Count "units" in a line for the claim-length threshold. Whitespace-delimited
/// words count as one each, EXCEPT a token carrying CJK characters counts each
/// CJK char as a unit — so a space-free Chinese/Japanese/Korean sentence (the
/// 深度解读 notes are largely Chinese) is not silently dropped by a word filter
/// tuned for English.
fn word_count(s: &str) -> usize {
    let mut n = 0;
    for token in s.split_whitespace() {
        let cjk = token.chars().filter(|c| is_cjk(*c)).count();
        if cjk > 0 {
            n += cjk;
        } else if !token.is_empty() {
            n += 1;
        }
    }
    n
}

fn is_cjk(c: char) -> bool {
    matches!(c as u32,
        0x4E00..=0x9FFF   // CJK Unified Ideographs
        | 0x3400..=0x4DBF // CJK Extension A
        | 0x3040..=0x30FF // Hiragana + Katakana
        | 0xAC00..=0xD7AF) // Hangul syllables
}

fn distinct_sections(claims: &[NormClaim]) -> usize {
    let set: std::collections::BTreeSet<&str> = claims.iter().map(|c| c.section.as_str()).collect();
    set.len()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nowledge::{SourceDetail, SourceInfo, SourceMemory};
    use ovp_review::CanonicalSummary;

    #[test]
    fn normalize_ovp_reads_concepts_frontmatter_and_claims() {
        let canonical = CanonicalSummary {
            concept_count: 2,
            slugs: vec!["agent-native-product-management".into(), "对话即工作".into()],
            evergreen_paths: vec![
                "10-Knowledge/Evergreen/agent-native-product-management.md".into(),
                "10-Knowledge/Evergreen/对话即工作.md".into(),
            ],
        };
        let note = "---\ntitle: \"A Guide\"\nsource: https://e.x/a\n---\n## 一句话定义\n\n> 这是一个关于代理原生产品管理的完整指南介绍\n\n## Details\n\n- a detail line with plenty of english words here\n";
        let subj = normalize_ovp(&canonical, Some(note));
        assert_eq!(subj.system, "ovp2");
        assert_eq!(subj.source.title, "A Guide");
        assert_eq!(subj.source.url, "https://e.x/a");
        assert_eq!(subj.concepts.len(), 2);
        assert_eq!(subj.concepts[1].key, "对话即工作", "pure-CJK slug normalizes to itself");
        // Both the CJK blockquote and the English bullet survive extraction.
        assert_eq!(subj.claims.len(), 2, "got {:?}", subj.claims);
        assert!(subj.claims.iter().any(|c| c.text.contains("代理原生产品管理")));
    }

    #[test]
    fn normalize_ovp_without_note_has_no_claims() {
        let canonical = CanonicalSummary { concept_count: 1, slugs: vec!["rag".into()], evergreen_paths: vec![] };
        let subj = normalize_ovp(&canonical, None);
        assert_eq!(subj.concepts.len(), 1);
        assert!(subj.claims.is_empty());
        assert_eq!(subj.source.title, "");
    }

    fn detail(memories: Vec<SourceMemory>, section_tree: Option<&str>) -> SourceDetail {
        SourceDetail {
            source: SourceInfo {
                id: "src_x".into(),
                source_url: "https://e.x/a".into(),
                original_name: "input.md".into(),
                lifecycle_state: "extracted".into(),
                summary: Some("a summary".into()),
                section_tree: section_tree.map(str::to_string),
                memory_count: memories.len() as u32,
                error_message: None,
            },
            memories,
        }
    }

    fn mem(title: &str, content: &str) -> SourceMemory {
        SourceMemory { id: "m".into(), title: title.into(), content: content.into(), unit_type: "fact".into() }
    }

    #[test]
    fn normalize_nowledge_maps_memories_to_concepts_and_claims() {
        let d = detail(
            vec![mem("Agent Native PM", "the conversation is the work"), mem("Compound Engineering", "reuse agents")],
            Some(r#"[{"level":1,"title":"Agent-native PM","line":1}]"#),
        );
        let subj = normalize_nowledge(&d, Some(7));
        assert_eq!(subj.system, "nowledge-mem");
        assert_eq!(subj.source.title, "Agent-native PM", "title from section_tree");
        assert_eq!(subj.concepts.len(), 2, "memory titles → concepts");
        assert_eq!(subj.claims.len(), 2, "memory contents → claims");
        assert_eq!(subj.structure.memory_count, 2);
        assert_eq!(subj.structure.global_crystal_count, Some(7));
        assert_eq!(subj.claims[0].section, "fact");
    }

    #[test]
    fn normalize_nowledge_tolerates_empty_and_malformed() {
        // No memories, malformed section_tree JSON → falls back to original_name.
        // global_crystal_count None (endpoint failed) must stay None, not 0.
        let d = detail(vec![], Some("{ not valid json"));
        let subj = normalize_nowledge(&d, None);
        assert!(subj.concepts.is_empty());
        assert!(subj.claims.is_empty());
        assert_eq!(subj.source.title, "input.md", "falls back to original_name");
        assert_eq!(subj.structure.global_crystal_count, None, "failure stays None, not 0");
        // A memory with an empty title is excluded from concepts but its content
        // still counts as a claim.
        let d2 = detail(vec![mem("", "a bare claim with content")], None);
        let subj2 = normalize_nowledge(&d2, Some(0));
        assert_eq!(subj2.concepts.len(), 0, "empty title → no concept");
        assert_eq!(subj2.claims.len(), 1, "content still a claim");
    }

    #[test]
    fn extract_claims_keeps_cjk_only_lines() {
        // A space-free Chinese sentence must NOT be dropped by the length filter.
        let body = "## 概念\n\n对话即工作是代理原生产品管理的核心理念转变\n";
        let claims = extract_markdown_claims(body);
        assert_eq!(claims.len(), 1, "CJK-only claim must survive: {claims:?}");
        assert_eq!(claims[0].section, "概念");
    }

    #[test]
    fn normalize_key_strips_punctuation_and_lowercases() {
        assert_eq!(normalize_key("Agent-Native Product Management!"), "agent-native-product-management");
        assert_eq!(normalize_key("RAG (v2)"), "rag-v2");
        assert_eq!(normalize_key("  spaced  out  "), "spaced-out");
    }

    #[test]
    fn split_frontmatter_separates_block_and_body() {
        let note = "---\ntitle: \"X\"\nsource: http://e.x/a\n---\n## H\n\n- a claim with several words here\n";
        let (fm, body) = split_frontmatter(note);
        assert_eq!(frontmatter_field(fm.as_deref().unwrap(), "title").as_deref(), Some("X"));
        assert_eq!(frontmatter_field(fm.as_deref().unwrap(), "source").as_deref(), Some("http://e.x/a"));
        assert!(body.contains("## H"));
    }

    #[test]
    fn extract_claims_tracks_section_and_skips_short_and_code() {
        let body = "## Section One\n\n- this is a claim with enough words to count\n\nshort\n\n```\ncode line ignored entirely here\n```\n### Sub\n> a quoted claim spanning several words too\n";
        let claims = extract_markdown_claims(body);
        assert_eq!(claims.len(), 2, "got {claims:?}");
        assert_eq!(claims[0].section, "Section One");
        assert!(claims[0].text.starts_with("this is a claim"));
        assert_eq!(claims[1].section, "Sub");
        assert!(claims[1].text.starts_with("a quoted claim"));
    }

    #[test]
    fn grounding_marks_overlapping_claims() {
        let mut subj = NormalizedSubject {
            system: "t".into(),
            source: NormSource::default(),
            concepts: vec![],
            claims: vec![
                NormClaim { text: "agent native product management workflow".into(), section: "s".into(), grounded: false, grounding_score: 0.0 },
                NormClaim { text: "completely unrelated zzzz qqqq wwww vvvv".into(), section: "s".into(), grounded: false, grounding_score: 0.0 },
            ],
            structure: NormStructure::default(),
            retrieval: vec![],
            notes: vec![],
        };
        let reference = "This guide covers agent native product management and the modern workflow.";
        audit_grounding(&mut subj, reference, 0.5);
        assert!(subj.claims[0].grounded, "overlapping claim should ground: {:?}", subj.claims[0]);
        assert!(!subj.claims[1].grounded, "unrelated claim should not ground");
        assert_eq!(subj.structure.grounded_claims, 1);
        assert_eq!(subj.structure.ungrounded_claims, 1);
        assert_eq!(subj.source.text_len, reference.len());
    }
}
