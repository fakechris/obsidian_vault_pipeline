//! Staleness recheck over ALREADY-DURABLE claims.
//!
//! The pre-write gate ([`super::lint_candidate`]) proves a claim's citations
//! ground at the moment it is written. Nothing re-asks the question afterwards,
//! so a durable claim whose supporting unit later moved — a reader pack
//! regenerated, a prompt version bumped, a source re-processed — keeps
//! asserting itself with a citation that no longer resolves. Nothing errors;
//! the claim simply stops being backed by what it says backs it.
//!
//! This module re-runs the SAME linter against the CURRENT grounding index and
//! reports. It is deliberately read-only and deliberately not a repair: a
//! stale claim is not a wrong claim, it is one whose evidence can no longer be
//! assumed without looking. Rewriting it is a consolidation write and belongs
//! behind the same gate and the same human as any other durable write.
//!
//! Second axis, free from the data we already have: how OLD the evidence is.
//! A citation can still ground verbatim while the world it describes has moved
//! on, and `case_id` already carries the capture date, so age needs no refetch
//! and no new field.

use std::collections::BTreeMap;

use std::path::Path;

use super::{
    Citation, CitationDefect, CrystalCandidate, CrystalClaim, CrystalStatus, FinalClass,
    GroundingIndex, StoreEvent, fold_ledger, lint_candidate,
};
use crate::units::Unit;

/// Age buckets, in days. Boundaries are reporting conveniences, not thresholds
/// anything branches on — nothing here decides a claim is wrong because it is
/// old.
const AGE_BUCKETS: [(&str, i64); 4] = [
    ("0-90d", 90),
    ("91-180d", 180),
    ("181-365d", 365),
    ("365d+", i64::MAX),
];

/// Bucket names in AGE ORDER. `age_buckets` is a map keyed by these names, and
/// sorting them as strings puts "181-365d" before "91-180d" — a histogram that
/// reads backwards. Render through this.
pub const AGE_BUCKET_NAMES: [&str; 4] = ["0-90d", "91-180d", "181-365d", "365d+"];

/// Why one claim came back stale. Mirrors [`CitationDefect`] but counts at the
/// claim level, since one broken citation is enough to stop trusting the claim.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct StaleCitation {
    pub case_id: String,
    pub unit_id: String,
    pub defect: CitationDefect,
}

/// One durable claim's recheck verdict.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ClaimRecheck {
    pub claim_id: String,
    pub n_citations: usize,
    pub n_grounded: usize,
    /// Empty when every citation still grounds.
    pub stale_citations: Vec<StaleCitation>,
    /// Days since the OLDEST cited capture; `None` when no `case_id` carried a
    /// parseable date prefix.
    pub oldest_evidence_days: Option<i64>,
    pub newest_evidence_days: Option<i64>,
}

impl ClaimRecheck {
    pub fn is_stale(&self) -> bool {
        !self.stale_citations.is_empty()
    }
}

/// The whole recheck.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct RecheckReport {
    pub n_claims: usize,
    pub n_intact: usize,
    pub n_stale: usize,
    /// Claim-level counts per defect kind, so "the packs were rebuilt" (mass
    /// `UnitNotFound`) reads differently from "a quote was edited"
    /// (`QuoteNotInUnit`).
    pub by_defect: BTreeMap<String, usize>,
    /// Only the stale ones — the intact majority is a count, not a list.
    pub stale: Vec<ClaimRecheck>,
    /// Distribution of each claim's OLDEST evidence. Reported, never gated.
    pub age_buckets: BTreeMap<String, usize>,
    /// Claims whose citations carried no parseable date at all.
    pub n_undated: usize,
}

