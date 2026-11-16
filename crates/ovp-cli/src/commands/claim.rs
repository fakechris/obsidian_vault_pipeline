//! `ovp2 claim <key> [--json]` — the evidence closure of one durable claim
//! on the CLI. Same loader, same lookup, same payload as the MCP `claim`
//! tool and the `ovp://claim/<key>` resource (`ovp_memory::closure`), so a
//! script or CI check gets exactly what an agent gets.

use std::path::{Path, PathBuf};

use ovp_domain::VaultLayout;
use ovp_memory::closure::{claim_closure, find_record};
use serde_json::Value;

use crate::CliError;

pub struct ClaimArgs {
    pub vault_root: PathBuf,
    /// A `claim_key`, a `claim_id` (when unambiguous), or an
    /// `ovp://claim/<key>` URI.
    pub key: String,
    pub json: bool,
}

pub fn run(args: ClaimArgs) -> Result<(), CliError> {
    let closure = closure_json(&args.vault_root, &args.key)?;
    if args.json {
        let text =
            serde_json::to_string_pretty(&closure).map_err(|e| CliError::Io(e.to_string()))?;
        println!("{text}");
    } else {
        print!("{}", render_human(&closure));
    }
    Ok(())
}

/// The closure payload for `key`, resolved the way MCP resolves it: ACTIVE
/// records with human patches overlaid, index optional (a missing index
/// leaves every citation's `source` null rather than failing).
pub fn closure_json(vault_root: &Path, key: &str) -> Result<Value, CliError> {
    let layout = VaultLayout::new();
    let records = ovp_api_projection::readers::load_active_records(vault_root, &layout);
    let record = find_record(&records, key.trim()).map_err(|e| CliError::Io(e.to_string()))?;
    let model = ovp_index::read_index(vault_root).ok();
    Ok(claim_closure(vault_root, record, model.as_ref()))
}

