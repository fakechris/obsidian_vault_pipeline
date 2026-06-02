//! Deterministic validation of raw units against the source (M14a.1: ref-scoped).
//!
//! Enforces **grounding + structure** only:
//! - `evidence_ref` names a real paragraph (`pNNN`),
//! - the `evidence_quote` is found WITHIN that paragraph (Exact → Whitespace →
//!   Relaxed ladder) — a located quote in its ref paragraph is required for
//!   `accepted`,
//! - required enums parsed (a malformed unit is rejected, not silently dropped),
//! - each argument surface is locatable in the quote or the ref paragraph.
//!
//! Quote found in a *different* paragraph ⇒ `needs_review` (`ref_mismatch`): the
//! evidence is real but the transport is wrong. Quote found nowhere ⇒ rejected.
//!
//! It does NOT judge faithfulness of `text` or whether attribution/modality
//! *values* are correct — those are semantic and go to human review.
//!
//! Pure + deterministic: same `(raw_values, source)` → identical `SourceExtraction`.

use std::collections::BTreeMap;

use sha2::{Digest, Sha256};

use crate::source_doc::SourceDoc;

use super::parser::RawUnit;
use super::source_map::{find_paragraph, paragraphs, Paragraph};
use super::{
    Argument, EvidenceLocation, MatchKind, SourceExtraction, Unit, UnitEvidence, UnitStatus,
    ValidationIssue, ValidationReport,
};

const SCHEMA_VERSION: u32 = 2;

/// Validate the raw `units[]` values against `source`, scoping each quote match
/// to the paragraph named by its `evidence_ref`.
pub fn validate(raw_values: &[serde_json::Value], source: &SourceDoc) -> SourceExtraction {
    let body = &source.body_markdown;
    let paras = paragraphs(body);
    let mut units: Vec<Unit> = Vec::with_capacity(raw_values.len());

    for (idx, value) in raw_values.iter().enumerate() {
        match serde_json::from_value::<RawUnit>(value.clone()) {
            Ok(raw) => units.push(validate_one(idx, raw, body, &paras)),
            Err(e) => units.push(malformed_unit(idx, value, &e.to_string())),
        }
    }

    let duplicate_groups = duplicate_groups(&units);
    let report = build_report(&units, duplicate_groups, None);
    SourceExtraction {
        source_id: source_id(source),
        source_fingerprint: hex_sha256(body.as_bytes()),
        title: source.title.clone(),
        source_url: source.source_url.clone(),
        schema_version: SCHEMA_VERSION,
        units,
        report,
    }
}

/// Build a parse-failed extraction (the model output was not a valid unit
/// envelope) so the review pack still records the failure.
pub fn extraction_parse_failed(source: &SourceDoc, detail: String) -> SourceExtraction {
    SourceExtraction {
        source_id: source_id(source),
        source_fingerprint: hex_sha256(source.body_markdown.as_bytes()),
        title: source.title.clone(),
        source_url: source.source_url.clone(),
        schema_version: SCHEMA_VERSION,
        units: Vec::new(),
        report: build_report(&[], Vec::new(), Some(detail)),
    }
}

