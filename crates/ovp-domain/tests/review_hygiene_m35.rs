//! M35 review-hygiene regression set — the first 20 human-review-batch claims
//! (real corpus data, 2026-07-06 audit), frozen as labeled fixtures.
//!
//! What this pins, deterministically and with ZERO model calls:
//! - `review_lane` routes by decidable structure (distinct sources + strength
//!   verdict): on this batch the human queue drops 20 → 6, and everything the
//!   judge flagged (overreach / over_synthesized) STAYS in the human queue.
//! - `collapse_review_duplicates` catches shared-evidence duplicates and — by
//!   design — does NOT touch the semantic duplicate pair
//!   (`agents-b003-8` / `agents-b010-7`): same idea, ZERO shared cited units,
//!   token jaccard 0.27. That pair is lineage/judge territory (a strengthen
//!   candidate), and collapsing it heuristically would violate the M35
//!   decidability rule. The boundary is asserted, not glossed.

use std::collections::BTreeSet;
use std::path::PathBuf;

use ovp_domain::crystal::{
    collapse_review_duplicates, review_lane, Citation, ClaimStrengthVerdict, FinalClass,
    ReviewEntry, ReviewLane, StrengthClass,
};

fn fixture(name: &str) -> serde_json::Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/review-hygiene-m35")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!("reading {}: {e}", path.display());
    }))
    .expect("fixture parses")
}

struct Case {
    id: String,
    claim: String,
    theme: String,
    citations: Vec<Citation>,
    strength: StrengthClass,
    evidence_sufficient: bool,
}

fn load_cases() -> Vec<Case> {
    fixture("claims.json")["claims"]
        .as_array()
        .expect("claims array")
        .iter()
        .map(|c| Case {
            id: c["id"].as_str().unwrap().to_string(),
            claim: c["claim"].as_str().unwrap().to_string(),
            theme: c["theme"].as_str().unwrap().to_string(),
            citations: c["citations"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| Citation {
                    case_id: x["case_id"].as_str().unwrap().to_string(),
                    unit_id: x["unit_id"].as_str().unwrap().to_string(),
                    quote: x["quote"].as_str().unwrap().to_string(),
                    claimed_line: None,
                })
                .collect(),
            strength: serde_json::from_value(c["strength"].clone()).unwrap(),
            evidence_sufficient: c["evidence_sufficient"].as_bool().unwrap(),
        })
        .collect()
}

fn verdict(case: &Case) -> ClaimStrengthVerdict {
    ClaimStrengthVerdict {
        claim_id: case.id.clone(),
        strength: case.strength,
        evidence_sufficient: case.evidence_sufficient,
        rationale: String::new(),
    }
}

fn distinct_sources(case: &Case) -> usize {
    case.citations.iter().map(|c| c.case_id.as_str()).collect::<BTreeSet<_>>().len()
}

fn entry(case: &Case) -> ReviewEntry {
    ReviewEntry {
        claim_id: case.id.clone(),
        claim: case.claim.clone(),
        theme: case.theme.clone(),
        final_class: FinalClass::Caveated,
        strength: case.strength,
        evidence_sufficient: case.evidence_sufficient,
        rationale: String::new(),
        citations: case.citations.clone(),
        lane: review_lane(distinct_sources(case), Some(&verdict(case))),
    }
}

#[test]
fn lane_routing_matches_the_audited_labels_and_shrinks_the_human_queue() {
    let cases = load_cases();
    assert_eq!(cases.len(), 20, "the frozen batch");
    let labels = fixture("labels.json");
    let mut human_queue = 0usize;
    for l in labels["labels"].as_array().unwrap() {
        let id = l["id"].as_str().unwrap();
        let case = cases.iter().find(|c| c.id == id).expect("label id in claims");
        let lane = review_lane(distinct_sources(case), Some(&verdict(case)));
        let expected = match l["expected_lane"].as_str().unwrap() {
            "source_insight" => ReviewLane::SourceInsight,
            _ => ReviewLane::Review,
        };
        assert_eq!(lane, expected, "lane for {id}");
        if lane == ReviewLane::Review {
            human_queue += 1;
        }
        // The core hygiene invariant: anything the judge flagged can NEVER be
        // parked out of the human queue.
        let judge_flagged = l["tags"]
            .as_array()
            .unwrap()
            .iter()
            .any(|t| t.as_str() == Some("overgeneralized_caught_by_judge"));
        if judge_flagged {
            assert_eq!(lane, ReviewLane::Review, "{id} was judge-flagged");
        }
    }
    assert_eq!(human_queue, 6, "human queue shrinks 20 -> 6 on this batch");
}

#[test]
fn collapse_leaves_the_real_batch_alone_including_the_semantic_duplicate_pair() {
    let cases = load_cases();
    let entries: Vec<ReviewEntry> = cases.iter().map(entry).collect();
    let (kept, collapsed) = collapse_review_duplicates(entries);
    assert!(
        collapsed.is_empty(),
        "no decidable duplicates in the real batch (the b003-8/b010-7 pair \
         shares zero cited units — semantic, out of Phase-0 scope): {collapsed:?}"
    );
    assert_eq!(kept.len(), 20);
}

#[test]
fn collapse_catches_a_cross_run_shared_evidence_duplicate() {
    let cases = load_cases();
    let mut entries: Vec<ReviewEntry> = cases.iter().map(entry).collect();
    // Simulate a later run re-synthesizing nearly the same claim from the same
    // evidence (different id, same citations, lightly reworded text).
    let src = cases.iter().find(|c| c.id == "agents-b004-2").unwrap();
    let mut dup = entry(src);
    dup.claim_id = "agents-zz99-1".into();
    dup.claim = format!("{} (restated)", src.claim);
    entries.push(dup);

    let (kept, collapsed) = collapse_review_duplicates(entries);
    assert_eq!(collapsed.len(), 1, "exactly the injected duplicate collapses");
    assert_eq!(collapsed[0].kept, "agents-b004-2");
    assert_eq!(collapsed[0].dropped, "agents-zz99-1");
    assert_eq!(kept.len(), 20);
}
