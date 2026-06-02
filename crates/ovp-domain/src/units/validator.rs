//! Deterministic validation of raw units against the source.
//!
//! Enforces **grounding + structure** only:
//! - the `evidence_quote` is found in the source (Exact → Whitespace → Relaxed
//!   ladder); a located quote is required for `accepted`,
//! - required enums parsed (a malformed unit is rejected, not silently dropped),
//! - each argument surface is locatable in the quote or near-context.
//!
//! It does NOT judge faithfulness of `text`, or whether the attribution/modality
//! *values* are correct — those are semantic and go to human review.
//!
//! Pure + deterministic: same `(raw_values, source)` → identical `SourceExtraction`.

use std::collections::BTreeMap;

use sha2::{Digest, Sha256};

use crate::source_doc::SourceDoc;

use super::parser::RawUnit;
use super::{
    Argument, EvidenceLocation, MatchKind, SourceExtraction, Unit, UnitEvidence, UnitStatus,
    ValidationIssue, ValidationReport,
};

const NEAR_CONTEXT_BYTES: usize = 200;
const SCHEMA_VERSION: u32 = 1;

/// Validate the raw `units[]` values against `source`. `raw_values` are the
/// opaque JSON values from [`super::parse_envelope`]; each is deserialized into a
/// [`RawUnit`] here so a single malformed unit becomes a rejected unit.
pub fn validate(raw_values: &[serde_json::Value], source: &SourceDoc) -> SourceExtraction {
    let body = &source.body_markdown;
    let mut units: Vec<Unit> = Vec::with_capacity(raw_values.len());

    for (idx, value) in raw_values.iter().enumerate() {
        match serde_json::from_value::<RawUnit>(value.clone()) {
            Ok(raw) => units.push(validate_one(idx, raw, body)),
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
/// envelope) so the review pack still records the failure rather than vanishing.
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

fn validate_one(idx: usize, raw: RawUnit, body: &str) -> Unit {
    let id = unit_id(idx, &raw.evidence_quote);
    let mut issues: Vec<ValidationIssue> = Vec::new();

    let quote = raw.evidence_quote.trim().to_string();
    let location = if quote.is_empty() {
        issues.push(ValidationIssue::new("unit.no_evidence", "empty evidence_quote"));
        None
    } else {
        match find_quote(body, &quote) {
            Some(loc) => Some(loc),
            None => {
                issues.push(ValidationIssue::new(
                    "unit.quote_not_found",
                    "evidence_quote does not appear in the source body",
                ));
                None
            }
        }
    };

    // Argument locatability (only meaningful when the quote located).
    let mut arguments: Vec<Argument> = Vec::with_capacity(raw.arguments.len());
    let mut any_arg_drift = false;
    for mut arg in raw.arguments {
        arg.locatable = match &location {
            Some(loc) => argument_locatable(&arg.surface, &quote, body, loc),
            None => false,
        };
        if !arg.locatable && location.is_some() {
            any_arg_drift = true;
        }
        arguments.push(arg);
    }
    if any_arg_drift {
        issues.push(ValidationIssue::new(
            "unit.argument_drift",
            "one or more argument surfaces not found in the quote or near-context",
        ));
    }

    // Status ladder: rejection beats needs_review beats accepted.
    let status = match &location {
        None => UnitStatus::Rejected,
        Some(loc) if loc.match_kind == MatchKind::Relaxed => {
            issues.push(ValidationIssue::new(
                "unit.quote_fuzzy_match",
                "quote matched only after stripping markdown/case — verify it",
            ));
            UnitStatus::NeedsReview
        }
        Some(_) if any_arg_drift => UnitStatus::NeedsReview,
        Some(_) => UnitStatus::Accepted,
    };

    Unit {
        id,
        kind: raw.kind,
        subtype: raw.subtype.filter(|s| !s.trim().is_empty()),
        text: raw.text.trim().to_string(),
        evidence: UnitEvidence { quote, location },
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
        // Best-effort kind; the unit is rejected regardless.
        kind: UnitKind::Assertion,
        subtype: None,
        text: value.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        evidence: UnitEvidence {
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
        issues: vec![ValidationIssue::new(
            "unit.malformed",
            format!("could not parse unit: {err}"),
        )],
    }
}

// ---- quote matching (UTF-8 safe; Exact → Whitespace → Relaxed) ----

fn find_quote(body: &str, quote: &str) -> Option<EvidenceLocation> {
    // Tier 1: verbatim byte substring.
    if let Some(start) = body.find(quote) {
        return Some(EvidenceLocation {
            byte_start: start,
            byte_end: start + quote.len(),
            line: line_of(body, start),
            match_kind: MatchKind::Exact,
        });
    }
    // Tier 2: whitespace-collapsed (case preserved).
    if let Some(loc) = normalized_match(body, quote, false, MatchKind::Whitespace) {
        return Some(loc);
    }
    // Tier 3: relaxed (whitespace + lowercase + markdown noise stripped).
    normalized_match(body, quote, true, MatchKind::Relaxed)
}

/// A char paired with the byte offset of the ORIGINAL char it came from.
struct Norm {
    chars: Vec<char>,
    orig: Vec<usize>,
}

fn normalize(s: &str, relaxed: bool) -> Norm {
    let mut chars = Vec::new();
    let mut orig = Vec::new();
    let mut prev_space = false;
    for (b, c) in s.char_indices() {
        if c.is_whitespace() {
            if !prev_space && !chars.is_empty() {
                chars.push(' ');
                orig.push(b);
                prev_space = true;
            }
            continue;
        }
        if relaxed && is_markdown_noise(c) {
            continue;
        }
        let c = if relaxed { c.to_ascii_lowercase() } else { c };
        chars.push(c);
        orig.push(b);
        prev_space = false;
    }
    if chars.last() == Some(&' ') {
        chars.pop();
        orig.pop();
    }
    Norm { chars, orig }
}

fn is_markdown_noise(c: char) -> bool {
    matches!(c, '*' | '_' | '`' | '#' | '>' | '~' | '[' | ']' | '(' | ')')
}

fn normalized_match(body: &str, quote: &str, relaxed: bool, kind: MatchKind) -> Option<EvidenceLocation> {
    let hay = normalize(body, relaxed);
    let needle = normalize(quote, relaxed);
    let i = find_subsequence(&hay.chars, &needle.chars)?;
    let byte_start = hay.orig[i];
    let after = i + needle.chars.len();
    let byte_end = if after < hay.orig.len() { hay.orig[after] } else { body.len() };
    Some(EvidenceLocation {
        byte_start,
        byte_end,
        line: line_of(body, byte_start),
        match_kind: kind,
    })
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

fn argument_locatable(surface: &str, quote: &str, body: &str, loc: &EvidenceLocation) -> bool {
    let s = surface.trim();
    if s.is_empty() {
        return false;
    }
    if contains_ci(quote, s) {
        return true;
    }
    // Near-context window around the located quote in the source.
    let start = loc.byte_start.saturating_sub(NEAR_CONTEXT_BYTES);
    let end = (loc.byte_end + NEAR_CONTEXT_BYTES).min(body.len());
    let window = &body[floor_char_boundary(body, start)..ceil_char_boundary(body, end)];
    contains_ci(window, s)
}

fn contains_ci(haystack: &str, needle: &str) -> bool {
    // Whitespace-collapsed, lowercased containment — robust to spacing/case.
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

fn floor_char_boundary(s: &str, mut i: usize) -> usize {
    if i >= s.len() {
        return s.len();
    }
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

fn ceil_char_boundary(s: &str, mut i: usize) -> usize {
    if i >= s.len() {
        return s.len();
    }
    while i < s.len() && !s.is_char_boundary(i) {
        i += 1;
    }
    i
}

// ---- dedup + metrics ----

fn duplicate_groups(units: &[Unit]) -> Vec<Vec<String>> {
    // Group non-rejected units by normalized text; surface groups of 2+.
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

    fn src(body: &str) -> SourceDoc {
        SourceDoc::article("T", "https://e/x", None, None, vec![], body)
    }

    fn raw(kind: &str, text: &str, quote: &str, args: &[&str]) -> serde_json::Value {
        let args: Vec<_> = args
            .iter()
            .map(|s| serde_json::json!({ "surface": s, "role": "topic" }))
            .collect();
        serde_json::json!({
            "kind": kind, "text": text, "evidence_quote": quote,
            "attribution": "author", "modality": "asserted", "arguments": args
        })
    }

    #[test]
    fn exact_quote_accepts_and_locates() {
        let body = "Intro line.\nA chunk is a structurally neutral container.\nMore.";
        let ex = validate(&[raw("assertion", "A chunk is neutral.", "A chunk is a structurally neutral container.", &["chunk"])], &src(body));
        assert_eq!(ex.report.accepted, 1);
        let u = &ex.units[0];
        assert_eq!(u.status, UnitStatus::Accepted);
        let loc = u.evidence.location.as_ref().unwrap();
        assert_eq!(loc.match_kind, MatchKind::Exact);
        assert_eq!(loc.line, 2, "quote is on line 2");
        assert!(u.arguments[0].locatable, "`chunk` is in the quote");
    }

    #[test]
    fn whitespace_variant_still_accepts() {
        let body = "A chunk is a   structurally\nneutral container.";
        let ex = validate(&[raw("assertion", "x", "A chunk is a structurally neutral container.", &[])], &src(body));
        let loc = ex.units[0].evidence.location.as_ref().unwrap();
        assert_eq!(loc.match_kind, MatchKind::Whitespace);
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
    }

    #[test]
    fn markdown_emphasis_is_relaxed_needs_review() {
        let body = "The **chunk** is the wrong unit.";
        let ex = validate(&[raw("assertion", "x", "The chunk is the wrong unit.", &[])], &src(body));
        let u = &ex.units[0];
        assert_eq!(u.evidence.location.as_ref().unwrap().match_kind, MatchKind::Relaxed);
        assert_eq!(u.status, UnitStatus::NeedsReview);
    }

    #[test]
    fn quote_not_in_source_rejects() {
        let ex = validate(&[raw("assertion", "x", "This sentence is not in the body at all.", &[])], &src("Some other text."));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.quote_not_found"));
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn empty_quote_rejects_no_evidence() {
        let ex = validate(&[raw("assertion", "x", "   ", &[])], &src("body"));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.no_evidence"));
    }

    #[test]
    fn argument_not_in_context_needs_review() {
        let body = "A chunk is a structurally neutral container.";
        let ex = validate(&[raw("assertion", "x", "A chunk is a structurally neutral container.", &["Azure AI Search"])], &src(body));
        assert_eq!(ex.units[0].status, UnitStatus::NeedsReview);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.argument_drift"));
        assert!((ex.report.argument_locatable_rate - 0.0).abs() < 1e-9);
    }

    #[test]
    fn malformed_unit_is_rejected_not_fatal() {
        // second unit lacks `attribution` → rejected; first still accepted.
        let body = "A chunk is a structurally neutral container.";
        let good = raw("assertion", "x", "A chunk is a structurally neutral container.", &[]);
        let bad = serde_json::json!({"kind":"assertion","text":"y","evidence_quote":"q","modality":"asserted"});
        let ex = validate(&[good, bad], &src(body));
        assert_eq!(ex.report.total, 2);
        assert_eq!(ex.report.accepted, 1);
        assert_eq!(ex.report.rejected, 1);
        assert!(ex.units[1].issues.iter().any(|i| i.code == "unit.malformed"));
    }

    #[test]
    fn duplicates_surfaced() {
        let body = "A chunk is a structurally neutral container.";
        let q = "A chunk is a structurally neutral container.";
        let ex = validate(&[raw("assertion", "Same point.", q, &[]), raw("assertion", "same point.", q, &[])], &src(body));
        assert_eq!(ex.report.duplicate_groups.len(), 1);
        assert_eq!(ex.report.duplicate_groups[0].len(), 2);
    }

    #[test]
    fn deterministic_under_repeat() {
        let body = "A chunk is a structurally neutral container.";
        let v = [raw("assertion", "x", "A chunk is a structurally neutral container.", &["chunk"])];
        let a = validate(&v, &src(body));
        let b = validate(&v, &src(body));
        assert_eq!(a, b);
    }
}
