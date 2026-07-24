use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::ask::{EvidenceItem, EvidenceKind};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VerificationReport {
    pub cited: usize,
    pub verified: usize,
    pub missing: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn verify_answer(answer: &str, evidence: &[EvidenceItem]) -> VerificationReport {
    let citations = extract_citations(answer);
    let mut warnings = Vec::new();
    if citations.is_empty() {
        warnings.push("no_citations".to_string());
    }

    let evidence_by_citation = evidence
        .iter()
        .map(|item| (citation_key(item), item))
        .collect::<BTreeMap<_, _>>();

    let mut verified = 0usize;
    let mut missing = Vec::new();
    for citation in &citations {
        match evidence_by_citation.get(citation) {
            None => missing.push(citation.clone()),
            Some(item) if item_is_verifiable(item) => verified += 1,
            Some(item) => {
                missing.push(citation.clone());
                warnings.push(unverifiable_warning(citation, item.kind));
            }
        }
    }

    VerificationReport {
        cited: citations.len(),
        verified,
        missing,
        warnings,
    }
}

fn extract_citations(answer: &str) -> Vec<String> {
    let mut out = BTreeSet::new();
    let mut rest = answer;
    while let Some(start) = rest.find('[') {
        let after_start = &rest[start + 1..];
        let Some(end) = after_start.find(']') else {
            break;
        };
        let candidate = after_start[..end].trim();
        if is_citation_candidate(candidate) {
            out.insert(normalize_candidate(candidate));
        }
        rest = &after_start[end + 1..];
    }
    out.into_iter().collect()
}

/// Citation keys in order of FIRST appearance in the answer (deduplicated).
/// The `verify_answer` report sorts keys; presentation surfaces (the portal's
/// `[1][2]` markers) need the reading order instead — same tokenizer, so the
/// two views can never disagree on what counts as a citation.
pub fn citations_in_order(answer: &str) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    let mut rest = answer;
    while let Some(start) = rest.find('[') {
        let after_start = &rest[start + 1..];
        let Some(end) = after_start.find(']') else {
            break;
        };
        let candidate = after_start[..end].trim();
        if is_citation_candidate(candidate) {
            let normalized = normalize_candidate(candidate);
            if seen.insert(normalized.clone()) {
                out.push(normalized);
            }
        }
        rest = &after_start[end + 1..];
    }
    out
}

fn is_citation_candidate(candidate: &str) -> bool {
    !candidate.is_empty()
        && (candidate.starts_with("ck-")
            || matches!(
                candidate.split_once(':').map(|(prefix, _)| prefix),
                Some("unit" | "card" | "claim" | "source")
            ))
}

/// Canonical citation key for a bracket candidate. Models reliably shorten
/// `[claim:ck-…]` to `[ck-…]` — the `ck-` ledger prefix is reserved and
/// unambiguous, so a bare key normalizes to its `claim:` form instead of
/// being dropped as a non-citation. Everything else passes through.
fn normalize_candidate(candidate: &str) -> String {
    if candidate.starts_with("ck-") {
        format!("claim:{candidate}")
    } else {
        candidate.to_string()
    }
}

/// The `<kind>:<id>` key an answer must use to cite this evidence item.
pub fn citation_key(item: &EvidenceItem) -> String {
    format!("{}:{}", kind_label(item.kind), item.id)
}

fn kind_label(kind: EvidenceKind) -> &'static str {
    match kind {
        EvidenceKind::Unit => "unit",
        EvidenceKind::Card => "card",
        EvidenceKind::Claim => "claim",
        EvidenceKind::Source => "source",
    }
}

fn item_is_verifiable(item: &EvidenceItem) -> bool {
    match item.kind {
        EvidenceKind::Unit => item
            .quote
            .as_deref()
            .is_some_and(|quote| !quote.trim().is_empty()),
        EvidenceKind::Card => card_cites_unit(item),
        EvidenceKind::Claim => true,
        // Library sources are verifiable by identity (sha) alone.
        EvidenceKind::Source => !item.id.trim().is_empty(),
    }
}

fn card_cites_unit(item: &EvidenceItem) -> bool {
    item.body.lines().any(|line| {
        line.strip_prefix("Cites:")
            .is_some_and(|cites| !cites.trim().is_empty())
    })
}

fn unverifiable_warning(citation: &str, kind: EvidenceKind) -> String {
    match kind {
        EvidenceKind::Unit => format!("unit_without_quote:{citation}"),
        EvidenceKind::Card => format!("card_without_cited_units:{citation}"),
        EvidenceKind::Claim => format!("claim_unverifiable:{citation}"),
        EvidenceKind::Source => format!("source_unverifiable:{citation}"),
    }
}

#[cfg(test)]
mod tests {
    use crate::ask::{EvidenceItem, EvidenceKind};
    use crate::verify::verify_answer;

