//! Precomputed portal aggregate (`.ovp/console/portal-summary.json`) — a derived
//! projection over the read model + crystal ledger, rebuilt after `daily` /
//! `index` so the SPA can hydrate headline stats and the theme wall without
//! extra round-trips.

use std::collections::{BTreeMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::crystal::DurableRecord;

const ACTIVITY_WINDOW_DAYS: usize = 30;

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct PortalSummary {
    #[serde(default)]
    pub generated_at: String,
    #[serde(default)]
    pub theme_count: usize,
    #[serde(default)]
    pub claim_count: usize,
    #[serde(default)]
    pub active_claim_count: usize,
    #[serde(default)]
    pub source_count: usize,
    #[serde(default)]
    pub recent_activity: Vec<ActivityEntry>,
    #[serde(default)]
    pub themes: Vec<ThemeSummary>,
    #[serde(default)]
    pub index_health: IndexHealth,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ThemeSummary {
    #[serde(default)]
    pub theme_id: Option<i64>,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub claim_count: usize,
    #[serde(default)]
    pub source_count: usize,
    #[serde(default)]
    pub latest_date: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ActivityEntry {
    #[serde(default)]
    pub date: String,
    #[serde(default)]
    pub new_packs: usize,
    #[serde(default)]
    pub new_claims: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct IndexHealth {
    #[serde(default)]
    pub last_daily_run: Option<String>,
    #[serde(default)]
    pub total_packs: usize,
    #[serde(default)]
    pub grounded_fraction: f64,
}

/// Minimal claim row view for summary activity + per-theme source counts.
#[derive(Debug, Clone, PartialEq)]
pub struct SummaryClaimRow {
    pub theme_id: Option<i64>,
    pub theme: Option<String>,
    pub sources: Vec<String>,
    pub run_date: Option<String>,
    pub active: bool,
}

/// Minimal pack row view for summary activity + grounded fraction.
#[derive(Debug, Clone, PartialEq)]
pub struct SummaryPackRow {
    pub date: Option<String>,
    pub units: usize,
}

/// Scalar totals folded from the read model.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct SummaryTotals {
    pub sources: usize,
    pub packs: usize,
    pub claims_total: usize,
    pub claims_active: usize,
    pub packs_with_units: usize,
}

/// Build a portal summary from active durable ledger records (theme overlay
/// already applied) and the matching read-model slices.
pub fn build_portal_summary(
    generated_at: &str,
    records: &[DurableRecord],
    claims: &[SummaryClaimRow],
    packs: &[SummaryPackRow],
    totals: SummaryTotals,
    last_daily_run: Option<&str>,
) -> PortalSummary {
    let themes = build_theme_summaries(records, claims);
    let recent_activity = build_recent_activity(claims, packs);
    let grounded_fraction = if totals.packs == 0 {
        0.0
    } else {
        totals.packs_with_units as f64 / totals.packs as f64
    };

    PortalSummary {
        generated_at: generated_at.to_string(),
        theme_count: themes.len(),
        claim_count: totals.claims_total,
        active_claim_count: totals.claims_active,
        source_count: totals.sources,
        recent_activity,
        themes,
        index_health: IndexHealth {
            last_daily_run: last_daily_run.map(str::to_string),
            total_packs: totals.packs,
            grounded_fraction,
        },
    }
}

fn build_theme_summaries(
    records: &[DurableRecord],
    claims: &[SummaryClaimRow],
) -> Vec<ThemeSummary> {
    let mut claim_counts: BTreeMap<String, (Option<i64>, String, usize)> = BTreeMap::new();
    for r in records {
        let key = theme_key(r.theme_id, &r.theme);
        let e = claim_counts
            .entry(key)
            .or_insert_with(|| (r.theme_id, r.theme.clone(), 0));
        e.2 += 1;
    }

    let mut source_sets: BTreeMap<String, HashSet<String>> = BTreeMap::new();
    let mut latest: BTreeMap<String, String> = BTreeMap::new();
    for c in claims.iter().filter(|c| c.active) {
        let theme = c.theme.as_deref().unwrap_or("");
        let key = theme_key(c.theme_id, theme);
        let set = source_sets.entry(key.clone()).or_default();
        for s in &c.sources {
            set.insert(s.clone());
        }
        if let Some(d) = c.run_date.as_deref() {
            latest
                .entry(key)
                .and_modify(|prev| {
                    if d > prev.as_str() {
                        *prev = d.to_string();
                    }
                })
                .or_insert_with(|| d.to_string());
        }
    }

    let mut out: Vec<ThemeSummary> = claim_counts
        .into_iter()
        .map(|(key, (theme_id, label, claim_count))| ThemeSummary {
            theme_id,
            label,
            claim_count,
            source_count: source_sets.get(&key).map(|s| s.len()).unwrap_or(0),
            latest_date: latest.get(&key).cloned(),
        })
        .collect();
    out.sort_by(|a, b| {
        b.claim_count
            .cmp(&a.claim_count)
            .then_with(|| a.theme_id.cmp(&b.theme_id))
            .then_with(|| a.label.cmp(&b.label))
    });
    out
}

fn theme_key(theme_id: Option<i64>, label: &str) -> String {
    match theme_id {
        Some(id) => format!("i{id}"),
        None => format!("t:{label}"),
    }
}

fn build_recent_activity(claims: &[SummaryClaimRow], packs: &[SummaryPackRow]) -> Vec<ActivityEntry> {
    let mut pack_by_day: BTreeMap<String, usize> = BTreeMap::new();
    for p in packs {
        if let Some(d) = p.date.as_deref() {
            *pack_by_day.entry(d.to_string()).or_default() += 1;
        }
    }
    let mut claim_by_day: BTreeMap<String, usize> = BTreeMap::new();
    for c in claims.iter().filter(|c| c.active) {
        if let Some(d) = c.run_date.as_deref() {
            *claim_by_day.entry(d.to_string()).or_default() += 1;
        }
    }

    let mut days: BTreeMap<String, ActivityEntry> = BTreeMap::new();
    for (date, n) in pack_by_day {
        days.entry(date.clone())
            .or_insert_with(|| ActivityEntry {
                date,
                new_packs: 0,
                new_claims: 0,
            })
            .new_packs = n;
    }
    for (date, n) in claim_by_day {
        days.entry(date.clone())
            .or_insert_with(|| ActivityEntry {
                date,
                new_packs: 0,
                new_claims: 0,
            })
            .new_claims = n;
    }

    let mut out: Vec<ActivityEntry> = days.into_values().collect();
    out.sort_by(|a, b| b.date.cmp(&a.date));
    out.truncate(ACTIVITY_WINDOW_DAYS);
    out.sort_by(|a, b| a.date.cmp(&b.date));
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crystal::{CrystalStatus, DurableRecord, FinalClass, ProvenanceClass, StrengthClass};

    fn sample_record(id: i64, label: &str) -> DurableRecord {
        DurableRecord {
            claim_key: format!("ck-{id}"),
            claim_id: format!("c-{id}"),
            claim: format!("claim {id}"),
            theme: label.to_string(),
            theme_id: Some(id),
            source_cases: vec![format!("case-{id}")],
            citations: vec![],
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: String::new(),
            final_class: FinalClass::Durable,
            run_id: "run-a".into(),
            status: CrystalStatus::Active,
        }
    }

    #[test]
    fn roundtrip_serde_defaults() {
        let summary = PortalSummary::default();
        let json = serde_json::to_string(&summary).unwrap();
        let back: PortalSummary = serde_json::from_str(&json).unwrap();
        assert_eq!(summary, back);
    }

    #[test]
    fn builder_empty_inputs() {
        let summary = build_portal_summary(
            "2026-07-01T00:00:00Z",
            &[],
            &[],
            &[],
            SummaryTotals::default(),
            None,
        );
        assert_eq!(summary.theme_count, 0);
        assert_eq!(summary.claim_count, 0);
        assert_eq!(summary.index_health.grounded_fraction, 0.0);
    }

    #[test]
    fn builder_populated_counts_and_themes() {
        let records = vec![
            sample_record(1, "Alpha"),
            sample_record(1, "Alpha"),
            sample_record(2, "Beta"),
        ];
        let claims = vec![
            SummaryClaimRow {
                theme_id: Some(1),
                theme: Some("Alpha".into()),
                sources: vec!["case-1".into(), "case-2".into()],
                run_date: Some("2026-07-02".into()),
                active: true,
            },
            SummaryClaimRow {
                theme_id: Some(2),
                theme: Some("Beta".into()),
                sources: vec!["case-3".into()],
                run_date: Some("2026-07-03".into()),
                active: true,
            },
        ];
        let packs = vec![
            SummaryPackRow {
                date: Some("2026-07-02".into()),
                units: 2,
            },
            SummaryPackRow {
                date: Some("2026-07-02".into()),
                units: 0,
            },
        ];
        let totals = SummaryTotals {
            sources: 10,
            packs: 2,
            claims_total: 2,
            claims_active: 3,
            packs_with_units: 1,
        };

        let summary = build_portal_summary(
            "2026-07-03T10:00:00Z",
            &records,
            &claims,
            &packs,
            totals,
            Some("2026-07-03T09:00:00Z"),
        );
        assert_eq!(summary.theme_count, 2);
        assert_eq!(summary.active_claim_count, 3);
        assert_eq!(summary.claim_count, 2);
        assert!((summary.index_health.grounded_fraction - 0.5).abs() < f64::EPSILON);
        assert_eq!(summary.themes[0].claim_count, 2);
        assert_eq!(summary.themes[0].source_count, 2);
        assert_eq!(
            summary
                .recent_activity
                .iter()
                .find(|d| d.date == "2026-07-02")
                .map(|d| d.new_packs),
            Some(2)
        );
    }
}