/// Compact operator rendering: identity, gates, text, then one line per
/// citation. Everything shown is in the JSON; nothing here is authoritative.
pub fn render_human(closure: &Value) -> String {
    let s = |k: &str| closure[k].as_str().unwrap_or("").to_string();
    let mut out = String::new();
    out.push_str(&format!("{}  (id {})\n", s("claim_key"), s("claim_id")));
    out.push_str(&format!(
        "theme: {} · strength: {} · provenance: {:.2}\n",
        s("theme"),
        s("strength"),
        closure["provenance_score"].as_f64().unwrap_or(0.0)
    ));
    out.push_str(&format!("claim: {}\n", s("claim")));
    match closure["claim_zh"].as_str() {
        Some(zh) if !zh.is_empty() => {
            out.push_str(&format!("claim_zh: {zh} [{}]\n", s("claim_zh_status")))
        }
        _ => out.push_str(&format!("claim_zh: (none) [{}]\n", s("claim_zh_status"))),
    }
    let citations = closure["citations"].as_array().cloned().unwrap_or_default();
    out.push_str(&format!("citations ({}):\n", citations.len()));
    for c in &citations {
        let line = match c["resolved_line"].as_u64() {
            Some(n) => format!("line {n}"),
            None => "line ?".to_string(),
        };
        let source = match c["source"].as_object() {
            Some(src) => src
                .get("title")
                .and_then(Value::as_str)
                .filter(|t| !t.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| {
                    format!(
                        "sha256 {}",
                        src.get("sha256").and_then(Value::as_str).unwrap_or("?")
                    )
                }),
            None => "(source not in index)".to_string(),
        };
        out.push_str(&format!(
            "  {} · {line} · {source}\n",
            c["case_id"].as_str().unwrap_or("?")
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        CrystalStatus, DurableCitation, DurableRecord, FinalClass, ProvenanceClass, StoreEvent,
        StoreOp, StrengthClass,
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

    /// A vault with a crystal ledger (two claims, one ambiguous id pair) and
    /// an index whose pack for `case-1` resolves to a titled source.
    fn fixture_vault() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        let layout = VaultLayout::new();
        let store = root.join(layout.crystal_store_dir());
        std::fs::create_dir_all(&store).unwrap();
        let events = [
            record("ck-aaa", "id-a", "case-1"),
            record("ck-bbb", "id-b", "case-2"),
            record("ck-one", "dup", "c1"),
            record("ck-two", "dup", "c2"),
        ]
        .into_iter()
        .map(|record| StoreEvent {
            op: StoreOp::Write,
            record,
            supersedes: None,
            reason: None,
        })
        .map(|e| serde_json::to_string(&e).unwrap() + "\n")
        .collect::<String>();
        std::fs::write(store.join("ledger.jsonl"), events).unwrap();

        let mut source = SourceRow::blank("deadbeef", SourceStatus::Processed);
        source.title = Some("A title".into());
        source.url = Some("https://example.com/a".into());
        let model = IndexModel {
            schema: INDEX_SCHEMA.into(),
            date: "2026-09-19".into(),
            built_at: None,
            run_id: None,
            totals: Default::default(),
            sources: vec![source],
            packs: vec![PackRow {
                pack_dir: "50-Inbox/03-Reader/case-1".into(),
                title: "A title".into(),
                date: None,
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec![],
                source_sha256: Some("deadbeef".into()),
            }],
            claims: vec![],
            runs: vec![],
            ops: Default::default(),
        };
        ovp_index::write_index(root, &model).unwrap();
        tmp
    }

    /// Drive the real MCP server over stdio for one `claim` call and return
    /// the tool's text payload.
    fn mcp_claim_text(root: &Path, key: &str) -> String {
        let req = serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": { "name": "claim", "arguments": { "key": key } }
        })
        .to_string()
            + "\n";
        let mut out: Vec<u8> = Vec::new();
        ovp_mcp::run_mcp_io(
            ovp_mcp::McpConfig {
                vault_root: root.to_path_buf(),
                ask_client: None,
            },
            std::io::Cursor::new(req),
            &mut out,
        )
        .unwrap();
        let resp: Value = serde_json::from_str(String::from_utf8(out).unwrap().trim()).unwrap();
        assert!(resp.get("error").is_none(), "{resp}");
        resp["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .to_string()
    }

    #[test]
    fn cli_json_equals_the_mcp_claim_payload() {
        let tmp = fixture_vault();
        for key in ["ck-aaa", "id-a", "ovp://claim/ck-aaa"] {
            let cli = closure_json(tmp.path(), key).unwrap();
            let cli_text = serde_json::to_string_pretty(&cli).unwrap();
            let mcp_text = mcp_claim_text(tmp.path(), key);
            assert_eq!(cli_text, mcp_text, "payload drift for `{key}`");
        }
        // And the join actually happened (not two identical nulls).
        let cli = closure_json(tmp.path(), "ck-aaa").unwrap();
        assert_eq!(cli["citations"][0]["source"]["title"], "A title");
        assert_eq!(
            cli["citations"][0]["source"]["uri"],
            "ovp://source/deadbeef"
        );
        // A claim whose pack is not in the index still answers, with null.
        let cli = closure_json(tmp.path(), "ck-bbb").unwrap();
        assert!(cli["citations"][0]["source"].is_null());
    }

    #[test]
    fn missing_index_is_not_an_error() {
        let tmp = fixture_vault();
        std::fs::remove_dir_all(tmp.path().join(".ovp/index")).unwrap();
        let cli = closure_json(tmp.path(), "ck-aaa").unwrap();
        assert_eq!(cli["claim_key"], "ck-aaa");
        assert!(cli["citations"][0]["source"].is_null());
    }

    #[test]
    fn unknown_and_ambiguous_keys_fail_with_the_shared_message() {
        let tmp = fixture_vault();
        let err = closure_json(tmp.path(), "nope").unwrap_err().to_string();
        assert!(
            err.contains("No active claim with key or id `nope`"),
            "{err}"
        );
        let err = closure_json(tmp.path(), "dup").unwrap_err().to_string();
        assert!(err.contains("ambiguous (2 records)"), "{err}");
        assert!(err.contains("ck-one") && err.contains("ck-two"), "{err}");
    }

    #[test]
    fn human_rendering_lists_one_line_per_citation() {
        let tmp = fixture_vault();
        let text = render_human(&closure_json(tmp.path(), "ck-aaa").unwrap());
        assert!(text.starts_with("ck-aaa  (id id-a)\n"), "{text}");
        assert!(
            text.contains("strength: supported · provenance: 0.80"),
            "{text}"
        );
        assert!(text.contains("claim_zh: (none) [missing]"), "{text}");
        assert!(
            text.contains("citations (1):\n  case-1 · line 12 · A title\n"),
            "{text}"
        );
        let text = render_human(&closure_json(tmp.path(), "ck-bbb").unwrap());
        assert!(
            text.contains("  case-2 · line 12 · (source not in index)\n"),
            "{text}"
        );
    }
}
