//! Pure builders for the read-only `/api/*` response bodies.
//!
//! Each returns `serde_json::Value` from already-read inputs — the SAME shape
//! the live server ships and the publisher snapshots. The live-only overlays
//! (`age_seconds`, `queued_live`, the heartbeat) are spliced on by the server
//! AFTER these; a static snapshot simply omits them.

use std::collections::HashMap;
use std::path::Path;

use ovp_domain::crystal::DurableRecord;
use ovp_domain::units::Unit;
use ovp_index::{ClaimRow, ClaimStatus, EvidenceModel, IndexModel, PackRow, Query, SourceRow};
use serde_json::{Value, json};

use crate::graph::{last_path_segment, theme_counts};

/// `/api/themes` — display themes with their active-claim counts.
pub fn themes_body(records: &[DurableRecord]) -> Value {
    let themes: Vec<Value> = theme_counts(records)
        .into_iter()
        .map(|(theme, count)| json!({ "theme": theme, "count": count }))
        .collect();
    Value::Array(themes)
}

/// `/api/flow` — the intake→reader→units→cards→crystal Sankey totals.
pub fn flow_body(model: &IndexModel) -> Value {
    let t = &model.totals;
    let total_units: usize = model.packs.iter().map(|p| p.units).sum();
    let total_cards: usize = model.packs.iter().map(|p| p.cards).sum();
    json!({
        "stages": ["intake", "reader", "units", "cards", "crystal", "blocked", "needs_content"],
        "flows": [
            { "from": "intake", "to": "reader", "value": t.processed, "label": "processed" },
            { "from": "intake", "to": "blocked", "value": t.blocked, "label": "blocked" },
            { "from": "intake", "to": "needs_content", "value": t.needs_content, "label": "needs content" },
            { "from": "reader", "to": "units", "value": total_units, "label": "accepted units" },
            { "from": "units", "to": "cards", "value": total_cards, "label": "cards kept" },
            { "from": "cards", "to": "crystal", "value": t.claims_durable, "label": "durable claims" },
        ],
    })
}

/// `/api/find` (and text `/api/search`) — the display-hit list for a query.
pub fn find_body(model: &IndexModel, query: &Query) -> Value {
    let hits = ovp_index::run_query(model, query);
    serde_json::to_value(&hits).unwrap_or_else(|_| json!([]))
}

/// `/api/entities` — the Tier-0 URL entity index, one row per entity id with
/// its kind, external URL, and mention count, most-mentioned first. Derived
/// entirely from `SourceRow.entities` (no sidecar), so live and publish agree.
pub fn entities_body(model: &IndexModel) -> Value {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for s in &model.sources {
        for e in &s.entities {
            *counts.entry(e.as_str()).or_default() += 1;
        }
    }
    let mut rows: Vec<(&str, usize)> = counts.into_iter().collect();
    rows.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    let entities: Vec<Value> = rows
        .into_iter()
        .filter_map(|(id, count)| {
            Some(json!({
                "id": id,
                "kind": ovp_domain::url_entities::kind_of_id(id)?,
                "url": ovp_domain::url_entities::url_for_id(id)?,
                "count": count,
            }))
        })
        .collect();
    json!({ "entities": entities })
}

/// `/api/entity/:id` — one entity: its external URL + the sources that mention
/// it (title/sha for linking) + the durable/caveated claims those sources
/// cite (via pack join). `None` when the id mentions no source (→ 404).
pub fn entity_body(model: &IndexModel, id: &str) -> Option<Value> {
    let id_lc = id.to_lowercase();
    let sources: Vec<&SourceRow> = model
        .sources
        .iter()
        .filter(|s| s.entities.iter().any(|e| *e == id_lc))
        .collect();
    if sources.is_empty() {
        return None;
    }
    // Claims whose cited case_ids join to any mentioning source's pack.
    let cases: std::collections::HashSet<String> = sources
        .iter()
        .filter_map(|s| s.pack_dir.as_deref().and_then(last_path_segment))
        .map(str::to_string)
        .collect();
    // Only durable/caveated claims — the endpoint contract + UI describe
    // active knowledge; a superseded/retracted claim rendered without a pill
    // would read as current.
    let mut claims: Vec<&ClaimRow> = model
        .claims
        .iter()
        .filter(|c| matches!(c.status, ClaimStatus::Durable | ClaimStatus::Caveated))
        .filter(|c| c.sources.iter().any(|s| cases.contains(s)))
        .collect();
    claims.sort_by_key(|c| {
        (
            match c.status {
                ClaimStatus::Durable => 0u8,
                ClaimStatus::Caveated => 1,
                _ => 2,
            },
            c.claim_id.clone(),
        )
    });
    Some(json!({
        "id": id_lc,
        "kind": ovp_domain::url_entities::kind_of_id(&id_lc),
        "url": ovp_domain::url_entities::url_for_id(&id_lc),
        "sources": sources.iter().map(|s| json!({
            "sha256": s.sha256,
            "title": s.title,
            "date": s.date,
        })).collect::<Vec<_>>(),
        "citing_claims": claims,
    }))
}

/// `/api/settings` — the PUBLIC subset only. Drops `vault_root`, `llm_configured`,
/// `ask_limits`, the last-run heartbeat, and live-queued backlog; keeps schema,
/// date, provenance stamp, and the public counts. The live server has its own
/// richer settings handler for the System page — this one is what a published
/// site ships.
pub fn settings_public_body(model: Option<&IndexModel>) -> Value {
    json!({
        "schema_version": model.map(|m| m.schema.clone()),
        "index_date": model.map(|m| m.date.clone()),
        "built_at": model.and_then(|m| m.built_at.clone()),
        "run_id": model.and_then(|m| m.run_id.clone()),
        "counts": model.map(|m| json!({
            "sources": m.totals.sources,
            "packs": m.totals.packs,
            "claims": m.totals.claims_durable + m.totals.claims_caveated,
        })),
        "version": env!("CARGO_PKG_VERSION"),
    })
}

