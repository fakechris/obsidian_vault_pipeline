//! Server-resolved citation RECEIPTS for agent answers — one implementation
//! for every product projection of the agent loop (HTTP /api/ask, MCP ask).
//! The verifier is fail-closed: fabricated references surface as
//! verified:false, ambiguous claim_id anchors get NO link.

use crate::verify::citations_in_order;
use ovp_domain::crystal::DurableRecord;
use ovp_index::evidence::EvidenceModel;
use ovp_index::IndexModel;

/// Models decorate citation ids in the wild — `[source:<sha> Some Title]`,
/// `[source: <sha>]` — and exact-string matching then fails receipts the
/// model clearly intended. Tolerant extraction: trim whitespace and angle
/// brackets, keep the first whitespace-delimited token. RESOLUTION stays
/// exact — a token that matches nothing is still verified:false.
pub fn citation_id_token(id: &str) -> &str {
    id.split_whitespace()
        .next()
        .unwrap_or("")
        .trim_matches(|c| c == '<' || c == '>')
}

/// Human label the model wrote after a bare id — `[source:<sha> Some Title]`.
/// Empty when the marker carried only the id (or angle brackets around it).
fn decorated_citation_title(rest: &str) -> Option<String> {
    let mut parts = rest.split_whitespace();
    let _id = parts.next()?;
    let title: String = parts.collect::<Vec<_>>().join(" ");
    let title = title
        .trim()
        .trim_matches(|c| c == '<' || c == '>' || c == '|')
        .trim();
    if title.is_empty() {
        None
    } else {
        Some(title.chars().take(120).collect())
    }
}

/// Operator-facing source label: prefer a real title, then path basename,
/// then a short host/url, never a raw 64-char sha as the primary line.
pub fn source_display_title(source: &ovp_index::SourceRow) -> String {
    if let Some(title) = source
        .title
        .as_deref()
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        return title.chars().take(120).collect();
    }
    if let Some(path) = source.rel_path.as_deref() {
        let base = path.rsplit(['/', '\\']).next().unwrap_or(path);
        let base = base.trim();
        if !base.is_empty() {
            return base.chars().take(120).collect();
        }
    }
    if let Some(url) = source.url.as_deref().map(str::trim).filter(|u| !u.is_empty()) {
        // host/path fragment is more scannable than a full tracking URL.
        let host = url
            .trim_start_matches("https://")
            .trim_start_matches("http://")
            .split(['?', '#'])
            .next()
            .unwrap_or(url);
        if !host.is_empty() {
            return host.chars().take(120).collect();
        }
    }
    // Last resort: short id — still better than dumping the full hash as the
    // only visible text in the citations rail.
    let short = source.sha256.chars().take(12).collect::<String>();
    format!("source {short}…")
}

/// Canonical citation id for markers/UI matching (`source:<sha>`, not the
/// decorated form). The raw answer key stays available via decorated titles.
fn citation_stable_id(kind: &str, token: &str) -> String {
    if kind.is_empty() {
        token.to_string()
    } else {
        format!("{kind}:{token}")
    }
}

pub fn agent_citations(
    answer: &str,
    model: &IndexModel,
    records: &[DurableRecord],
    evidence: Option<&EvidenceModel>,
) -> Vec<serde_json::Value> {
    citations_in_order(answer)
        .into_iter()
        .map(|key| {
            let (kind, rest) = key.split_once(':').unwrap_or(("", key.as_str()));
            let id = citation_id_token(rest);
            let stable_id = citation_stable_id(kind, id);
            let decorated = decorated_citation_title(rest);
            match kind {
                "claim" => {
                    let hit = records.iter().find(|r| r.claim_key == id);
                    // Fail-closed anchor (same rule as the legacy citation
                    // path): claim_ids can collide across runs, and a shared
                    // anchor could open the WRONG claim — verified stays true
                    // (the key resolved uniquely), the link is omitted.
                    let link = hit.and_then(|r| {
                        let same_id =
                            records.iter().filter(|o| o.claim_id == r.claim_id).count();
                        (same_id == 1).then(|| format!("/knowledge#{}", r.claim_id))
                    });
                    let title = hit
                        .map(|r| r.claim.chars().take(120).collect::<String>())
                        .or(decorated);
                    serde_json::json!({
                        "id": stable_id,
                        "kind": "claim",
                        "title": title,
                        "link_target": link,
                        "verified": hit.is_some(),
                    })
                }
                "source" => {
                    let hit = model.sources.iter().find(|s| s.sha256 == id);
                    let title = hit
                        .map(source_display_title)
                        .or(decorated)
                        .or_else(|| {
                            let short = id.chars().take(12).collect::<String>();
                            Some(format!("source {short}…"))
                        });
                    serde_json::json!({
                        "id": stable_id,
                        "kind": "source",
                        "title": title,
                        "link_target": hit.map(|s| format!("/library/{}", s.sha256)),
                        "verified": hit.is_some(),
                    })
                }
                // Units/cards live in the evidence sidecar — the sidecar rows
                // carry their source sha, so resolution needs NO focus
                // context (2026-08-07 operator report: every surface that
                // reconstructed these client-side showed raw ids; resolve
                // them HERE, once). Fail-closed on ambiguity, like claims.
                "unit" => {
                    let hits: Vec<_> = evidence
                        .map(|ev| ev.units.iter().filter(|u| u.unit_id == id).collect())
                        .unwrap_or_default();
                    let hit = (hits.len() == 1).then(|| hits[0]);
                    let title = hit
                        .map(|u| {
                            let body = if u.quote.trim().is_empty() { &u.text } else { &u.quote };
                            body.trim().chars().take(120).collect::<String>()
                        })
                        .filter(|t| !t.is_empty())
                        .or(decorated);
                    let link = hit.and_then(|u| {
                        u.source_sha256
                            .as_deref()
                            .map(|sha| format!("/library/{sha}?tab=memory"))
                    });
                    serde_json::json!({
                        "id": stable_id,
                        "kind": "unit",
                        "title": title,
                        "link_target": link,
                        "verified": hit.is_some(),
                    })
                }
                "card" => {
                    // Evidence card ids are already `card:`-prefixed; markers
                    // appear both bare and doubled in the wild — accept
                    // either, resolution stays exact.
                    let hits: Vec<_> = evidence
                        .map(|ev| {
                            ev.cards
                                .iter()
                                .filter(|c| c.id == stable_id || c.id == id)
                                .collect()
                        })
                        .unwrap_or_default();
                    let hit = (hits.len() == 1).then(|| hits[0]);
                    let title = hit
                        .map(|c| c.title.trim().chars().take(120).collect::<String>())
                        .filter(|t| !t.is_empty())
                        .or(decorated);
                    let link = hit.and_then(|c| {
                        c.source_sha256
                            .as_deref()
                            .map(|sha| format!("/library/{sha}?tab=memory"))
                    });
                    serde_json::json!({
                        "id": stable_id,
                        "kind": "card",
                        "title": title,
                        "link_target": link,
                        "verified": hit.is_some(),
                    })
                }
                _ => serde_json::json!({
                    "id": stable_id,
                    "kind": kind,
                    "title": decorated,
                    "link_target": serde_json::Value::Null,
                    "verified": false,
                }),
            }
        })
        .collect()
}

