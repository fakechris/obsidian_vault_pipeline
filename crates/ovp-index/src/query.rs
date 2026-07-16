//! Read-only queries over the index model: list / filter / substring search
//! across sources, packs, claims, and runs. Powers `ovp2 find`.

use serde::Serialize;

use crate::evidence::EvidenceModel;
use crate::model::{ClaimStatus, IndexModel, SourceStatus};
use crate::score::lexical_score;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueryKind {
    Sources,
    Packs,
    Claims,
    Runs,
    Cards,
    Units,
    /// The tag vocabulary itself: one row per canonical tag with its source
    /// count. Never included in the default all-kinds sweep — only an
    /// explicit `--kind tags` lists it.
    Tags,
}

#[derive(Debug, Default, Clone)]
pub struct Query {
    /// Restrict to one row kind (default: all kinds).
    pub kind: Option<QueryKind>,
    /// Status filter, matched against the row's serialized status
    /// (`processed`, `failed`, `durable`, `caveated`, …).
    pub status: Option<String>,
    /// Date prefix filter (`2026`, `2026-06`, `2026-06-09`).
    pub date: Option<String>,
    /// Case-insensitive substring over titles, URLs, paths, card titles,
    /// claim text, themes, run ids.
    pub term: Option<String>,
    /// Canonical tag filter (exact match after normalization). Tags are a
    /// source-level axis: a tag filter restricts sources and excludes the
    /// kinds that carry no tags (packs/claims/runs/cards/units), the same
    /// way a date filter excludes claims.
    pub tag: Option<String>,
}

/// One result row, kind-tagged, with a printable line and a link target.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Hit {
    pub kind: String,
    pub status: String,
    pub line: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    /// Stable row id, kind-specific, so API consumers (the portal search)
    /// can build entity links without parsing `line`: source → sha256,
    /// pack → pack_dir, claim → claim_id, run → run_id, card/unit → the
    /// evidence row id. Additive — CLI text output ignores it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
}

