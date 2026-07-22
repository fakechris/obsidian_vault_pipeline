//! `ovp-mcp` — synchronous stdio JSON-RPC server implementing the Model
//! Context Protocol (MCP). No async runtime. Reads newline-delimited JSON-RPC
//! from stdin, dispatches to OVP tools, writes responses to stdout.

use std::io::{self, BufRead, Write};
use std::path::PathBuf;

use ovp_api_projection::{bodies, readers};
use ovp_domain::VaultLayout;
use ovp_domain::crystal::DurableRecord;
use ovp_domain::crystal::theme_pages::ThemePagesFile;
use ovp_domain::tags::TagAliases;
use ovp_index::{IndexModel, Query, QueryKind, read_index, run_query};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// LLM client factory for the `ask` tool — same contract as the server's
/// `ServeConfig::ask_client`: `None` means no live LLM is configured and
/// `ask` answers with a clear configuration error instead of failing
/// silently. The factory builds a fresh (cassette-recording) client per ask.
pub type AskClientFactory =
    std::sync::Arc<dyn Fn() -> Result<Box<dyn ovp_llm::ModelClient>, String> + Send + Sync>;

pub struct McpConfig {
    pub vault_root: PathBuf,
    pub ask_client: Option<AskClientFactory>,
}

#[derive(Deserialize)]
struct RpcRequest {
    jsonrpc: String,
    id: Option<Value>,
    method: String,
    #[serde(default)]
    params: Value,
}

#[derive(Serialize)]
struct RpcResponse {
    jsonrpc: &'static str,
    id: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<RpcError>,
}

#[derive(Debug, Serialize)]
struct RpcError {
    code: i32,
    message: String,
}

struct McpState {
    vault_root: PathBuf,
    layout: VaultLayout,
    ask_client: Option<AskClientFactory>,
}

impl McpState {
    fn load_model(&self) -> Option<IndexModel> {
        read_index(&self.vault_root).ok()
    }

    /// ACTIVE durable records with display themes applied — the same fold the
    /// live server uses (`readers::load_active_records`); corrupt state
    /// degrades to empty (read tools answer, `ovp2 index` fails loud).
    fn load_records(&self) -> Vec<DurableRecord> {
        readers::load_active_records(&self.vault_root, &self.layout)
    }

    fn load_theme_pages(&self) -> Option<ThemePagesFile> {
        let path = self
            .vault_root
            .join(self.layout.crystal_store_dir())
            .join("theme_pages.json");
        ThemePagesFile::load(&path).ok().flatten()
    }
}

