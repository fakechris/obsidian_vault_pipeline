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
                "name": "ovp_search",
                "description": "Hybrid search across OVP vault knowledge (durable claims, source documents, reader packs, and evidence). Returns ranked results with snippets and an honest RetrieveCoverage ledger across Lexical (BM25), Semantic (Dense), and Claims layers. Aligned with DeepSeek Harness and Claude Code knowledge retrieval.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": { "type": "string", "description": "Search query terms or phrase" },
                        "kind": {
                            "type": "string",
                            "enum": ["all", "sources", "claims", "packs", "evidence"],
                            "description": "Restrict to specific knowledge layer (default: all)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return (1-50, default: 10)"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "ovp_read_note",
                "description": "Read a source document or raw capture note with line numbers and pagination. Prevents context overflow for coding agents. Accepts note path, content sha256, or title.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Vault-relative path, sha256 hash, or note title"
                        },
                        "offset_line": {
                            "type": "integer",
                            "description": "1-based starting line number (default: 1)"
                        },
                        "line_limit": {
                            "type": "integer",
                            "description": "Maximum number of lines to return (1-500, default: 100)"
                        }
                    },
                    "required": ["key"]
                }
            },
            {
                "name": "ovp_list_themes",
                "description": "List high-level knowledge themes and topic clusters synthesized from vault claims. Aligned with DeepSeek Harness and Claude Code knowledge discovery.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Optional search term to filter theme labels or descriptions"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum themes to return (1-100, default: 20)"
                        }
                    }
                }
            },
            {
                "name": "find",
                "description": "Query the OVP index: sources, packs, claims, runs, tags, entities. Filter by kind, status, date, tag, entity, or free-text term.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
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
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
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
                "description": "Ask the VAULT AGENT: a tool-loop agent that searches claims, sources, evidence cards, and full article bodies, reads originals when needed, and answers with server-VERIFIED receipts ([claim:…]/[source:…] resolved against the ledger/index — fabricated references are flagged, never trusted) plus honest per-layer coverage. Prefer this over answering from memory for anything the vault may cover; audit any [claim:…] citation with the `claim` tool. Pass the returned session id as `chat` to continue a conversation.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": { "type": "string", "description": "The question for the vault agent" },
                        "chat": { "type": "string", "description": "Session id from a prior ask — continues that conversation. Retrying the SAME question on the same chat replays the completed turn (no second model call)." },
                        "idempotency_key": { "type": "string", "description": "Optional stable retry key — a retry with the same key replays the completed turn instead of running a new one" }
                    },
                    "required": ["question"]
                }
            },
            {
                "name": "claim",
                "description": "Read one durable claim's FULL evidence closure: claim text, gate verdicts, and every citation resolved to its verbatim quote, line, and source (title/sha/url). Accepts a claim_key (ck-…), a claim_id, or an ovp://claim/<key> URI. This is how an answer's [claim:…] citation is audited.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
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
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
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
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
                "inputSchema": { "type": "object", "properties": {} }
            },
            {
                "name": "status",
                "description": "Get OVP pipeline status: totals, recent runs, blocked sources.",
                "readOnly": true,
                "annotations": {
                    "readOnly": true,
                    "readOnlyHint": true
                },
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
        "ovp_search" => tool_ovp_search(state, &arguments),
        "ovp_read_note" => tool_ovp_read_note(state, &arguments),
        "ovp_list_themes" => tool_ovp_list_themes(state, &arguments),
        _ => Err(RpcError {
            code: -32602,
            message: format!("Unknown tool: {name}"),
        }),
    }
}