pub fn run_query(model: &IndexModel, q: &Query) -> Vec<Hit> {
    let term = q.term.as_deref().map(str::to_lowercase);
    let matches = |hay: &[&str]| -> bool {
        match &term {
            None => true,
            Some(t) => hay.iter().any(|h| h.to_lowercase().contains(t)),
        }
    };
    let status_ok = |s: &str| q.status.as_deref().map(|want| want == s).unwrap_or(true);
    let date_ok = |d: Option<&str>| match q.date.as_deref() {
        None => true,
        Some(prefix) => d.is_some_and(|d| d.starts_with(prefix)),
    };
    let kind_ok = |k: QueryKind| q.kind.map(|want| want == k).unwrap_or(true);
    // Normalized once so `--tag Claude_Code` matches the canonical form. An
    // explicit filter that normalizes to nothing (`tag=#`, whitespace) must
    // match NOTHING — collapsing it to "no filter" would silently return
    // every row.
    let tag = match q.tag.as_deref() {
        None => None,
        Some(raw) => match ovp_domain::tags::normalize_tag(raw) {
            Some(t) => Some(t),
            None => return Vec::new(),
        },
    };
    let tag_ok = |tags: &[String]| match &tag {
        None => true,
        Some(t) => tags.iter().any(|have| have == t),
    };

    let mut hits = Vec::new();

    // The vocabulary listing is explicit-only (never in the all-kinds sweep):
    // one row per canonical tag over the status/date-filtered sources, count
    // descending. `term` narrows by substring; a `--tag` filter is exact.
    if q.kind == Some(QueryKind::Tags) {
        let mut counts: std::collections::BTreeMap<&str, usize> = std::collections::BTreeMap::new();
        for s in &model.sources {
            if !status_ok(source_status_str(s.status)) || !date_ok(s.date.as_deref()) {
                continue;
            }
            for t in &s.tags {
                *counts.entry(t.as_str()).or_default() += 1;
            }
        }
        let mut rows: Vec<(&str, usize)> = counts
            .into_iter()
            .filter(|(t, _)| matches(&[t]) && tag.as_deref().map(|want| *t == want).unwrap_or(true))
            .collect();
        rows.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));
        for (t, n) in rows {
            hits.push(Hit {
                kind: "tag".into(),
                status: "tag".into(),
                line: format!("{t} ({n})"),
                path: None,
                id: Some(t.to_string()),
            });
        }
        return hits;
    }

    if kind_ok(QueryKind::Sources) {
        for s in &model.sources {
            let status = source_status_str(s.status);
            if !status_ok(status) || !date_ok(s.date.as_deref()) || !tag_ok(&s.tags) {
                continue;
            }
            let title = s.title.as_deref().unwrap_or("(untitled)");
            let url = s.url.as_deref().unwrap_or("");
            let path = s.rel_path.as_deref().unwrap_or("");
            if !matches(&[title, url, path]) {
                continue;
            }
            let mut line = format!("{title} [{status}]");
            if let Some(d) = &s.date {
                line.push_str(&format!(" {d}"));
            }
            if !s.tags.is_empty() {
                line.push_str(&format!(" #{}", s.tags.join(" #")));
            }
            if s.fail_count > 0 {
                line.push_str(&format!(" fails={}", s.fail_count));
            }
            if let Some(r) = &s.last_reason {
                line.push_str(&format!(" — {r}"));
            }
            hits.push(Hit {
                kind: "source".into(),
                status: status.into(),
                line,
                path: s.rel_path.clone(),
                id: Some(s.sha256.clone()),
            });
        }
    }

    if kind_ok(QueryKind::Packs) {
        for p in &model.packs {
            // Packs/claims/runs carry no tags; a tag filter excludes them.
            if tag.is_some() || !status_ok("pack") || !date_ok(p.date.as_deref()) {
                continue;
            }
            let cards_joined = p.card_titles.join(" | ");
            if !matches(&[&p.title, &p.pack_dir, &cards_joined]) {
                continue;
            }
            hits.push(Hit {
                kind: "pack".into(),
                status: "pack".into(),
                line: format!(
                    "{} ({} cards / {} units){}",
                    p.title,
                    p.cards,
                    p.units,
                    if p.json_repaired {
                        " [json-repaired]"
                    } else {
                        ""
                    }
                ),
                path: Some(format!("{}/reader.md", p.pack_dir)),
                id: Some(p.pack_dir.clone()),
            });
        }
    }

    if kind_ok(QueryKind::Claims) {
        for c in &model.claims {
            let status = claim_status_str(c.status);
            // Claims carry no date or tags; either filter excludes them.
            if !status_ok(status) || q.date.is_some() || tag.is_some() {
                continue;
            }
            let theme = c.theme.as_deref().unwrap_or("");
            if !matches(&[&c.claim, theme, &c.claim_id]) {
                continue;
            }
            let mut line = format!("{} [{status}] {}", c.claim_id, c.claim);
            if !c.sources.is_empty() {
                line.push_str(&format!(" ({} sources)", c.sources.len()));
            }
            hits.push(Hit {
                kind: "claim".into(),
                status: status.into(),
                line,
                path: None,
                id: Some(c.claim_id.clone()),
            });
        }
    }

    if kind_ok(QueryKind::Runs) {
        for r in &model.runs {
            if tag.is_some() || !status_ok("run") || !date_ok(Some(&r.date)) {
                continue;
            }
            if !matches(&[&r.run_id, &r.date]) {
                continue;
            }
            hits.push(Hit {
                kind: "run".into(),
                status: "run".into(),
                line: format!(
                    "{} {} — ok={} failed={} skipped={} ingested={}",
                    r.date, r.run_id, r.succeeded, r.failed, r.skipped, r.ingested
                ),
                path: Some(r.report_file.clone()),
                id: Some(r.run_id.clone()),
            });
        }
    }

    hits
}

pub fn run_evidence_query(evidence: &EvidenceModel, q: &Query, limit: usize) -> Vec<Hit> {
    let status_ok = |s: &str| q.status.as_deref().map(|want| want == s).unwrap_or(true);
    let kind_ok = |k: QueryKind| q.kind.map(|want| want == k).unwrap_or(true);
    let term = q.term.as_deref().unwrap_or("");
    let mut scored: Vec<(f64, String, Hit)> = Vec::new();

    // Evidence rows carry no date or tags; either filter excludes them.
    if q.date.is_some() || q.tag.is_some() {
        return Vec::new();
    }

    if kind_ok(QueryKind::Cards) && status_ok("card") {
        for card in &evidence.cards {
            let score = match q.term.as_deref() {
                None => 1.0,
                Some(_) => lexical_score(
                    term,
                    &[
                        &card.source_title,
                        &card.title,
                        &card.content,
                        &card.pack_dir,
                    ],
                ),
            };
            if score <= 0.0 {
                continue;
            }
            scored.push((
                score,
                card.id.clone(),
                Hit {
                    kind: "card".into(),
                    status: "card".into(),
                    line: format!(
                        "{} — {}{}",
                        card.source_title,
                        card.title,
                        preview_suffix(&card.content)
                    ),
                    path: Some(format!("{}/reader.md", card.pack_dir)),
                    id: Some(card.id.clone()),
                },
            ));
        }
    }

    if kind_ok(QueryKind::Units) && status_ok("unit") {
        for unit in &evidence.units {
            let line_str = unit.line.map(|n| format!(" line {n}")).unwrap_or_default();
            let score = match q.term.as_deref() {
                None => 1.0,
                Some(_) => lexical_score(
                    term,
                    &[&unit.source_title, &unit.text, &unit.quote, &unit.pack_dir],
                ),
            };
            if score <= 0.0 {
                continue;
            }
            scored.push((
                score,
                unit.id.clone(),
                Hit {
                    kind: "unit".into(),
                    status: "unit".into(),
                    line: format!(
                        "{}{} — {}{}",
                        unit.source_title,
                        line_str,
                        unit.text,
                        preview_suffix(&unit.quote)
                    ),
                    path: Some(format!("{}/reader.md", unit.pack_dir)),
                    id: Some(unit.id.clone()),
                },
            ));
        }
    }

    scored.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.1.cmp(&b.1))
    });
    scored
        .into_iter()
        .take(limit)
        .map(|(_, _, hit)| hit)
        .collect()
}

