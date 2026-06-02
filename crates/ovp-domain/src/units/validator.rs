//! Deterministic validation against the RENDERED source view (M14a.2).
//!
//! The model is shown finest-grain rendered spans (`[p017.s002] plain text`) and
//! anchors each unit's `evidence_ref` to a span id (or a bare paragraph id). The
//! validator resolves that ref in the SAME rendered view and matches the quote
//! there — so "what the model saw" == "what we validate", eliminating the
//! raw-markdown / smart-quote / fullwidth mismatches the RCA found.
//!
//! Enforces **grounding + structure**: ref resolves, quote located in the ref
//! span (or its paragraph), located quote required for `accepted`. Arguments are
//! **advisory** (a warning, never gating). It does NOT judge faithfulness of
//! `text` or attribution/modality *values* — those are human review.
//!
//! Pure + deterministic.

use std::collections::BTreeMap;

use sha2::{Digest, Sha256};

use crate::source_doc::SourceDoc;

use super::parser::RawUnit;
use super::source_map::{fold_char, rendered_view, strip_markdown_links, RenderedSpan};
use super::{
    Argument, EvidenceLocation, MatchKind, SourceExtraction, Unit, UnitEvidence, UnitStatus,
    ValidationIssue, ValidationReport,
};

const SCHEMA_VERSION: u32 = 3;

/// A paragraph rolled up from its rendered spans, for the "ref a bare paragraph
/// id" case and the cross-span fallback.
struct ParaGroup {
    id: String,
    text: String,
    src_start: usize,
    src_end: usize,
}

fn paragraph_groups(spans: &[RenderedSpan]) -> Vec<ParaGroup> {
    let mut out: Vec<ParaGroup> = Vec::new();
    for sp in spans {
        match out.last_mut() {
            Some(g) if g.id == sp.para_id => {
                g.text.push(' ');
                g.text.push_str(&sp.text);
                g.src_end = sp.src_end;
            }
            _ => out.push(ParaGroup {
                id: sp.para_id.clone(),
                text: sp.text.clone(),
                src_start: sp.src_start,
                src_end: sp.src_end,
            }),
        }
    }
    out
}

