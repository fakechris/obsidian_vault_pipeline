//! Claim propagation analysis: origin detection, adoption timeline, credit rate.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::{AdoptionEntry, DurableRecord};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PropagationSummary {
    pub origin_case_id: Option<String>,
    pub adoption_count: usize,
    pub credit_rate: Option<f64>,
    pub timeline: Vec<AdoptionEntry>,
}

/// Stamp `origin_source`, `adoption_timeline`, and `credit_rate` on each record
/// by grouping on normalized claim text among the supplied slice.
pub fn overlay_propagation(records: &mut [DurableRecord]) {
    let mut groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (i, rec) in records.iter().enumerate() {
        groups.entry(rec.claim.trim().to_string()).or_default().push(i);
    }
    for indices in groups.into_values() {
        if indices.len() < 2 {
            continue;
        }
        let slice: Vec<DurableRecord> = indices.iter().map(|&i| records[i].clone()).collect();
        let summary = compute_propagation(&slice);
        if summary.origin_case_id.is_none() {
            continue;
        }
        for i in indices {
            records[i].origin_source = summary.origin_case_id.clone();
            records[i].adoption_timeline = summary.timeline.clone();
            records[i].credit_rate = summary.credit_rate;
        }
    }
}

/// Compute propagation for records sharing the same claim text (2+ required).
pub fn compute_propagation(records: &[DurableRecord]) -> PropagationSummary {
    let empty = PropagationSummary {
        origin_case_id: None,
        adoption_count: 0,
        credit_rate: None,
        timeline: Vec::new(),
    };
    if records.len() < 2 {
        return empty;
    }

    let mut first_seen: BTreeMap<String, (usize, Option<String>)> = BTreeMap::new();
    for (rec_idx, rec) in records.iter().enumerate() {
        let rec_date = run_date_from_run_id(&rec.run_id);
        let rec_ord = record_order_key(rec);
        for case in &rec.source_cases {
            first_seen
                .entry(case.clone())
                .and_modify(|(best_idx, best_date)| {
                    let best_ord = record_order_key(&records[*best_idx]);
                    if (rec_ord.clone(), rec_date.as_deref()) < (best_ord, best_date.as_deref()) {
                        *best_idx = rec_idx;
                        *best_date = rec_date.clone();
                    }
                })
                .or_insert((rec_idx, rec_date.clone()));
        }
    }

    if first_seen.is_empty() {
        return empty;
    }

    let mut ordered: Vec<(String, usize, Option<String>)> = first_seen
        .into_iter()
        .map(|(case, (idx, date))| (case, idx, date))
        .collect();
    ordered.sort_by(|a, b| {
        let ord_a = record_order_key(&records[a.1]);
        let ord_b = record_order_key(&records[b.1]);
        (ord_a, a.2.as_deref(), &a.0).cmp(&(ord_b, b.2.as_deref(), &b.0))
    });

    let (origin_case, _, origin_date) = &ordered[0];
    let origin_case = origin_case.clone();
    let origin_date = origin_date.clone();

    let mut timeline: Vec<AdoptionEntry> = ordered
        .iter()
        .skip(1)
        .map(|(case, _, date)| AdoptionEntry {
            source_case_id: case.clone(),
            first_seen_date: date.clone(),
            lag_days: lag_days(origin_date.as_deref(), date.as_deref()),
        })
        .collect();
    timeline.sort_by(|a, b| {
        (
            a.lag_days.unwrap_or(i64::MAX),
            a.first_seen_date.as_deref(),
            a.source_case_id.as_str(),
        )
            .cmp(&(
                b.lag_days.unwrap_or(i64::MAX),
                b.first_seen_date.as_deref(),
                b.source_case_id.as_str(),
            ))
    });

    let origin_key = record_order_key(&records[ordered[0].1]);
    let mut later_total = 0usize;
    let mut later_citing = 0usize;
    for rec in records {
        if record_order_key(rec) <= origin_key {
            continue;
        }
        later_total += 1;
        if rec.source_cases.iter().any(|c| c == &origin_case)
            || rec.citations.iter().any(|c| c.case_id == origin_case)
        {
            later_citing += 1;
        }
    }

    let credit_rate = if later_total == 0 {
        None
    } else {
        Some(later_citing as f64 / later_total as f64)
    };

    PropagationSummary {
        origin_case_id: Some(origin_case),
        adoption_count: timeline.len(),
        credit_rate,
        timeline,
    }
}

fn record_order_key(rec: &DurableRecord) -> (Option<String>, String) {
    (run_date_from_run_id(&rec.run_id), rec.run_id.clone())
}

fn run_date_from_run_id(run_id: &str) -> Option<String> {
    if let Some(i) = run_id.find("20") {
        let slice = &run_id[i..];
        if slice.len() >= 10 {
            let cand = &slice[..10];
            if is_iso_day(cand) {
                return Some(cand.to_string());
            }
        }
        if slice.len() >= 8 {
            let cand = &slice[..8];
            if cand.bytes().all(|b| b.is_ascii_digit()) {
                return Some(format!("{}-{}-{}", &cand[..4], &cand[4..6], &cand[6..8]));
            }
        }
    }
    None
}