/// Resolve `key` (a `claim_key`, `claim_id`, or `ovp://claim/<key>` URI)
/// against the active records. claim_key wins; claim_id is a convenience
/// alias resolved only when unambiguous.
fn find_record<'a>(records: &'a [DurableRecord], key: &str) -> Result<&'a DurableRecord, RpcError> {
    let key = key.strip_prefix("ovp://claim/").unwrap_or(key);
    if let Some(r) = records.iter().find(|r| r.claim_key == key) {
        return Ok(r);
    }
    let by_id: Vec<&DurableRecord> = records.iter().filter(|r| r.claim_id == key).collect();
    match by_id.as_slice() {
        [one] => Ok(one),
        [] => Err(RpcError {
            code: -32602,
            message: format!("No active claim with key or id `{key}`"),
        }),
        many => Err(RpcError {
            code: -32602,
            message: format!(
                "claim_id `{key}` is ambiguous ({} records) — use the claim_key: {}",
                many.len(),
                many.iter()
                    .map(|r| r.claim_key.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }),
    }
}

/// The full evidence closure for one claim: text + gate verdicts + every
/// citation resolved to its source row (title/sha) when the index knows it.
/// This is the payload behind both the `claim` tool and `ovp://claim/<key>`.
fn claim_closure(record: &DurableRecord, model: Option<&IndexModel>) -> Value {
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
    serde_json::json!({
        "uri": format!("ovp://claim/{}", record.claim_key),
        "claim_key": record.claim_key,
        "claim_id": record.claim_id,
        "claim": record.claim,
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

pub fn run_mcp(config: McpConfig) -> Result<(), String> {
    let state = McpState {
        vault_root: config.vault_root,
        layout: VaultLayout::new(),
        ask_client: config.ask_client,
    };

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }

        let req: RpcRequest = match serde_json::from_str(&line) {
            Ok(r) => r,
            Err(e) => {
                let resp = RpcResponse {
                    jsonrpc: "2.0",
                    id: Value::Null,
                    result: None,
                    error: Some(RpcError {
                        code: -32700,
                        message: format!("Parse error: {e}"),
                    }),
                };
                write_response(&mut out, &resp);
                continue;
            }
        };

        if req.jsonrpc != "2.0" {
            let resp = RpcResponse {
                jsonrpc: "2.0",
                id: req.id.unwrap_or(Value::Null),
                result: None,
                error: Some(RpcError {
                    code: -32600,
                    message: "Invalid jsonrpc version".into(),
                }),
            };
            write_response(&mut out, &resp);
            continue;
        }

        let id = req.id.unwrap_or(Value::Null);
        let result = dispatch(&state, &req.method, &req.params);

        let resp = match result {
            Ok(val) => RpcResponse {
                jsonrpc: "2.0",
                id,
                result: Some(val),
                error: None,
            },
            Err(e) => RpcResponse {
                jsonrpc: "2.0",
                id,
                result: None,
                error: Some(e),
            },
        };
        write_response(&mut out, &resp);
    }

    Ok(())
}

fn write_response(out: &mut impl Write, resp: &RpcResponse) {
    if let Ok(json) = serde_json::to_string(resp) {
        let _ = writeln!(out, "{json}");
        let _ = out.flush();
    }
}

fn dispatch(state: &McpState, method: &str, params: &Value) -> Result<Value, RpcError> {
    match method {
        "initialize" => handle_initialize(),
        "tools/list" => handle_tools_list(),
        "tools/call" => handle_tools_call(state, params),
        "resources/list" => handle_resources_list(),
        "resources/templates/list" => handle_resources_templates_list(),
        "resources/read" => handle_resources_read(state, params),
        "notifications/initialized" | "notifications/cancelled" => Ok(Value::Null),
        _ => Err(RpcError {
            code: -32601,
            message: format!("Method not found: {method}"),
        }),
    }
}

fn handle_initialize() -> Result<Value, RpcError> {
    Ok(serde_json::json!({
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
            "resources": {}
        },
        "serverInfo": {
            "name": "ovp-mcp",
            "version": "0.1.0"
        }
    }))
}

fn handle_tools_list() -> Result<Value, RpcError> {
    Ok(serde_json::json!({
        "tools": [
            {
                "name": "find",
                "description": "Query the OVP index: sources, packs, claims, runs, tags, entities. Filter by kind, status, date, tag, entity, or free-text term.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "term": { "type": "string", "description": "Free-text search term" },
                        "kind": { "type": "string", "enum": ["sources", "packs", "claims", "runs", "tags", "entities"] },
                        "status": { "type": "string" },
                        "date": { "type": "string", "description": "Date prefix (YYYY or YYYY-MM or YYYY-MM-DD)" },
                        "tag": { "type": "string", "description": "Canonical tag filter over sources (kind=tags lists the vocabulary)" },
                        "entity": { "type": "string", "description": "URL entity id filter over sources, e.g. github:owner/repo (kind=entities lists the index)" }
                    }
                }
            },
            {
                "name": "search",
                "description": "Full-text search across OVP product state (sources, packs, claims).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": { "type": "string", "description": "Search query" }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "ask",
                "description": "Ask a question answered FROM THE VAULT'S EVIDENCE (active durable claims, cards, verbatim units). The answer carries inline citations and a deterministic report of how many citation IDs resolve against the supplied evidence (ID resolution — it does not prove every sentence is cited). Prefer this over answering from memory for anything the vault may cover; audit any [claim:…] citation with the `claim` tool.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": { "type": "string", "description": "The question to answer from vault evidence" }
                    },
                    "required": ["question"]
                }
            },
            {
                "name": "claim",
                "description": "Read one durable claim's FULL evidence closure: claim text, gate verdicts, and every citation resolved to its verbatim quote, line, and source (title/sha/url). Accepts a claim_key (ck-…), a claim_id, or an ovp://claim/<key> URI. This is how an answer's [claim:…] citation is audited.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": { "type": "string", "description": "claim_key (preferred, stable), claim_id, or ovp://claim/<key>" }
                    },
                    "required": ["key"]
                }
            },
            {
                "name": "theme_page",
                "description": "Read one grounded topic page (wiki-style narrative woven from durable claims; every sentence carries a [claim:<key>] citation resolvable via the `claim` tool). Lists all pages when no theme is given.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "theme": { "type": "string", "description": "Theme label (exact) or community id (t000 / 0). Omit to list available pages." }
                    }
                }
            },
            {
                "name": "doctor",
                "description": "Run health checks over OVP vault state.",
                "inputSchema": { "type": "object", "properties": {} }
            },
            {
                "name": "status",
                "description": "Get OVP pipeline status: totals, recent runs, blocked sources.",
                "inputSchema": { "type": "object", "properties": {} }
            }
        ]
    }))
}

