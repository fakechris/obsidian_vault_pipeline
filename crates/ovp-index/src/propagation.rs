//! Attach the claim-propagation projection (INV-1882) to active claim rows.
//!
//! Joins each cited case to its source row (via the pack's `source_sha256`),
//! takes the source's publication date (`content_date`), and reads a later
//! source's text — once per file per build — to see whether it links or
//! credits the origin. The rule itself lives in
//! `ovp_domain::crystal::propagation`.

use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use ovp_domain::crystal::propagation::{compute_propagation, SourceFacts};

use crate::model::{AdoptionTimelineEntry, ClaimRow, ClaimStatus, PackRow, SourceRow};

/// Returns how many claims received a propagation origin.
pub fn attach_propagation(
    vault_root: &Path,
    claims: &mut [ClaimRow],
    sources: &[SourceRow],
    packs: &[PackRow],
) -> usize {
    let by_sha: HashMap<&str, &SourceRow> =
        sources.iter().map(|s| (s.sha256.as_str(), s)).collect();
    let case_source: BTreeMap<&str, &SourceRow> = packs
        .iter()
        .filter_map(|p| {
            let case = p.pack_dir.rsplit('/').next().unwrap_or(p.pack_dir.as_str());
            Some((case, *by_sha.get(p.source_sha256.as_deref()?)?))
        })
        .collect();
    let mut texts: HashMap<String, Option<String>> = HashMap::new();

    let mut stamped = 0;
    for claim in claims
        .iter_mut()
        .filter(|c| matches!(c.status, ClaimStatus::Durable | ClaimStatus::Caveated))
    {
        let facts: Vec<SourceFacts> = claim
            .sources
            .iter()
            .filter_map(|case| {
                let s = case_source.get(case.as_str())?;
                Some(SourceFacts {
                    case_id: case.clone(),
                    identity: s.sha256.clone(),
                    published: s.content_date.clone(),
                    url: s.url.clone(),
                    author: s.author.clone(),
                })
            })
            .collect();
        let Some(p) = compute_propagation(&facts, |case| {
            let rel = case_source.get(case)?.rel_path.as_deref()?;
            texts
                .entry(rel.to_string())
                .or_insert_with(|| std::fs::read_to_string(vault_root.join(rel)).ok())
                .clone()
        }) else {
            continue;
        };
        claim.origin_source = Some(p.origin_case_id);
        claim.adoption_count = Some(p.adoptions.len());
        claim.credit_rate = p.credit_rate;
        claim.adoption_timeline = p
            .adoptions
            .into_iter()
            .map(|a| AdoptionTimelineEntry {
                source_case_id: a.case_id,
                first_seen_date: Some(a.published),
                lag_days: Some(a.lag_days),
                citation_type: a.citation_type,
            })
            .collect();
        stamped += 1;
    }
    stamped
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SourceStatus;
    use ovp_domain::citation_support::CitationType;

    fn source(sha: &str, date: &str, url: &str, rel: &str) -> SourceRow {
        let mut s = SourceRow::blank(sha, SourceStatus::Processed);
        s.content_date = Some(date.into());
        s.url = Some(url.into());
        s.rel_path = Some(rel.into());
        s
    }

    fn pack(case: &str, sha: &str) -> PackRow {
        PackRow {
            pack_dir: format!("40-Resources/Reader/{case}"),
            title: case.into(),
            date: None,
            units: 1,
            cards: 1,
            json_repaired: false,
            card_titles: vec![],
            source_sha256: Some(sha.into()),
        }
    }

    fn claim(status: ClaimStatus, cases: &[&str]) -> ClaimRow {
        ClaimRow {
            claim_id: "c".into(),
            claim_key: Some("ck".into()),
            claim: "claim".into(),
            theme: None,
            theme_id: None,
            status,
            sources: cases.iter().map(|c| (*c).to_string()).collect(),
            strength: None,
            run_id: None,
            run_date: None,
            lane: None,
            patched_by: None,
            xy: None,
            origin_source: None,
            adoption_timeline: Vec::new(),
            adoption_count: None,
            credit_rate: None,
        }
    }

    #[test]
    fn stamps_active_claims_from_publication_dates_and_source_text() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("b.md"), "as https://a.dev/post shows").unwrap();
        let sources = vec![
            source("sha-a", "2026-01-01", "https://a.dev/post", "a.md"),
            source("sha-b", "2026-01-04", "https://b.dev/x", "b.md"),
        ];
        let packs = vec![pack("case-b", "sha-b"), pack("case-a", "sha-a")];
        let mut claims = vec![
            claim(ClaimStatus::Durable, &["case-b", "case-a"]),
            claim(ClaimStatus::Superseded, &["case-b", "case-a"]),
            claim(ClaimStatus::Durable, &["case-a"]),
        ];
        let n = attach_propagation(dir.path(), &mut claims, &sources, &packs);
        assert_eq!(n, 1);
        let c = &claims[0];
        assert_eq!(c.origin_source.as_deref(), Some("case-a"));
        assert_eq!(c.adoption_count, Some(1));
        assert_eq!(c.credit_rate, Some(1.0));
        assert_eq!(c.adoption_timeline[0].lag_days, Some(3));
        assert_eq!(
            c.adoption_timeline[0].citation_type,
            CitationType::ExactLink
        );
        assert!(
            claims[1].origin_source.is_none(),
            "superseded rows are not stamped"
        );
        assert!(
            claims[2].origin_source.is_none(),
            "single-source claims have no propagation"
        );
    }
}
