//! Server-resolved citation RECEIPTS for agent answers — one implementation
//! for every product projection of the agent loop (HTTP /api/ask, MCP ask).
//! The verifier is fail-closed: fabricated references surface as
//! verified:false, ambiguous claim_id anchors get NO link.

use crate::verify::citations_in_order;
use ovp_domain::crystal::DurableRecord;
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

pub fn agent_citations(
    answer: &str,
    model: &IndexModel,
    records: &[DurableRecord],
) -> Vec<serde_json::Value> {
    citations_in_order(answer)
        .into_iter()
        .map(|key| {
            let (kind, id) = key.split_once(':').unwrap_or(("", key.as_str()));
            let id = citation_id_token(id);
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
                    serde_json::json!({
                        "id": key,
                        "kind": "claim",
                        "title": hit.map(|r| r.claim.chars().take(120).collect::<String>()),
                        "link_target": link,
                        "verified": hit.is_some(),
                    })
                }
                "source" => {
                    let hit = model.sources.iter().find(|s| s.sha256 == id);
                    serde_json::json!({
                        "id": key,
                        "kind": "source",
                        "title": hit.and_then(|s| s.title.clone()),
                        "link_target": hit.map(|s| format!("/library/{}", s.sha256)),
                        "verified": hit.is_some(),
                    })
                }
                _ => serde_json::json!({
                    "id": key,
                    "kind": kind,
                    "title": serde_json::Value::Null,
                    "link_target": serde_json::Value::Null,
                    "verified": false,
                }),
            }
        })
        .collect()
}

/// [`agent_citations`] without an index snapshot (fresh/unindexed vault):
/// claim receipts still resolve against the ledger; source references cannot
/// be verified and honestly say so.
pub fn agent_citations_unindexed(
    answer: &str,
    records: &[DurableRecord],
) -> Vec<serde_json::Value> {
    citations_in_order(answer)
        .into_iter()
        .map(|key| {
            let (kind, id) = key.split_once(':').unwrap_or(("", key.as_str()));
            let id = citation_id_token(id);
            if kind == "claim" {
                let hit = records.iter().find(|r| r.claim_key == id);
                let link = hit.and_then(|r| {
                    let same_id = records.iter().filter(|o| o.claim_id == r.claim_id).count();
                    (same_id == 1).then(|| format!("/knowledge#{}", r.claim_id))
                });
                serde_json::json!({
                    "id": key,
                    "kind": "claim",
                    "title": hit.map(|r| r.claim.chars().take(120).collect::<String>()),
                    "link_target": link,
                    "verified": hit.is_some(),
                })
            } else {
                serde_json::json!({
                    "id": key,
                    "kind": kind,
                    "title": serde_json::Value::Null,
                    "link_target": serde_json::Value::Null,
                    "verified": false,
                })
            }
        })
        .collect()
}
