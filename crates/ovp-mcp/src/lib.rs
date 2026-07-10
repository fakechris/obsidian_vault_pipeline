//! `ovp-mcp` — synchronous stdio JSON-RPC server implementing the Model
//! Context Protocol (MCP). No async runtime. Reads newline-delimited JSON-RPC
//! from stdin, dispatches to OVP tools, writes responses to stdout.

use std::io::{self, BufRead, Write};
use std::path::PathBuf;

use ovp_domain::VaultLayout;
use ovp_index::{read_index, run_query, IndexModel, Query, QueryKind};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub struct McpConfig {
    pub vault_root: PathBuf,
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

#[derive(Serialize)]
struct RpcError {
    code: i32,
    message: String,
}

struct McpState {
    vault_root: PathBuf,
    _layout: VaultLayout,
}

impl McpState {
    fn load_model(&self) -> Option<IndexModel> {
        read_index(&self.vault_root).ok()
    }
}

pub fn run_mcp(config: McpConfig) -> Result<(), String> {
    let state = McpState {
        vault_root: config.vault_root,
        _layout: VaultLayout::new(),
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
                    error: Some(RpcError { code: -32700, message: format!("Parse error: {e}") }),
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
                error: Some(RpcError { code: -32600, message: "Invalid jsonrpc version".into() }),
            };
            write_response(&mut out, &resp);
            continue;
        }

        let id = req.id.unwrap_or(Value::Null);
        let result = dispatch(&state, &req.method, &req.params);

        let resp = match result {
            Ok(val) => RpcResponse { jsonrpc: "2.0", id, result: Some(val), error: None },
            Err(e) => RpcResponse { jsonrpc: "2.0", id, result: None, error: Some(e) },
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
        "resources/read" => handle_resources_read(state, params),
        "notifications/initialized" | "notifications/cancelled" => {
            Ok(Value::Null)
        }
        _ => Err(RpcError { code: -32601, message: format!("Method not found: {method}") }),
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
                "description": "Query the OVP index: sources, packs, claims, runs. Filter by kind, status, date, or free-text term.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "term": { "type": "string", "description": "Free-text search term" },
                        "kind": { "type": "string", "enum": ["sources", "packs", "claims", "runs"] },
                        "status": { "type": "string" },
                        "date": { "type": "string", "description": "Date prefix (YYYY or YYYY-MM or YYYY-MM-DD)" }
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
    let arguments = params.get("arguments").cloned().unwrap_or(Value::Object(Default::default()));

    match name {
        "find" => tool_find(state, &arguments),
        "search" => tool_search(state, &arguments),
        "status" => tool_status(state),
        "doctor" => tool_doctor(state),
        _ => Err(RpcError { code: -32602, message: format!("Unknown tool: {name}") }),
    }
}

fn tool_find(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let model = state.load_model()
        .ok_or_else(|| RpcError { code: -32000, message: "Index not available".into() })?;

    let query = Query {
        kind: args.get("kind").and_then(|v| v.as_str()).and_then(|k| match k {
            "sources" => Some(QueryKind::Sources),
            "packs" => Some(QueryKind::Packs),
            "claims" => Some(QueryKind::Claims),
            "runs" => Some(QueryKind::Runs),
            _ => None,
        }),
        status: args.get("status").and_then(|v| v.as_str()).map(String::from),
        date: args.get("date").and_then(|v| v.as_str()).map(String::from),
        term: args.get("term").and_then(|v| v.as_str()).map(String::from),
    };

    let hits = run_query(&model, &query);
    let text = serde_json::to_string_pretty(&hits).unwrap_or_else(|_| "[]".into());

    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_search(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let model = state.load_model()
        .ok_or_else(|| RpcError { code: -32000, message: "Index not available".into() })?;

    let term = args.get("query").and_then(|v| v.as_str()).map(String::from);
    let query = Query { kind: None, status: None, date: None, term };
    let hits = run_query(&model, &query);
    let text = serde_json::to_string_pretty(&hits).unwrap_or_else(|_| "[]".into());

    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_status(state: &McpState) -> Result<Value, RpcError> {
    let model = state.load_model()
        .ok_or_else(|| RpcError { code: -32000, message: "Index not available".into() })?;

    let text = format!(
        "OVP Status (index date: {})\n\
         Sources: {} (queued={}, processed={}, failed={}, blocked={})\n\
         Packs: {}\n\
         Claims: durable={}, caveated={}\n\
         Runs: {}\n\
         Queue depth: {}\n\
         Blocked sources: {}",
        model.date,
        model.totals.sources, model.totals.queued, model.totals.processed,
        model.totals.failed, model.totals.blocked,
        model.totals.packs,
        model.totals.claims_durable, model.totals.claims_caveated,
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
            }
        ]
    }))
}

fn handle_resources_read(state: &McpState, params: &Value) -> Result<Value, RpcError> {
    let uri = params.get("uri").and_then(|v| v.as_str()).unwrap_or("");

    match uri {
        "ovp://index" => {
            let model = state.load_model()
                .ok_or_else(|| RpcError { code: -32000, message: "Index not available".into() })?;
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
        _ => Err(RpcError { code: -32602, message: format!("Unknown resource: {uri}") }),
    }
}