fn validate_one(idx: usize, raw: RawUnit, body: &str, paras: &[Paragraph]) -> Unit {
    let id = unit_id(idx, &raw.evidence_quote);
    let mut issues: Vec<ValidationIssue> = Vec::new();

    let quote = raw.evidence_quote.trim().to_string();
    let pref = raw.evidence_ref.trim().to_string();
    let para = find_paragraph(paras, &pref);

    // Locate the quote, scoped to the declared paragraph.
    let mut location: Option<EvidenceLocation> = None;
    let mut ref_mismatch = false;
    if quote.is_empty() {
        issues.push(ValidationIssue::new("unit.no_evidence", "empty evidence_quote"));
    } else if para.is_none() {
        issues.push(ValidationIssue::new(
            "unit.ref_not_found",
            format!("evidence_ref `{pref}` is not a paragraph id"),
        ));
    } else {
        let para = para.unwrap();
        match locate(&para.text, &quote) {
            Some((s, e, kind)) => {
                let byte_start = para.byte_start + s;
                let byte_end = para.byte_start + e;
                location = Some(EvidenceLocation {
                    byte_start,
                    byte_end,
                    line: line_of(body, byte_start),
                    match_kind: kind,
                });
            }
            // Faithful-render tier: match after rendering BOTH sides to plain
            // text (link text, smart quotes, fullwidth CJK, emphasis, case).
            // Paragraph-granular location (sub-offsets lost in the transform).
            None if rendered_contains(&para.text, &quote) => {
                location = Some(EvidenceLocation {
                    byte_start: para.byte_start,
                    byte_end: para.byte_end,
                    line: line_of(body, para.byte_start),
                    match_kind: MatchKind::Rendered,
                });
            }
            None => match find_in_any_paragraph(paras, &quote) {
                Some(other) => {
                    ref_mismatch = true;
                    issues.push(ValidationIssue::new(
                        "unit.ref_mismatch",
                        format!("quote found in `{other}`, not the declared `{pref}`"),
                    ));
                }
                None => issues.push(ValidationIssue::new(
                    "unit.quote_not_found",
                    format!("quote not found in `{pref}` or anywhere in the source"),
                )),
            },
        }
    }

    // Argument locatability — within the quote or the referenced paragraph.
    let para_text = para.map(|p| p.text.as_str()).unwrap_or("");
    let mut arguments: Vec<Argument> = Vec::with_capacity(raw.arguments.len());
    let mut any_arg_drift = false;
    for mut arg in raw.arguments {
        arg.locatable = location.is_some() && argument_locatable(&arg.surface, &quote, para_text);
        if !arg.locatable && location.is_some() {
            any_arg_drift = true;
        }
        arguments.push(arg);
    }
    if any_arg_drift {
        issues.push(ValidationIssue::new(
            "unit.argument_drift",
            "one or more argument surfaces not found in the quote or its paragraph",
        ));
    }

    // Status: rejection beats needs_review beats accepted. Exact / Whitespace /
    // Rendered are all faithful matches → accepted (modulo argument drift).
    let status = match &location {
        Some(_) if any_arg_drift => UnitStatus::NeedsReview,
        Some(_) => UnitStatus::Accepted,
        None if ref_mismatch => UnitStatus::NeedsReview,
        None => UnitStatus::Rejected,
    };

    Unit {
        id,
        kind: raw.kind,
        subtype: raw.subtype.filter(|s| !s.trim().is_empty()),
        text: raw.text.trim().to_string(),
        evidence: UnitEvidence { paragraph_ref: pref, quote, location },
        attribution: raw.attribution,
        modality: raw.modality,
        arguments,
        status,
        issues,
    }
}

fn malformed_unit(idx: usize, value: &serde_json::Value, err: &str) -> Unit {
    use super::{Attribution, Modality, UnitKind};
    let raw_text = value.to_string();
    Unit {
        id: unit_id(idx, &raw_text),
        kind: UnitKind::Assertion,
        subtype: None,
        text: value.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        evidence: UnitEvidence {
            paragraph_ref: value
                .get("evidence_ref")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            quote: value
                .get("evidence_quote")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            location: None,
        },
        attribution: Attribution::SystemInterpretation,
        modality: Modality::Uncertain,
        arguments: Vec::new(),
        status: UnitStatus::Rejected,
        issues: vec![ValidationIssue::new("unit.malformed", format!("could not parse unit: {err}"))],
    }
}

// ---- quote matching (UTF-8 safe; Exact → Whitespace → Relaxed) ----

/// Locate `quote` within `hay` with byte-precise offsets: verbatim, then
/// whitespace-insensitive. The faithful-render tier is handled separately by the
/// caller (it is paragraph-granular, so it does not return offsets here).
fn locate(hay: &str, quote: &str) -> Option<(usize, usize, MatchKind)> {
    if let Some(s) = hay.find(quote) {
        return Some((s, s + quote.len(), MatchKind::Exact));
    }
    normalized_locate(hay, quote).map(|(s, e)| (s, e, MatchKind::Whitespace))
}

fn find_in_any_paragraph(paras: &[Paragraph], quote: &str) -> Option<String> {
    paras
        .iter()
        .find_map(|p| (locate(&p.text, quote).is_some() || rendered_contains(&p.text, quote)).then(|| p.id.clone()))
}

