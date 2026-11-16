//! The evidence closure of one durable claim — the payload behind
//! `ovp2 claim <key> --json`, the MCP `claim` tool, and the
//! `ovp://claim/<key>` resource. One function so the three surfaces cannot
//! drift.
//!
//! Lives here rather than in `ovp-index` on purpose: the closure carries the
//! bilingual projection (`bilingual::evaluate_claim_projection`), and
//! `ovp-memory` already depends on `ovp-index`. Hoisting it into the index
//! crate would be a dependency cycle.

use std::path::Path;

use ovp_domain::crystal::DurableRecord;
use ovp_index::IndexModel;
use serde_json::Value;

/// URI prefix accepted (and stripped) by [`find_record`].
pub const CLAIM_URI_PREFIX: &str = "ovp://claim/";

/// Why a claim lookup did not resolve to exactly one record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClosureError {
    /// Neither a `claim_key` nor a `claim_id` matched.
    NotFound { key: String },
    /// Several active records share the `claim_id`; the caller must use the
    /// `claim_key`. `claim_keys` lists the candidates in ledger order.
    Ambiguous {
        key: String,
        claim_keys: Vec<String>,
    },
}

impl std::fmt::Display for ClosureError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ClosureError::NotFound { key } => {
                write!(f, "No active claim with key or id `{key}`")
            }
            ClosureError::Ambiguous { key, claim_keys } => write!(
                f,
                "claim_id `{key}` is ambiguous ({} records) — use the claim_key: {}",
                claim_keys.len(),
                claim_keys.join(", ")
            ),
        }
    }
}

impl std::error::Error for ClosureError {}

/// Resolve `key` (a `claim_key`, `claim_id`, or `ovp://claim/<key>` URI)
/// against the active records. claim_key wins; claim_id is a convenience
/// alias resolved only when unambiguous.
pub fn find_record<'a>(
    records: &'a [DurableRecord],
    key: &str,
) -> Result<&'a DurableRecord, ClosureError> {
    let key = key.strip_prefix(CLAIM_URI_PREFIX).unwrap_or(key);
    if let Some(r) = records.iter().find(|r| r.claim_key == key) {
        return Ok(r);
    }
    let by_id: Vec<&DurableRecord> = records.iter().filter(|r| r.claim_id == key).collect();
    match by_id.as_slice() {
        [one] => Ok(one),
        [] => Err(ClosureError::NotFound {
            key: key.to_string(),
        }),
        many => Err(ClosureError::Ambiguous {
            key: key.to_string(),
            claim_keys: many.iter().map(|r| r.claim_key.clone()).collect(),
        }),
    }
}