pub fn validate(raw_values: &[serde_json::Value], source: &SourceDoc) -> SourceExtraction {
    let body = &source.body_markdown;
    let spans = rendered_view(body);
    let paras = paragraph_groups(&spans);
    let mut units: Vec<Unit> = Vec::with_capacity(raw_values.len());

    for (idx, value) in raw_values.iter().enumerate() {
        match serde_json::from_value::<RawUnit>(value.clone()) {
            Ok(raw) => units.push(validate_one(idx, raw, body, &spans, &paras)),
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

fn validate_one(
    idx: usize,
    raw: RawUnit,
    body: &str,
    spans: &[RenderedSpan],
    paras: &[ParaGroup],
) -> Unit {
    let id = unit_id(idx, &raw.evidence_quote);
    let mut issues: Vec<ValidationIssue> = Vec::new();

    let quote = raw.evidence_quote.trim().to_string();
    let reff = raw.evidence_ref.trim().to_string();

    // Resolve the ref: a span id (p017.s002) or a bare paragraph id (p017).
    let span = spans.iter().find(|s| s.id == reff);
    let para = paras.iter().find(|p| p.id == reff);
    let (ref_text, ref_range): (Option<&str>, Option<(usize, usize)>) = match (span, para) {
        (Some(s), _) => (Some(&s.text), Some((s.src_start, s.src_end))),
        (None, Some(p)) => (Some(&p.text), Some((p.src_start, p.src_end))),
        (None, None) => (None, None),
    };
    // The parent paragraph of a span ref, for the cross-span fallback.
    let parent = span.and_then(|s| paras.iter().find(|p| p.id == s.para_id));

    let mut location: Option<EvidenceLocation> = None;
    let mut ref_mismatch = false;

    if quote.is_empty() {
        issues.push(ValidationIssue::new("unit.no_evidence", "empty evidence_quote"));
    } else if ref_text.is_none() {
        issues.push(ValidationIssue::new(
            "unit.ref_not_found",
            format!("evidence_ref `{reff}` is not a span or paragraph id"),
        ));
    } else if let Some((_, _, kind)) = locate(ref_text.unwrap(), &quote) {
        location = Some(loc_at(body, ref_range.unwrap(), kind));
    } else if let Some(p) = parent {
        // Cross-span: quote not in the named span but in its paragraph.
        if let Some((_, _, kind)) = locate(&p.text, &quote) {
            issues.push(ValidationIssue::new(
                "unit.spans_paragraph",
                format!("quote spans beyond `{reff}`; matched in paragraph `{}`", p.id),
            ));
            location = Some(loc_at(body, (p.src_start, p.src_end), kind));
        }
    }

    // Not found in ref/parent → is it anywhere in the view? (ref_mismatch).
    if location.is_none() && !quote.is_empty() && ref_text.is_some() {
        match find_anywhere(spans, paras, &quote) {
            Some(other) => {
                ref_mismatch = true;
                issues.push(ValidationIssue::new(
                    "unit.ref_mismatch",
                    format!("quote found in `{other}`, not the declared `{reff}`"),
                ));
            }
            None => issues.push(ValidationIssue::new(
                "unit.quote_not_found",
                format!("quote not found in `{reff}` or anywhere in the rendered view"),
            )),
        }
    }

    // Arguments are ADVISORY: compute locatability + warn, but never gate status.
    let mut arguments: Vec<Argument> = Vec::with_capacity(raw.arguments.len());
    let mut drift = 0usize;
    for mut arg in raw.arguments {
        arg.locatable = location.is_some()
            && ref_text.map(|t| arg_in(&arg.surface, &quote, t)).unwrap_or(false);
        if !arg.locatable && location.is_some() {
            drift += 1;
        }
        arguments.push(arg);
    }
    if drift > 0 {
        issues.push(ValidationIssue::new(
            "unit.argument_drift_advisory",
            format!("{drift} argument(s) not found in the quote/span — advisory only"),
        ));
    }

    // Status: located → accepted; located-elsewhere → needs_review; else rejected.
    // Argument drift does NOT change status (advisory).
    let status = match (&location, ref_mismatch) {
        (Some(_), _) => UnitStatus::Accepted,
        (None, true) => UnitStatus::NeedsReview,
        (None, false) => UnitStatus::Rejected,
    };

    Unit {
        id,
        kind: raw.kind,
        subtype: raw.subtype.filter(|s| !s.trim().is_empty()),
        text: raw.text.trim().to_string(),
        evidence: UnitEvidence { ref_id: reff, quote, location },
        attribution: raw.attribution,
        modality: raw.modality,
        arguments,
        status,
        issues,
    }
}

/// Location is span/paragraph-GRANULAR: the ref's original source byte range.
/// Sub-offsets within the matched text are NOT usable, because matching happens
/// on the RENDERED span text (links collapsed, markers stripped, fullwidth
/// folded) whose offsets do not align with the raw-markdown body. So we report
/// the whole ref span's source range — always a valid char boundary.
fn loc_at(body: &str, range: (usize, usize), kind: MatchKind) -> EvidenceLocation {
    let (rs, re) = range;
    EvidenceLocation { byte_start: rs, byte_end: re, line: line_of(body, rs), match_kind: kind }
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
            ref_id: value.get("evidence_ref").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            quote: value.get("evidence_quote").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            location: None,
        },
        attribution: Attribution::SystemInterpretation,
        modality: Modality::Uncertain,
        arguments: Vec::new(),
        status: UnitStatus::Rejected,
        issues: vec![ValidationIssue::new("unit.malformed", format!("could not parse unit: {err}"))],
    }
}

// ---- matching (Exact → Whitespace → Rendered), scoped to a span/paragraph ----

fn locate(hay: &str, quote: &str) -> Option<(usize, usize, MatchKind)> {
    if let Some(s) = hay.find(quote) {
        return Some((s, s + quote.len(), MatchKind::Exact));
    }
    if let Some((s, e)) = normalized_locate(hay, quote) {
        return Some((s, e, MatchKind::Whitespace));
    }
    // Rendered tier — fold smart/fullwidth + link text on both sides. Offsets are
    // not meaningful after the transform, so the caller uses the span range.
    if rendered_contains(hay, quote) {
        return Some((0, hay.len(), MatchKind::Rendered));
    }
    None
}

fn find_anywhere(spans: &[RenderedSpan], paras: &[ParaGroup], quote: &str) -> Option<String> {
    spans
        .iter()
        .find_map(|s| locate(&s.text, quote).map(|_| s.id.clone()))
        .or_else(|| paras.iter().find_map(|p| locate(&p.text, quote).map(|_| p.id.clone())))
}

fn rendered_contains(hay: &str, quote: &str) -> bool {
    let q = render_norm(quote);
    !q.is_empty() && render_norm(hay).contains(&q)
}

fn render_norm(s: &str) -> String {
    let linked = strip_markdown_links(s);
    let mut out = String::with_capacity(linked.len());
    for c in linked.chars() {
        let c = fold_char(c);
        if c.is_whitespace() || matches!(c, '*' | '_' | '`' | '#' | '>' | '~' | '[' | ']' | '(' | ')') {
            continue;
        }
        out.push(c.to_ascii_lowercase());
    }
    out
}

struct Norm {
    chars: Vec<char>,
    orig: Vec<usize>,
}

fn normalize(s: &str) -> Norm {
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
    // Clamp to a char boundary defensively (callers pass span starts, which are
    // boundaries, but never slice mid-UTF-8).
    let mut o = byte_offset.min(body.len());
    while o > 0 && !body.is_char_boundary(o) {
        o -= 1;
    }
    body[..o].bytes().filter(|&b| b == b'\n').count() + 1
}

fn arg_in(surface: &str, quote: &str, ref_text: &str) -> bool {
    let s = surface.trim();
    !s.is_empty() && (contains_ci(quote, s) || contains_ci(ref_text, s))
}

fn contains_ci(haystack: &str, needle: &str) -> bool {
    let h = render_norm(haystack);
    let n = render_norm(needle);
    !n.is_empty() && h.contains(&n)
}

// ---- dedup + metrics ----

fn duplicate_groups(units: &[Unit]) -> Vec<Vec<String>> {
    let mut by_text: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for u in units.iter().filter(|u| u.status != UnitStatus::Rejected) {
        by_text.entry(render_norm(&u.text)).or_default().push(u.id.clone());
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
    let located = units.iter().filter(|u| u.evidence.location.is_some()).count();
    let has = |code: &str| units.iter().filter(|u| u.issues.iter().any(|i| i.code == code)).count();
    let ref_found = units
        .iter()
        .filter(|u| !u.issues.iter().any(|i| i.code == "unit.ref_not_found" || i.code == "unit.malformed"))
        .count();
    let accepted_without_quote = units
        .iter()
        .filter(|u| u.status == UnitStatus::Accepted && u.evidence.location.is_none())
        .count();

    ValidationReport {
        total,
        accepted,
        rejected,
        needs_review,
        ref_found_rate: ratio(ref_found, total),
        quote_found_rate: ratio(located, total),
        quote_maps_to_original: located,
        accepted_without_quote,
        ref_mismatch: has("unit.ref_mismatch"),
        quote_not_found: has("unit.quote_not_found"),
        argument_drift_advisory: has("unit.argument_drift_advisory"),
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

    // p001 heading; p002 two sentences; p003 a markdown link + bold.
    const BODY: &str =
        "# Why the chunk is a bad unit\n\nA chunk is a structurally neutral container. It knows nothing about ownership.\n\nUse [vitest-evals](https://x/y) and **Blockify** here.";

    fn src(body: &str) -> SourceDoc {
        SourceDoc::article("T", "https://e/x", None, None, vec![], body)
    }

    fn raw(reff: &str, quote: &str, args: &[&str]) -> serde_json::Value {
        let args: Vec<_> =
            args.iter().map(|s| serde_json::json!({ "surface": s, "role": "topic" })).collect();
        serde_json::json!({
            "kind": "assertion", "text": "t", "evidence_ref": reff, "evidence_quote": quote,
            "attribution": "author", "modality": "asserted", "arguments": args
        })
    }

    #[test]
    fn quote_in_span_accepts_and_maps_to_original() {
        let ex = validate(&[raw("p002.s001", "A chunk is a structurally neutral container.", &["chunk"])], &src(BODY));
        assert_eq!(ex.report.accepted, 1);
        let u = &ex.units[0];
        let loc = u.evidence.location.as_ref().unwrap();
        assert_eq!(&BODY[loc.byte_start..loc.byte_end], "A chunk is a structurally neutral container.");
        assert!(u.arguments[0].locatable);
    }

    #[test]
    fn rendered_link_and_bold_match_via_view() {
        // p003 rendered text is "Use vitest-evals and Blockify here." — model quotes that.
        let ex = validate(&[raw("p003.s001", "Use vitest-evals and Blockify here.", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
    }

    #[test]
    fn bare_paragraph_ref_resolves() {
        let ex = validate(&[raw("p002", "It knows nothing about ownership.", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
    }

    #[test]
    fn cross_span_quote_matches_parent_paragraph() {
        // Quote spans both sentences of p002 but refs only the first span.
        let q = "A chunk is a structurally neutral container. It knows nothing about ownership.";
        let ex = validate(&[raw("p002.s001", q, &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.spans_paragraph"));
    }

    #[test]
    fn wrong_ref_but_real_quote_is_ref_mismatch() {
        let ex = validate(&[raw("p003.s001", "It knows nothing about ownership.", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::NeedsReview);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.ref_mismatch"));
        assert_eq!(ex.report.ref_mismatch, 1);
    }

    #[test]
    fn quote_nowhere_rejects() {
        let ex = validate(&[raw("p002.s001", "this is not in the article at all", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert_eq!(ex.report.quote_not_found, 1);
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn bad_ref_rejects() {
        let ex = validate(&[raw("p099.s009", "A chunk is a structurally neutral container.", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Rejected);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.ref_not_found"));
    }

    #[test]
    fn argument_drift_is_advisory_not_gating() {
        // Quote located → accepted DESPITE an argument that doesn't locate.
        let ex = validate(&[raw("p002.s001", "A chunk is a structurally neutral container.", &["Pinecone"])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted, "arg drift must NOT block accept");
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.argument_drift_advisory"));
        assert_eq!(ex.report.argument_drift_advisory, 1);
    }

    #[test]
    fn cjk_semicolon_list_item_matches_its_span() {
        let body = "标题\n\n情景记忆：昨天发生了啥；语义记忆：你叫什么；程序性记忆：怎么完成";
        // The model anchors to one ；-item span and quotes (ASCII colon variant).
        let ex = validate(&[raw("p002.s002", "语义记忆:你叫什么", &[])], &src(body));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
    }

    #[test]
    fn deterministic_under_repeat() {
        let v = [raw("p002.s001", "A chunk is a structurally neutral container.", &["chunk"])];
        assert_eq!(validate(&v, &src(BODY)), validate(&v, &src(BODY)));
    }
}