/// [`agent_citations`] without an index snapshot (fresh/unindexed vault):
/// claim receipts still resolve against the ledger; source references cannot
/// be verified and honestly say so. Decorated marker titles still surface so
/// the UI is not stuck showing only a hash.
pub fn agent_citations_unindexed(
    answer: &str,
    records: &[DurableRecord],
) -> Vec<serde_json::Value> {
    citations_in_order(answer)
        .into_iter()
        .map(|key| {
            let (kind, rest) = key.split_once(':').unwrap_or(("", key.as_str()));
            let id = citation_id_token(rest);
            let stable_id = citation_stable_id(kind, id);
            let decorated = decorated_citation_title(rest);
            if kind == "claim" {
                let hit = records.iter().find(|r| r.claim_key == id);
                let link = hit.and_then(|r| {
                    let same_id = records.iter().filter(|o| o.claim_id == r.claim_id).count();
                    (same_id == 1).then(|| format!("/knowledge#{}", r.claim_id))
                });
                let title = hit
                    .map(|r| r.claim.chars().take(120).collect::<String>())
                    .or(decorated);
                serde_json::json!({
                    "id": stable_id,
                    "kind": "claim",
                    "title": title,
                    "link_target": link,
                    "verified": hit.is_some(),
                })
            } else if kind == "source" {
                // No index → cannot verify or deep-link; still prefer any
                // model-decorated title over dumping the raw sha.
                let title = decorated.or_else(|| {
                    let short = id.chars().take(12).collect::<String>();
                    Some(format!("source {short}…"))
                });
                serde_json::json!({
                    "id": stable_id,
                    "kind": "source",
                    "title": title,
                    "link_target": serde_json::Value::Null,
                    "verified": false,
                })
            } else {
                serde_json::json!({
                    "id": stable_id,
                    "kind": kind,
                    "title": decorated,
                    "link_target": serde_json::Value::Null,
                    "verified": false,
                })
            }
        })
        .collect()
}

/// Compact display line for a tool call's arguments ("query=agent memory ·
/// limit=10") — narration for the live progress trail. Scalar fields only,
/// well-known keys first, capped so a pathological argument can't flood the
/// feed. Null when nothing scalar is present.
pub fn args_brief(arguments: &serde_json::Value) -> serde_json::Value {
    const PRIORITY: [&str; 6] = ["query", "claim_key", "claim_id", "source_id", "cursor", "limit"];
    let Some(obj) = arguments.as_object() else {
        return serde_json::Value::Null;
    };
    let render = |v: &serde_json::Value| match v {
        serde_json::Value::String(s) => Some(s.clone()),
        serde_json::Value::Number(n) => Some(n.to_string()),
        serde_json::Value::Bool(b) => Some(b.to_string()),
        _ => None,
    };
    let mut parts: Vec<String> = Vec::new();
    for key in PRIORITY {
        if let Some(v) = obj.get(key).and_then(&render) {
            parts.push(format!("{key}={v}"));
        }
    }
    for (key, value) in obj {
        if parts.len() >= 3 {
            break;
        }
        if !PRIORITY.contains(&key.as_str())
            && let Some(v) = render(value)
        {
            parts.push(format!("{key}={v}"));
        }
    }
    if parts.is_empty() {
        return serde_json::Value::Null;
    }
    let mut brief = parts.join(" · ");
    if brief.chars().count() > 120 {
        brief = brief.chars().take(119).collect::<String>() + "…";
    }
    serde_json::Value::String(brief)
}