fn preview_suffix(s: &str) -> String {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let preview: String = trimmed.chars().take(160).collect();
    if trimmed.chars().count() > 160 {
        format!(" — {preview}...")
    } else {
        format!(" — {preview}")
    }
}

pub fn source_status_str(s: SourceStatus) -> &'static str {
    match s {
        SourceStatus::Queued => "queued",
        SourceStatus::Processed => "processed",
        SourceStatus::Failed => "failed",
        SourceStatus::Blocked => "blocked",
        SourceStatus::NeedsContent => "needs_content",
        SourceStatus::Unparseable => "unparseable",
        SourceStatus::Duplicate => "duplicate",
    }
}

pub fn claim_status_str(s: ClaimStatus) -> &'static str {
    match s {
        ClaimStatus::Durable => "durable",
        ClaimStatus::Superseded => "superseded",
        ClaimStatus::Retracted => "retracted",
        ClaimStatus::Caveated => "caveated",
    }
}

#[cfg(test)]
mod tests {
    use crate::evidence::{CardEvidenceRow, EVIDENCE_SCHEMA, EvidenceModel, UnitEvidenceRow};
    use crate::query::{Query, QueryKind, run_evidence_query};

    fn evidence() -> EvidenceModel {
        EvidenceModel {
            schema: EVIDENCE_SCHEMA.into(),
            date: "2026-07-06".into(),
            cards: vec![CardEvidenceRow {
                id: "card:40-Resources/Reader/a:0".into(),
                pack_dir: "40-Resources/Reader/a".into(),
                source_sha256: Some("sha-a".into()),
                source_title: "Agent Memory Systems".into(),
                title: "Memory as state".into(),
                content: "Agent memory should be treated as persistent state.".into(),
                unit_type: Some("claim".into()),
                cited_unit_ids: vec!["u-001".into()],
            }],
            units: vec![UnitEvidenceRow {
                id: "unit:40-Resources/Reader/a:u-001".into(),
                pack_dir: "40-Resources/Reader/a".into(),
                source_sha256: Some("sha-a".into()),
                source_title: "Agent Memory Systems".into(),
                unit_id: "u-001".into(),
                text: "代理记忆是持久状态。".into(),
                quote: "Agent memory should be treated as persistent state.".into(),
                line: Some(12),
                attribution: "author".into(),
                modality: "asserted".into(),
            }],
            warnings: vec![],
        }
    }

    #[test]
    fn evidence_query_searches_card_content() {
        let hits = run_evidence_query(
            &evidence(),
            &Query {
                kind: Some(QueryKind::Cards),
                term: Some("persistent state".into()),
                ..Default::default()
            },
            10,
        );

        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].kind, "card");
        assert!(hits[0].line.contains("Memory as state"));
        assert_eq!(
            hits[0].path.as_deref(),
            Some("40-Resources/Reader/a/reader.md")
        );
    }

    #[test]
    fn evidence_query_searches_cjk_units_and_returns_stable_path() {
        let hits = run_evidence_query(
            &evidence(),
            &Query {
                kind: Some(QueryKind::Units),
                term: Some("记忆".into()),
                ..Default::default()
            },
            10,
        );

        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].kind, "unit");
        assert!(hits[0].line.contains("代理记忆"));
        assert_eq!(
            hits[0].path.as_deref(),
            Some("40-Resources/Reader/a/reader.md")
        );
    }
}