fn is_iso_day(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b[..4].iter().all(|c| c.is_ascii_digit())
        && b[5..7].iter().all(|c| c.is_ascii_digit())
        && b[8..10].iter().all(|c| c.is_ascii_digit())
}

fn lag_days(origin: Option<&str>, adopter: Option<&str>) -> Option<i64> {
    match (origin, adopter) {
        (Some(o), Some(a)) => {
            let o = iso_day_number(o)?;
            let a = iso_day_number(a)?;
            Some(a - o)
        }
        _ => None,
    }
}

fn iso_day_number(s: &str) -> Option<i64> {
    if !is_iso_day(s) {
        return None;
    }
    let y: i64 = s[..4].parse().ok()?;
    let m: i64 = s[5..7].parse().ok()?;
    let d: i64 = s[8..10].parse().ok()?;
    Some(y * 372 + m * 31 + d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crystal::{
        CrystalStatus, DurableCitation, FinalClass, ProvenanceClass, StrengthClass,
    };

    fn rec(
        key: &str,
        claim: &str,
        run_id: &str,
        cases: &[&str],
        cite_origin: bool,
        origin: &str,
    ) -> DurableRecord {
        let mut source_cases: Vec<String> = cases.iter().map(|c| (*c).to_string()).collect();
        if cite_origin && !source_cases.contains(&origin.to_string()) {
            source_cases.push(origin.to_string());
        }
        source_cases.sort();
        source_cases.dedup();
        let citations: Vec<DurableCitation> = source_cases
            .iter()
            .map(|case_id| DurableCitation {
                case_id: case_id.clone(),
                unit_id: "u-0".into(),
                quote: "q".into(),
                resolved_line: Some(1),
                citation_type: Default::default(),
            })
            .collect();
        DurableRecord {
            claim_key: key.into(),
            claim_id: format!("id-{key}"),
            claim: claim.into(),
            theme: "t".into(),
            theme_id: None,
            source_cases,
            citations,
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "ok".into(),
            final_class: FinalClass::Durable,
            run_id: run_id.into(),
            status: CrystalStatus::Active,
            origin_source: None,
            adoption_timeline: Vec::new(),
            credit_rate: None,
        }
    }

    #[test]
    fn three_sources_origin_is_earliest_and_timeline_sorted() {
        let claim = "Agents need durable memory";
        let records = vec![
            rec("ck-b", claim, "daily-2026-01-10", &["case-b"], false, "case-a"),
            rec("ck-a", claim, "daily-2026-01-05", &["case-a"], false, "case-a"),
            rec("ck-c", claim, "daily-2026-01-20", &["case-c"], true, "case-a"),
            rec("ck-d", claim, "daily-2026-01-25", &["case-d"], true, "case-a"),
        ];
        let summary = compute_propagation(&records);
        assert_eq!(summary.origin_case_id.as_deref(), Some("case-a"));
        assert_eq!(summary.adoption_count, 3);
        assert_eq!(summary.timeline.len(), 3);
        assert_eq!(
            summary
                .timeline
                .iter()
                .map(|e| e.lag_days)
                .collect::<Vec<_>>(),
            vec![Some(5), Some(15), Some(20)]
        );
        assert_eq!(summary.credit_rate, Some(2.0 / 3.0));
    }

    #[test]
    fn single_or_empty_records_yield_none() {
        let one = vec![rec(
            "ck-a",
            "solo",
            "daily-2026-01-05",
            &["case-a"],
            false,
            "case-a",
        )];
        let summary = compute_propagation(&one);
        assert!(summary.origin_case_id.is_none());
        assert!(summary.timeline.is_empty());
        assert!(summary.credit_rate.is_none());

        let summary = compute_propagation(&[]);
        assert!(summary.origin_case_id.is_none());
    }

    #[test]
    fn durable_record_deserializes_without_propagation_fields() {
        let json = r#"{
            "claim_key": "ck-legacy",
            "claim_id": "c-legacy",
            "claim": "Legacy claim",
            "theme": "t",
            "source_cases": ["case-a"],
            "citations": [],
            "provenance_score": 0.5,
            "provenance_class": "durable",
            "strength": "supported",
            "strength_rationale": "r",
            "final_class": "durable",
            "run_id": "daily-2026-01-01",
            "status": "active"
        }"#;
        let rec: DurableRecord = serde_json::from_str(json).unwrap();
        assert!(rec.origin_source.is_none());
        assert!(rec.adoption_timeline.is_empty());
        assert!(rec.credit_rate.is_none());
    }

    #[test]
    fn overlay_stamps_all_siblings() {
        let claim = "Shared claim text";
        let mut records = vec![
            rec("ck-1", claim, "daily-2026-02-01", &["src-1"], false, "src-1"),
            rec("ck-2", claim, "daily-2026-02-10", &["src-2"], false, "src-1"),
        ];
        overlay_propagation(&mut records);
        for r in &records {
            assert_eq!(r.origin_source.as_deref(), Some("src-1"));
            assert_eq!(r.adoption_timeline.len(), 1);
            assert_eq!(r.adoption_timeline[0].source_case_id, "src-2");
        }
    }
}