    fn evidence() -> Vec<EvidenceItem> {
        vec![
            EvidenceItem {
                id: "unit:40-Resources/Reader/memory:u-1".into(),
                kind: EvidenceKind::Unit,
                title: "Agent Memory line 12".into(),
                body: "Text: Agent memory persists.".into(),
                quote: Some("Agent memory persists across sessions.".into()),
                path: Some("40-Resources/Reader/memory/reader.md".into()),
            },
            EvidenceItem {
                id: "card:40-Resources/Reader/memory:0".into(),
                kind: EvidenceKind::Card,
                title: "Agent Memory - Memory as state".into(),
                body: "Content: Memory is state.\nCites: u-1".into(),
                quote: None,
                path: Some("40-Resources/Reader/memory/reader.md".into()),
            },
            EvidenceItem {
                id: "claim-memory-1".into(),
                kind: EvidenceKind::Claim,
                title: "durable claim theme=memory".into(),
                body: "Claim: Agent memory is persistent state.".into(),
                quote: None,
                path: None,
            },
        ]
    }

    #[test]
    fn verifies_cited_unit_when_quote_backed_evidence_was_supplied() {
        let report = verify_answer(
            "Memory persists across sessions [unit:unit:40-Resources/Reader/memory:u-1].",
            &evidence(),
        );

        assert_eq!(report.cited, 1);
        assert_eq!(report.verified, 1);
        assert!(report.missing.is_empty());
        assert!(report.warnings.is_empty());
    }

    #[test]
    fn citation_ids_may_contain_spaces_from_pack_directories() {
        let evidence = vec![EvidenceItem {
            id: "unit:40-Resources/Reader/2026-06-09_The Chunk Problem-359a9830:u-1".into(),
            kind: EvidenceKind::Unit,
            title: "The Chunk Problem line 1".into(),
            body: "Text: A chunk is structurally neutral.".into(),
            quote: Some("A chunk is a structurally neutral container.".into()),
            path: Some(
                "40-Resources/Reader/2026-06-09_The Chunk Problem-359a9830/reader.md".into(),
            ),
        }];

        let report = verify_answer(
            "Chunks are neutral [unit:unit:40-Resources/Reader/2026-06-09_The Chunk Problem-359a9830:u-1].",
            &evidence,
        );

        assert_eq!(report.cited, 1);
        assert_eq!(report.verified, 1);
        assert!(report.missing.is_empty());
    }

    #[test]
    fn reports_unknown_citation_ids_as_missing() {
        let report = verify_answer(
            "This cites evidence that was not supplied [unit:missing].",
            &evidence(),
        );

        assert_eq!(report.cited, 1);
        assert_eq!(report.verified, 0);
        assert_eq!(report.missing, vec!["unit:missing"]);
    }

    #[test]
    fn verifies_present_card_and_claim_citations() {
        let report = verify_answer(
            "Cards summarize breadth [card:card:40-Resources/Reader/memory:0], while claims carry synthesis [claim:claim-memory-1].",
            &evidence(),
        );

        assert_eq!(report.cited, 2);
        assert_eq!(report.verified, 2);
        assert!(report.missing.is_empty());
    }

    #[test]
    fn citations_in_order_keeps_first_appearance_order_and_dedupes() {
        let answer = "B first [claim:b]. Then A [unit:a], b again [claim:b], \
                      not-a-citation [see note], unterminated [unit:x";
        assert_eq!(
            crate::verify::citations_in_order(answer),
            vec!["claim:b".to_string(), "unit:a".to_string()],
        );
    }

    #[test]
    fn bare_ck_keys_normalize_to_claim_citations() {
        // Models reliably shorten `[claim:ck-…]` to `[ck-…]`; the reserved
        // ck- prefix is unambiguous, so both forms verify identically.
        let evidence = vec![EvidenceItem {
            id: "ck-793a2f557d2be6f9".into(),
            kind: EvidenceKind::Claim,
            title: "durable claim".into(),
            body: "Claim: governed forgetting is necessary.".into(),
            quote: None,
            path: None,
        }];
        let report = verify_answer(
            "Forgetting is governed [ck-793a2f557d2be6f9], twice [claim:ck-793a2f557d2be6f9].",
            &evidence,
        );
        assert_eq!(report.cited, 1, "both forms collapse to one citation");
        assert_eq!(report.verified, 1);
        assert!(report.missing.is_empty());
        assert_eq!(
            crate::verify::citations_in_order(
                "[ck-793a2f557d2be6f9] then [claim:ck-793a2f557d2be6f9]"
            ),
            vec!["claim:ck-793a2f557d2be6f9".to_string()],
        );
    }

    #[test]
    fn no_citations_is_not_an_error_but_is_warned() {
        let report = verify_answer("The context is insufficient to answer.", &evidence());

        assert_eq!(report.cited, 0);
        assert_eq!(report.verified, 0);
        assert!(report.missing.is_empty());
        assert_eq!(report.warnings, vec!["no_citations"]);
    }
}