fn handle_tools_call(state: &McpState, params: &Value) -> Result<Value, RpcError> {
    let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let arguments = params
        .get("arguments")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));

    match name {
        "find" => tool_find(state, &arguments),
        "search" => tool_search(state, &arguments),
        "ask" => tool_ask(state, &arguments),
        "claim" => tool_claim(state, &arguments),
        "theme_page" => tool_theme_page(state, &arguments),
        "status" => tool_status(state),
        "doctor" => tool_doctor(state),
        _ => Err(RpcError {
            code: -32602,
            message: format!("Unknown tool: {name}"),
        }),
    }
}

fn tool_ask(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let question = args
        .get("question")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| RpcError {
            code: -32602,
            message: "`question` is required".into(),
        })?;
    // Explicit configuration error, never a silent degrade: an agent must be
    // able to tell "no LLM wired" from "the vault has no answer".
    let Some(factory) = &state.ask_client else {
        return Err(RpcError {
            code: -32000,
            message: "ask is not configured — run ovp2 built with `--features anthropic` \
                      and set ANTHROPIC_API_KEY in the MCP server's environment"
                .into(),
        });
    };
    let mut model = state.load_model().ok_or_else(|| RpcError {
        code: -32000,
        message: "Index not available — run `ovp2 index` first".into(),
    })?;
    // This tool promises answers grounded in DURABLE claims — caveated,
    // superseded, and retracted rows must not be retrievable here (codex
    // P1; they also could not be audited by the active-record `claim` tool).
    model
        .claims
        .retain(|c| c.status == ovp_index::ClaimStatus::Durable);
    // Missing evidence.json = a fresh/claims-only vault (fine, noted);
    // a CORRUPT sidecar is surfaced, never silently downgraded to
    // claims-only while the report implies full evidence (codex P2).
    // Existence is checked BEFORE the read so a read failure on a present
    // file is always classified as corruption, not misfiled as missing.
    let (evidence, degraded_note) = if !ovp_index::evidence::evidence_path(&state.vault_root)
        .exists()
    {
        (
            None,
            Some("note: no evidence sidecar — answer drawn from claims only (no cards/units)"),
        )
    } else {
        match ovp_index::read_evidence(&state.vault_root) {
            Ok(e) => (Some(e), None),
            Err(e) => {
                return Err(RpcError {
                    code: -32000,
                    message: format!(
                        "evidence sidecar unreadable ({e}) — rebuild with `ovp2 index` before asking"
                    ),
                });
            }
        }
    };
    let mut client = factory().map_err(|e| RpcError {
        code: -32000,
        message: format!("ask client configuration invalid: {e}"),
    })?;
    let ask_args = ovp_memory::ask::AskArgs {
        question: question.to_string(),
        verify_citations: true,
        save_chat: false,
        ..Default::default()
    };
    let result = ovp_memory::ask::ask_with_optional_evidence(
        &model,
        evidence.as_ref(),
        client.as_mut(),
        &ask_args,
        &state.vault_root,
    )
    .map_err(|e| RpcError {
        code: -32000,
        message: format!("ask failed: {e}"),
    })?;

    // Answer first, then the deterministic verification report — the agent
    // sees HOW grounded the answer is, not just the prose. The report checks
    // that citation IDs resolve against the supplied evidence; it does NOT
    // prove every sentence is cited (that gate exists only for theme pages).
    let mut text = result.answer.trim().to_string();
    if let Some(v) = &result.verification {
        text.push_str(&format!(
            "\n\n---\nverification (citation-ID resolution, not per-sentence): \
             {} citation(s), {} resolved against supplied evidence",
            v.cited, v.verified
        ));
        if !v.missing.is_empty() {
            text.push_str(&format!("; UNRESOLVED: {}", v.missing.join(", ")));
        }
        if !v.warnings.is_empty() {
            text.push_str(&format!("; warnings: {}", v.warnings.join(", ")));
        }
        text.push_str(
            "\naudit any [claim:<key>] citation with the `claim` tool \
             (accepts ck- claim keys, claim ids, and ovp://claim/ URIs)",
        );
    }
    if let Some(note) = degraded_note {
        text.push('\n');
        text.push_str(note);
    }
    Ok(serde_json::json!({ "content": [{ "type": "text", "text": text }] }))
}