/// Days between the capture date embedded in `case_id` and `today`.
///
/// `case_id` is a reader-pack directory name and the live vault has TWO
/// layouts — `<date>_<title>-<hash8>` and `<hash8>-<date>_<title>…`. Anchoring
/// on position 0 reads the second form as undated, which on the real ledger
/// meant 72% of claims silently lost their age. So scan for the first
/// well-formed `YYYY-MM-DD` anywhere in the id; both layouts put the capture
/// date ahead of the title.
///
/// Returns `None` when no plausible date is present — a malformed id must not
/// count as "captured today", which would report the oldest evidence in the
/// vault as the newest.
pub fn evidence_age_days(case_id: &str, today: (i32, u32, u32)) -> Option<i64> {
    let b = case_id.as_bytes();
    for start in 0..b.len().saturating_sub(9) {
        // A date must not be glued to a longer digit run (a 12-digit id would
        // otherwise yield a nonsense year from its first four digits).
        if start > 0 && b[start - 1].is_ascii_digit() {
            continue;
        }
        let Some(head) = case_id.get(start..start + 10) else {
            continue;
        };
        if let Some(age) = parse_civil(head).map(|(y, m, d)| {
            days_from_civil(today.0, today.1, today.2) - days_from_civil(y, m, d)
        }) {
            return Some(age);
        }
    }
    None
}

/// `YYYY-MM-DD` with real calendar bounds. Deliberately strict: this is the
/// only thing standing between a typo'd id and a fabricated age, and
/// `days_from_civil` will happily convert `2026-02-31` into a real number that
/// nothing downstream can tell apart from a true date.
fn parse_civil(s: &str) -> Option<(i32, u32, u32)> {
    let bytes = s.as_bytes();
    if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
        return None;
    }
    let y: i32 = s.get(0..4)?.parse().ok()?;
    let m: u32 = s.get(5..7)?.parse().ok()?;
    let d: u32 = s.get(8..10)?.parse().ok()?;
    if !(1970..=9999).contains(&y) || !(1..=12).contains(&m) || d < 1 || d > days_in_month(y, m) {
        return None;
    }
    Some((y, m, d))
}

fn days_in_month(y: i32, m: u32) -> u32 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        2 => 28,
        _ => 0,
    }
}

/// Howard Hinnant's `days_from_civil`. Chrono is not a dependency of this
/// crate's pure layer and the caller already knows today's date.
fn days_from_civil(y: i32, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = (y - era * 400) as i64;
    let mp = ((m + 9) % 12) as i64;
    let doy = (153 * mp + 2) / 5 + d as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era as i64 * 146_097 + doe - 719_468
}

fn bucket_for(days: i64) -> &'static str {
    for (name, hi) in AGE_BUCKETS {
        if days <= hi {
            return name;
        }
    }
    AGE_BUCKETS[AGE_BUCKETS.len() - 1].0
}

fn defect_name(d: &CitationDefect) -> &'static str {
    match d {
        CitationDefect::CaseNotFound => "case_not_found",
        CitationDefect::UnitNotFound => "unit_not_found",
        CitationDefect::UnitNotAccepted => "unit_not_accepted",
        CitationDefect::QuoteNotInUnit => "quote_not_in_unit",
    }
}

/// Re-lint `durable` against the CURRENT index and fold in evidence age.
///
/// `today` is passed in rather than read from the clock so the whole thing
/// stays pure and the report is reproducible from the same inputs.
pub fn recheck(
    durable: &CrystalCandidate,
    index: &GroundingIndex,
    today: (i32, u32, u32),
) -> RecheckReport {
    let lint = lint_candidate(durable, index);
    let mut by_defect: BTreeMap<String, usize> = BTreeMap::new();
    let mut age_buckets: BTreeMap<String, usize> = BTreeMap::new();
    for (name, _) in AGE_BUCKETS {
        age_buckets.insert(name.to_string(), 0);
    }
    let mut stale = Vec::new();
    let mut n_undated = 0usize;

    for (claim, lint_row) in durable.items.iter().zip(lint.claims.iter()) {
        let mut ages: Vec<i64> = Vec::new();
        for c in &claim.citations {
            if let Some(days) = evidence_age_days(&c.case_id, today) {
                ages.push(days);
            }
        }
        let oldest = ages.iter().copied().max();
        let newest = ages.iter().copied().min();
        match oldest {
            Some(d) => *age_buckets.entry(bucket_for(d).to_string()).or_insert(0) += 1,
            None => n_undated += 1,
        }

        let stale_citations: Vec<StaleCitation> = lint_row
            .citations
            .iter()
            .filter_map(|v| {
                v.defect.clone().map(|defect| StaleCitation {
                    case_id: v.case_id.clone(),
                    unit_id: v.unit_id.clone(),
                    defect,
                })
            })
            .collect();

        // Count each defect kind ONCE per claim: a pack rebuild breaks every
        // citation in a claim at once, and a citation-level tally would report
        // that as many separate problems instead of one.
        let mut kinds: Vec<&str> = stale_citations.iter().map(|s| defect_name(&s.defect)).collect();
        kinds.sort_unstable();
        kinds.dedup();
        for k in kinds {
            *by_defect.entry(k.to_string()).or_insert(0) += 1;
        }

        if !stale_citations.is_empty() {
            stale.push(ClaimRecheck {
                claim_id: claim.id.clone(),
                n_citations: lint_row.n_citations,
                n_grounded: lint_row.n_grounded,
                stale_citations,
                oldest_evidence_days: oldest,
                newest_evidence_days: newest,
            });
        }
    }

    let n_claims = durable.items.len();
    let n_stale = stale.len();
    RecheckReport {
        n_claims,
        n_intact: n_claims - n_stale,
        n_stale,
        by_defect,
        stale,
        age_buckets,
        n_undated,
    }
}