// ---- faithful plain-text render (M14a.RCA fix) ----

/// True if `quote` is contained in `hay` after rendering BOTH to plain text:
/// markdown link text extracted, smart quotes / dashes / fullwidth-CJK folded to
/// ASCII, emphasis + whitespace stripped, case-folded. All faithful, reversible
/// normalizations, so a match here is grounded (the model copied the rendered
/// form; the source is raw markdown).
fn rendered_contains(hay: &str, quote: &str) -> bool {
    let q = render_norm(quote);
    !q.is_empty() && render_norm(hay).contains(&q)
}

fn render_norm(s: &str) -> String {
    let linked = strip_markdown_links(s);
    let mut out = String::with_capacity(linked.len());
    for c in linked.chars() {
        let c = fold_char(c);
        if c.is_whitespace() || is_markdown_noise(c) {
            continue;
        }
        out.push(c.to_ascii_lowercase());
    }
    out
}

/// Replace `[text](url)` / `![alt](url)` with just the visible `text`/`alt`.
fn strip_markdown_links(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < s.len() {
        if !s.is_char_boundary(i) {
            i += 1;
            continue;
        }
        if bytes[i] == b'[' {
            if let Some(close) = s[i + 1..].find(']') {
                let text_end = i + 1 + close;
                let after = text_end + 1;
                if after < s.len() && bytes.get(after) == Some(&b'(') {
                    if let Some(paren) = s[after..].find(')') {
                        out.push_str(&s[i + 1..text_end]); // the link text
                        i = after + paren + 1;
                        continue;
                    }
                }
            }
        }
        let ch = s[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

/// Fold a single char to its ASCII equivalent: fullwidth ASCII (used in CJK
/// text) → halfwidth, smart quotes/dashes + common CJK punctuation → ASCII.
fn fold_char(c: char) -> char {
    match c {
        // Fullwidth ASCII (common in CJK text) → halfwidth. This already covers
        // fullwidth ：，；！？＇＂（） etc. via the -0xFEE0 offset.
        '\u{FF01}'..='\u{FF5E}' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c),
        '\u{3000}' => ' ',                                   // ideographic space
        '\u{2018}' | '\u{2019}' => '\'',                      // smart single quotes
        '\u{201C}' | '\u{201D}' | '\u{300C}' | '\u{300D}' => '"', // smart / 「」 quotes
        '\u{2013}' | '\u{2014}' => '-',                       // en / em dash
        '\u{3001}' => ',',                                    // 、 ideographic comma
        '\u{3002}' => '.',                                    // 。 ideographic stop
        _ => c,
    }
}

struct Norm {
    chars: Vec<char>,
    orig: Vec<usize>,
}

fn normalize(s: &str) -> Norm {
    // Whitespace-INSENSITIVE: drop whitespace entirely rather than collapse it to
    // a single space. Whitespace is not meaningful for "is this quote a span of
    // the source", and a model — especially in CJK, which has no inter-word
    // spaces — routinely drops a newline/space the source has. `orig[i]` still
    // maps each kept char to its original byte offset (for precise location).
    let mut chars = Vec::new();
    let mut orig = Vec::new();
    for (b, c) in s.char_indices() {
        if c.is_whitespace() {
            continue;
        }
        chars.push(c);
        orig.push(b);
    }
    Norm { chars, orig }
}

fn is_markdown_noise(c: char) -> bool {
    matches!(c, '*' | '_' | '`' | '#' | '>' | '~' | '[' | ']' | '(' | ')')
}

fn normalized_locate(hay: &str, quote: &str) -> Option<(usize, usize)> {
    let h = normalize(hay);
    let n = normalize(quote);
    let i = find_subsequence(&h.chars, &n.chars)?;
    let byte_start = h.orig[i];
    let after = i + n.chars.len();
    let byte_end = if after < h.orig.len() { h.orig[after] } else { hay.len() };
    Some((byte_start, byte_end))
}

fn find_subsequence(hay: &[char], needle: &[char]) -> Option<usize> {
    if needle.is_empty() || needle.len() > hay.len() {
        return None;
    }
    (0..=hay.len() - needle.len()).find(|&i| hay[i..i + needle.len()] == needle[..])
}

fn line_of(body: &str, byte_offset: usize) -> usize {
    body[..byte_offset.min(body.len())].bytes().filter(|&b| b == b'\n').count() + 1
}

// ---- argument locatability ----

fn argument_locatable(surface: &str, quote: &str, para_text: &str) -> bool {
    let s = surface.trim();
    !s.is_empty() && (contains_ci(quote, s) || contains_ci(para_text, s))
}

fn contains_ci(haystack: &str, needle: &str) -> bool {
    let h = collapse_lower(haystack);
    let n = collapse_lower(needle);
    !n.is_empty() && h.contains(&n)
}

fn collapse_lower(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_space = false;
    for c in s.chars() {
        if c.is_whitespace() {
            if !prev_space && !out.is_empty() {
                out.push(' ');
                prev_space = true;
            }
        } else {
            out.push(c.to_ascii_lowercase());
            prev_space = false;
        }
    }
    out.trim().to_string()
}

// ---- dedup + metrics ----

fn duplicate_groups(units: &[Unit]) -> Vec<Vec<String>> {
    let mut by_text: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for u in units.iter().filter(|u| u.status != UnitStatus::Rejected) {
        by_text.entry(collapse_lower(&u.text)).or_default().push(u.id.clone());
    }
    by_text.into_values().filter(|g| g.len() > 1).collect()
}

fn build_report(
    units: &[Unit],
    duplicate_groups: Vec<Vec<String>>,
    parse_error: Option<String>,
) -> ValidationReport {
    let total = units.len();
    let accepted = units.iter().filter(|u| u.status == UnitStatus::Accepted).count();
    let rejected = units.iter().filter(|u| u.status == UnitStatus::Rejected).count();
    let needs_review = units.iter().filter(|u| u.status == UnitStatus::NeedsReview).count();
    let with_quote = units.iter().filter(|u| u.evidence.location.is_some()).count();
    let accepted_without_quote = units
        .iter()
        .filter(|u| u.status == UnitStatus::Accepted && u.evidence.location.is_none())
        .count();
    let total_args: usize = units.iter().map(|u| u.arguments.len()).sum();
    let locatable_args: usize =
        units.iter().flat_map(|u| &u.arguments).filter(|a| a.locatable).count();

    ValidationReport {
        total,
        accepted,
        rejected,
        needs_review,
        quote_found_rate: ratio(with_quote, total),
        accepted_without_quote,
        argument_locatable_rate: if total_args == 0 { 1.0 } else { ratio(locatable_args, total_args) },
        duplicate_groups,
        parse_error,
    }
}

fn ratio(num: usize, den: usize) -> f64 {
    if den == 0 {
        0.0
    } else {
        num as f64 / den as f64
    }
}

fn unit_id(idx: usize, quote: &str) -> String {
    format!("u-{idx:03}-{}", &hex_sha256(quote.as_bytes())[..8])
}

fn source_id(source: &SourceDoc) -> String {
    if source.source_url.trim().is_empty() {
        source.title.clone()
    } else {
        source.source_url.clone()
    }
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    use std::fmt::Write;
    for b in digest.iter() {
        write!(s, "{b:02x}").expect("infallible");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    // A body with three paragraphs: p001 heading, p002 chunk, p003 blockify.
    const BODY: &str = "# Why the chunk is a bad unit\n\nA chunk is a structurally neutral container.\n\nBlockify converts documents into IdeaBlocks.";

    fn src(body: &str) -> SourceDoc {
        SourceDoc::article("T", "https://e/x", None, None, vec![], body)
    }

    fn raw(reff: &str, quote: &str, args: &[&str]) -> serde_json::Value {
        let args: Vec<_> = args
            .iter()
            .map(|s| serde_json::json!({ "surface": s, "role": "topic" }))
            .collect();
        serde_json::json!({
            "kind": "assertion", "text": "t", "evidence_ref": reff, "evidence_quote": quote,
            "attribution": "author", "modality": "asserted", "arguments": args
        })
    }

    #[test]
    fn quote_in_ref_paragraph_accepts_and_maps_to_body() {
        let ex = validate(&[raw("p002", "A chunk is a structurally neutral container.", &["chunk"])], &src(BODY));
        assert_eq!(ex.report.accepted, 1);
        let u = &ex.units[0];
        assert_eq!(u.evidence.paragraph_ref, "p002");
        let loc = u.evidence.location.as_ref().unwrap();
        assert_eq!(loc.line, 3, "p002 is on line 3 of the body");
        assert_eq!(&BODY[loc.byte_start..loc.byte_end], "A chunk is a structurally neutral container.");
        assert!(u.arguments[0].locatable);
    }

    #[test]
    fn ref_not_a_paragraph_rejects() {
        let ex = validate(&[raw("p099", "A chunk is a structurally neutral container.", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.ref_not_found"));
    }

    #[test]
    fn quote_in_wrong_paragraph_is_ref_mismatch_needs_review() {
        // Quote belongs to p003 but the model declared p002.
        let ex = validate(&[raw("p002", "Blockify converts documents into IdeaBlocks.", &[])], &src(BODY));
        let u = &ex.units[0];
        assert_eq!(u.status, UnitStatus::NeedsReview);
        assert!(u.issues.iter().any(|i| i.code == "unit.ref_mismatch"));
        // ref_mismatch does NOT count as quote_found (failed transport discipline).
        assert!(u.evidence.location.is_none());
        assert_eq!(ex.report.quote_found_rate, 0.0);
    }

    #[test]
    fn quote_nowhere_rejects() {
        let ex = validate(&[raw("p002", "this sentence is in no paragraph", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.quote_not_found"));
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn markdown_emphasis_matches_via_render_and_accepts() {
        let body = "# H\n\nThe **chunk** is the wrong unit.";
        let ex = validate(&[raw("p002", "The chunk is the wrong unit.", &[])], &src(body));
        let u = &ex.units[0];
        assert_eq!(u.evidence.location.as_ref().unwrap().match_kind, MatchKind::Rendered);
        assert_eq!(u.status, UnitStatus::Accepted);
    }

    #[test]
    fn markdown_link_text_and_smart_quotes_match_via_render() {
        // Model copies visible link text + ASCII apostrophe; source has the
        // markdown link syntax + a smart apostrophe. Render tier recovers it.
        let body = "# H\n\nUse [vitest-evals](https://x/y) when it\u{2019}s offline.";
        let ex = validate(&[raw("p002", "Use vitest-evals when it's offline.", &[])], &src(body));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
        assert_eq!(ex.units[0].evidence.location.as_ref().unwrap().match_kind, MatchKind::Rendered);
    }

    #[test]
    fn fullwidth_cjk_punctuation_matches_via_render() {
        // Source has fullwidth colon/comma; model copies ASCII. Fold recovers it.
        let body = "标题\n\n记忆分为三类：情景、语义、程序。";
        let ex = validate(&[raw("p002", "记忆分为三类:情景,语义,程序.", &[])], &src(body));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
    }

    #[test]
    fn empty_quote_rejects() {
        let ex = validate(&[raw("p002", "   ", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.no_evidence"));
    }

    #[test]
    fn argument_not_in_paragraph_needs_review() {
        let ex = validate(&[raw("p002", "A chunk is a structurally neutral container.", &["Azure"])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::NeedsReview);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.argument_drift"));
    }

    #[test]
    fn missing_ref_is_malformed_rejected() {
        let bad = serde_json::json!({"kind":"assertion","text":"t","evidence_quote":"q","attribution":"author","modality":"asserted"});
        let ex = validate(&[bad], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.malformed"));
    }

    #[test]
    fn cjk_quote_in_ref_paragraph() {
        let body = "标题\n\n首先对大模型的两次API调用之间是没有记忆的。\n\n第三段。";
        let ex = validate(&[raw("p002", "两次API调用之间是没有记忆的", &[])], &src(body));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
    }

    #[test]
    fn deterministic_under_repeat() {
        let v = [raw("p002", "A chunk is a structurally neutral container.", &["chunk"])];
        assert_eq!(validate(&v, &src(BODY)), validate(&v, &src(BODY)));
    }
}