fn tool_claim(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let key = args
        .get("key")
        .and_then(|v| v.as_str())
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| RpcError {
            code: -32602,
            message: "`key` is required".into(),
        })?;
    let records = state.load_records();
    let record = find_record(&records, key.trim())?;
    let model = state.load_model();
    let text = serde_json::to_string_pretty(&claim_closure(record, model.as_ref()))
        .unwrap_or_else(|_| "{}".into());
    Ok(serde_json::json!({ "content": [{ "type": "text", "text": text }] }))
}

/// One theme page + ONLY its claims from the lookup, so the closure travels
/// with the narrative. `theme` accepts a label (exact), `t003`, `3`, or an
/// `ovp://theme-page/<id>` URI — the payload behind both the `theme_page`
/// tool and the `ovp://theme-page/<id>` resource.
fn theme_page_payload(state: &McpState, theme: &str) -> Result<Value, RpcError> {
    let pages = state.load_theme_pages();
    let records = state.load_records();
    let body = bodies::theme_pages_body(pages.as_ref(), &records);
    let all = body["pages"].as_array().cloned().unwrap_or_default();

    let theme = theme.strip_prefix("ovp://theme-page/").unwrap_or(theme);
    // `t003` / `3` → community id; anything else matches the label exactly.
    let by_id = theme.strip_prefix('t').unwrap_or(theme).parse::<i64>().ok();
    let page = all.iter().find(|p| {
        by_id.is_some_and(|id| p["community_id"].as_i64() == Some(id))
            || p["label"].as_str() == Some(theme)
    });
    let Some(page) = page else {
        return Err(RpcError {
            code: -32602,
            message: format!(
                "No topic page for `{theme}` — call theme_page without arguments to list pages"
            ),
        });
    };
    let keys: std::collections::BTreeSet<String> = page["sections"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|s| s["body"].as_str())
        .flat_map(ovp_domain::crystal::theme_pages::extract_claim_citations)
        .collect();
    let claims: serde_json::Map<String, Value> = body["claims"]
        .as_object()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|(k, _)| keys.contains(k))
        .collect();
    Ok(serde_json::json!({ "page": page, "claims": claims }))
}

