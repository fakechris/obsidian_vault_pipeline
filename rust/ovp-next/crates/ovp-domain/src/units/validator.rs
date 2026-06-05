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

use super::normalize::{contains_ci, render_norm};
use super::parser::RawUnit;
use super::source_map::{rendered_view, RenderedSpan};
use super::{
    Argument, EvidenceLocation, MatchKind, SourceExtraction, Unit, UnitEvidence, UnitStatus,
    ValidationIssue, ValidationReport,
};

const SCHEMA_VERSION: u32 = 3;

/// Max radius (in spans) the deterministic window expands on each side of the
/// ref. Bounded so a match stays NEAR the ref (never a whole-article accept).
const WINDOW_RADIUS: usize = 6;
/// Similarity at/above which a non-deterministic match is flagged for review
/// (never accepted). High on purpose — only genuinely near-verbatim quotes.
const NEAR_MATCH_THRESHOLD: f64 = 0.95;

pub fn validate(raw_values: &[serde_json::Value], source: &SourceDoc) -> SourceExtraction {
    let body = &source.body_markdown;
    let spans = rendered_view(body);
    let mut units: Vec<Unit> = Vec::with_capacity(raw_values.len());

    for (idx, value) in raw_values.iter().enumerate() {
        match serde_json::from_value::<RawUnit>(value.clone()) {
            Ok(raw) => units.push(validate_one(idx, raw, body, &spans)),
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

fn validate_one(idx: usize, raw: RawUnit, body: &str, spans: &[RenderedSpan]) -> Unit {
    let id = unit_id(idx, &raw.evidence_quote);
    let mut issues: Vec<ValidationIssue> = Vec::new();

    let quote = raw.evidence_quote.trim().to_string();
    let reff = raw.evidence_ref.trim().to_string();

    // Resolve the ref to a contiguous span-index range [lo..=hi] in the flat
    // span list: a span id (p017.s002) is one span; a bare paragraph id (p017)
    // is all its spans. ref_text/ref_range describe that exact ref region.
    let ref_idx = resolve_ref(spans, &reff);

    let mut location: Option<EvidenceLocation> = None;
    let mut ref_mismatch = false;
    let mut near_match = false;

    if quote.is_empty() {
        issues.push(ValidationIssue::new("unit.no_evidence", "empty evidence_quote"));
    } else if ref_idx.is_none() {
        issues.push(ValidationIssue::new(
            "unit.ref_not_found",
            format!("evidence_ref `{reff}` is not a span or paragraph id"),
        ));
    } else {
        let (lo, hi) = ref_idx.unwrap();
        // Tier A: exact/rendered substring inside the ref region itself.
        let ref_text = concat_spans(spans, lo, hi);
        if let Some((_, _, kind)) = locate(&ref_text, &quote) {
            location = Some(loc_at(body, (spans[lo].src_start, spans[hi].src_end), kind));
        } else if let Some((wlo, whi, kind)) = window_match(spans, lo, hi, &quote) {
            // Tier B: deterministic match in a contiguous window around the ref
            // (the quote straddles span/paragraph boundaries). Still verbatim.
            issues.push(ValidationIssue::new(
                "unit.spans_window",
                format!("quote spans a window `{}`..`{}` around the ref", spans[wlo].id, spans[whi].id),
            ));
            location = Some(loc_at(
                body,
                (spans[wlo].src_start, spans[whi].src_end),
                MatchKind::RenderedWindow,
            ));
            let _ = kind;
        } else if let Some(other) = find_anywhere(spans, &quote) {
            // Tier C: real quote, but far from the ref → ref_mismatch.
            ref_mismatch = true;
            issues.push(ValidationIssue::new(
                "unit.ref_mismatch",
                format!("quote found near `{other}`, not the declared `{reff}`"),
            ));
        } else if best_similarity(&ref_text, &quote) >= NEAR_MATCH_THRESHOLD {
            // Tier D: SIMILARITY only (no deterministic substring) → needs_review,
            // NEVER accepted. Grounding must stay deterministic.
            near_match = true;
            issues.push(ValidationIssue::new(
                "unit.near_match",
                "quote is a close-but-not-verbatim match in the ref — verify (not auto-accepted)",
            ));
        } else {
            issues.push(ValidationIssue::new(
                "unit.quote_not_found",
                format!("quote not found in `{reff}`, a window around it, or elsewhere"),
            ));
        }
    }

    // Arguments are ADVISORY: compute locatability + warn, but never gate status.
    let ctx = ref_idx.map(|(lo, hi)| concat_spans(spans, lo, hi)).unwrap_or_default();
    let mut arguments: Vec<Argument> = Vec::with_capacity(raw.arguments.len());
    let mut drift = 0usize;
    for mut arg in raw.arguments {
        arg.locatable = location.is_some() && arg_in(&arg.surface, &quote, &ctx);
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

    // Status: deterministic match → accepted; located-elsewhere or near-match →
    // needs_review; else rejected. Argument drift does NOT gate (advisory).
    let status = if location.is_some() {
        UnitStatus::Accepted
    } else if ref_mismatch || near_match {
        UnitStatus::NeedsReview
    } else {
        UnitStatus::Rejected
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

/// Resolve `reff` to a contiguous span-index range `[lo..=hi]`: a span id
/// (`p017.s002`) → one span; a bare paragraph id (`p017`) → all its spans.
fn resolve_ref(spans: &[RenderedSpan], reff: &str) -> Option<(usize, usize)> {
    if let Some(i) = spans.iter().position(|s| s.id == reff) {
        return Some((i, i));
    }
    let first = spans.iter().position(|s| s.para_id == reff)?;
    let last = spans.iter().rposition(|s| s.para_id == reff)?;
    Some((first, last))
}

/// Rendered text of spans `[lo..=hi]` joined by a space (whitespace-insensitive
/// matching ignores the join).
fn concat_spans(spans: &[RenderedSpan], lo: usize, hi: usize) -> String {
    spans[lo..=hi].iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ")
}

/// Deterministic match in a contiguous WINDOW around the ref region, expanding
/// symmetrically up to [`WINDOW_RADIUS`]. Returns the matched window's span
/// bounds. Bounded so the match stays near the ref — never the whole article.
fn window_match(
    spans: &[RenderedSpan],
    lo: usize,
    hi: usize,
    quote: &str,
) -> Option<(usize, usize, MatchKind)> {
    for r in 1..=WINDOW_RADIUS {
        let wlo = lo.saturating_sub(r);
        let whi = (hi + r).min(spans.len() - 1);
        let text = concat_spans(spans, wlo, whi);
        if let Some((_, _, kind)) = locate(&text, quote) {
            return Some((wlo, whi, kind));
        }
        if wlo == 0 && whi == spans.len() - 1 {
            break;
        }
    }
    None
}

/// A real (deterministic) match somewhere far from the ref → ref_mismatch.
fn find_anywhere(spans: &[RenderedSpan], quote: &str) -> Option<String> {
    spans.iter().find_map(|s| locate(&s.text, quote).map(|_| s.id.clone()))
}

/// Char-bigram Dice coefficient (0..1) between `quote` and the best same-length
/// window of `hay`, both render-normalized. Diagnostic ONLY (→ needs_review).
fn best_similarity(hay: &str, quote: &str) -> f64 {
    let h: Vec<char> = render_norm(hay).chars().collect();
    let q: Vec<char> = render_norm(quote).chars().collect();
    if q.len() < 2 || h.len() < q.len() {
        return 0.0;
    }
    let qb = bigrams(&q);
    let step = (q.len() / 4).max(1);
    let mut best = 0.0f64;
    let mut i = 0;
    while i + q.len() <= h.len() {
        let wb = bigrams(&h[i..i + q.len()]);
        let inter = qb.iter().filter(|b| wb.contains(b)).count();
        let dice = 2.0 * inter as f64 / (qb.len() + wb.len()) as f64;
        if dice > best {
            best = dice;
        }
        i += step;
    }
    best
}

fn bigrams(cs: &[char]) -> Vec<(char, char)> {
    cs.windows(2).map(|w| (w[0], w[1])).collect()
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

/// True if `quote` is a DETERMINISTIC substring of `text` (exact / whitespace /
/// faithful-render) — the same grounding test the validator uses to accept.
/// Exposed for the M14a.4 copy-only probe.
pub(crate) fn deterministic_contains(text: &str, quote: &str) -> bool {
    !quote.trim().is_empty() && locate(text, quote).is_some()
}

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

fn rendered_contains(hay: &str, quote: &str) -> bool {
    let q = render_norm(quote);
    !q.is_empty() && render_norm(hay).contains(&q)
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
        span_window_matches: has("unit.spans_window"),
        near_match_needs_review: has("unit.near_match"),
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
    fn cross_span_quote_matches_via_window() {
        // Quote spans both sentences of p002 but refs only the first span →
        // deterministic window match (RenderedWindow), accepted.
        let q = "A chunk is a structurally neutral container. It knows nothing about ownership.";
        let ex = validate(&[raw("p002.s001", q, &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.spans_window"));
        assert_eq!(ex.units[0].evidence.location.as_ref().unwrap().match_kind, MatchKind::RenderedWindow);
        assert_eq!(ex.report.span_window_matches, 1);
    }

    #[test]
    fn non_verbatim_quote_is_never_accepted() {
        // The core M14a.3 invariant: a quote that is not a deterministic
        // substring (even rendered/windowed) is NEVER accepted — no fuzzy
        // grounding. It is needs_review (near) or rejected, never accepted.
        let ex = validate(&[raw("p002.s001", "A chunk is a structurally neutral box.", &[])], &src(BODY));
        assert_ne!(ex.units[0].status, UnitStatus::Accepted, "non-verbatim must not be accepted");
        assert!(ex.units[0].evidence.location.is_none(), "no location for a non-deterministic match");
        assert_eq!(ex.report.accepted, 0);
    }

    #[test]
    fn near_verbatim_typo_is_needs_review_not_accepted() {
        // A 1-char typo (≥0.95 similar, NOT a substring) → near_match needs_review.
        let ex = validate(&[raw("p002.s001", "A chunk is a structurally neutrai container.", &[])], &src(BODY));
        assert_ne!(ex.units[0].status, UnitStatus::Accepted);
        assert!(ex.units[0].evidence.location.is_none());
    }

    #[test]
    fn adjacent_wrong_ref_recovered_by_window() {
        // Quote is in p002.s002 but ref'd p003.s001 (adjacent) → deterministic
        // window match near the ref → accepted (grounded), flagged spans_window.
        let ex = validate(&[raw("p003.s001", "It knows nothing about ownership.", &[])], &src(BODY));
        assert_eq!(ex.units[0].status, UnitStatus::Accepted);
        assert!(ex.units[0].issues.iter().any(|i| i.code == "unit.spans_window"));
    }

    #[test]
    fn distant_wrong_ref_is_ref_mismatch() {
        // 12 single-sentence paragraphs; ref p001 but quote from p012 — far
        // beyond the window radius → ref_mismatch (needs_review), not accepted.
        let body: String =
            (1..=12).map(|i| format!("Distinct sentence number {i} alpha.")).collect::<Vec<_>>().join("\n\n");
        let ex = validate(&[raw("p001.s001", "Distinct sentence number 12 alpha.", &[])], &src(&body));
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