// ---- Vault loaders (shared by `crystal-recheck`, `crystal-lint`, `doctor`) ----

/// Build the grounding index from every `<packs_dir>/<case>/units.accepted.json`.
/// Errors (as a message) on an unreadable/unparseable pack or when there are
/// no packs at all — an empty index would make every citation "not found".
pub fn build_grounding_index(packs_dir: &Path) -> Result<GroundingIndex, String> {
    let mut index = GroundingIndex::new();
    let entries = std::fs::read_dir(packs_dir)
        .map_err(|e| format!("reading packs dir {}: {e}", packs_dir.display()))?;
    for entry in entries.flatten() {
        if !entry.path().is_dir() {
            continue;
        }
        let units_path = entry.path().join("units.accepted.json");
        if !units_path.exists() {
            continue;
        }
        let text = std::fs::read_to_string(&units_path)
            .map_err(|e| format!("reading {}: {e}", units_path.display()))?;
        let units: Vec<Unit> = serde_json::from_str(&text)
            .map_err(|e| format!("parsing {}: {e}", units_path.display()))?;
        index.insert(entry.file_name().to_string_lossy().to_string(), units);
    }
    if index.is_empty() {
        return Err(format!("no units.accepted.json under {}", packs_dir.display()));
    }
    Ok(index)
}

/// Rebuild a lintable candidate from what the vault CURRENTLY asserts.
///
/// Goes through the typed `StoreEvent` + [`fold_ledger`] rather than reading
/// `op` strings. The ledger has three ops, not one: `Supersede` also flips its
/// predecessor to `Superseded`, and `Retract` marks a record retracted.
/// Re-implementing "latest write wins" reproduces none of that, so it would
/// recheck retracted claims as if they were live and miss the replacements.
///
/// Typed deserialization is also the point for citations: a hand-rolled
/// `filter_map` drops a malformed citation silently, and a claim that loses
/// all of them then reports as intact — staleness is measured from citation
/// defects, and a claim with no citations has none.
pub fn durable_from_ledger(path: &Path) -> Result<CrystalCandidate, String> {
    let text =
        std::fs::read_to_string(path).map_err(|e| format!("reading {}: {e}", path.display()))?;
    let mut events: Vec<StoreEvent> = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let ev: StoreEvent = serde_json::from_str(line)
            .map_err(|e| format!("{}:{}: {e}", path.display(), i + 1))?;
        events.push(ev);
    }
    let items = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active && r.final_class == FinalClass::Durable)
        .map(|r| CrystalClaim {
            id: r.claim_key,
            claim: r.claim,
            theme: r.theme,
            citations: r
                .citations
                .into_iter()
                .map(|c| Citation {
                    case_id: c.case_id,
                    unit_id: c.unit_id,
                    quote: c.quote,
                    claimed_line: None,
                })
                .collect(),
            caveat: None,
        })
        .collect();
    Ok(CrystalCandidate { items })
}