fn tool_theme_page(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let theme = args
        .get("theme")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let Some(theme) = theme else {
        // No theme → the directory of available pages.
        let pages = state.load_theme_pages();
        let records = state.load_records();
        let body = bodies::theme_pages_body(pages.as_ref(), &records);
        let listing: Vec<Value> = body["pages"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|p| {
                serde_json::json!({
                    "community_id": p["community_id"],
                    "label": p["label"],
                    "label_zh": p["label_zh"],
                    "claim_count": p["claim_count"],
                    "uri": format!("ovp://theme-page/{}", p["community_id"]),
                })
            })
            .collect();
        let text = serde_json::to_string_pretty(&listing).unwrap_or_else(|_| "[]".into());
        return Ok(serde_json::json!({ "content": [{ "type": "text", "text": text }] }));
    };
    let out = theme_page_payload(state, theme)?;
    let text = serde_json::to_string_pretty(&out).unwrap_or_else(|_| "{}".into());
    Ok(serde_json::json!({ "content": [{ "type": "text", "text": text }] }))
}

fn tool_find(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let model = state.load_model().ok_or_else(|| RpcError {
        code: -32000,
        message: "Index not available".into(),
    })?;

    // A queried alias resolves to its canonical tag, same as `ovp2 find`.
    // A broken alias table degrades to normalize-only here (a read tool
    // should answer, not crash); `ovp2 index` is where breakage fails loud.
    let tag = args.get("tag").and_then(|v| v.as_str()).map(|raw| {
        let aliases = TagAliases::load(&state.vault_root).unwrap_or_default();
        aliases.resolve_raw(raw).unwrap_or_else(|| raw.to_string())
    });
    let query = Query {
        kind: args
            .get("kind")
            .and_then(|v| v.as_str())
            .and_then(|k| match k {
                "sources" => Some(QueryKind::Sources),
                "packs" => Some(QueryKind::Packs),
                "claims" => Some(QueryKind::Claims),
                "runs" => Some(QueryKind::Runs),
                "tags" => Some(QueryKind::Tags),
                "entities" => Some(QueryKind::Entities),
                _ => None,
            }),
        status: args
            .get("status")
            .and_then(|v| v.as_str())
            .map(String::from),
        date: args.get("date").and_then(|v| v.as_str()).map(String::from),
        term: args.get("term").and_then(|v| v.as_str()).map(String::from),
        tag,
        entity: args
            .get("entity")
            .and_then(|v| v.as_str())
            .map(String::from),
    };

    let hits = run_query(&model, &query);
    let text = serde_json::to_string_pretty(&hits).unwrap_or_else(|_| "[]".into());

    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_search(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let model = state.load_model().ok_or_else(|| RpcError {
        code: -32000,
        message: "Index not available".into(),
    })?;

    let term = args.get("query").and_then(|v| v.as_str()).map(String::from);
    let query = Query {
        kind: None,
        status: None,
        date: None,
        term,
        tag: None,
        entity: None,
    };
    let hits = run_query(&model, &query);
    let text = serde_json::to_string_pretty(&hits).unwrap_or_else(|_| "[]".into());

    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_status(state: &McpState) -> Result<Value, RpcError> {
    let model = state.load_model().ok_or_else(|| RpcError {
        code: -32000,
        message: "Index not available".into(),
    })?;

    let text = format!(
        "OVP Status (index date: {})\n\
         Sources: {} (queued={}, processed={}, failed={}, blocked={})\n\
         Packs: {}\n\
         Claims: durable={}, caveated={}\n\
         Runs: {}\n\
         Queue depth: {}\n\
         Blocked sources: {}",
        model.date,
        model.totals.sources,
        model.totals.queued,
        model.totals.processed,
        model.totals.failed,
        model.totals.blocked,
        model.totals.packs,
        model.totals.claims_durable,
        model.totals.claims_caveated,
        model.totals.runs,
        model.ops.queue_depth,
        model.ops.blocked_sources.len(),
    );

    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_doctor(state: &McpState) -> Result<Value, RpcError> {
    let model = state.load_model();
    let mut findings = Vec::new();

    match model.as_ref() {
        None => {
            findings.push("FAIL: Index not available — run `ovp2 index` first.");
        }
        Some(m) => {
            if m.ops.blocked_sources.is_empty() {
                findings.push("OK: No blocked sources.");
            } else {
                findings.push("WARN: Blocked sources present (see `find --status blocked`).");
            }
            findings.push("OK: Index readable.");
        }
    }

    let text = findings.join("\n");
    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn handle_resources_list() -> Result<Value, RpcError> {
    Ok(serde_json::json!({
        "resources": [
            {
                "uri": "ovp://index",
                "name": "OVP Index",
                "description": "The full product index (JSON)",
                "mimeType": "application/json"
            },
            {
                "uri": "ovp://working-memory",
                "name": "Working Memory",
                "description": "Today's working memory context package",
                "mimeType": "text/markdown"
            },
        ]
    }))
}

/// Dynamic (parameterized) resources are advertised as RFC 6570 URI
/// templates, not fake concrete entries — a client must never receive a
/// listed URI that `resources/read` cannot dereference.
fn handle_resources_templates_list() -> Result<Value, RpcError> {
    Ok(serde_json::json!({
        "resourceTemplates": [
            {
                "uriTemplate": "ovp://claim/{claim_key}",
                "name": "Durable claim (stable reference)",
                "description": "One durable claim's full evidence closure — same payload as the `claim` tool. claim_keys (ck-…) are deterministic and survive re-runs, so these URIs are safe to store in notes and answers.",
                "mimeType": "application/json"
            },
            {
                "uriTemplate": "ovp://source/{sha256}",
                "name": "Source document (stable reference)",
                "description": "One captured source's metadata and markdown body, addressed by content sha256.",
                "mimeType": "application/json"
            },
            {
                "uriTemplate": "ovp://theme-page/{community_id}",
                "name": "Grounded topic page",
                "description": "One theme's topic page with its claims lookup — same payload as the `theme_page` tool. The `theme_page` tool called without arguments lists these URIs.",
                "mimeType": "application/json"
            }
        ]
    }))
}

fn handle_resources_read(state: &McpState, params: &Value) -> Result<Value, RpcError> {
    let uri = params.get("uri").and_then(|v| v.as_str()).unwrap_or("");

    match uri {
        "ovp://index" => {
            let model = state.load_model().ok_or_else(|| RpcError {
                code: -32000,
                message: "Index not available".into(),
            })?;
            let json = serde_json::to_string(&model).unwrap_or_else(|_| "{}".into());
            Ok(serde_json::json!({
                "contents": [{ "uri": uri, "mimeType": "application/json", "text": json }]
            }))
        }
        "ovp://working-memory" => {
            let wm_path = state.vault_root.join(".ovp/working-memory.md");
            let text = std::fs::read_to_string(&wm_path)
                .unwrap_or_else(|_| "(working memory not yet generated — run `ovp2 daily`)".into());
            Ok(serde_json::json!({
                "contents": [{ "uri": uri, "mimeType": "text/markdown", "text": text }]
            }))
        }
        _ if uri.starts_with("ovp://claim/") => {
            let records = state.load_records();
            let record = find_record(&records, uri)?;
            let model = state.load_model();
            let json = serde_json::to_string(&claim_closure(record, model.as_ref()))
                .unwrap_or_else(|_| "{}".into());
            Ok(serde_json::json!({
                "contents": [{ "uri": uri, "mimeType": "application/json", "text": json }]
            }))
        }
        _ if uri.starts_with("ovp://theme-page/") => {
            let payload = theme_page_payload(state, uri)?;
            let json = serde_json::to_string(&payload).unwrap_or_else(|_| "{}".into());
            Ok(serde_json::json!({
                "contents": [{ "uri": uri, "mimeType": "application/json", "text": json }]
            }))
        }
        _ if uri.starts_with("ovp://source/") => {
            let sha = uri.strip_prefix("ovp://source/").unwrap_or(uri);
            let model = state.load_model().ok_or_else(|| RpcError {
                code: -32000,
                message: "Index not available".into(),
            })?;
            let src = model
                .sources
                .iter()
                .find(|s| s.sha256 == sha)
                .ok_or_else(|| RpcError {
                    code: -32602,
                    message: format!("No source with sha256 `{sha}`"),
                })?;
            let (doc, truncated, err) =
                readers::read_source_doc(&state.vault_root, &state.layout, src.rel_path.as_deref());
            let json = serde_json::to_string(&serde_json::json!({
                "uri": uri,
                "source": src,
                "markdown": doc,
                "truncated": truncated,
                "doc_error": err,
            }))
            .unwrap_or_else(|_| "{}".into());
            Ok(serde_json::json!({
                "contents": [{ "uri": uri, "mimeType": "application/json", "text": json }]
            }))
        }
        _ => Err(RpcError {
            code: -32602,
            message: format!("Unknown resource: {uri}"),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        CrystalStatus, DurableCitation, FinalClass, ProvenanceClass, StoreEvent, StoreOp,
        StrengthClass,
    };

    fn record(key: &str, id: &str, case: &str) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: id.into(),
            claim: format!("claim text for {key}"),
            theme: "Agent memory".into(),
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

    /// A vault with a two-claim ledger and a one-page theme_pages.json.
    fn fixture_vault() -> (tempfile::TempDir, McpState) {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let layout = VaultLayout::new();
        let store = root.join(layout.crystal_store_dir());
        std::fs::create_dir_all(&store).unwrap();
        let events = [
            StoreEvent {
                op: StoreOp::Write,
                record: record("ck-aaa", "id-a", "case-1"),
                supersedes: None,
                reason: None,
            },
            StoreEvent {
                op: StoreOp::Write,
                record: record("ck-bbb", "id-b", "case-2"),
                supersedes: None,
                reason: None,
            },
        ];
        let ledger: String = events
            .iter()
            .map(|e| serde_json::to_string(e).unwrap() + "\n")
            .collect();
        std::fs::write(store.join("ledger.jsonl"), ledger).unwrap();
        std::fs::write(
            store.join("theme_pages.json"),
            serde_json::json!({
                "schema": "ovp.theme_pages/v1",
                "pages": [{
                    "community_id": 0,
                    "label": "Agent memory",
                    "label_zh": "智能体记忆",
                    "claim_keys": ["ck-aaa", "ck-bbb"],
                    "sections": [{"heading": "H",
                                   "body": "One [claim:ck-aaa]. Two [claim:ck-bbb]."}]
                }]
            })
            .to_string(),
        )
        .unwrap();
        let state = McpState {
            vault_root: root,
            layout,
            ask_client: None,
        };
        (tmp, state)
    }

    fn call(state: &McpState, tool: &str, args: serde_json::Value) -> Result<Value, RpcError> {
        dispatch(
            state,
            "tools/call",
            &serde_json::json!({ "name": tool, "arguments": args }),
        )
    }

    fn text_of(v: &Value) -> String {
        v["content"][0]["text"].as_str().unwrap().to_string()
    }

    #[test]
    fn claim_tool_returns_the_evidence_closure_by_key_id_or_uri() {
        let (_tmp, state) = fixture_vault();
        for key in ["ck-aaa", "id-a", "ovp://claim/ck-aaa"] {
            let v = call(&state, "claim", serde_json::json!({ "key": key })).unwrap();
            let closure: Value = serde_json::from_str(&text_of(&v)).unwrap();
            assert_eq!(closure["claim_key"], "ck-aaa", "lookup by `{key}`");
            assert_eq!(closure["uri"], "ovp://claim/ck-aaa");
            assert_eq!(closure["citations"][0]["quote"], "verbatim quote");
            assert_eq!(closure["citations"][0]["resolved_line"], 12);
        }
        let err = call(&state, "claim", serde_json::json!({ "key": "nope" })).unwrap_err();
        assert!(err.message.contains("No active claim"), "{}", err.message);
    }

    #[test]
    fn ambiguous_claim_id_lists_the_candidate_keys() {
        let records = vec![record("ck-one", "dup", "c1"), record("ck-two", "dup", "c2")];
        let err = find_record(&records, "dup").unwrap_err();
        assert!(err.message.contains("ambiguous"), "{}", err.message);
        assert!(err.message.contains("ck-one") && err.message.contains("ck-two"));
    }

    #[test]
    fn theme_page_tool_lists_and_fetches_by_label_or_id() {
        let (_tmp, state) = fixture_vault();
        // Listing.
        let v = call(&state, "theme_page", serde_json::json!({})).unwrap();
        let listing: Value = serde_json::from_str(&text_of(&v)).unwrap();
        assert_eq!(listing[0]["label"], "Agent memory");
        assert_eq!(listing[0]["uri"], "ovp://theme-page/0");
        // By label and by t000 — the page ships with its claims closure.
        for theme in ["Agent memory", "t000", "0"] {
            let v = call(&state, "theme_page", serde_json::json!({ "theme": theme })).unwrap();
            let out: Value = serde_json::from_str(&text_of(&v)).unwrap();
            assert_eq!(out["page"]["label"], "Agent memory", "lookup by `{theme}`");
            assert_eq!(out["claims"]["ck-aaa"]["claim_id"], "id-a");
            assert_eq!(out["claims"]["ck-bbb"]["claim_id"], "id-b");
        }
        let err = call(
            &state,
            "theme_page",
            serde_json::json!({ "theme": "Ghost" }),
        )
        .unwrap_err();
        assert!(err.message.contains("No topic page"), "{}", err.message);
    }

    #[test]
    fn theme_page_uris_from_the_listing_are_dereferenceable() {
        // codex P2: every URI a tool emits must resolve via resources/read.
        let (_tmp, state) = fixture_vault();
        let v = call(&state, "theme_page", serde_json::json!({})).unwrap();
        let listing: Value = serde_json::from_str(&text_of(&v)).unwrap();
        let uri = listing[0]["uri"].as_str().unwrap().to_string();
        assert_eq!(uri, "ovp://theme-page/0");
        let read = dispatch(&state, "resources/read", &serde_json::json!({ "uri": uri })).unwrap();
        let payload: Value =
            serde_json::from_str(read["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(payload["page"]["label"], "Agent memory");
    }

    #[test]
    fn dynamic_uris_are_advertised_as_templates_not_fake_resources() {
        let (_tmp, state) = fixture_vault();
        let listed = dispatch(&state, "resources/list", &Value::Null).unwrap();
        for r in listed["resources"].as_array().unwrap() {
            let uri = r["uri"].as_str().unwrap();
            assert!(
                !uri.contains('<') && !uri.contains('{'),
                "concrete resource list must not carry placeholders: {uri}"
            );
        }
        let templates = dispatch(&state, "resources/templates/list", &Value::Null).unwrap();
        let uris: Vec<&str> = templates["resourceTemplates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["uriTemplate"].as_str().unwrap())
            .collect();
        assert!(uris.contains(&"ovp://claim/{claim_key}"), "{uris:?}");
        assert!(
            uris.contains(&"ovp://theme-page/{community_id}"),
            "{uris:?}"
        );
    }

    #[test]
    fn ask_without_a_client_is_a_clear_configuration_error() {
        let (_tmp, state) = fixture_vault();
        let err = call(&state, "ask", serde_json::json!({ "question": "q" })).unwrap_err();
        assert!(
            err.message.contains("ask is not configured"),
            "{}",
            err.message
        );
        assert!(err.message.contains("ANTHROPIC_API_KEY"), "{}", err.message);
    }

    #[test]
    fn ask_with_a_client_reaches_past_the_config_gate() {
        let (_tmp, mut state) = fixture_vault();
        // A configured factory moves the failure past "not configured" — the
        // fixture vault has no index, so the next honest error is index
        // availability (the full pipeline is covered in ovp-memory).
        state.ask_client = Some(std::sync::Arc::new(|| Err("never built".into())));
        let err = call(&state, "ask", serde_json::json!({ "question": "q" })).unwrap_err();
        assert!(
            err.message.contains("Index not available"),
            "{}",
            err.message
        );
    }

    #[test]
    fn claim_resource_reads_by_stable_uri() {
        let (_tmp, state) = fixture_vault();
        let v = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": "ovp://claim/ck-bbb" }),
        )
        .unwrap();
        let text = v["contents"][0]["text"].as_str().unwrap();
        let closure: Value = serde_json::from_str(text).unwrap();
        assert_eq!(closure["claim_id"], "id-b");
    }
}
