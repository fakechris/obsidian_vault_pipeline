//! Read-only queries over the index model: list / filter / substring search
//! across sources, packs, claims, and runs. Powers `ovp-next find`.

use serde::Serialize;

use crate::model::{ClaimStatus, IndexModel, SourceStatus};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueryKind {
    Sources,
    Packs,
    Claims,
    Runs,
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
}

/// One result row, kind-tagged, with a printable line and a link target.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Hit {
    pub kind: String,
    pub status: String,
    pub line: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
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

    let mut hits = Vec::new();

    if kind_ok(QueryKind::Sources) {
        for s in &model.sources {
            let status = source_status_str(s.status);
            if !status_ok(status) || !date_ok(s.date.as_deref()) {
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
            });
        }
    }

    if kind_ok(QueryKind::Packs) {
        for p in &model.packs {
            if !status_ok("pack") || !date_ok(p.date.as_deref()) {
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
                    if p.json_repaired { " [json-repaired]" } else { "" }
                ),
                path: Some(format!("{}/reader.md", p.pack_dir)),
            });
        }
    }

    if kind_ok(QueryKind::Claims) {
        for c in &model.claims {
            let status = claim_status_str(c.status);
            // Claims carry no date; a date filter excludes them.
            if !status_ok(status) || q.date.is_some() {
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
            hits.push(Hit { kind: "claim".into(), status: status.into(), line, path: None });
        }
    }

    if kind_ok(QueryKind::Runs) {
        for r in &model.runs {
            if !status_ok("run") || !date_ok(Some(&r.date)) {
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
            });
        }
    }

    hits
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