/// Recheck a vault's durable claims against its current reader packs. Shared
/// by `crystal-recheck` and `doctor` so the health check and the command can
/// never drift into disagreeing about what is stale. `today` is the civil
/// date used for evidence age (callers supply local time).
pub fn recheck_vault(
    vault_root: &Path,
    packs_dir: Option<&Path>,
    ledger: Option<&Path>,
    today: (i32, u32, u32),
) -> Result<RecheckReport, String> {
    let packs_dir =
        packs_dir.map(Path::to_path_buf).unwrap_or_else(|| vault_root.join("40-Resources/Reader"));
    let ledger = ledger
        .map(Path::to_path_buf)
        .unwrap_or_else(|| vault_root.join(".ovp/crystal/ledger.jsonl"));
    let durable = durable_from_ledger(&ledger)?;
    let index = build_grounding_index(&packs_dir)?;
    Ok(recheck(&durable, &index, today))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crystal::{Citation, CrystalClaim};
    use crate::units::{Unit, UnitEvidence, UnitStatus};

    const TODAY: (i32, u32, u32) = (2026, 8, 25);

    fn unit(id: &str, quote: &str) -> Unit {
        Unit {
            id: id.into(),
            kind: crate::units::UnitKind::Assertion,
            subtype: None,
            text: quote.into(),
            evidence: UnitEvidence {
                ref_id: "p001".into(),
                quote: quote.into(),
                location: None,
            },
            attribution: crate::units::Attribution::Author,
            modality: crate::units::Modality::Asserted,
            arguments: Vec::new(),
            status: UnitStatus::Accepted,
            issues: Vec::new(),
        }
    }

    fn claim(id: &str, case: &str, unit_id: &str, quote: &str) -> CrystalClaim {
        CrystalClaim {
            id: id.into(),
            claim: format!("claim {id}"),
            theme: String::new(),
            citations: vec![Citation {
                case_id: case.into(),
                unit_id: unit_id.into(),
                quote: quote.into(),
                claimed_line: None,
            }],
            caveat: None,
        }
    }

    #[test]
    fn age_parses_the_case_id_date_prefix() {
        assert_eq!(evidence_age_days("2026-08-25_Title-abc123", TODAY), Some(0));
        assert_eq!(evidence_age_days("2026-08-15_Title-abc123", TODAY), Some(10));
        assert_eq!(evidence_age_days("2025-08-25_Title-abc123", TODAY), Some(365));
    }

    #[test]
    fn age_also_parses_the_hash_prefixed_layout() {
        // Verbatim from the live ledger. This layout is the MAJORITY there
        // (4185 of 5182 citations); anchoring on position 0 reported all of
        // them as undated while the summary still looked healthy.
        assert_eq!(
            evidence_age_days("1b16bbab-2026-06-11_Building_a_Good_Vertical_Agent-1b16bbab_", TODAY),
            Some(75)
        );
        assert_eq!(
            evidence_age_days("1bd54847-2026-04-25_What_is_an_Agent_Harness_", TODAY),
            Some(122)
        );
    }

    #[test]
    fn a_malformed_case_id_is_undated_not_fresh() {
        // Silently treating these as day 0 would report the oldest evidence in
        // the vault as the newest.
        for id in [
            "no-date-here",
            "2026-13-01_Bad",
            "2026-08-99_Bad",
            "",
            "20260825_x",
            "1969-12-31_TooOld",
        ] {
            assert_eq!(evidence_age_days(id, TODAY), None, "{id}");
        }
    }

    #[test]
    fn a_long_digit_run_does_not_masquerade_as_a_date() {
        // `2026-08-25` embedded in a longer number is not a capture date.
        assert_eq!(evidence_age_days("12026-08-250_x", TODAY), None);
    }

    #[test]
    fn an_impossible_calendar_date_is_undated() {
        // `days_from_civil` converts these into perfectly real-looking numbers,
        // so a day-range check alone would fabricate an age nothing downstream
        // could tell apart from a true one.
        for id in [
            "2026-02-31_Bad",
            "2025-02-29_NotALeapYear",
            "2026-04-31_Bad",
            "2026-06-31_Bad",
        ] {
            assert_eq!(evidence_age_days(id, TODAY), None, "{id}");
        }
        // Real leap day still parses.
        assert!(evidence_age_days("2024-02-29_Good", TODAY).is_some());
    }

    #[test]
    fn an_intact_claim_is_not_reported_stale() {
        let mut index = GroundingIndex::new();
        index.insert("2026-08-01_Case-aaa".into(), vec![unit("u-1", "the quote")]);
        let cand = CrystalCandidate {
            items: vec![claim("c1", "2026-08-01_Case-aaa", "u-1", "the quote")],
        };
        let r = recheck(&cand, &index, TODAY);
        assert_eq!((r.n_claims, r.n_stale, r.n_intact), (1, 0, 1));
        assert!(r.stale.is_empty());
        assert_eq!(r.age_buckets["0-90d"], 1);
    }

    #[test]
    fn a_moved_unit_makes_the_claim_stale_with_its_defect() {
        // The pack was regenerated and unit ids were renumbered — the classic
        // way a durable claim quietly stops being backed by its citation.
        let mut index = GroundingIndex::new();
        index.insert("2026-08-01_Case-aaa".into(), vec![unit("u-9", "the quote")]);
        let cand = CrystalCandidate {
            items: vec![claim("c1", "2026-08-01_Case-aaa", "u-1", "the quote")],
        };
        let r = recheck(&cand, &index, TODAY);
        assert_eq!(r.n_stale, 1);
        assert_eq!(r.by_defect["unit_not_found"], 1);
        assert_eq!(r.stale[0].stale_citations[0].defect, CitationDefect::UnitNotFound);
    }

    #[test]
    fn an_edited_quote_reads_as_quote_drift_not_a_missing_unit() {
        let mut index = GroundingIndex::new();
        index.insert("2026-08-01_Case-aaa".into(), vec![unit("u-1", "a different quote")]);
        let cand = CrystalCandidate {
            items: vec![claim("c1", "2026-08-01_Case-aaa", "u-1", "the quote")],
        };
        let r = recheck(&cand, &index, TODAY);
        assert_eq!(r.by_defect["quote_not_in_unit"], 1);
    }

    #[test]
    fn one_broken_pack_counts_once_per_claim_not_once_per_citation() {
        // A rebuilt pack breaks every citation in a claim at once. Tallying per
        // citation would report one event as many problems and make the defect
        // histogram useless for telling "packs rebuilt" from "quotes edited".
        let index = GroundingIndex::new(); // whole case gone
        let cand = CrystalCandidate {
            items: vec![CrystalClaim {
                id: "c1".into(),
                claim: "c".into(),
                theme: String::new(),
                citations: vec![
                    Citation {
                        case_id: "2026-08-01_Case-aaa".into(),
                        unit_id: "u-1".into(),
                        quote: "q".into(),
                        claimed_line: None,
                    },
                    Citation {
                        case_id: "2026-08-01_Case-aaa".into(),
                        unit_id: "u-2".into(),
                        quote: "q".into(),
                        claimed_line: None,
                    },
                ],
                caveat: None,
            }],
        };
        let r = recheck(&cand, &index, TODAY);
        assert_eq!(r.n_stale, 1);
        assert_eq!(r.by_defect["case_not_found"], 1, "one claim, not two citations");
        assert_eq!(r.stale[0].stale_citations.len(), 2, "both are still listed");
    }

    #[test]
    fn age_uses_the_oldest_citation_and_undated_claims_are_counted_separately() {
        let mut index = GroundingIndex::new();
        index.insert("2026-08-01_New-aaa".into(), vec![unit("u-1", "q")]);
        index.insert("2024-01-01_Old-bbb".into(), vec![unit("u-1", "q")]);
        index.insert("nodate_Case-ccc".into(), vec![unit("u-1", "q")]);
        let two_sources = CrystalClaim {
            id: "c1".into(),
            claim: "c".into(),
            theme: String::new(),
            citations: vec![
                Citation {
                    case_id: "2026-08-01_New-aaa".into(),
                    unit_id: "u-1".into(),
                    quote: "q".into(),
                    claimed_line: None,
                },
                Citation {
                    case_id: "2024-01-01_Old-bbb".into(),
                    unit_id: "u-1".into(),
                    quote: "q".into(),
                    claimed_line: None,
                },
            ],
            caveat: None,
        };
        let cand = CrystalCandidate {
            items: vec![two_sources, claim("c2", "nodate_Case-ccc", "u-1", "q")],
        };
        let r = recheck(&cand, &index, TODAY);
        assert_eq!(r.n_stale, 0, "both still ground");
        // The claim leans on 2024 evidence; reporting it as fresh because one
        // citation is recent would hide exactly what this axis exists to show.
        assert_eq!(r.age_buckets["365d+"], 1);
        assert_eq!(r.n_undated, 1);
    }
}