/// The MCP projection of the shared vault agent (A0: one tool registry, two
/// projections). Runs the SAME loop/tools/policy as POST /api/ask; the reply
/// is the answer plus server-verified receipts, per-layer coverage, and the
/// session id for continuation. Deliverable turns also land on the saved-chat
/// History surface, so portal and MCP conversations share one product record.
fn tool_ask(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    use ovp_memory::agent::{run_agent_turn, AgentConfig, AgentError, StoppedReason};
    use ovp_memory::agent_transcript::{valid_session_id, SessionStore};
    use ovp_memory::vault_tools::VaultTools;

    let question = args
        .get("question")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| RpcError {
            code: -32602,
            message: "`question` is required".into(),
        })?;
    // A supplied `chat` continues that session; anything else (including an
    // invalid id) gets a fresh generated session — the reply always says
    // which id to use next, so continuity is one copy-paste away.
    let supplied_chat = match args.get("chat") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if valid_session_id(s.trim()) => Some(s.trim()),
        // A malformed supplied chat fails LOUD (HTTP parity): silently
        // minting a fresh session would break the continuity (and the
        // replay contract) the caller asked for.
        Some(_) => {
            return Err(RpcError {
                code: -32602,
                message: "`chat` must be a valid session id ([A-Za-z0-9_-], ≤64 chars)"
                    .into(),
            });
        }
    };
    let session = match supplied_chat {
        Some(id) => id.to_string(),
        None => {
            static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let millis = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or(0);
            format!("mcp-{millis}-{}-{seq}", std::process::id())
        }
    };
    // Idempotency: explicit key wins; a CONTINUED session derives one from
    // (chat, question) so a host retry of a timed-out ask replays the
    // completed turn instead of paying for (and double-appending) a second
    // one. A fresh generated session cannot collide, so it needs no key.
    let explicit_key = match args.get("idempotency_key") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if !s.trim().is_empty() && s.trim().len() <= 128 => {
            Some(s.trim().to_string())
        }
        // A malformed supplied key must fail LOUD (HTTP-path parity):
        // dropping it would silently lose paid-call idempotency, or worse,
        // fall back to the auto-derived key and replay a different turn.
        Some(_) => {
            return Err(RpcError {
                code: -32602,
                message: "`idempotency_key` must be a non-empty string of at most 128 chars".into(),
            });
        }
    };
    // Same rule as the HTTP path: a key is only useful on a STABLE session
    // (replay lookup is session-local) — a key on a generated session could
    // never replay and would silently re-run a paid turn on retry.
    if explicit_key.is_some() && supplied_chat.is_none() {
        return Err(RpcError {
            code: -32602,
            message: "`idempotency_key` requires a stable `chat` — a generated \
                      session cannot replay a retried turn"
                .into(),
        });
    }
    let idem_key = explicit_key
        .or_else(|| {
            supplied_chat.map(|chat| {
                let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
                for byte in chat.bytes().chain([0u8]).chain(question.bytes()) {
                    hash ^= u64::from(byte);
                    hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
                }
                format!("mcp-auto-{hash:016x}")
            })
        });
    // Provider-free replay parity with the HTTP path: a keyed retry of a
    // completed turn answers from the transcript BEFORE building a client.
    let sessions_dir = state.vault_root.join(".ovp").join("ask-sessions");
    let mut store = SessionStore::open(&sessions_dir, &session).map_err(|e| RpcError {
        code: -32000,
        message: format!("ask session store: {e}"),
    })?;
    if let Some(key) = idem_key.as_deref()
        && let Some(done) = store.completed_turn_for_key(key)
    {
        let records = ovp_api_projection::readers::load_active_records(
            &state.vault_root,
            &ovp_domain::vault_layout::VaultLayout::new(),
        );
        let citations = match read_index(&state.vault_root).ok() {
            Some(m) => ovp_memory::receipts::agent_citations(&done.answer, &m, &records, ovp_index::read_evidence(&state.vault_root).ok().as_ref()),
            None => ovp_memory::receipts::agent_citations_unindexed(&done.answer, &records),
        };
        let verified = citations.iter().filter(|c| c["verified"] == true).count();
        let unverified: Vec<&str> = citations
            .iter()
            .filter(|c| c["verified"] != true)
            .filter_map(|c| c["id"].as_str())
            .collect();
        let mut text = done.answer.trim().to_string();
        text.push_str(&format!(
            "\n\n---\nreceipts: {} citation(s), {verified} verified against the vault",
            citations.len()
        ));
        if !unverified.is_empty() {
            text.push_str(&format!("; UNVERIFIED: {}", unverified.join(", ")));
        }
        // Tool provenance survives the replay — rebuilt from the transcript,
        // exactly like the HTTP replay path.
        let trail = store.tool_trail_for_turn(&done.turn_id);
        if !trail.is_empty() {
            text.push_str("\nagent trail:");
            for (tool, _id, is_error, _summary, arguments, note, _hits) in trail {
                let mark = if is_error { "✗" } else { "✓" };
                let args = match ovp_memory::receipts::args_brief(&arguments) {
                    Value::String(s) => format!(" {s}"),
                    _ => String::new(),
                };
                let note = note.map(|n| format!(" → {n}")).unwrap_or_default();
                text.push_str(&format!("\n  {tool}{mark}{args}{note}"));
            }
        }
        text.push_str(&format!(
            "\nidempotent replay of turn {} (no new model call)\nsession: {session} \
             (pass as `chat` to continue)",
            done.turn_id
        ));
        // History repair, PER TURN: the export may have failed after earlier
        // turns landed, so file existence is not enough — repair when this
        // turn's Q line is absent. (An identical question asked twice in one
        // session skips the repair; the retry contract makes that turn a
        // replay of the first anyway.)
        let this_turn_missing = !ovp_memory::ask::agent_chat_contains_question(
            &state.vault_root,
            &session,
            question,
        );
        if matches!(done.stopped_reason.as_str(), "final" | "need_user" | "refusal")
            && !done.answer.is_empty()
            && this_turn_missing
            && let Err(e) = ovp_memory::ask::save_agent_chat_turn(
                &state.vault_root,
                &session,
                question,
                &done.answer,
            )
        {
            text.push_str(&format!("\nnote: chat history save failed: {e}"));
        }
        return Ok(serde_json::json!({ "content": [{ "type": "text", "text": text }] }));
    }
    // Explicit configuration error, never a silent degrade — checked AFTER
    // the replay preflight, so a keyed retry of a completed turn answers
    // provider-free even when no LLM is configured (HTTP-path parity).
    let Some(factory) = &state.ask_client else {
        return Err(RpcError {
            code: -32000,
            message: "ask is not configured — run ovp2 built with `--features anthropic` \
                      and set an API key via System → LLM Provider (or ANTHROPIC_API_KEY)"
                .into(),
        });
    };
    let mut client = factory().map_err(|e| {
        if e == "llm_not_configured" {
            RpcError {
                code: -32000,
                message: "ask is not configured — set an API key via System → LLM Provider \
                          (or ANTHROPIC_API_KEY); no restart needed after save"
                    .into(),
            }
        } else {
            RpcError {
                code: -32000,
                message: format!("ask client configuration invalid: {e}"),
            }
        }
    })?;
    // Same knobs as the HTTP wiring (parity is the contract): the agent
    // serves unindexed vaults too — tools report layer unavailability
    // honestly instead of refusing the question up front.
    let cfg = AgentConfig {
        model: ovp_memory::ask::AskArgs::default().model_name,
        system: ovp_memory::agent_policy::AGENT_POLICY.to_string(),
        max_tokens: 4096,
        max_rounds: 10,
        deadline: std::time::Duration::from_secs(90),
        ..AgentConfig::default()
    };
    let mut tools = VaultTools::new(&state.vault_root)
        .with_result_cap(cfg.max_result_bytes.saturating_sub(2 * 1024));
    let outcome = run_agent_turn(
        client.as_mut(),
        &mut tools,
        &mut store,
        question,
        idem_key.as_deref(),
        &cfg,
    )
        .map_err(|e| match e {
            AgentError::SessionBusy => RpcError {
                code: -32000,
                message: format!(
                    "session `{session}` has a turn in flight — retry shortly or start a new chat"
                ),
            },
            AgentError::Store(d) => RpcError {
                code: -32000,
                message: format!("ask session store: {d}"),
            },
        })?;

    // Receipts verify against the SAME index snapshot the tools served from
    // (or degrade honestly when the vault has no index).
    let records = ovp_api_projection::readers::load_active_records(
        &state.vault_root,
        &ovp_domain::vault_layout::VaultLayout::new(),
    );
    let citations = match tools.index_snapshot().as_deref() {
        Some(m) => ovp_memory::receipts::agent_citations(&outcome.answer, m, &records, ovp_index::read_evidence(&state.vault_root).ok().as_ref()),
        None => ovp_memory::receipts::agent_citations_unindexed(&outcome.answer, &records),
    };
    // A racing process completed this keyed turn between the preflight
    // lookup and the engine's own replay check — nothing ran HERE, so this
    // request's tools carry no coverage and no trail, and history was the
    // racer's to save. Answer + receipts only, marked as a replay.
    if outcome.idempotent_replay {
        let verified = citations.iter().filter(|c| c["verified"] == true).count();
        let unverified: Vec<&str> = citations
            .iter()
            .filter(|c| c["verified"] != true)
            .filter_map(|c| c["id"].as_str())
            .collect();
        let mut text = outcome.answer.trim().to_string();
        text.push_str(&format!(
            "\n\n---\nreceipts: {} citation(s), {verified} verified against the vault",
            citations.len()
        ));
        if !unverified.is_empty() {
            text.push_str(&format!("; UNVERIFIED: {}", unverified.join(", ")));
        }
        // The trail IS available — reconstructed from the committed
        // transcript by the engine's replay — and a racing retry deserves
        // the same 复盘 detail as any other reply.
        if !outcome.tool_trace.is_empty() {
            text.push_str("\nagent trail:");
            for t2 in &outcome.tool_trace {
                let mark = if t2.is_error { "✗" } else { "✓" };
                let args = match ovp_memory::receipts::args_brief(&t2.arguments) {
                    Value::String(s) => format!(" {s}"),
                    _ => String::new(),
                };
                let note = t2
                    .result_note
                    .as_deref()
                    .map(|n| format!(" → {n}"))
                    .unwrap_or_default();
                text.push_str(&format!("\n  {}{mark}{args}{note}", t2.tool));
            }
        }
        text.push_str(&format!(
            "\nidempotent replay of turn {} (completed by a concurrent request)\n\
             session: {session} (pass as `chat` to continue)",
            outcome.turn_id
        ));
        return Ok(serde_json::json!({ "content": [{ "type": "text", "text": text }] }));
    }
    let coverage = tools.coverage();

    let mut text = outcome.answer.trim().to_string();
    text.push_str("\n\n---\n");
    let verified = citations.iter().filter(|c| c["verified"] == true).count();
    let unverified: Vec<&str> = citations
        .iter()
        .filter(|c| c["verified"] != true)
        .filter_map(|c| c["id"].as_str())
        .collect();
    text.push_str(&format!(
        "receipts: {} citation(s), {verified} verified against the vault",
        citations.len()
    ));
    if !unverified.is_empty() {
        text.push_str(&format!("; UNVERIFIED: {}", unverified.join(", ")));
    }
    let cov = serde_json::to_value(coverage).unwrap_or_default();
    if let Some(obj) = cov.as_object() {
        let line = obj
            .iter()
            .map(|(k, v)| format!("{k}={}", v.as_str().unwrap_or("?")))
            .collect::<Vec<_>>()
            .join(" · ");
        text.push_str(&format!("\ncoverage: {line}"));
    }
    if !outcome.tool_trace.is_empty() {
        // One line per call, 复盘-grade: what was asked, what came back.
        text.push_str("\nagent trail:");
        for t in &outcome.tool_trace {
            let mark = if t.is_error { "✗" } else { "✓" };
            let args = match ovp_memory::receipts::args_brief(&t.arguments) {
                Value::String(s) => format!(" {s}"),
                _ => String::new(),
            };
            let note = t
                .result_note
                .as_deref()
                .map(|n| format!(" → {n}"))
                .unwrap_or_default();
            text.push_str(&format!("\n  {}{mark}{args}{note}", t.tool));
        }
    }
    if outcome.stopped_reason != StoppedReason::Final {
        text.push_str(&format!(
            "\nstopped: {} — the answer may be partial",
            outcome.stopped_reason.as_str()
        ));
    }
    text.push_str(&format!(
        "\nsession: {session} (pass as `chat` to continue) — audit any [claim:<key>] \
         citation with the `claim` tool"
    ));

    // History parity with the HTTP path: deliverable turns land on the
    // saved-chat surface; a save failure is reported in-band, never silent.
    let deliverable = matches!(
        outcome.stopped_reason,
        StoppedReason::Final | StoppedReason::NeedUser | StoppedReason::Refusal
    );
    if deliverable
        && !outcome.answer.is_empty()
        && let Err(e) =
            ovp_memory::ask::save_agent_chat_turn(&state.vault_root, &session, question, &outcome.answer)
    {
        text.push_str(&format!("\nnote: chat history save failed: {e}"));
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

fn resolve_source_note(
    state: &McpState,
    key: &str,
) -> Result<(PathBuf, Option<String>, Option<String>), RpcError> {
    let key_clean = key.trim();
    if key_clean.is_empty() {
        return Err(RpcError {
            code: -32602,
            message: "`key` cannot be empty".into(),
        });
    }

    // 1. Check if index model has a matching source
    let model = state.load_model();
    if let Some(m) = &model
        && let Some(src) = m.sources.iter().find(|s| {
            s.sha256 == key_clean
                || (key_clean.len() >= 8 && s.sha256.starts_with(key_clean))
                || s.rel_path.as_deref() == Some(key_clean)
                || s.title.as_deref().is_some_and(|t| t.eq_ignore_ascii_case(key_clean))
        })
        && let Some(rel) = &src.rel_path
    {
        let path = ovp_domain::vault_layout::lifecycle_moved_path(
            &state.vault_root,
            &state.layout,
            rel,
            Some(&src.sha256),
        )
        .unwrap_or_else(|| state.vault_root.join(rel));
        if path.is_file() {
            return Ok((path, Some(src.sha256.clone()), src.title.clone()));
        }
    }

    // 2. Direct path check (safe relative check)
    let p = std::path::Path::new(key_clean);
    if !key_clean.contains("..") && !p.is_absolute() {
        let direct = state.vault_root.join(key_clean);
        if direct.is_file() {
            return Ok((direct, None, None));
        }
        let with_md = state.vault_root.join(format!("{key_clean}.md"));
        if with_md.is_file() {
            return Ok((with_md, None, None));
        }
        let in_raw = state.vault_root.join(state.layout.inbox_raw_dir()).join(key_clean);
        if in_raw.is_file() {
            return Ok((in_raw, None, None));
        }
        let in_raw_md = state.vault_root.join(state.layout.inbox_raw_dir()).join(format!("{key_clean}.md"));
        if in_raw_md.is_file() {
            return Ok((in_raw_md, None, None));
        }
        for cap_dir in state.layout.capture_dirs() {
            let in_cap = state.vault_root.join(cap_dir).join(key_clean);
            if in_cap.is_file() {
                return Ok((in_cap, None, None));
            }
            let in_cap_md = state.vault_root.join(cap_dir).join(format!("{key_clean}.md"));
            if in_cap_md.is_file() {
                return Ok((in_cap_md, None, None));
            }
        }
    }

    Err(RpcError {
        code: -32602,
        message: format!("Note not found for key `{key_clean}`. Tip: use `ovp_search` or `find` to discover valid note paths or sha256."),
    })
}

fn tool_ovp_read_note(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let key = args
        .get("key")
        .and_then(|v| v.as_str())
        .ok_or_else(|| RpcError {
            code: -32602,
            message: "`key` is required".into(),
        })?;

    let (file_path, sha256, title) = resolve_source_note(state, key)?;

    let full_content = std::fs::read_to_string(&file_path).map_err(|e| RpcError {
        code: -32000,
        message: format!("Failed to read file `{}`: {e}", file_path.display()),
    })?;

    let lines: Vec<&str> = full_content.lines().collect();
    let total_lines = lines.len();

    let offset_line = args
        .get("offset_line")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as usize;

    let line_limit = args
        .get("line_limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(100)
        .clamp(1, 500) as usize;

    let start_idx = offset_line.saturating_sub(1);
    let end_idx = (start_idx + line_limit).min(total_lines);
    let lines_returned = end_idx.saturating_sub(start_idx);
    let has_more = end_idx < total_lines;

    let mut rendered_lines = Vec::with_capacity(lines_returned);
    for (idx, line) in lines.iter().enumerate().take(end_idx).skip(start_idx) {
        rendered_lines.push(format!("{:>5} | {}", idx + 1, line));
    }
    let body_text = rendered_lines.join("\n");

    let rel_display = file_path
        .strip_prefix(&state.vault_root)
        .ok()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|| file_path.to_string_lossy().to_string());

    let payload = serde_json::json!({
        "path": rel_display,
        "sha256": sha256,
        "title": title,
        "offset_line": offset_line,
        "line_limit": line_limit,
        "total_lines": total_lines,
        "lines_returned": lines_returned,
        "has_more": has_more,
        "content": body_text,
    });

    let text = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "{}".into());
    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_ovp_search(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let query = args
        .get("query")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| RpcError {
            code: -32602,
            message: "`query` is required".into(),
        })?;

    let kind = args
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("all");

    if !matches!(kind, "all" | "sources" | "claims" | "packs" | "evidence") {
        return Err(RpcError {
            code: -32602,
            message: format!("Invalid kind `{kind}`: expected one of 'all', 'sources', 'claims', 'packs', 'evidence'"),
        });
    }

    let limit = args
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(10)
        .clamp(1, 50) as usize;

    let model = state.load_model();
    let records = state.load_records();

    let mut results: Vec<Value> = Vec::new();
    let mut total_matches = 0;
    let mut lexical_matches = 0;
    let mut claim_matches = 0;

    let search_all = kind == "all";
    let search_claims = search_all || kind == "claims";
    let search_sources = search_all || kind == "sources";
    let search_evidence = search_all || kind == "packs" || kind == "evidence";

    // 1. Claims search
    if search_claims {
        if let Some(m) = &model {
            let claims_val = ovp_memory::vault_tools::search_claims(m, &records, query, limit, None);
            if let Some(hits) = claims_val.get("hits").and_then(|v| v.as_array()) {
                claim_matches = hits.len();
                total_matches += claim_matches;
                for (rank, hit) in hits.iter().enumerate() {
                    let key = hit.get("claim_key").and_then(|v| v.as_str()).unwrap_or("");
                    let id = hit.get("claim_id").and_then(|v| v.as_str()).unwrap_or("");
                    let claim_text = hit.get("claim").and_then(|v| v.as_str()).unwrap_or("");
                    let theme = hit.get("theme").and_then(|v| v.as_str()).unwrap_or("");
                    let status = hit.get("status").and_then(|v| v.as_str()).unwrap_or("durable");
                    let score = 1.0 / (1.0 + (rank as f64) * 0.1);

                    results.push(serde_json::json!({
                        "kind": "claim",
                        "id": if !key.is_empty() { key } else { id },
                        "title": claim_text,
                        "snippet": format!("[Theme: {theme}] {claim_text}"),
                        "uri": if !key.is_empty() { format!("ovp://claim/{key}") } else { format!("ovp://claim/{id}") },
                        "status": status,
                        "score": (score * 1000.0).round() / 1000.0,
                    }));
                }
            }
        } else if !records.is_empty() {
            let q_lower = query.to_lowercase();
            let matched_records: Vec<&DurableRecord> = records
                .iter()
                .filter(|r| r.claim.to_lowercase().contains(&q_lower) || r.theme.to_lowercase().contains(&q_lower))
                .collect();
            claim_matches = matched_records.len();
            total_matches += claim_matches;
            for (rank, r) in matched_records.into_iter().take(limit).enumerate() {
                let score = 1.0 / (1.0 + (rank as f64) * 0.1);
                results.push(serde_json::json!({
                    "kind": "claim",
                    "id": r.claim_key,
                    "title": r.claim,
                    "snippet": format!("[Theme: {}] {}", r.theme, r.claim),
                    "uri": format!("ovp://claim/{}", r.claim_key),
                    "status": "durable",
                    "score": (score * 1000.0).round() / 1000.0,
                }));
            }
        }
    }

    // 2. Sources search
    if search_sources
        && let Some(m) = &model
    {
        let sources_val = ovp_memory::vault_tools::search_sources(m, query, limit);
        if let Some(hits) = sources_val.get("hits").and_then(|v| v.as_array()) {
            let src_count = hits.len();
            lexical_matches += src_count;
            total_matches += src_count;
            for (rank, hit) in hits.iter().enumerate() {
                let sha = hit.get("source_id").and_then(|v| v.as_str()).unwrap_or("");
                let title = hit.get("title").and_then(|v| v.as_str()).unwrap_or("Untitled");
                let path = hit.get("rel_path").and_then(|v| v.as_str());
                let author = hit.get("author").and_then(|v| v.as_str()).unwrap_or("");
                let snippet = if !author.is_empty() {
                    format!("Author: {author} | Title: {title}")
                } else {
                    title.to_string()
                };
                let score = 0.8 / (1.0 + (rank as f64) * 0.1);

                results.push(serde_json::json!({
                    "kind": "source",
                    "id": sha,
                    "title": title,
                    "snippet": snippet,
                    "path": path,
                    "uri": format!("ovp://source/{sha}"),
                    "status": "processed",
                    "score": (score * 1000.0).round() / 1000.0,
                }));
            }
        }
    }

    // 3. Evidence / Packs search
    if search_evidence
        && let Some(m) = &model
    {
        let evidence_val = ovp_memory::vault_tools::search_evidence(m, query, limit);
        if let Some(hits) = evidence_val.get("hits").and_then(|v| v.as_array()) {
            let pack_count = hits.len();
            lexical_matches += pack_count;
            total_matches += pack_count;
            for (rank, pack) in hits.iter().enumerate() {
                let dir = pack.get("pack_dir").and_then(|v| v.as_str()).unwrap_or("");
                let title = pack.get("pack_title").and_then(|v| v.as_str()).unwrap_or(dir);
                let cards = pack.get("matched_cards")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|c| c.as_str()).collect::<Vec<_>>().join("; "))
                    .unwrap_or_default();
                let snippet = if !cards.is_empty() {
                    format!("Cards: {cards}")
                } else {
                    title.to_string()
                };
                let score = 0.7 / (1.0 + (rank as f64) * 0.1);

                results.push(serde_json::json!({
                    "kind": "evidence",
                    "id": dir,
                    "title": title,
                    "snippet": snippet,
                    "path": dir,
                    "uri": Value::Null,
                    "status": "grounded",
                    "score": (score * 1000.0).round() / 1000.0,
                }));
            }
        }
    }

    results.sort_by(|a, b| {
        let sa = a.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let sb = b.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
        sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
    });

    let truncated = results.len() > limit || total_matches > results.len();
    results.truncate(limit);

    // RetrieveCoverage ledger
    let has_dense = state.vault_root.join(".ovp").join("embeddings").is_dir()
        || state.vault_root.join(".ovp").join("vector").is_dir();
    let coverage = serde_json::json!({
        "lexical": {
            "available": model.is_some(),
            "match_count": lexical_matches,
            "detail": if let Some(m) = &model {
                format!("BM25/FTS index active across {} sources and {} packs", m.sources.len(), m.packs.len())
            } else {
                "Index not loaded — run `ovp2 index` to enable lexical search".into()
            }
        },
        "semantic": {
            "available": has_dense,
            "match_count": 0,
            "detail": if has_dense {
                "Dense vector embeddings active"
            } else {
                "Dense embeddings not configured — fallback to lexical & crystal graph"
            }
        },
        "claims": {
            "available": !records.is_empty(),
            "match_count": claim_matches,
            "detail": format!("Durable crystal ledger with {} active records", records.len())
        }
    });

    let payload = serde_json::json!({
        "query": query,
        "kind": kind,
        "total_matches": total_matches,
        "returned_matches": results.len(),
        "truncated": truncated,
        "coverage": coverage,
        "results": results,
    });

    let text = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "{}".into());
    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": text }]
    }))
}

