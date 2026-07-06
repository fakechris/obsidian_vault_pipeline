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
            out.insert(candidate.to_string());
        }
        rest = &after_start[end + 1..];
    }
    out.into_iter().collect()
}

fn is_citation_candidate(candidate: &str) -> bool {
    !candidate.is_empty()
        && matches!(
            candidate.split_once(':').map(|(prefix, _)| prefix),
            Some("unit" | "card" | "claim")
        )
}

fn citation_key(item: &EvidenceItem) -> String {
    format!("{}:{}", kind_label(item.kind), item.id)
}

fn kind_label(kind: EvidenceKind) -> &'static str {
    match kind {
        EvidenceKind::Unit => "unit",
        EvidenceKind::Card => "card",
        EvidenceKind::Claim => "claim",
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
    fn no_citations_is_not_an_error_but_is_warned() {
        let report = verify_answer("The context is insufficient to answer.", &evidence());

        assert_eq!(report.cited, 0);
        assert_eq!(report.verified, 0);
        assert!(report.missing.is_empty());
        assert_eq!(report.warnings, vec!["no_citations"]);
    }
}