#[cfg(test)]
mod loader_tests {
    use super::*;
    use std::path::PathBuf;

    fn write_ledger(dir: &std::path::Path, lines: &[&str]) -> PathBuf {
        let p = dir.join("ledger.jsonl");
        std::fs::write(&p, lines.join("\n")).unwrap();
        p
    }

    /// A fresh per-test dir that is removed on drop — even when an assertion
    /// panics — and never under a shared /tmp path.
    fn tmp(_name: &str) -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    /// A full durable record — the typed `StoreEvent` shape the ledger really
    /// carries, not the trimmed JSON a hand-rolled parser would have accepted.
    fn rec(key: &str, claim: &str, class: &str, unit: &str) -> String {
        format!(
            r#"{{"claim_key":"{key}","claim_id":"{key}","claim":"{claim}","theme":"t",
                 "source_cases":["2026-01-01_X-a"],
                 "citations":[{{"case_id":"2026-01-01_X-a","unit_id":"{unit}","quote":"q","resolved_line":1}}],
                 "provenance_score":1.0,"provenance_class":"durable","strength":"supported",
                 "strength_rationale":"r","final_class":"{class}","run_id":"r1","status":"active"}}"#
        )
        .replace('\n', "")
    }

    #[test]
    fn only_active_durable_records_are_rechecked() {
        let d = tmp("scope");
        let p = write_ledger(
            d.path(),
            &[
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-1", "a", "durable", "u-1")),
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-2", "b", "caveated", "u-2")),
                "",
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        let ids: Vec<&str> = c.items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, vec!["ck-1"], "caveated stays out");
    }

    #[test]
    fn a_retracted_claim_is_not_rechecked() {
        // Retract is a real ledger op. Rechecking a retracted claim reports
        // defects the vault does not assert — and "latest write wins" cannot
        // see it, which is why this goes through the domain's fold.
        let d = tmp("retract");
        let p = write_ledger(
            d.path(),
            &[
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-1", "a", "durable", "u-1")),
                &format!(r#"{{"op":"retract","record":{}}}"#, rec("ck-1", "a", "durable", "u-1")),
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        assert!(c.items.is_empty(), "retracted claims are not live assertions");
    }

    #[test]
    fn a_supersede_swaps_in_the_replacement_and_drops_the_predecessor() {
        let d = tmp("supersede");
        let p = write_ledger(
            d.path(),
            &[
                &format!(r#"{{"op":"write","record":{}}}"#, rec("ck-old", "old", "durable", "u-old")),
                &format!(
                    r#"{{"op":"supersede","record":{},"supersedes":"ck-old"}}"#,
                    rec("ck-new", "new", "durable", "u-new")
                ),
            ],
        );
        let c = durable_from_ledger(&p).unwrap();
        let ids: Vec<&str> = c.items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, vec!["ck-new"], "the superseded predecessor drops out");
        assert_eq!(c.items[0].citations[0].unit_id, "u-new");
    }

    #[test]
    fn a_malformed_citation_fails_loudly_instead_of_vanishing() {
        // Dropping it would leave a claim with zero citations, and staleness is
        // measured from citation DEFECTS — a claim with none reports intact.
        let d = tmp("badcit");
        let p = write_ledger(
            d.path(),
            &[r#"{"op":"write","record":{"claim_key":"ck-1","claim_id":"ck-1","claim":"a","theme":"t","source_cases":[],"citations":[{"case_id":"c","unit_id":123,"quote":"q"}],"provenance_score":1.0,"provenance_class":"durable","strength":"supported","strength_rationale":"r","final_class":"durable","run_id":"r1","status":"active"}}"#],
        );
        assert!(durable_from_ledger(&p).is_err());
    }

    #[test]
    fn a_corrupt_ledger_line_fails_loudly() {
        // Skipping unparseable lines would let the report under-count silently
        // and still print a confident "all intact".
        let d = tmp("corrupt");
        let p = write_ledger(d.path(), &["not json at all"]);
        assert!(durable_from_ledger(&p).is_err());
    }
}