fn tool_ovp_list_themes(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let query = args
        .get("query")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_lowercase);

    let limit = args
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(20)
        .clamp(1, 100) as usize;

    let pages = state.load_theme_pages();
    let records = state.load_records();
    let body = bodies::theme_pages_body(pages.as_ref(), &records);

    let mut listing: Vec<Value> = Vec::new();
    if let Some(pages_arr) = body["pages"].as_array() {
        for p in pages_arr {
            let label = p["label"].as_str().unwrap_or("");
            let label_zh = p["label_zh"].as_str().unwrap_or("");
            let comm_id = p["community_id"].as_i64().unwrap_or(0);
            let claim_count = p["claim_count"].as_i64().unwrap_or(0);

            let mut sample_claims: Vec<Value> = Vec::new();
            if let Some(sections) = p["sections"].as_array() {
                for sec in sections {
                    if let Some(body_text) = sec["body"].as_str() {
                        let cits = ovp_domain::crystal::theme_pages::extract_claim_citations(body_text);
                        for c in cits {
                            if sample_claims.len() < 3 && !sample_claims.iter().any(|sc| sc["key"] == c) {
                                let claim_text = body["claims"]
                                    .get(&c)
                                    .and_then(|obj| obj.get("claim"))
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("");
                                sample_claims.push(serde_json::json!({
                                    "key": c,
                                    "claim": claim_text,
                                }));
                            }
                        }
                    }
                }
            }

            if let Some(q) = &query {
                let matches_label = label.to_lowercase().contains(q);
                let matches_zh = label_zh.to_lowercase().contains(q);
                let matches_claims = sample_claims.iter().any(|sc| {
                    sc["key"].as_str().is_some_and(|k| k.to_lowercase().contains(q))
                        || sc["claim"].as_str().is_some_and(|c| c.to_lowercase().contains(q))
                });
                if !matches_label && !matches_zh && !matches_claims {
                    continue;
                }
            }

            listing.push(serde_json::json!({
                "community_id": comm_id,
                "label": label,
                "label_zh": label_zh,
                "claim_count": claim_count,
                "uri": format!("ovp://theme-page/{comm_id}"),
                "sample_claims": sample_claims,
            }));
        }
    }

    if listing.is_empty() && !records.is_empty() {
        use std::collections::BTreeMap;
        let mut groups: BTreeMap<String, (i64, Vec<String>)> = BTreeMap::new();
        for r in &records {
            let comm_id = r.theme_id.unwrap_or(0);
            let entry = groups
                .entry(r.theme.clone())
                .or_insert_with(|| (comm_id, Vec::new()));
            if entry.1.len() < 3 && !entry.1.contains(&r.claim) {
                entry.1.push(r.claim.clone());
            }
        }
        for (theme_label, (comm_id, sample_texts)) in groups {
            if let Some(q) = &query {
                let matches_label = theme_label.to_lowercase().contains(q);
                let matches_sample = sample_texts.iter().any(|t| t.to_lowercase().contains(q));
                if !matches_label && !matches_sample {
                    continue;
                }
            }
            let sample_claims: Vec<Value> = sample_texts
                .into_iter()
                .map(|txt| serde_json::json!({ "claim": txt }))
                .collect();

            listing.push(serde_json::json!({
                "community_id": comm_id,
                "label": theme_label,
                "label_zh": "",
                "claim_count": records.iter().filter(|r| r.theme == theme_label).count(),
                "uri": format!("ovp://theme-page/{comm_id}"),
                "sample_claims": sample_claims,
            }));
        }
    }

    let total_themes = listing.len();
    let truncated = total_themes > limit;
    listing.truncate(limit);

    let payload = serde_json::json!({
        "total_themes": total_themes,
        "returned_themes": listing.len(),
        "truncated": truncated,
        "themes": listing,
    });

    let text = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "{}".into());
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
            let (doc, truncated, err) = readers::read_source_doc(
                &state.vault_root,
                &state.layout,
                src.rel_path.as_deref(),
                Some(&src.sha256),
            );
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

    /// A full agent turn through the MCP projection: 0-tool scripted reply
    /// on an unindexed fixture vault — the reply carries the answer, honest
    /// receipts (the fabricated key flagged UNVERIFIED), the session id for
    /// continuation, and the deliverable turn lands on saved-chat History.
    #[test]
    fn ask_runs_a_full_agent_turn_with_receipts_and_history() {
        struct Scripted;
        impl ovp_llm::ModelClient for Scripted {
            fn call(
                &mut self,
                request: &ovp_llm::ModelRequest,
            ) -> Result<ovp_llm::ModelReply, ovp_llm::CallError> {
                Ok(ovp_llm::ModelReply {
                    model: request.model.clone(),
                    text: "answer [claim:ck-fabricated]".into(),
                    stop_reason: ovp_llm::StopReason::EndTurn,
                    usage: ovp_llm::Usage { input_tokens: 1, output_tokens: 1 },
                    blocks: None,
                    raw_stop_reason: None,
                })
            }
        }
        let (tmp, mut state) = fixture_vault();
        state.ask_client = Some(std::sync::Arc::new(|| {
            Ok(Box::new(Scripted) as Box<dyn ovp_llm::ModelClient>)
        }));
        let v = call(
            &state,
            "ask",
            serde_json::json!({ "question": "q?", "chat": "mcp-turn-test" }),
        )
        .unwrap();
        let text = v["content"][0]["text"].as_str().unwrap();
        assert!(text.starts_with("answer"), "{text}");
        assert!(text.contains("UNVERIFIED: claim:ck-fabricated"), "{text}");
        assert!(text.contains("session: mcp-turn-test"), "{text}");
        assert!(text.contains("coverage:"), "{text}");
        // Transcript (audit) + saved chat (History product surface).
        assert!(
            tmp.path().join(".ovp/ask-sessions/mcp-turn-test.jsonl").is_file()
        );
        let md = std::fs::read_to_string(
            tmp.path().join(".ovp/chats/mcp-turn-test.md"),
        )
        .unwrap();
        assert!(md.contains("**Q:** q?"), "{md}");
    }

    /// A host retry (same chat + same question) replays the completed turn:
    /// one transcript turn, one History block, no second model call.
    #[test]
    fn ask_retry_on_same_chat_replays_without_a_second_turn() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        static CALLS: AtomicUsize = AtomicUsize::new(0);
        struct Counting;
        impl ovp_llm::ModelClient for Counting {
            fn call(
                &mut self,
                request: &ovp_llm::ModelRequest,
            ) -> Result<ovp_llm::ModelReply, ovp_llm::CallError> {
                CALLS.fetch_add(1, Ordering::SeqCst);
                Ok(ovp_llm::ModelReply {
                    model: request.model.clone(),
                    text: "stable answer".into(),
                    stop_reason: ovp_llm::StopReason::EndTurn,
                    usage: ovp_llm::Usage { input_tokens: 1, output_tokens: 1 },
                    blocks: None,
                    raw_stop_reason: None,
                })
            }
        }
        let (tmp, mut state) = fixture_vault();
        state.ask_client = Some(std::sync::Arc::new(|| {
            Ok(Box::new(Counting) as Box<dyn ovp_llm::ModelClient>)
        }));
        let args = serde_json::json!({ "question": "same q", "chat": "mcp-retry-test" });
        let first = call(&state, "ask", args.clone()).unwrap();
        assert!(first["content"][0]["text"].as_str().unwrap().starts_with("stable answer"));
        let retry = call(&state, "ask", args).unwrap();
        let text = retry["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("idempotent replay"), "{text}");
        assert_eq!(CALLS.load(Ordering::SeqCst), 1, "no second paid model call");
        let transcript = std::fs::read_to_string(
            tmp.path().join(".ovp/ask-sessions/mcp-retry-test.jsonl"),
        )
        .unwrap();
        assert_eq!(transcript.matches("turn_finished").count(), 1);
        let md =
            std::fs::read_to_string(tmp.path().join(".ovp/chats/mcp-retry-test.md")).unwrap();
        assert_eq!(md.matches("**Q:** same q").count(), 1, "{md}");
    }

    #[test]
    fn ask_with_a_client_reaches_past_the_config_gate() {
        let (_tmp, mut state) = fixture_vault();
        // A configured factory moves the failure past "not configured" — the
        // agent path serves unindexed vaults, so the next honest failure is
        // the client construction itself (the loop/tools/receipts pipeline
        // is covered in ovp-memory/ovp-server).
        state.ask_client = Some(std::sync::Arc::new(|| Err("never built".into())));
        let err = call(&state, "ask", serde_json::json!({ "question": "q" })).unwrap_err();
        assert!(
            err.message.contains("ask client configuration invalid: never built"),
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

    #[test]
    fn tools_list_declares_aligned_tools_and_read_only_annotations() {
        let (_tmp, state) = fixture_vault();
        let res = dispatch(&state, "tools/list", &serde_json::json!({})).unwrap();
        let tools = res["tools"].as_array().expect("tools array");

        let names: Vec<&str> = tools
            .iter()
            .filter_map(|t| t["name"].as_str())
            .collect();

        assert!(names.contains(&"ovp_search"));
        assert!(names.contains(&"ovp_read_note"));
        assert!(names.contains(&"ovp_list_themes"));
        assert!(names.contains(&"find"));
        assert!(names.contains(&"search"));
        assert!(names.contains(&"ask"));
        assert!(names.contains(&"claim"));
        assert!(names.contains(&"theme_page"));

        for tool in tools {
            assert_eq!(
                tool["readOnly"],
                true,
                "tool `{}` must be marked readOnly",
                tool["name"]
            );
            assert_eq!(
                tool["annotations"]["readOnlyHint"],
                true,
                "tool `{}` must have readOnlyHint: true",
                tool["name"]
            );
        }
    }

    #[test]
    fn ovp_search_returns_ranked_results_and_honest_coverage() {
        let (_tmp, state) = fixture_vault();

        // Missing query error
        let err = call(&state, "ovp_search", serde_json::json!({})).unwrap_err();
        assert_eq!(err.code, -32602);

        // Invalid kind error
        let err = call(
            &state,
            "ovp_search",
            serde_json::json!({ "query": "memory", "kind": "invalid_kind" }),
        )
        .unwrap_err();
        assert_eq!(err.code, -32602);
        assert!(err.message.contains("Invalid kind `invalid_kind`"));

        // Valid search over claims
        let v = call(
            &state,
            "ovp_search",
            serde_json::json!({ "query": "Agent memory", "limit": 10 }),
        )
        .unwrap();
        let parsed: Value = serde_json::from_str(&text_of(&v)).unwrap();
        assert_eq!(parsed["query"], "Agent memory");
        assert_eq!(parsed["kind"], "all");
        assert!(parsed["total_matches"].as_u64().unwrap() >= 2);

        let results = parsed["results"].as_array().unwrap();
        assert_eq!(results.len(), 2);
        assert!(results.iter().any(|r| r["id"] == "ck-aaa"));
        assert!(results.iter().any(|r| r["id"] == "ck-bbb"));
        assert_eq!(results[0]["kind"], "claim");

        let coverage = &parsed["coverage"];
        assert_eq!(coverage["claims"]["available"], true);
        assert_eq!(coverage["claims"]["match_count"], 2);
        assert!(coverage["claims"]["detail"].as_str().unwrap().contains("2 active records"));
        assert_eq!(coverage["semantic"]["available"], false);
    }

    #[test]
    fn ovp_read_note_paginates_with_line_numbers_and_metadata() {
        let (tmp, state) = fixture_vault();

        // Write a multi-line note into vault
        let note_rel = "Sources/2026-03-01_test.md";
        let note_path = tmp.path().join(note_rel);
        std::fs::create_dir_all(note_path.parent().unwrap()).unwrap();
        let content: String = (1..=25)
            .map(|i| format!("Line {i} content text"))
            .collect::<Vec<_>>()
            .join("\n");
        std::fs::write(&note_path, &content).unwrap();

        // Missing key error
        let err = call(&state, "ovp_read_note", serde_json::json!({})).unwrap_err();
        assert_eq!(err.code, -32602);

        // Nonexistent note
        let err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "Sources/does_not_exist.md" }),
        )
        .unwrap_err();
        assert_eq!(err.code, -32602);
        assert!(err.message.contains("Note not found"));

        // Page 1: lines 1-10
        let v = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "offset_line": 1,
                "line_limit": 10
            }),
        )
        .unwrap();
        let parsed: Value = serde_json::from_str(&text_of(&v)).unwrap();
        assert_eq!(parsed["offset_line"], 1);
        assert_eq!(parsed["line_limit"], 10);
        assert_eq!(parsed["total_lines"], 25);
        assert_eq!(parsed["lines_returned"], 10);
        assert_eq!(parsed["has_more"], true);

        let lines_text = parsed["content"].as_str().unwrap();
        assert!(lines_text.starts_with("    1 | Line 1 content text"));
        assert!(lines_text.contains("   10 | Line 10 content text"));
        assert!(!lines_text.contains("Line 11"));

        // Page 2: lines 21-25 (offset 21, limit 10)
        let v2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "offset_line": 21,
                "line_limit": 10
            }),
        )
        .unwrap();
        let parsed2: Value = serde_json::from_str(&text_of(&v2)).unwrap();
        assert_eq!(parsed2["offset_line"], 21);
        assert_eq!(parsed2["total_lines"], 25);
        assert_eq!(parsed2["lines_returned"], 5);
        assert_eq!(parsed2["has_more"], false);
        let lines_text2 = parsed2["content"].as_str().unwrap();
        assert!(lines_text2.starts_with("   21 | Line 21 content text"));
        assert!(lines_text2.ends_with("   25 | Line 25 content text"));
    }

    #[test]
    fn ovp_list_themes_lists_clusters_and_filters() {
        let (_tmp, state) = fixture_vault();

        // List all themes
        let v = call(&state, "ovp_list_themes", serde_json::json!({})).unwrap();
        let parsed: Value = serde_json::from_str(&text_of(&v)).unwrap();
        assert_eq!(parsed["total_themes"], 1);
        assert_eq!(parsed["returned_themes"], 1);
        assert_eq!(parsed["truncated"], false);

        let themes = parsed["themes"].as_array().unwrap();
        assert_eq!(themes.len(), 1);
        assert_eq!(themes[0]["community_id"], 0);
        assert_eq!(themes[0]["label"], "Agent memory");
        assert_eq!(themes[0]["label_zh"], "智能体记忆");
        assert_eq!(themes[0]["claim_count"], 2);
        assert_eq!(themes[0]["uri"], "ovp://theme-page/0");

        let sample_claims = themes[0]["sample_claims"].as_array().unwrap();
        assert_eq!(sample_claims.len(), 2);
        assert_eq!(sample_claims[0]["key"], "ck-aaa");
        assert_eq!(sample_claims[1]["key"], "ck-bbb");

        // Filter by matching query
        let v_match = call(
            &state,
            "ovp_list_themes",
            serde_json::json!({ "query": "memory" }),
        )
        .unwrap();
        let parsed_match: Value = serde_json::from_str(&text_of(&v_match)).unwrap();
        assert_eq!(parsed_match["total_themes"], 1);

        // Filter by non-matching query
        let v_nomatch = call(
            &state,
            "ovp_list_themes",
            serde_json::json!({ "query": "unrelated_xyz" }),
        )
        .unwrap();
        let parsed_nomatch: Value = serde_json::from_str(&text_of(&v_nomatch)).unwrap();
        assert_eq!(parsed_nomatch["total_themes"], 0);
        assert_eq!(parsed_nomatch["themes"].as_array().unwrap().len(), 0);
    }
}