/// `/api/claim/:id` — one durable claim with its citations resolved to source
/// metadata. Returns `None` when no active record matches `id` (→ 404).
///
/// `include_unit_text` gates the FULL grounded-unit sentence (read from each
/// pack's `units.accepted.json`): the live server includes it (the app's
/// evidence layer), the publisher sets it `false` so a public claim ships only
/// the short verbatim `quote` for provenance, never the fuller source text.
pub fn claim_body(
    records: &[DurableRecord],
    model: Option<&IndexModel>,
    reader_root: &Path,
    id: &str,
    include_unit_text: bool,
) -> Option<Value> {
    let rec = records
        .iter()
        .find(|r| r.claim_key == id || r.claim_id == id)?;

    let source_lookup: HashMap<String, &SourceRow> = model
        .map(|m| m.sources.iter().map(|s| (s.sha256.clone(), s)).collect())
        .unwrap_or_default();
    let pack_lookup: HashMap<String, &PackRow> = model
        .map(|m| {
            m.packs
                .iter()
                .filter_map(|p| Some((last_path_segment(&p.pack_dir)?.to_string(), p)))
                .collect()
        })
        .unwrap_or_default();

    let mut citations = Vec::new();
    for cit in &rec.citations {
        let unit_text = if include_unit_text {
            let units_path = reader_root.join(&cit.case_id).join("units.accepted.json");
            std::fs::read_to_string(&units_path)
                .ok()
                .and_then(|raw| serde_json::from_str::<Vec<Unit>>(&raw).ok())
                .and_then(|units| units.into_iter().find(|u| u.id == cit.unit_id).map(|u| u.text))
                .unwrap_or_default()
        } else {
            String::new()
        };

        let (source_title, source_url, source_sha) =
            if let Some(pack) = pack_lookup.get(cit.case_id.as_str()) {
                let sha = pack.source_sha256.as_deref().unwrap_or("").to_string();
                let src = source_lookup.get(&sha);
                (
                    src.and_then(|s| s.title.clone()).unwrap_or_else(|| pack.title.clone()),
                    src.and_then(|s| s.url.clone()).unwrap_or_default(),
                    sha,
                )
            } else {
                (cit.case_id.clone(), String::new(), String::new())
            };

        citations.push(json!({
            "unit_id": cit.unit_id,
            "unit_text": unit_text,
            "quote": cit.quote,
            "resolved_line": cit.resolved_line,
            "case_id": cit.case_id,
            "source_title": source_title,
            "source_url": source_url,
            "source_sha256": source_sha,
        }));
    }

    Some(json!({
        "claim_id": rec.claim_key,
        "claim": rec.claim,
        "theme": rec.theme,
        "strength": format!("{:?}", rec.strength).to_lowercase(),
        "citations": citations,
    }))
}

/// The source markdown payload for `/api/source/:sha`. `None` in publish (lite)
/// mode — the full third-party synthesis is not republished; the client links
/// out via `source.url` instead.
pub struct SourceDoc {
    pub markdown: Option<String>,
    pub truncated: bool,
    pub error: Option<String>,
}

/// `/api/source/:sha` — the three-layer source detail: SourceRow meta, the
/// memory layer (cards + grounded units from the evidence sidecar), the durable
/// claims citing this source, and the doc body. Pass `doc = None` for the
/// public lite page (markdown omitted). Returns `None` when the sha is unknown.
pub fn source_body(
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
    sha: &str,
    doc: Option<SourceDoc>,
) -> Option<Value> {
    let source = model.sources.iter().find(|s| s.sha256 == sha)?;

    let evidence_available = evidence.is_some();
    let pack_dir = source.pack_dir.as_deref();
    let belongs =
        |row_sha: Option<&str>, row_pack: &str| row_sha == Some(sha) || pack_dir == Some(row_pack);

    let cards: Vec<Value> = evidence
        .map(|ev| {
            ev.cards
                .iter()
                .filter(|c| belongs(c.source_sha256.as_deref(), &c.pack_dir))
                .map(|c| json!({ "title": c.title, "content": c.content }))
                .collect()
        })
        .unwrap_or_default();
    let units: Vec<Value> = evidence
        .map(|ev| {
            ev.units
                .iter()
                .filter(|u| belongs(u.source_sha256.as_deref(), &u.pack_dir))
                .map(|u| {
                    json!({
                        "unit_id": u.unit_id,
                        "text": u.text,
                        "quote": u.quote,
                        "line": u.line,
                        "attribution": u.attribution,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let case_id = pack_dir.and_then(last_path_segment);
    let mut citing: Vec<&ClaimRow> = match case_id {
        Some(case) => model
            .claims
            .iter()
            .filter(|c| c.sources.iter().any(|s| s == case))
            .collect(),
        None => Vec::new(),
    };
    citing.sort_by_key(|c| {
        (
            match c.status {
                ClaimStatus::Durable => 0u8,
                ClaimStatus::Caveated => 1,
                _ => 2,
            },
            c.claim_id.clone(),
        )
    });

    let doc = doc.unwrap_or(SourceDoc { markdown: None, truncated: false, error: None });
    Some(json!({
        "source": source,
        "memory": {
            "evidence_available": evidence_available,
            "cards": cards,
            "units": units,
        },
        "citing_claims": citing,
        "doc": {
            "markdown": doc.markdown,
            "truncated": doc.truncated,
            "error": doc.error,
        },
    }))
}