/// The full evidence closure for one claim: text + gate verdicts + every
/// citation resolved to its source row (title/sha) when the index knows it.
/// `model` is optional: without an index every citation's `source` is null,
/// never an error (a read surface answers; `ovp2 index` is where it fails).
pub fn claim_closure(
    vault_root: &Path,
    record: &DurableRecord,
    model: Option<&IndexModel>,
) -> Value {
    // pack_dir basenames key claim↔source joins everywhere else too.
    let source_of = |case_id: &str| -> Value {
        let Some(m) = model else { return Value::Null };
        let sha = m
            .packs
            .iter()
            .find(|p| p.pack_dir.rsplit(['/', '\\']).next() == Some(case_id))
            .and_then(|p| p.source_sha256.clone());
        let Some(sha) = sha else { return Value::Null };
        let Some(src) = m.sources.iter().find(|s| s.sha256 == sha) else {
            return Value::Null;
        };
        serde_json::json!({
            "sha256": src.sha256,
            "title": src.title,
            "url": src.url,
            "uri": format!("ovp://source/{}", src.sha256),
        })
    };
    let view =
        crate::bilingual::evaluate_claim_projection(vault_root, &record.claim_key, &record.claim);
    serde_json::json!({
        "uri": format!("{CLAIM_URI_PREFIX}{}", record.claim_key),
        "claim_key": record.claim_key,
        "claim_id": record.claim_id,
        "claim": record.claim,
        "claim_zh": view.text_zh,
        "claim_zh_status": view.status,
        "theme": record.theme,
        "strength": record.strength,
        "provenance_score": record.provenance_score,
        "citations": record.citations.iter().map(|c| serde_json::json!({
            "case_id": c.case_id,
            "unit_id": c.unit_id,
            "quote": c.quote,
            "resolved_line": c.resolved_line,
            "source": source_of(&c.case_id),
        })).collect::<Vec<Value>>(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        CrystalStatus, DurableCitation, FinalClass, ProvenanceClass, StrengthClass,
    };
    use ovp_index::{INDEX_SCHEMA, IndexModel, PackRow, SourceRow, SourceStatus};

    fn record(key: &str, id: &str, case: &str) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: id.into(),
            claim: format!("claim text for {key}"),
            theme: "Agent memory".into(),
            theme_id: None,
            source_cases: vec![case.into()],
            citations: vec![DurableCitation {
                case_id: case.into(),
                unit_id: "u-1".into(),
                quote: "verbatim quote".into(),
                resolved_line: Some(12),
            }],
            provenance_score: 0.8,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "test".into(),
            final_class: FinalClass::Durable,
            run_id: "r1".into(),
            status: CrystalStatus::Active,
        }
    }

    fn model_with_pack(case: &str, sha: &str, title: &str) -> IndexModel {
        let mut source = SourceRow::blank(sha, SourceStatus::Processed);
        source.title = Some(title.into());
        source.url = Some("https://example.com/a".into());
        IndexModel {
            schema: INDEX_SCHEMA.into(),
            date: "2026-09-19".into(),
            built_at: None,
            run_id: None,
            totals: Default::default(),
            sources: vec![source],
            packs: vec![PackRow {
                pack_dir: format!("50-Inbox/03-Reader/{case}"),
                title: title.into(),
                date: None,
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec![],
                source_sha256: Some(sha.into()),
            }],
            claims: vec![],
            runs: vec![],
            ops: Default::default(),
        }
    }

    #[test]
    fn find_record_accepts_key_id_and_uri() {
        let records = vec![record("ck-aaa", "id-a", "case-1")];
        for key in ["ck-aaa", "id-a", "ovp://claim/ck-aaa"] {
            assert_eq!(
                find_record(&records, key).unwrap().claim_key,
                "ck-aaa",
                "{key}"
            );
        }
    }

    #[test]
    fn find_record_not_found_message() {
        let records = vec![record("ck-aaa", "id-a", "case-1")];
        let err = find_record(&records, "nope").unwrap_err();
        assert_eq!(err, ClosureError::NotFound { key: "nope".into() });
        assert_eq!(err.to_string(), "No active claim with key or id `nope`");
    }

    #[test]
    fn find_record_ambiguous_lists_candidate_keys() {
        let records = vec![record("ck-one", "dup", "c1"), record("ck-two", "dup", "c2")];
        let err = find_record(&records, "dup").unwrap_err();
        assert_eq!(
            err,
            ClosureError::Ambiguous {
                key: "dup".into(),
                claim_keys: vec!["ck-one".into(), "ck-two".into()]
            }
        );
        assert_eq!(
            err.to_string(),
            "claim_id `dup` is ambiguous (2 records) — use the claim_key: ck-one, ck-two"
        );
    }

    #[test]
    fn closure_resolves_sources_through_pack_dir_basename() {
        let tmp = tempfile::tempdir().unwrap();
        let rec = record("ck-aaa", "id-a", "case-1");
        let model = model_with_pack("case-1", "deadbeef", "A title");
        let v = claim_closure(tmp.path(), &rec, Some(&model));
        assert_eq!(v["uri"], "ovp://claim/ck-aaa");
        assert_eq!(v["claim_zh_status"], "missing");
        assert_eq!(v["strength"], "supported");
        let src = &v["citations"][0]["source"];
        assert_eq!(src["sha256"], "deadbeef");
        assert_eq!(src["title"], "A title");
        assert_eq!(src["uri"], "ovp://source/deadbeef");
    }

    #[test]
    fn closure_without_index_or_unknown_pack_yields_null_source() {
        let tmp = tempfile::tempdir().unwrap();
        let rec = record("ck-aaa", "id-a", "case-1");
        let none = claim_closure(tmp.path(), &rec, None);
        assert!(none["citations"][0]["source"].is_null());
        let other = model_with_pack("case-9", "cafe", "Other");
        let miss = claim_closure(tmp.path(), &rec, Some(&other));
        assert!(miss["citations"][0]["source"].is_null());
        // Everything but `source` is identical regardless of the index.
        let mut a = none.clone();
        let mut b = miss.clone();
        a["citations"][0]["source"] = Value::Null;
        b["citations"][0]["source"] = Value::Null;
        assert_eq!(a, b);
    }
}
