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
use ovp_memory::closure::claim_closure;
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

/// Resolve `key` against the active records — the shared
/// [`ovp_memory::closure::find_record`], with its lookup failures mapped to
/// the JSON-RPC invalid-params error. Message text is the shared one, so the
/// CLI and MCP report the same words.
fn find_record<'a>(records: &'a [DurableRecord], key: &str) -> Result<&'a DurableRecord, RpcError> {
    ovp_memory::closure::find_record(records, key).map_err(|e| RpcError {
        code: -32602,
        message: e.to_string(),
    })
}

pub fn run_mcp(config: McpConfig) -> Result<(), String> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    run_mcp_io(config, stdin.lock(), stdout.lock())
}

pub fn run_mcp_io(
    config: McpConfig,
    reader: impl BufRead,
    mut out: impl Write,
) -> Result<(), String> {
    let state = McpState {
        vault_root: config.vault_root,
        layout: VaultLayout::new(),
        ask_client: config.ask_client,
    };

    for line in reader.lines() {
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
                "description": "Read a source document or raw capture note with line numbers, snapshot cursor, and pagination. Prevents context overflow for coding agents. Accepts note path, content sha256, or title. Supports expected_source_hash and cursor continuation to prevent version drift mid-read.",
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
                        },
                        "expected_source_hash": {
                            "type": "string",
                            "description": "Optional SHA-256 hash expected by caller. If the note was modified mid-reading, returns `version_changed` status instead of serving mixed pages."
                        },
                        "cursor": {
                            "type": "string",
                            "description": "Opaque continuation cursor (`next_cursor`) from a previous `ovp_read_note` call. Encodes snapshot hash and next offset line."
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
                "description": "Run the same health checks as `ovp2 doctor` (ledger/fs consistency, orphan packs, stale index, crystal integrity + staleness, run recency, disk usage, legacy artifacts, inbox orphans). Returns a text summary plus structuredContent = DiagnosticReport {diagnostics:[{origin,severity,code,message,hint?}]}; PASS checks are omitted. Read-only, never fixes.",
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
    use ovp_memory::agent::{AgentConfig, AgentError, StoppedReason, run_agent_turn};
    use ovp_memory::agent_transcript::{SessionStore, valid_session_id};
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
                message: "`chat` must be a valid session id ([A-Za-z0-9_-], ≤64 chars)".into(),
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
    let idem_key = explicit_key.or_else(|| {
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
            Some(m) => ovp_memory::receipts::agent_citations(
                &done.answer,
                &m,
                &records,
                ovp_index::read_evidence(&state.vault_root).ok().as_ref(),
            ),
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
        let this_turn_missing =
            !ovp_memory::ask::agent_chat_contains_question(&state.vault_root, &session, question);
        if matches!(
            done.stopped_reason.as_str(),
            "final" | "need_user" | "refusal"
        ) && !done.answer.is_empty()
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
        Some(m) => ovp_memory::receipts::agent_citations(
            &outcome.answer,
            m,
            &records,
            ovp_index::read_evidence(&state.vault_root).ok().as_ref(),
        ),
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
        && let Err(e) = ovp_memory::ask::save_agent_chat_turn(
            &state.vault_root,
            &session,
            question,
            &outcome.answer,
        )
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
    let text =
        serde_json::to_string_pretty(&claim_closure(&state.vault_root, record, model.as_ref()))
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
    let mut body = bodies::theme_pages_body(pages.as_ref(), &records);
    ovp_memory::bilingual::splice_theme_pages_zh(&state.vault_root, pages.as_ref(), &mut body);
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
    let mut out = serde_json::json!({ "page": page, "claims": claims });
    if let Some(c) = body.get("bilingual_corrupt") {
        out.as_object_mut()
            .unwrap()
            .insert("bilingual_corrupt".into(), c.clone());
    }
    Ok(out)
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
        let mut body = bodies::theme_pages_body(pages.as_ref(), &records);
        ovp_memory::bilingual::splice_theme_pages_zh(&state.vault_root, pages.as_ref(), &mut body);
        let listing: Vec<Value> = body["pages"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|p| {
                serde_json::json!({
                    "community_id": p["community_id"],
                    "label": p["label"],
                    "label_zh": p["label_zh"],
                    "sections_zh_status": p["sections_zh_status"],
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

fn hex_encode(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        use std::fmt::Write;
        let _ = write!(s, "{:02x}", b);
    }
    s
}

fn is_markdown_file(p: &std::path::Path) -> bool {
    p.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("md") || e.eq_ignore_ascii_case("markdown"))
}

fn is_safe_vault_note_path(p: &std::path::Path, vault_root: &std::path::Path) -> bool {
    if !p.is_file() || !is_markdown_file(p) {
        return false;
    }
    if let Ok(rel) = p.strip_prefix(vault_root) {
        let first_comp = rel.components().next().and_then(|c| match c {
            std::path::Component::Normal(s) => s.to_str(),
            _ => None,
        });
        if first_comp.is_some_and(|s| s == "target" || s == "node_modules") {
            return false;
        }
        !rel.components()
            .any(|c| c.as_os_str().to_str().is_some_and(|s| s.starts_with('.')))
    } else {
        false
    }
}

fn hex_decode(s: &str) -> Option<String> {
    if !s.len().is_multiple_of(2) || !s.is_ascii() {
        return None;
    }
    let bytes_in = s.as_bytes();
    let mut bytes = Vec::with_capacity(bytes_in.len() / 2);
    for chunk in bytes_in.chunks_exact(2) {
        let high = (chunk[0] as char).to_digit(16)? as u8;
        let low = (chunk[1] as char).to_digit(16)? as u8;
        bytes.push((high << 4) | low);
    }
    String::from_utf8(bytes).ok()
}

fn encode_cursor(source_hash: &str, next_offset_line: usize, rel_path: Option<&str>) -> String {
    if let Some(path) = rel_path {
        let encoded_path = hex_encode(path.as_bytes());
        format!("ovp_cur_v1:{source_hash}:{next_offset_line}:{encoded_path}")
    } else {
        format!("ovp_cur_v1:{source_hash}:{next_offset_line}")
    }
}

fn parse_cursor(cursor: &str) -> Option<(String, usize, Option<String>)> {
    let cursor = cursor.trim();
    if let Some(rest) = cursor.strip_prefix("ovp_cur_v1:") {
        let parts: Vec<&str> = rest.split(':').collect();
        if parts.len() == 2 {
            if let Ok(offset) = parts[1].parse::<usize>() {
                return Some((parts[0].to_string(), offset, None));
            }
        } else if parts.len() == 3
            && let Ok(offset) = parts[1].parse::<usize>()
        {
            let path = hex_decode(parts[2])?;
            return Some((parts[0].to_string(), offset, Some(path)));
        }
        return None;
    }
    if cursor.starts_with('{')
        && cursor.ends_with('}')
        && let Ok(val) = serde_json::from_str::<Value>(cursor)
    {
        let hash = val
            .get("source_hash")
            .or_else(|| val.get("sha256"))
            .or_else(|| val.get("h"))
            .and_then(|v| v.as_str());
        let offset = val
            .get("offset_line")
            .or_else(|| val.get("offset"))
            .or_else(|| val.get("o"))
            .and_then(|v| v.as_u64());
        let path = val
            .get("path")
            .or_else(|| val.get("p"))
            .and_then(|v| v.as_str())
            .map(ToString::to_string);
        if let (Some(h), Some(o)) = (hash, offset) {
            return Some((h.to_string(), o as usize, path));
        }
    }
    None
}

fn hash_matches(expected: &str, actual: &str) -> bool {
    let exp = expected.trim().to_ascii_lowercase();
    let act = actual.to_ascii_lowercase();
    if exp.len() >= 8 {
        act.starts_with(&exp) || exp == act
    } else {
        exp == act
    }
}

fn stream_sha256(path: &std::path::Path) -> Option<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut file = std::fs::File::open(path).ok()?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let count = file.read(&mut buffer).ok()?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Some(format!("{:x}", hasher.finalize()))
}

fn find_note_by_hash(
    vault_root: &std::path::Path,
    _layout: &VaultLayout,
    key_clean: &str,
) -> Result<Option<(PathBuf, Option<String>)>, RpcError> {
    if key_clean.len() < 8 || !key_clean.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Ok(None);
    }

    let hash8 = &key_clean[..8].to_ascii_lowercase();
    let key_lower = key_clean.to_ascii_lowercase();

    let mut stem_candidates: Vec<PathBuf> = Vec::new();
    let mut non_stem_candidates: Vec<PathBuf> = Vec::new();
    let mut seen_paths: std::collections::HashSet<PathBuf> = std::collections::HashSet::new();
    let mut visited_dirs: std::collections::HashSet<PathBuf> = std::collections::HashSet::new();
    if let Ok(real_root) = std::fs::canonicalize(vault_root) {
        visited_dirs.insert(real_root);
    }

    // Recursive search queue starting from vault_root (covers vault root and all nested subdirectories)
    let mut dirs_to_visit = vec![vault_root.to_path_buf()];

    while let Some(dir) = dirs_to_visit.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue,
        };

        for f in entries.flatten() {
            let path = f.path();
            if let Ok(ft) = f.file_type() {
                let name = f.file_name();
                let name_str = name.to_string_lossy();
                let is_dir = ft.is_dir() || (ft.is_symlink() && path.is_dir());
                if is_dir {
                    // Skip hidden dirs and repo build dirs at root
                    let is_repo_build_dir =
                        dir == vault_root && (name_str == "target" || name_str == "node_modules");
                    if !name_str.starts_with('.')
                        && !is_repo_build_dir
                        && let Ok(real_dir) = std::fs::canonicalize(&path)
                        && visited_dirs.insert(real_dir)
                    {
                        dirs_to_visit.push(path);
                    }
                } else if !name_str.starts_with('.')
                    && (ft.is_file() || (ft.is_symlink() && path.is_file()))
                    && is_markdown_file(&path)
                    && seen_paths.insert(path.clone())
                {
                    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
                    let ends_with_stem_hash = stem
                        .rsplit('-')
                        .next()
                        .is_some_and(|h| h.len() == 8 && h.eq_ignore_ascii_case(hash8));

                    if ends_with_stem_hash {
                        if key_clean.len() == 64 {
                            if let Some(full_sha) = stream_sha256(&path)
                                && full_sha.starts_with(&key_lower)
                            {
                                // Exact full 64-char SHA match found; identical duplicates are permitted,
                                // exit immediately without hashing any further files.
                                return Ok(Some((path, Some(full_sha))));
                            }
                        } else {
                            stem_candidates.push(path);
                        }
                    } else {
                        non_stem_candidates.push(path);
                    }
                }
            }
        }
    }

    // 1. Exact 64-char full SHA lookup: check non-stem candidates with immediate exit on match
    if key_clean.len() == 64 {
        for path in non_stem_candidates {
            if let Some(full_sha) = stream_sha256(&path)
                && full_sha.starts_with(&key_lower)
            {
                return Ok(Some((path, Some(full_sha))));
            }
        }
        return Ok(None);
    }

    // 2. Prefix lookup (< 64 chars):
    // Collect genuine matches from both stem and non-stem candidates to honestly detect ambiguity.
    let mut matching_paths: Vec<(PathBuf, Option<String>)> = Vec::new();

    for path in stem_candidates {
        if key_clean.len() > 8 {
            if let Some(full_sha) = stream_sha256(&path)
                && full_sha.starts_with(&key_lower)
            {
                matching_paths.push((path, Some(full_sha)));
            }
        } else {
            let full_sha = stream_sha256(&path);
            matching_paths.push((path, full_sha));
        }
    }

    for path in non_stem_candidates {
        if let Some(full_sha) = stream_sha256(&path)
            && full_sha.starts_with(&key_lower)
        {
            matching_paths.push((path, Some(full_sha)));
        }
    }

    if matching_paths.len() == 1 {
        Ok(Some(matching_paths.remove(0)))
    } else if matching_paths.len() > 1 {
        // If all matches share the identical full sha256, they are duplicate files with identical content.
        let first_sha = matching_paths[0].1.as_deref();
        let all_identical_content = first_sha.is_some()
            && matching_paths
                .iter()
                .all(|(_, s)| s.as_deref() == first_sha);

        if all_identical_content {
            Ok(Some(matching_paths.remove(0)))
        } else {
            Err(RpcError {
                code: -32602,
                message: format!(
                    "Multiple notes match content hash stem `{key_clean}` (ambiguous match)"
                ),
            })
        }
    } else {
        Ok(None)
    }
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
                || s.title
                    .as_deref()
                    .is_some_and(|t| t.eq_ignore_ascii_case(key_clean))
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
    let has_hidden_or_traversal = key_clean.contains("..")
        || p.is_absolute()
        || p.components()
            .any(|c| c.as_os_str().to_str().is_some_and(|s| s.starts_with('.')));

    let first_comp = p.components().next().and_then(|c| match c {
        std::path::Component::Normal(s) => s.to_str(),
        _ => None,
    });
    let is_build_dir = first_comp.is_some_and(|s| s == "target" || s == "node_modules");

    if !has_hidden_or_traversal && !is_build_dir {
        let direct = state.vault_root.join(key_clean);
        if direct.is_file() && is_markdown_file(&direct) {
            return Ok((direct, None, None));
        }
        let with_md = state.vault_root.join(format!("{key_clean}.md"));
        if with_md.is_file() && is_markdown_file(&with_md) {
            return Ok((with_md, None, None));
        }
        let in_raw = state
            .vault_root
            .join(state.layout.inbox_raw_dir())
            .join(key_clean);
        if in_raw.is_file() && is_markdown_file(&in_raw) {
            return Ok((in_raw, None, None));
        }
        let in_raw_md = state
            .vault_root
            .join(state.layout.inbox_raw_dir())
            .join(format!("{key_clean}.md"));
        if in_raw_md.is_file() && is_markdown_file(&in_raw_md) {
            return Ok((in_raw_md, None, None));
        }
        for cap_dir in state.layout.capture_dirs() {
            let in_cap = state.vault_root.join(cap_dir).join(key_clean);
            if in_cap.is_file() && is_markdown_file(&in_cap) {
                return Ok((in_cap, None, None));
            }
            let in_cap_md = state
                .vault_root
                .join(cap_dir)
                .join(format!("{key_clean}.md"));
            if in_cap_md.is_file() && is_markdown_file(&in_cap_md) {
                return Ok((in_cap_md, None, None));
            }
        }
        if let Some(moved) = ovp_domain::vault_layout::lifecycle_moved_path(
            &state.vault_root,
            &state.layout,
            key_clean,
            None,
        ) && moved.is_file()
            && is_markdown_file(&moved)
        {
            return Ok((moved, None, None));
        }
        let in_raw_rel = format!("{}/{}", state.layout.inbox_raw_dir(), key_clean);
        if let Some(moved) = ovp_domain::vault_layout::lifecycle_moved_path(
            &state.vault_root,
            &state.layout,
            &in_raw_rel,
            None,
        ) && moved.is_file()
            && is_markdown_file(&moved)
        {
            return Ok((moved, None, None));
        }
        let in_raw_md_rel = format!("{}/{}.md", state.layout.inbox_raw_dir(), key_clean);
        if let Some(moved) = ovp_domain::vault_layout::lifecycle_moved_path(
            &state.vault_root,
            &state.layout,
            &in_raw_md_rel,
            None,
        ) && moved.is_file()
            && is_markdown_file(&moved)
        {
            return Ok((moved, None, None));
        }
    }

    // 3. Scan by content hash if key looks like hex sha (>= 8 chars)
    if let Some((path, full_sha)) = find_note_by_hash(&state.vault_root, &state.layout, key_clean)?
    {
        return Ok((path, full_sha, None));
    }

    Err(RpcError {
        code: -32602,
        message: format!(
            "Note not found for key `{key_clean}`. Tip: use `ovp_search` or `find` to discover valid note paths or sha256."
        ),
    })
}

fn collect_path_variants(layout: &VaultLayout, raw: &str) -> Vec<String> {
    let norm = raw.trim().replace('\\', "/");
    let mut variants = Vec::new();

    let mut add = |s: String| {
        let clean = s.trim_matches('/').to_string();
        if !clean.is_empty() && !variants.contains(&clean) {
            variants.push(clean);
        }
    };

    add(norm.clone());
    if norm.ends_with(".md") {
        add(norm.trim_end_matches(".md").to_string());
    } else {
        add(format!("{norm}.md"));
    }

    let raw_prefix = format!("{}/", layout.inbox_raw_dir());
    if let Some(stripped) = norm.strip_prefix(&raw_prefix) {
        add(stripped.to_string());
        if stripped.ends_with(".md") {
            add(stripped.trim_end_matches(".md").to_string());
        } else {
            add(format!("{stripped}.md"));
        }
    } else {
        add(format!("{raw_prefix}{norm}"));
        if norm.ends_with(".md") {
            add(format!("{}{}", raw_prefix, norm.trim_end_matches(".md")));
        } else {
            add(format!("{raw_prefix}{norm}.md"));
        }
    }

    for cap in layout.capture_dirs() {
        let cap_prefix = format!("{cap}/");
        if let Some(stripped) = norm.strip_prefix(&cap_prefix) {
            add(stripped.to_string());
            if stripped.ends_with(".md") {
                add(stripped.trim_end_matches(".md").to_string());
            } else {
                add(format!("{stripped}.md"));
            }
        } else {
            add(format!("{cap_prefix}{norm}"));
            if norm.ends_with(".md") {
                add(format!("{}{}", cap_prefix, norm.trim_end_matches(".md")));
            } else {
                add(format!("{cap_prefix}{norm}.md"));
            }
        }
    }

    variants
}

fn path_aliases_match(
    vault_root: &std::path::Path,
    layout: &VaultLayout,
    p1: &str,
    p2: &str,
    cursor_hash: Option<&str>,
) -> bool {
    let p1_clean = p1.trim().replace('\\', "/");
    let p2_clean = p2.trim().replace('\\', "/");
    if p1_clean == p2_clean {
        return true;
    }

    let v1 = collect_path_variants(layout, &p1_clean);
    let v2 = collect_path_variants(layout, &p2_clean);

    let expand = |vars: Vec<String>| -> Vec<String> {
        let mut expanded = vars.clone();
        for cand in &vars {
            if let Some(moved) = ovp_domain::vault_layout::lifecycle_moved_path(
                vault_root,
                layout,
                cand,
                cursor_hash,
            ) {
                let moved_rel = moved
                    .strip_prefix(vault_root)
                    .ok()
                    .map(|p| p.to_string_lossy().replace('\\', "/"))
                    .unwrap_or_else(|| moved.to_string_lossy().replace('\\', "/"));
                for moved_cand in collect_path_variants(layout, &moved_rel) {
                    if !expanded.contains(&moved_cand) {
                        expanded.push(moved_cand);
                    }
                }
            }
        }
        expanded
    };

    let exp1 = expand(v1);
    let exp2 = expand(v2);

    exp1.iter().any(|a| exp2.contains(a))
}

fn key_matches_cursor_identity(
    vault_root: &std::path::Path,
    layout: &VaultLayout,
    key: &str,
    cursor_hash: Option<&str>,
    cursor_path: Option<&str>,
) -> bool {
    let key_clean = key.trim();
    if let Some(h) = cursor_hash
        && hash_matches(key_clean, h)
    {
        return true;
    }
    if let Some(cur_p) = cursor_path
        && path_aliases_match(vault_root, layout, key_clean, cur_p, cursor_hash)
    {
        return true;
    }
    false
}

fn tool_ovp_read_note(state: &McpState, args: &Value) -> Result<Value, RpcError> {
    let key = args
        .get("key")
        .and_then(|v| v.as_str())
        .ok_or_else(|| RpcError {
            code: -32602,
            message: "`key` is required".into(),
        })?;

    let cursor_arg = args
        .get("cursor")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let mut cursor_hash = None;
    let mut cursor_offset = None;
    let mut cursor_path = None;
    if let Some(c) = cursor_arg {
        match parse_cursor(c) {
            Some((h, o, p)) => {
                cursor_hash = Some(h);
                cursor_offset = Some(o);
                cursor_path = p;
            }
            None => {
                return Err(RpcError {
                    code: -32602,
                    message: format!(
                        "Invalid cursor `{c}`: expected `ovp_cur_v1:<hash>:<offset>` or JSON cursor token"
                    ),
                });
            }
        }
    }

    let explicit_hash = args
        .get("expected_source_hash")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string);

    // Validate that cursor hash and explicit expected_source_hash do not conflict
    if let (Some(exp), Some(cur)) = (&explicit_hash, &cursor_hash)
        && !exp.eq_ignore_ascii_case(cur)
    {
        return Err(RpcError {
            code: -32602,
            message: format!(
                "Conflicting expected hashes: expected_source_hash `{exp}` does not match cursor hash `{cur}`"
            ),
        });
    }

    let effective_expected_hash = explicit_hash.or(cursor_hash.clone());

    let resolve_note = |state: &McpState,
                        key: &str|
     -> Result<(PathBuf, Option<String>, Option<String>), RpcError> {
        match resolve_source_note(state, key) {
            Ok(res) => {
                if let Some(cur_path) = &cursor_path {
                    let resolved_rel = res
                        .0
                        .strip_prefix(&state.vault_root)
                        .ok()
                        .map(|p| p.to_string_lossy().replace('\\', "/"))
                        .unwrap_or_else(|| res.0.to_string_lossy().replace('\\', "/"));
                    let cur_norm = cur_path.replace('\\', "/");

                    // Only perform lifecycle mapping and fallback checks if paths differ
                    if resolved_rel != cur_norm
                        && !path_aliases_match(
                            &state.vault_root,
                            &state.layout,
                            &resolved_rel,
                            &cur_norm,
                            cursor_hash.as_deref(),
                        )
                    {
                        let hash_match = cursor_hash
                            .as_deref()
                            .and_then(|h| {
                                find_note_by_hash(&state.vault_root, &state.layout, h)
                                    .ok()
                                    .flatten()
                            })
                            .is_some_and(|(p, _)| p == res.0);

                        if !hash_match {
                            return Err(RpcError {
                                code: -32602,
                                message: format!(
                                    "Conflicting target note: key `{key}` resolved to `{resolved_rel}`, which does not match cursor note `{cur_norm}`"
                                ),
                            });
                        }
                    }
                }
                Ok(res)
            }
            Err(e) => {
                // Only fall back to cursor note if requested key corresponds to cursor identity or hash.
                // A missing or mistyped key for note B combined with a cursor for note A must be rejected.
                if !key_matches_cursor_identity(
                    &state.vault_root,
                    &state.layout,
                    key,
                    cursor_hash.as_deref(),
                    cursor_path.as_deref(),
                ) {
                    return Err(e);
                }

                // When an unindexed note is opened using its content hash and then edited before
                // the next cursor request, resolving the original key fails before version comparison.
                // Fall back to cursor_path if present to locate the note and report version_changed.
                if let Some(cur_path) = &cursor_path {
                    match resolve_source_note(state, cur_path) {
                        Ok(res) => Ok(res),
                        Err(_) => {
                            if let Some(h) = cursor_hash.as_deref()
                                && let Some((p, full_sha)) =
                                    find_note_by_hash(&state.vault_root, &state.layout, h)?
                            {
                                Ok((p, full_sha, None))
                            } else {
                                Err(e)
                            }
                        }
                    }
                } else if let Some(h) = cursor_hash.as_deref()
                    && let Some((p, full_sha)) =
                        find_note_by_hash(&state.vault_root, &state.layout, h)?
                {
                    Ok((p, full_sha, None))
                } else {
                    Err(e)
                }
            }
        }
    };

    let (file_path, registered_sha256, title) = if let Some(cur_path) = &cursor_path
        && cursor_hash
            .as_deref()
            .is_some_and(|h| hash_matches(key.trim(), h))
    {
        // When key is a hash matching cursor_hash, prefer the cursor-bound note path.
        // This avoids selecting an unedited identical duplicate if the cursor note was edited.
        let cur_norm = cur_path.replace('\\', "/");
        if let Ok(res) = resolve_source_note(state, &cur_norm) {
            res
        } else if let Some(moved) = ovp_domain::vault_layout::lifecycle_moved_path(
            &state.vault_root,
            &state.layout,
            &cur_norm,
            cursor_hash.as_deref(),
        )
        .filter(|p| is_safe_vault_note_path(p, &state.vault_root))
        {
            (moved, None, None)
        } else {
            resolve_note(state, key)?
        }
    } else {
        resolve_note(state, key)?
    };

    let full_content = std::fs::read_to_string(&file_path).map_err(|e| RpcError {
        code: -32000,
        message: format!("Failed to read file `{}`: {e}", file_path.display()),
    })?;

    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(full_content.as_bytes());
    let actual_sha256 = format!("{:x}", hasher.finalize());

    let lines: Vec<&str> = full_content.lines().collect();
    let total_lines = lines.len();

    let offset_line = args
        .get("offset_line")
        .and_then(|v| v.as_u64())
        .map(|v| v.max(1) as usize)
        .or(cursor_offset)
        .unwrap_or(1);

    let line_limit = args
        .get("line_limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(100)
        .clamp(1, 500) as usize;

    let rel_display = file_path
        .strip_prefix(&state.vault_root)
        .ok()
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_else(|| file_path.to_string_lossy().replace('\\', "/"));

    let source_id = registered_sha256.as_deref().unwrap_or(&actual_sha256);
    let uri = format!("ovp://source/{source_id}");

    let start_idx = offset_line.saturating_sub(1).min(total_lines);

    // Version drift check (A3)
    if let Some(expected) = &effective_expected_hash
        && !hash_matches(expected, &actual_sha256)
    {
        let reason = format!(
            "Source version drift detected for `{rel_display}`: expected hash `{expected}` does not match current hash `{actual_sha256}`. Note was modified mid-reading; restart pagination from line 1 with the current hash to prevent mixed pages."
        );

        let payload = serde_json::json!({
            "status": "version_changed",
            "version_changed": true,
            "error": "version_changed",
            "reason": reason,
            "version_invalidation_reason": "source_hash_mismatch",
            "path": rel_display,
            "uri": uri,
            "title": title,
            "registered_sha256": registered_sha256,
            "expected_source_hash": expected,
            "current_source_hash": actual_sha256,
            "sha256": actual_sha256,
            "source_hash": actual_sha256,
            "offset_line": offset_line,
            "line_limit": line_limit,
            "total_lines": total_lines,
            "lines_returned": 0,
            "has_more": false,
            "next_cursor": Value::Null,
            "content": "",
            "raw_content": "",
        });

        let text = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "{}".into());
        return Ok(serde_json::json!({
            "content": [{ "type": "text", "text": text }],
            "isError": true
        }));
    }

    let end_idx = (start_idx + line_limit).min(total_lines);
    let lines_returned = end_idx.saturating_sub(start_idx);
    let has_more = end_idx < total_lines;

    let next_cursor = if has_more {
        Some(encode_cursor(
            &actual_sha256,
            end_idx + 1,
            Some(&rel_display),
        ))
    } else {
        None
    };

    let mut rendered_lines = Vec::with_capacity(lines_returned);
    for (idx, line) in lines.iter().enumerate().take(end_idx).skip(start_idx) {
        rendered_lines.push(format!("{:>5} | {}", idx + 1, line));
    }
    let body_text = rendered_lines.join("\n");
    let raw_text = lines[start_idx..end_idx].join("\n");

    let payload = serde_json::json!({
        "status": "ok",
        "version_changed": false,
        "path": rel_display,
        "uri": uri,
        "registered_sha256": registered_sha256,
        "sha256": actual_sha256,
        "source_hash": actual_sha256,
        "title": title,
        "offset_line": offset_line,
        "line_limit": line_limit,
        "total_lines": total_lines,
        "lines_returned": lines_returned,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "content": body_text,
        "raw_content": raw_text,
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

    let kind = args.get("kind").and_then(|v| v.as_str()).unwrap_or("all");

    if !matches!(kind, "all" | "sources" | "claims" | "packs" | "evidence") {
        return Err(RpcError {
            code: -32602,
            message: format!(
                "Invalid kind `{kind}`: expected one of 'all', 'sources', 'claims', 'packs', 'evidence'"
            ),
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
            let claims_val =
                ovp_memory::vault_tools::search_claims(m, &records, query, limit, None);
            if let Some(hits) = claims_val.get("hits").and_then(|v| v.as_array()) {
                claim_matches = hits.len();
                total_matches += claim_matches;
                for (rank, hit) in hits.iter().enumerate() {
                    let key = hit.get("claim_key").and_then(|v| v.as_str()).unwrap_or("");
                    let id = hit.get("claim_id").and_then(|v| v.as_str()).unwrap_or("");
                    let claim_text = hit.get("claim").and_then(|v| v.as_str()).unwrap_or("");
                    let theme = hit.get("theme").and_then(|v| v.as_str()).unwrap_or("");
                    let status = hit
                        .get("status")
                        .and_then(|v| v.as_str())
                        .unwrap_or("durable");
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
                .filter(|r| {
                    r.claim.to_lowercase().contains(&q_lower)
                        || r.theme.to_lowercase().contains(&q_lower)
                })
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
    if search_sources && let Some(m) = &model {
        let sources_val = ovp_memory::vault_tools::search_sources(m, query, limit);
        if let Some(hits) = sources_val.get("hits").and_then(|v| v.as_array()) {
            let src_count = hits.len();
            lexical_matches += src_count;
            total_matches += src_count;
            for (rank, hit) in hits.iter().enumerate() {
                let sha = hit.get("source_id").and_then(|v| v.as_str()).unwrap_or("");
                let title = hit
                    .get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Untitled");
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
    if search_evidence && let Some(m) = &model {
        let evidence_val = ovp_memory::vault_tools::search_evidence(m, query, limit);
        if let Some(hits) = evidence_val.get("hits").and_then(|v| v.as_array()) {
            let pack_count = hits.len();
            lexical_matches += pack_count;
            total_matches += pack_count;
            for (rank, pack) in hits.iter().enumerate() {
                let dir = pack.get("pack_dir").and_then(|v| v.as_str()).unwrap_or("");
                let title = pack
                    .get("pack_title")
                    .and_then(|v| v.as_str())
                    .unwrap_or(dir);
                let cards = pack
                    .get("matched_cards")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|c| c.as_str())
                            .collect::<Vec<_>>()
                            .join("; ")
                    })
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

    let lexical_status = if model.is_some() {
        "ready"
    } else {
        "missing_index"
    };

    // tool_ovp_search executes lexical index and crystal claims only; semantic vector queries are not executed by this tool.
    let semantic_status = "disabled";

    let indexed_caveated_claims_count = model
        .as_ref()
        .map(|m| {
            m.claims
                .iter()
                .filter(|row| row.status == ovp_index::ClaimStatus::Caveated)
                .count()
        })
        .unwrap_or(0);
    let durable_claims_count = records.len();
    let total_claims_available = durable_claims_count + indexed_caveated_claims_count;

    let claims_status = if total_claims_available > 0 {
        "ready"
    } else if model.is_some() {
        "empty"
    } else {
        "missing_index"
    };

    let mut warnings = Vec::new();
    if model.is_none() {
        warnings.push("Lexical index is missing (.ovp/index.json not found). Search cannot match sources or reader packs; 0 matches must NOT be interpreted as 'no data in vault'. Run `ovp2 index` to index.".to_string());
    }
    warnings.push(if has_dense {
        "Dense semantic vector store is present on disk, but dense vector retrieval is disabled (not executed by ovp_search).".to_string()
    } else {
        "Dense semantic vector retrieval is disabled (no embeddings in .ovp/embeddings). Search runs across lexical and crystal graph lanes only.".to_string()
    });
    if total_claims_available == 0 && model.is_none() {
        warnings.push(
            "Crystal claims ledger is empty or missing. Durable claims cannot be retrieved."
                .to_string(),
        );
    }

    let has_unindexed_or_disabled_lanes = lexical_status != "ready" || semantic_status != "ready";
    let coverage_warning = if model.is_none() && total_matches == 0 {
        Some(
            "Search yielded 0 matches, but lexical index is missing and semantic retrieval is disabled. This reflects incomplete retrieval coverage, NOT an empty vault.",
        )
    } else if total_matches == 0 {
        Some(
            "Search yielded 0 matches across active lanes (lexical, claims). Semantic vector retrieval is disabled.",
        )
    } else {
        Some(
            "One or more search lanes are disabled or unindexed; results reflect active lanes only.",
        )
    };

    let coverage = serde_json::json!({
        "lexical": {
            "status": lexical_status,
            "available": model.is_some(),
            "match_count": lexical_matches,
            "detail": if let Some(m) = &model {
                format!("BM25/FTS index active across {} sources and {} packs", m.sources.len(), m.packs.len())
            } else {
                "Index not loaded — run `ovp2 index` to enable lexical search".into()
            }
        },
        "semantic": {
            "status": semantic_status,
            "available": false,
            "match_count": 0,
            "detail": if has_dense {
                "Dense embeddings present on disk, but vector retrieval is not queried by ovp_search"
            } else {
                "Dense embeddings not configured — fallback to lexical & crystal graph"
            }
        },
        "claims": {
            "status": claims_status,
            "available": total_claims_available > 0,
            "match_count": claim_matches,
            "detail": if durable_claims_count > 0 && indexed_caveated_claims_count > 0 {
                format!("Durable crystal ledger with {} active records and {} indexed caveated claims", durable_claims_count, indexed_caveated_claims_count)
            } else if durable_claims_count > 0 {
                format!("Durable crystal ledger with {} active records", durable_claims_count)
            } else if indexed_caveated_claims_count > 0 {
                format!("Indexed caveated claims with {} records (durable ledger empty)", indexed_caveated_claims_count)
            } else {
                "No claims in ledger or index".to_string()
            }
        },
        "has_unindexed_or_disabled_lanes": has_unindexed_or_disabled_lanes,
        "warnings": warnings,
    });

    let payload = serde_json::json!({
        "query": query,
        "kind": kind,
        "total_matches": total_matches,
        "returned_matches": results.len(),
        "truncated": truncated,
        "coverage": coverage,
        "coverage_warning": coverage_warning,
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
                        let cits =
                            ovp_domain::crystal::theme_pages::extract_claim_citations(body_text);
                        for c in cits {
                            if sample_claims.len() < 3
                                && !sample_claims.iter().any(|sc| sc["key"] == c)
                            {
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
                    sc["key"]
                        .as_str()
                        .is_some_and(|k| k.to_lowercase().contains(q))
                        || sc["claim"]
                            .as_str()
                            .is_some_and(|c| c.to_lowercase().contains(q))
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
    // The real engine, not a hand-maintained echo of it: the CLI, the portal
    // and this tool must never disagree about what is wrong with a vault.
    let findings = ovp_doctor::run_checks(&state.vault_root, &ovp_doctor::DoctorOptions::default());
    let report = ovp_doctor::to_diagnostics(&findings);

    let mut lines: Vec<String> = Vec::new();
    for d in &report.diagnostics {
        let sev = match d.severity {
            ovp_domain::diagnostics::Severity::Error => "FAIL",
            ovp_domain::diagnostics::Severity::Warning => "WARN",
            ovp_domain::diagnostics::Severity::Info => "INFO",
        };
        lines.push(format!("{sev} {}: {}", d.code, d.message));
        if let Some(h) = &d.hint {
            lines.push(format!("  -> {h}"));
        }
    }
    let passes = findings.len() - report.diagnostics.len();
    lines.push(format!(
        "summary: {} pass, {} info, {} warn, {} fail",
        passes,
        report.count(ovp_domain::diagnostics::Severity::Info),
        report.count(ovp_domain::diagnostics::Severity::Warning),
        report.count(ovp_domain::diagnostics::Severity::Error),
    ));

    Ok(serde_json::json!({
        "content": [{ "type": "text", "text": lines.join("\n") }],
        "structuredContent": report,
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
            let json =
                serde_json::to_string(&claim_closure(&state.vault_root, record, model.as_ref()))
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
            if let Some(model) = state.load_model()
                && let Some(src) = model.sources.iter().find(|s| s.sha256 == sha)
            {
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
                return Ok(serde_json::json!({
                    "contents": [{ "uri": uri, "mimeType": "application/json", "text": json }]
                }));
            }

            // Fallback: resolve directly via resolve_source_note
            if let Ok((path, registered_sha, title)) = resolve_source_note(state, sha) {
                let rel = path
                    .strip_prefix(&state.vault_root)
                    .ok()
                    // Vault-relative paths are '/'-separated everywhere else in
                    // this file (see the three equivalent sites above). Without
                    // this, Windows yields '50-Inbox\\02-Processed\\...' and
                    // read_source_doc cannot open it, so `markdown` comes back null.
                    .map(|p| p.to_string_lossy().replace('\\', "/"));
                let (doc, truncated, err) = readers::read_source_doc(
                    &state.vault_root,
                    &state.layout,
                    rel.as_deref(),
                    registered_sha.as_deref().or(Some(sha)),
                );
                let json = serde_json::to_string(&serde_json::json!({
                    "uri": uri,
                    "source": {
                        "sha256": registered_sha.unwrap_or_else(|| sha.to_string()),
                        "title": title.unwrap_or_else(|| path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string()),
                        "rel_path": rel,
                    },
                    "markdown": doc,
                    "truncated": truncated,
                    "doc_error": err,
                }))
                .unwrap_or_else(|_| "{}".into());
                return Ok(serde_json::json!({
                    "contents": [{ "uri": uri, "mimeType": "application/json", "text": json }]
                }));
            }

            Err(RpcError {
                code: -32602,
                message: format!("No source with sha256 `{sha}`"),
            })
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
            assert_eq!(closure["claim_zh_status"], "missing");
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
            assert_eq!(out["page"]["sections_zh_status"], "missing");
            assert_eq!(out["claims"]["ck-aaa"]["claim_id"], "id-a");
            assert_eq!(out["claims"]["ck-aaa"]["claim_zh_status"], "missing");
            assert_eq!(out["claims"]["ck-bbb"]["claim_id"], "id-b");
            assert_eq!(out["claims"]["ck-bbb"]["claim_zh_status"], "missing");
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
                    usage: ovp_llm::Usage {
                        input_tokens: 1,
                        output_tokens: 1,
                    },
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
            tmp.path()
                .join(".ovp/ask-sessions/mcp-turn-test.jsonl")
                .is_file()
        );
        let md = std::fs::read_to_string(tmp.path().join(".ovp/chats/mcp-turn-test.md")).unwrap();
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
                    usage: ovp_llm::Usage {
                        input_tokens: 1,
                        output_tokens: 1,
                    },
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
        assert!(
            first["content"][0]["text"]
                .as_str()
                .unwrap()
                .starts_with("stable answer")
        );
        let retry = call(&state, "ask", args).unwrap();
        let text = retry["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("idempotent replay"), "{text}");
        assert_eq!(CALLS.load(Ordering::SeqCst), 1, "no second paid model call");
        let transcript =
            std::fs::read_to_string(tmp.path().join(".ovp/ask-sessions/mcp-retry-test.jsonl"))
                .unwrap();
        assert_eq!(transcript.matches("turn_finished").count(), 1);
        let md = std::fs::read_to_string(tmp.path().join(".ovp/chats/mcp-retry-test.md")).unwrap();
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
            err.message
                .contains("ask client configuration invalid: never built"),
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

        let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

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
                tool["readOnly"], true,
                "tool `{}` must be marked readOnly",
                tool["name"]
            );
            assert_eq!(
                tool["annotations"]["readOnlyHint"], true,
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
        assert!(
            coverage["claims"]["detail"]
                .as_str()
                .unwrap()
                .contains("2 active records")
        );
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

    #[test]
    fn doctor_tool_returns_the_real_engine_report() {
        // The fixture has a crystal ledger but no index and no reader packs,
        // so the engine must have something to say — and every entry must be
        // a doctor diagnostic in the shared shape, not a hand-written line.
        let (_tmp, state) = fixture_vault();
        let out = tool_doctor(&state).unwrap();
        let text = out["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("summary:"), "{text}");
        let diags = out["structuredContent"]["diagnostics"].as_array().unwrap();
        assert!(!diags.is_empty(), "{out}");
        for d in diags {
            assert_eq!(d["origin"], "doctor");
            assert!(d["code"].as_str().unwrap().starts_with("doctor."), "{d}");
            assert!(
                matches!(d["severity"].as_str(), Some("info" | "warning" | "error")),
                "{d}"
            );
        }
        assert!(
            diags.iter().any(|d| d["code"] == "doctor.stale-index"),
            "a vault with no index must surface the stale-index check: {out}"
        );
    }

    #[test]
    fn ovp_read_note_cursor_pagination_and_concatenation() {
        let (tmp, state) = fixture_vault();
        let note_rel = "Sources/2026-03-01_long_note.md";
        let note_path = tmp.path().join(note_rel);
        std::fs::create_dir_all(note_path.parent().unwrap()).unwrap();

        // 150 lines of unique text
        let lines: Vec<String> = (1..=150)
            .map(|i| format!("Document section {i}: paragraph explaining concept {i}."))
            .collect();
        std::fs::write(&note_path, lines.join("\n")).unwrap();

        // Page 1: lines 1-60
        let p1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "offset_line": 1,
                "line_limit": 60
            }),
        )
        .unwrap();
        let val1: Value = serde_json::from_str(&text_of(&p1)).unwrap();
        assert_eq!(val1["status"], "ok");
        assert_eq!(val1["version_changed"], false);
        assert_eq!(val1["offset_line"], 1);
        assert_eq!(val1["line_limit"], 60);
        assert_eq!(val1["total_lines"], 150);
        assert_eq!(val1["lines_returned"], 60);
        assert_eq!(val1["has_more"], true);
        let cur1 = val1["next_cursor"].as_str().expect("page 1 next_cursor");
        let hash1 = val1["sha256"].as_str().expect("page 1 sha256");
        assert!(cur1.starts_with(&format!("ovp_cur_v1:{hash1}:61")));
        let raw1 = val1["raw_content"].as_str().unwrap();

        // Page 2: lines 61-120 using cursor
        let p2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "cursor": cur1,
                "line_limit": 60
            }),
        )
        .unwrap();
        let val2: Value = serde_json::from_str(&text_of(&p2)).unwrap();
        assert_eq!(val2["status"], "ok");
        assert_eq!(val2["offset_line"], 61);
        assert_eq!(val2["lines_returned"], 60);
        assert_eq!(val2["has_more"], true);
        assert_eq!(val2["sha256"], hash1);
        let cur2 = val2["next_cursor"].as_str().expect("page 2 next_cursor");
        assert!(cur2.starts_with(&format!("ovp_cur_v1:{hash1}:121")));
        let raw2 = val2["raw_content"].as_str().unwrap();

        // Page 3: lines 121-150 using cursor
        let p3 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "cursor": cur2,
                "line_limit": 60
            }),
        )
        .unwrap();
        let val3: Value = serde_json::from_str(&text_of(&p3)).unwrap();
        assert_eq!(val3["status"], "ok");
        assert_eq!(val3["offset_line"], 121);
        assert_eq!(val3["lines_returned"], 30);
        assert_eq!(val3["has_more"], false);
        assert!(val3["next_cursor"].is_null());
        assert_eq!(val3["sha256"], hash1);
        let raw3 = val3["raw_content"].as_str().unwrap();

        // Verify seamless concatenation across all 3 pages
        let concatenated = format!("{raw1}\n{raw2}\n{raw3}");
        assert_eq!(concatenated, lines.join("\n"));
    }

    #[test]
    fn ovp_read_note_detects_version_drift_mid_reading() {
        let (tmp, state) = fixture_vault();
        let note_rel = "Sources/2026-03-01_drift_note.md";
        let note_path = tmp.path().join(note_rel);
        std::fs::create_dir_all(note_path.parent().unwrap()).unwrap();

        std::fs::write(
            &note_path,
            "Line 1 original\nLine 2 original\nLine 3 original",
        )
        .unwrap();

        // Read page 1
        let p1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "offset_line": 1,
                "line_limit": 2
            }),
        )
        .unwrap();
        let val1: Value = serde_json::from_str(&text_of(&p1)).unwrap();
        let original_hash = val1["sha256"].as_str().unwrap().to_string();
        let cursor1 = val1["next_cursor"].as_str().unwrap().to_string();

        // Note is modified on disk mid-reading
        std::fs::write(
            &note_path,
            "Line 1 MODIFIED\nLine 2 original\nLine 3 original\nLine 4 new",
        )
        .unwrap();

        // Attempt to read next page using cursor (which embeds original_hash)
        let drift_resp = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "cursor": cursor1,
                "line_limit": 2
            }),
        )
        .unwrap();

        // Outer RPC call response carries isError: true
        assert_eq!(drift_resp["isError"], true);

        let drift_val: Value = serde_json::from_str(&text_of(&drift_resp)).unwrap();
        assert_eq!(drift_val["status"], "version_changed");
        assert_eq!(drift_val["version_changed"], true);
        assert_eq!(
            drift_val["version_invalidation_reason"],
            "source_hash_mismatch"
        );
        assert_eq!(drift_val["expected_source_hash"], original_hash);
        assert_ne!(drift_val["current_source_hash"], original_hash);
        assert_eq!(drift_val["lines_returned"], 0);
        assert_eq!(drift_val["has_more"], false);
        assert!(drift_val["next_cursor"].is_null());
        assert_eq!(drift_val["content"], "");
        assert!(
            drift_val["reason"]
                .as_str()
                .unwrap()
                .contains("Source version drift detected")
        );

        // Direct explicit expected_source_hash also fails loud with version_changed
        let direct_drift = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "offset_line": 2,
                "expected_source_hash": original_hash
            }),
        )
        .unwrap();
        assert_eq!(direct_drift["isError"], true);
        let direct_val: Value = serde_json::from_str(&text_of(&direct_drift)).unwrap();
        assert_eq!(direct_val["status"], "version_changed");
    }

    #[test]
    fn ovp_read_note_resolves_lifecycle_moved_note_by_hash_or_path() {
        let (tmp, state) = fixture_vault();

        // Put a processed note in 50-Inbox/03-Processed/2026-03/article-a1b2c3d4.md
        let proc_dir = tmp.path().join(state.layout.processed_dir("2026-03"));
        std::fs::create_dir_all(&proc_dir).unwrap();
        let file_path = proc_dir.join("article-a1b2c3d4.md");
        std::fs::write(&file_path, "Processed article content\nLine 2").unwrap();

        // 1. Resolve by previous raw path (50-Inbox/01-Raw/2026-03/article-a1b2c3d4.md)
        let raw_key = format!(
            "{}/2026-03/article-a1b2c3d4.md",
            state.layout.inbox_raw_dir()
        );
        let resp1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": raw_key }),
        )
        .unwrap();
        let val1: Value = serde_json::from_str(&text_of(&resp1)).unwrap();
        assert_eq!(val1["status"], "ok");
        assert_eq!(val1["total_lines"], 2);

        // 2. Resolve by hash8 stem
        let resp2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "a1b2c3d4" }),
        )
        .unwrap();
        let val2: Value = serde_json::from_str(&text_of(&resp2)).unwrap();
        assert_eq!(val2["status"], "ok");
        assert_eq!(val2["total_lines"], 2);
    }

    #[test]
    fn ovp_search_reports_honest_coverage_for_unindexed_and_disabled_lanes() {
        let (_tmp, state) = fixture_vault();

        let resp = call(
            &state,
            "ovp_search",
            serde_json::json!({ "query": "nonexistent_term_xyz", "kind": "sources" }),
        )
        .unwrap();
        let val: Value = serde_json::from_str(&text_of(&resp)).unwrap();

        assert_eq!(val["total_matches"], 0);
        let cov = &val["coverage"];
        assert_eq!(cov["lexical"]["status"], "missing_index");
        assert_eq!(cov["lexical"]["available"], false);
        assert_eq!(cov["semantic"]["status"], "disabled");
        assert_eq!(cov["semantic"]["available"], false);
        assert_eq!(cov["has_unindexed_or_disabled_lanes"], true);

        // Crucial A4 check: 0 matches must carry a coverage warning explaining that 0 matches does not mean empty vault!
        let warning = val["coverage_warning"]
            .as_str()
            .expect("coverage_warning must be present");
        assert!(
            warning.contains("NOT an empty vault"),
            "Warning was: {warning}"
        );
    }

    #[test]
    fn multi_host_identical_document_location_and_deep_citation_reading() {
        let (tmp, state) = fixture_vault();

        // Setup a shared note with a citation-worthy statement
        let note_rel = "Sources/2026-03-01_architecture_spec.md";
        let note_path = tmp.path().join(note_rel);
        std::fs::create_dir_all(note_path.parent().unwrap()).unwrap();
        let doc_content = "\
# Architecture Specification
Line 2: System boundary definition
Line 3: The unified kernel enforces strict read-only execution on all citation paths.
Line 4: Verification receipt generation.
Line 5: End of specification.";
        std::fs::write(&note_path, doc_content).unwrap();

        // Compute expected hash of document
        use sha2::{Digest, Sha256};
        let expected_hash = format!("{:x}", Sha256::digest(doc_content.as_bytes()));

        // --- Host 1 (Claude Code / Droid simulator) ---
        // 1. Initialize session
        let init_1 = dispatch(&state, "initialize", &serde_json::json!({})).unwrap();
        assert_eq!(init_1["protocolVersion"], "2024-11-05");

        // 2. Discover tools
        let tools_1 = dispatch(&state, "tools/list", &serde_json::json!({})).unwrap();
        assert!(
            tools_1["tools"]
                .as_array()
                .unwrap()
                .iter()
                .any(|t| t["name"] == "ovp_read_note")
        );

        // 3. Read note with offset and limit
        let read_1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "offset_line": 1,
                "line_limit": 3
            }),
        )
        .unwrap();
        let val_1: Value = serde_json::from_str(&text_of(&read_1)).unwrap();
        assert_eq!(val_1["sha256"], expected_hash);
        assert_eq!(val_1["uri"], format!("ovp://source/{expected_hash}"));
        let cursor_1 = val_1["next_cursor"].as_str().unwrap().to_string();

        // 4. Continue reading citation via cursor
        let read_1_p2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "cursor": cursor_1,
                "line_limit": 3
            }),
        )
        .unwrap();
        let val_1_p2: Value = serde_json::from_str(&text_of(&read_1_p2)).unwrap();
        assert_eq!(val_1_p2["offset_line"], 4);
        assert!(
            val_1_p2["content"]
                .as_str()
                .unwrap()
                .contains("Verification receipt generation")
        );

        // --- Host 2 (Cursor / Codex simulator) ---
        // 1. Initialize session
        let init_2 = dispatch(&state, "initialize", &serde_json::json!({})).unwrap();
        assert_eq!(init_2["protocolVersion"], "2024-11-05");

        // 2. Direct read of the identical note using expected_source_hash pin
        let read_2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": note_rel,
                "expected_source_hash": expected_hash,
                "offset_line": 3,
                "line_limit": 2
            }),
        )
        .unwrap();
        let val_2: Value = serde_json::from_str(&text_of(&read_2)).unwrap();

        // Parity verification across both hosts (A1 & A6)
        assert_eq!(
            val_1["sha256"], val_2["sha256"],
            "Both hosts read exact same sha256"
        );
        assert_eq!(
            val_1["uri"], val_2["uri"],
            "Both hosts resolve exact same resource URI"
        );
        assert!(
            val_2["content"]
                .as_str()
                .unwrap()
                .contains("Line 3: The unified kernel enforces strict read-only execution")
        );
    }

    #[test]
    fn stdio_transport_initialize_tools_list_and_call_stream() {
        let (tmp, _state) = fixture_vault();
        let note_rel = "Sources/stdio_test_doc.md";
        let note_path = tmp.path().join(note_rel);
        std::fs::create_dir_all(note_path.parent().unwrap()).unwrap();
        let lines: Vec<String> = (1..=20).map(|i| format!("Stdio line {i}")).collect();
        std::fs::write(&note_path, lines.join("\n")).unwrap();

        let req1 = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        });
        let req2 = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        });
        let req3 = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ovp_read_note",
                "arguments": {
                    "key": note_rel,
                    "offset_line": 1,
                    "line_limit": 10
                }
            }
        });

        let mut input_bytes = Vec::new();
        input_bytes.extend(serde_json::to_vec(&req1).unwrap());
        input_bytes.push(b'\n');
        input_bytes.extend(serde_json::to_vec(&req2).unwrap());
        input_bytes.push(b'\n');
        input_bytes.extend(serde_json::to_vec(&req3).unwrap());
        input_bytes.push(b'\n');

        let mut output_bytes = Vec::new();
        let cfg = McpConfig {
            vault_root: tmp.path().to_path_buf(),
            ask_client: None,
        };

        run_mcp_io(cfg, std::io::Cursor::new(input_bytes), &mut output_bytes).unwrap();

        let out_str = String::from_utf8(output_bytes).unwrap();
        let resp_lines: Vec<&str> = out_str.lines().filter(|l| !l.trim().is_empty()).collect();
        assert_eq!(resp_lines.len(), 3);

        // Verify response 1 (initialize)
        let r1: Value = serde_json::from_str(resp_lines[0]).unwrap();
        assert_eq!(r1["id"], 1);
        assert_eq!(r1["result"]["protocolVersion"], "2024-11-05");

        // Verify response 2 (tools/list)
        let r2: Value = serde_json::from_str(resp_lines[1]).unwrap();
        assert_eq!(r2["id"], 2);
        let tools = r2["result"]["tools"].as_array().unwrap();
        assert!(tools.iter().any(|t| t["name"] == "ovp_read_note"));

        // Verify response 3 (tools/call ovp_read_note)
        let r3: Value = serde_json::from_str(resp_lines[2]).unwrap();
        assert_eq!(r3["id"], 3);
        let content_text = r3["result"]["content"][0]["text"].as_str().unwrap();
        let parsed_note: Value = serde_json::from_str(content_text).unwrap();
        assert_eq!(parsed_note["offset_line"], 1);
        assert_eq!(parsed_note["lines_returned"], 10);
        assert_eq!(parsed_note["has_more"], true);
        assert!(parsed_note["next_cursor"].as_str().unwrap().contains(":11"));
    }

    #[test]
    fn resource_read_source_fallback_and_full_hash_stem_validation() {
        let (tmp, state) = fixture_vault();

        let content = "Processed article content for testing full sha resolution.";
        use sha2::{Digest, Sha256};
        let actual_hash = format!("{:x}", Sha256::digest(content.as_bytes()));
        let hash8 = &actual_hash[..8];

        // 1. Write an unindexed note in processed directory with stem article-<hash8>.md
        let proc_dir = tmp.path().join(state.layout.processed_dir("2026-03"));
        std::fs::create_dir_all(&proc_dir).unwrap();
        let file_path = proc_dir.join(format!("article-{hash8}.md"));
        std::fs::write(&file_path, content).unwrap();

        // Read note by 8-char stem
        let read_val = call(&state, "ovp_read_note", serde_json::json!({ "key": hash8 })).unwrap();
        let parsed_read: Value = serde_json::from_str(&text_of(&read_val)).unwrap();
        assert_eq!(parsed_read["status"], "ok");
        assert_eq!(parsed_read["sha256"], actual_hash);
        let resource_uri = parsed_read["uri"].as_str().unwrap();

        // Verify resources/read can open this URI even when model.sources is absent
        let res_resp = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": resource_uri }),
        )
        .unwrap();
        let contents = res_resp["contents"].as_array().unwrap();
        assert_eq!(contents.len(), 1);
        let res_payload: Value =
            serde_json::from_str(contents[0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(res_payload["markdown"], content);

        // Verify that long hash lookup (> 8 chars) that starts with hash8 but doesn't match actual_hash fails
        let bogus_long_sha = format!("{hash8}deadbeef{}", "0".repeat(48));
        let bad_lookup = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": bogus_long_sha }),
        );
        assert!(
            bad_lookup.is_err(),
            "bogus long hash must not match just by stem"
        );

        // Verify that full actual_hash succeeds
        let good_lookup = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": actual_hash }),
        );
        assert!(good_lookup.is_ok(), "full matching sha256 must resolve");

        // 2. Unindexed note in Sources/ without -<hash8> in filename (ordinary capture)
        let sources_dir = tmp.path().join("Sources");
        std::fs::create_dir_all(&sources_dir).unwrap();
        let ordinary_path = sources_dir.join("ordinary_article.md");
        let ordinary_content =
            "This is an unindexed article in Sources without any hash stem in its name.";
        std::fs::write(&ordinary_path, ordinary_content).unwrap();

        let ordinary_hash = format!("{:x}", Sha256::digest(ordinary_content.as_bytes()));
        let ordinary_read = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "Sources/ordinary_article.md" }),
        )
        .unwrap();
        let parsed_ordinary: Value = serde_json::from_str(&text_of(&ordinary_read)).unwrap();
        assert_eq!(parsed_ordinary["status"], "ok");
        assert_eq!(parsed_ordinary["sha256"], ordinary_hash);
        let ordinary_uri = parsed_ordinary["uri"].as_str().unwrap();
        assert_eq!(ordinary_uri, format!("ovp://source/{ordinary_hash}"));

        // Verify resources/read can resolve ordinary_article by its ovp://source/<hash> URI!
        let ordinary_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": ordinary_uri }),
        )
        .unwrap();
        let res_contents = ordinary_res["contents"].as_array().unwrap();
        assert_eq!(res_contents.len(), 1);
        let res_doc: Value =
            serde_json::from_str(res_contents[0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(res_doc["markdown"], ordinary_content);
        assert_eq!(res_doc["truncated"], false);
        assert_eq!(res_doc["doc_error"], Value::Null);

        // 3. Response-size cap and truncation behavior on large unindexed note
        let large_path = sources_dir.join("large_note.md");
        let large_content = "x".repeat(ovp_api_projection::MAX_SOURCE_DOC_BYTES + 200);
        std::fs::write(&large_path, &large_content).unwrap();
        let large_hash = format!("{:x}", Sha256::digest(large_content.as_bytes()));
        let large_uri = format!("ovp://source/{large_hash}");

        let large_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": large_uri }),
        )
        .unwrap();
        let large_doc: Value =
            serde_json::from_str(large_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(
            large_doc["truncated"], true,
            "large note must be bounded by MAX_SOURCE_DOC_BYTES"
        );
        assert!(
            large_doc["markdown"].as_str().unwrap().len()
                <= ovp_api_projection::MAX_SOURCE_DOC_BYTES
        );

        // 4. Root-level note and deeply nested note resolution
        let root_content = "Root-level note content.";
        let root_hash = format!("{:x}", Sha256::digest(root_content.as_bytes()));
        std::fs::write(tmp.path().join("root_note.md"), root_content).unwrap();

        let root_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": format!("ovp://source/{root_hash}") }),
        )
        .unwrap();
        let root_doc: Value =
            serde_json::from_str(root_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(root_doc["markdown"], root_content);

        let nested_dir = tmp.path().join("Sources/topic/nested");
        std::fs::create_dir_all(&nested_dir).unwrap();
        let nested_content = "Deeply nested note content.";
        let nested_hash = format!("{:x}", Sha256::digest(nested_content.as_bytes()));
        std::fs::write(nested_dir.join("nested_doc.md"), nested_content).unwrap();

        let nested_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": format!("ovp://source/{nested_hash}") }),
        )
        .unwrap();
        let nested_doc: Value =
            serde_json::from_str(nested_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(nested_doc["markdown"], nested_content);

        // 5. Conflicting cursor and explicit hashes validation
        let conflict_call = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": "root_note.md",
                "cursor": format!("ovp_cur_v1:{root_hash}:1"),
                "expected_source_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
            }),
        );
        assert!(
            conflict_call.is_err(),
            "conflicting cursor and expected_source_hash must be rejected"
        );
        let err = conflict_call.unwrap_err();
        assert_eq!(err.code, -32602);
        assert!(err.message.contains("Conflicting expected hashes"));

        // 6. Notes in ordinary vault folder named `docs` must be resolvable via resources/read
        let docs_dir = tmp.path().join("docs");
        std::fs::create_dir_all(&docs_dir).unwrap();
        let docs_content = "Note stored in ordinary vault folder named docs.";
        let docs_hash = format!("{:x}", Sha256::digest(docs_content.as_bytes()));
        std::fs::write(docs_dir.join("article.md"), docs_content).unwrap();

        let docs_read = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "docs/article.md" }),
        )
        .unwrap();
        let docs_parsed: Value = serde_json::from_str(&text_of(&docs_read)).unwrap();
        assert_eq!(docs_parsed["status"], "ok");
        let docs_uri = docs_parsed["uri"].as_str().unwrap();
        assert_eq!(docs_uri, format!("ovp://source/{docs_hash}"));

        let docs_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": docs_uri }),
        )
        .unwrap();
        let docs_res_doc: Value =
            serde_json::from_str(docs_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(docs_res_doc["markdown"], docs_content);

        // 7. Retain note identity in continuation cursor when unindexed note opened by hash is edited
        let multi_content_v1 = "line 1\nline 2\nline 3\nline 4\n";
        let multi_hash_v1 = format!("{:x}", Sha256::digest(multi_content_v1.as_bytes()));
        let multi_file = docs_dir.join("paginated.md");
        std::fs::write(&multi_file, multi_content_v1).unwrap();

        // Page 1 opened by content hash
        let p1_call = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": multi_hash_v1,
                "line_limit": 2
            }),
        )
        .unwrap();
        let p1_parsed: Value = serde_json::from_str(&text_of(&p1_call)).unwrap();
        assert_eq!(p1_parsed["status"], "ok");
        let next_cursor = p1_parsed["next_cursor"].as_str().unwrap().to_string();

        // Edit the file on disk before page 2
        let multi_content_v2 = "line 1\nline 2\nline 3 modified\nline 4\n";
        let multi_hash_v2 = format!("{:x}", Sha256::digest(multi_content_v2.as_bytes()));
        std::fs::write(&multi_file, multi_content_v2).unwrap();

        // Page 2 using original key (old hash) and cursor must NOT return "Note not found".
        // It must locate the changed file and return version_changed!
        let p2_call = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": multi_hash_v1,
                "cursor": next_cursor
            }),
        )
        .unwrap();
        assert_eq!(p2_call["isError"], true);
        let p2_parsed: Value = serde_json::from_str(&text_of(&p2_call)).unwrap();
        assert_eq!(p2_parsed["status"], "version_changed");
        assert_eq!(p2_parsed["expected_source_hash"], multi_hash_v1);
        assert_eq!(p2_parsed["current_source_hash"], multi_hash_v2);

        // 8. Malformed cursor with non-ASCII UTF-8 characters must NOT panic and return -32602
        let bad_cursor_call = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": "docs/article.md",
                "cursor": "ovp_cur_v1:aaaaaaaa:1:中a"
            }),
        );
        assert!(
            bad_cursor_call.is_err(),
            "malformed cursor must return RpcError"
        );
        let bad_err = bad_cursor_call.unwrap_err();
        assert_eq!(bad_err.code, -32602);

        // 9. Identical duplicate unindexed notes must resolve unambiguously via full-hash resource read
        let dup_content = "Identical duplicate markdown content.";
        let dup_hash = format!("{:x}", Sha256::digest(dup_content.as_bytes()));
        std::fs::write(sources_dir.join("dup1.md"), dup_content).unwrap();
        std::fs::write(sources_dir.join("dup2.md"), dup_content).unwrap();

        let dup_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": format!("ovp://source/{dup_hash}") }),
        )
        .unwrap();
        let dup_doc: Value =
            serde_json::from_str(dup_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(dup_doc["markdown"], dup_content);

        // 10. Lifecycle move between pages: cursor retains identity across Raw -> Processed move
        let raw_dir = tmp.path().join("00-Inbox/raw");
        std::fs::create_dir_all(&raw_dir).unwrap();
        let cycle_content = "raw line 1\nraw line 2\nraw line 3\nraw line 4\n";
        let cycle_file = raw_dir.join("cycle.md");
        std::fs::write(&cycle_file, cycle_content).unwrap();

        let cycle_p1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": "00-Inbox/raw/cycle.md",
                "line_limit": 2
            }),
        )
        .unwrap();
        let cycle_p1_parsed: Value = serde_json::from_str(&text_of(&cycle_p1)).unwrap();
        assert_eq!(cycle_p1_parsed["status"], "ok");
        let cycle_cur = cycle_p1_parsed["next_cursor"].as_str().unwrap();

        // Move file from raw to processed with hash stem in filename
        let proc_month_dir = tmp.path().join("02-Processed/2026-09");
        std::fs::create_dir_all(&proc_month_dir).unwrap();
        let proc_file = proc_month_dir.join("cycle-12345678.md");
        std::fs::rename(&cycle_file, &proc_file).unwrap();

        // Continue reading page 2 with key pointing to new processed path and cursor having raw path
        let cycle_p2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": "02-Processed/2026-09/cycle-12345678.md",
                "cursor": cycle_cur
            }),
        )
        .unwrap();
        let cycle_p2_parsed: Value = serde_json::from_str(&text_of(&cycle_p2)).unwrap();
        assert_eq!(cycle_p2_parsed["status"], "ok");
        assert_eq!(cycle_p2_parsed["offset_line"], 3);

        // 11. Large unindexed note (> 10 MiB) without hash stem must resolve via resources/read
        let huge_file = docs_dir.join("huge_unindexed.md");
        let huge_data = "header\n".to_string() + &"y".repeat(10 * 1024 * 1024 + 500);
        std::fs::write(&huge_file, &huge_data).unwrap();
        let huge_hash = format!("{:x}", Sha256::digest(huge_data.as_bytes()));

        let huge_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": format!("ovp://source/{huge_hash}") }),
        )
        .unwrap();
        let huge_doc: Value =
            serde_json::from_str(huge_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(huge_doc["truncated"], true);

        // 12. Mismatched key identity: mistyped or unrelated key with valid cursor from Note A must be rejected
        let mismatch_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": "nonexistent/mistyped_note_b.md",
                "cursor": cycle_cur
            }),
        )
        .unwrap_err();
        assert_eq!(mismatch_err.code, -32602);
        assert!(
            mismatch_err
                .message
                .contains("Note not found for key `nonexistent/mistyped_note_b.md`")
        );

        // 13. Duplicate unindexed notes: opening by hash, editing selected note before page 2 returns version_changed
        let dup_dir = tmp.path().join("00-Inbox/duplicates");
        std::fs::create_dir_all(&dup_dir).unwrap();
        let dup_text = "line 1\nline 2\nline 3\nline 4\n";
        let dup1_file = dup_dir.join("dup1.md");
        let dup2_file = dup_dir.join("dup2.md");
        std::fs::write(&dup1_file, dup_text).unwrap();
        std::fs::write(&dup2_file, dup_text).unwrap();
        let dup_common_hash = format!("{:x}", Sha256::digest(dup_text.as_bytes()));

        let dup_p1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": dup_common_hash,
                "line_limit": 2
            }),
        )
        .unwrap();
        let dup_p1_val: Value = serde_json::from_str(&text_of(&dup_p1)).unwrap();
        assert_eq!(dup_p1_val["status"], "ok");
        let dup_cur = dup_p1_val["next_cursor"].as_str().unwrap();

        // Decode cursor to see which file was selected, then edit THAT file
        let (_, _, selected_rel) = parse_cursor(dup_cur).unwrap();
        let selected_file = tmp.path().join(selected_rel.unwrap());
        std::fs::write(&selected_file, "modified line 1\nmodified line 2\n").unwrap();

        // Reading page 2 using original hash and cursor must detect version drift on the selected file
        let dup_p2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": dup_common_hash,
                "cursor": dup_cur
            }),
        )
        .unwrap();
        assert_eq!(dup_p2["isError"], true);
        let dup_p2_val: Value = serde_json::from_str(&text_of(&dup_p2)).unwrap();
        assert_eq!(dup_p2_val["status"], "version_changed");

        // 14. Coverage status includes indexed claims when durable ledger is empty
        let index_model = ovp_index::IndexModel {
            schema: ovp_index::INDEX_SCHEMA.into(),
            date: "2026-09-01".into(),
            built_at: Some("2026-09-01T00:00:00Z".into()),
            run_id: Some("test-run".into()),
            totals: ovp_index::Totals::default(),
            sources: vec![],
            packs: vec![],
            claims: vec![ovp_index::ClaimRow {
                claim_id: "c-indexed-1".into(),
                claim_key: None,
                claim: "Indexed claim text".into(),
                theme: Some("Some theme".into()),
                theme_id: None,
                status: ovp_index::ClaimStatus::Caveated,
                sources: vec![],
                strength: None,
                run_id: None,
                run_date: None,
                lane: None,
                patched_by: None,
            }],
            runs: vec![],
            ops: ovp_index::OpsState::default(),
        };
        let tmp_empty = tempfile::tempdir().unwrap();
        ovp_index::write_index(tmp_empty.path(), &index_model).unwrap();

        let state_with_indexed_claims = McpState {
            vault_root: tmp_empty.path().to_path_buf(),
            layout: VaultLayout,
            ask_client: None,
        };
        let search_resp = call(
            &state_with_indexed_claims,
            "ovp_search",
            serde_json::json!({ "query": "Indexed", "kind": "claims" }),
        )
        .unwrap();
        let search_val: Value = serde_json::from_str(&text_of(&search_resp)).unwrap();
        assert_eq!(search_val["coverage"]["claims"]["status"], "ready");
        assert_eq!(search_val["coverage"]["claims"]["available"], true);
        assert!(search_val["total_matches"].as_u64().unwrap() >= 1);

        // 15. Stem matching prioritizes stem-hashed files and full-hash lookups exit early
        let stem_dir = tmp.path().join("02-Processed/stem_priority");
        std::fs::create_dir_all(&stem_dir).unwrap();
        let stem_note_content = "Stem note content for fast lookup.";
        let stem_note_sha = format!("{:x}", Sha256::digest(stem_note_content.as_bytes()));
        let stem_hash8 = &stem_note_sha[..8];
        let stem_file = stem_dir.join(format!("note-{stem_hash8}.md"));
        std::fs::write(&stem_file, stem_note_content).unwrap();

        // Also create a non-stem note
        let non_stem_content = "Non-stem note content without hash in filename.";
        let non_stem_sha = format!("{:x}", Sha256::digest(non_stem_content.as_bytes()));
        let non_stem_file = stem_dir.join("non_stem.md");
        std::fs::write(&non_stem_file, non_stem_content).unwrap();

        // Resolving by 8-char stem hash should resolve directly to stem note
        let (found_stem, _) = find_note_by_hash(tmp.path(), &VaultLayout, stem_hash8)
            .unwrap()
            .expect("should find stem note");
        assert_eq!(found_stem, stem_file);

        // Resolving by full 64-char hash should resolve cleanly for both stem and non-stem
        let (found_full_stem, _) = find_note_by_hash(tmp.path(), &VaultLayout, &stem_note_sha)
            .unwrap()
            .expect("should find stem note by full hash");
        assert_eq!(found_full_stem, stem_file);

        let (found_full_non_stem, _) = find_note_by_hash(tmp.path(), &VaultLayout, &non_stem_sha)
            .unwrap()
            .expect("should find non-stem note by full hash");
        assert_eq!(found_full_non_stem, non_stem_file);

        // 16. Raw note and processed copy coexistence: cursor continuation sticks to existing raw note
        let coexist_raw_dir = tmp.path().join("00-Inbox/raw/2026-09");
        let coexist_proc_dir = tmp.path().join("02-Processed/2026-09");
        std::fs::create_dir_all(&coexist_raw_dir).unwrap();
        std::fs::create_dir_all(&coexist_proc_dir).unwrap();

        let raw_lines = "raw line 1\nraw line 2\nraw line 3\nraw line 4\n";
        let raw_sha = format!("{:x}", Sha256::digest(raw_lines.as_bytes()));
        let proc_lines = "proc line 1\nproc line 2\nproc line 3\nproc line 4\n";
        let filename = "coexist_doc.md";

        let coexist_raw_file = coexist_raw_dir.join(filename);
        let coexist_proc_file = coexist_proc_dir.join(filename);
        std::fs::write(&coexist_raw_file, raw_lines).unwrap();
        std::fs::write(&coexist_proc_file, proc_lines).unwrap();

        // Read page 1 using raw_sha (hash of the raw note)
        let coexist_p1 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": raw_sha,
                "line_limit": 2
            }),
        )
        .unwrap();
        let coexist_p1_val: Value = serde_json::from_str(&text_of(&coexist_p1)).unwrap();
        assert_eq!(coexist_p1_val["status"], "ok");
        let coexist_cur = coexist_p1_val["next_cursor"].as_str().unwrap();

        // Page 2 using cursor must continue reading raw note, NOT switch to proc note
        let coexist_p2 = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": raw_sha,
                "cursor": coexist_cur
            }),
        )
        .unwrap();
        let coexist_p2_val: Value = serde_json::from_str(&text_of(&coexist_p2)).unwrap();
        assert_eq!(coexist_p2_val["status"], "ok");
        assert_eq!(coexist_p2_val["offset_line"], 3);
        assert!(
            coexist_p2_val["raw_content"]
                .as_str()
                .unwrap()
                .contains("raw line 3")
        );

        // 17. Unindexed uppercase .MD file and symlinked note resolve cleanly via emitted ovp://source/<hash> URI
        let upper_content = "Uppercase markdown note content.";
        let upper_file = sources_dir.join("uppercase_article.MD");
        std::fs::write(&upper_file, upper_content).unwrap();

        let upper_read = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "Sources/uppercase_article.MD" }),
        )
        .unwrap();
        let upper_read_val: Value = serde_json::from_str(&text_of(&upper_read)).unwrap();
        assert_eq!(upper_read_val["status"], "ok");
        let upper_uri = upper_read_val["uri"].as_str().unwrap();
        assert!(upper_uri.starts_with("ovp://source/"));

        // Dereference emitted URI via resources/read
        let upper_res = dispatch(
            &state,
            "resources/read",
            &serde_json::json!({ "uri": upper_uri }),
        )
        .unwrap();
        let upper_doc: Value =
            serde_json::from_str(upper_res["contents"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(upper_doc["markdown"], upper_content);

        // 18. Notes inside symlinked folders resolve cleanly via emitted URI, and excluded root dirs reject direct reading
        let ext_dir = tempfile::tempdir().unwrap();
        let ext_note = ext_dir.path().join("ext_doc.md");
        let ext_content = "External content inside symlinked directory.";
        std::fs::write(&ext_note, ext_content).unwrap();

        // Symlink external dir into vault root
        #[cfg(unix)]
        {
            let symlink_dir = tmp.path().join("LinkedDocs");
            std::os::unix::fs::symlink(ext_dir.path(), &symlink_dir).unwrap();

            let sym_read = call(
                &state,
                "ovp_read_note",
                serde_json::json!({ "key": "LinkedDocs/ext_doc.md" }),
            )
            .unwrap();
            let sym_read_val: Value = serde_json::from_str(&text_of(&sym_read)).unwrap();
            assert_eq!(sym_read_val["status"], "ok");
            let sym_uri = sym_read_val["uri"].as_str().unwrap();

            let sym_res = dispatch(
                &state,
                "resources/read",
                &serde_json::json!({ "uri": sym_uri }),
            )
            .unwrap();
            let sym_doc: Value =
                serde_json::from_str(sym_res["contents"][0]["text"].as_str().unwrap()).unwrap();
            assert_eq!(sym_doc["markdown"], ext_content);
        }

        // Direct reading of notes under root target/ or node_modules/ is rejected
        let target_dir = tmp.path().join("target");
        std::fs::create_dir_all(&target_dir).unwrap();
        std::fs::write(target_dir.join("build_artifact.md"), "build artifact").unwrap();

        let target_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "target/build_artifact.md" }),
        );
        assert!(target_err.is_err());

        // 19. Hidden markdown files (e.g. `.private-deadbeef.md`) are rejected both by direct path and by hash
        let hidden_file = sources_dir.join(".private-deadbeef.md");
        std::fs::write(&hidden_file, "secret hidden content").unwrap();

        let hidden_path_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "Sources/.private-deadbeef.md" }),
        );
        assert!(
            hidden_path_err.is_err(),
            "direct path reading of hidden file must be rejected"
        );

        let hidden_hash_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": "deadbeef" }),
        );
        assert!(
            hidden_hash_err.is_err(),
            "hash lookup of hidden file must be rejected"
        );

        // 20. Caller-supplied JSON cursor targeting non-markdown or hidden file via lifecycle path
        let proc_dir = tmp.path().join("50-Inbox/03-Processed/2026-09");
        std::fs::create_dir_all(&proc_dir).unwrap();
        let private_txt = proc_dir.join(".private.txt");
        std::fs::write(&private_txt, "super secret private txt").unwrap();

        // The key must RESOLVE for this guard to be reached at all — with an
        // unresolvable key the request dies at note lookup and the cursor is
        // never consulted, so the test would pass for the wrong reason.
        // Note also that `parse_cursor` reads `path` / `p`; a `rel_path` key is
        // silently dropped, which would leave the cursor carrying no path.
        let guard_note_rel = "50-Inbox/03-Processed/2026-09/cursor-guard.md";
        std::fs::write(
            tmp.path().join(guard_note_rel),
            "Line one\nLine two\nLine three",
        )
        .unwrap();

        let fake_cur_obj = serde_json::json!({
            "source_hash": "deadbeef12345678",
            "offset_line": 2,
            "path": "50-Inbox/03-Processed/2026-09/.private.txt"
        });
        let fake_cur_str = serde_json::to_string(&fake_cur_obj).unwrap();

        let bypass_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": guard_note_rel,
                "cursor": fake_cur_str
            }),
        );
        assert!(
            bypass_err.is_err(),
            "ineligible file reached via cursor lifecycle path must be rejected"
        );
        let bypass_msg = bypass_err.unwrap_err().message;
        assert!(
            bypass_msg.contains("Conflicting target note"),
            "must be refused by the cursor/key mismatch guard, not by an unrelated lookup failure; got: {bypass_msg}"
        );

        // 21. Duplicate stem matches with identical content succeed on 8-char lookup, while conflicting content is rejected
        let stem8 = "beefcafe";
        let dup_stem_dir = tmp.path().join("00-Inbox/dup_stem");
        std::fs::create_dir_all(&dup_stem_dir).unwrap();
        let stem_file1 = dup_stem_dir.join(format!("note1-{stem8}.md"));
        let stem_file2 = dup_stem_dir.join(format!("note2-{stem8}.md"));
        let identical_text = "identical content for both stem files\nline 2";
        std::fs::write(&stem_file1, identical_text).unwrap();
        std::fs::write(&stem_file2, identical_text).unwrap();

        let stem_read = call(&state, "ovp_read_note", serde_json::json!({ "key": stem8 })).unwrap();
        let stem_read_val: Value = serde_json::from_str(&text_of(&stem_read)).unwrap();
        assert_eq!(stem_read_val["status"], "ok");

        // Conflicting content with same 8-char stem suffix is rejected as ambiguous
        let conflict_stem8 = "cafebabe";
        let conf_file1 = dup_stem_dir.join(format!("note1-{conflict_stem8}.md"));
        let conf_file2 = dup_stem_dir.join(format!("note2-{conflict_stem8}.md"));
        std::fs::write(&conf_file1, "first text").unwrap();
        std::fs::write(&conf_file2, "second text different content").unwrap();

        let conf_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": conflict_stem8 }),
        )
        .unwrap_err();
        assert_eq!(conf_err.code, -32602);
        assert!(
            conf_err
                .message
                .contains("Multiple notes match content hash stem")
        );

        // 22. Ambiguity check between stem-named note and non-stem note sharing the same hash prefix
        let plain_file = dup_stem_dir.join("plain-unindexed-note.md");
        std::fs::write(&plain_file, "some plain note content without stem suffix").unwrap();
        let plain_sha = stream_sha256(&plain_file).unwrap();
        let plain_prefix = &plain_sha[..8];

        // Unique lookup for plain_prefix finds plain_file
        let plain_read = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": plain_prefix }),
        )
        .unwrap();
        let plain_read_val: Value = serde_json::from_str(&text_of(&plain_read)).unwrap();
        assert_eq!(plain_read_val["status"], "ok");

        // A stem note that has the same 8-char suffix in filename, but conflicting content
        let stem_conflict = dup_stem_dir.join(format!("stemconflict-{plain_prefix}.md"));
        std::fs::write(&stem_conflict, "completely different content in stem note").unwrap();

        // Reading by plain_prefix must detect ambiguity between stem note and non-stem note
        let ambig_err = call(
            &state,
            "ovp_read_note",
            serde_json::json!({ "key": plain_prefix }),
        )
        .unwrap_err();
        assert_eq!(ambig_err.code, -32602);
        assert!(
            ambig_err
                .message
                .contains("Multiple notes match content hash stem")
        );

        // 23. Relative-path alias continuation across lifecycle moves
        let raw_rel_dir = tmp.path().join("50-Inbox/01-Raw/2026-09");
        std::fs::create_dir_all(&raw_rel_dir).unwrap();
        let raw_rel_file = raw_rel_dir.join("article-lifecycle1.md");
        std::fs::write(
            &raw_rel_file,
            "First line of article\nSecond line of article\nThird line",
        )
        .unwrap();

        // Open with relative key "2026-09/article-lifecycle1.md" and line_limit=1
        let rel_key = "2026-09/article-lifecycle1.md";
        let page1_resp = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": rel_key,
                "line_limit": 1
            }),
        )
        .unwrap();
        let page1_val: Value = serde_json::from_str(&text_of(&page1_resp)).unwrap();
        assert_eq!(page1_val["status"], "ok");
        let cursor_rel = page1_val["next_cursor"].as_str().unwrap();

        // Move note from Raw to Processed (standard lifecycle move)
        let proc_rel_dir = tmp.path().join("50-Inbox/03-Processed/2026-09");
        std::fs::create_dir_all(&proc_rel_dir).unwrap();
        let proc_rel_file = proc_rel_dir.join("article-lifecycle1.md");
        std::fs::rename(&raw_rel_file, &proc_rel_file).unwrap();

        // Continue pagination using the SAME relative key and cursor
        let page2_resp = call(
            &state,
            "ovp_read_note",
            serde_json::json!({
                "key": rel_key,
                "cursor": cursor_rel,
                "line_limit": 1
            }),
        )
        .unwrap();
        let page2_val: Value = serde_json::from_str(&text_of(&page2_resp)).unwrap();
        assert_eq!(page2_val["status"], "ok");
        assert_eq!(page2_val["raw_content"], "Second line of article");

        // 24. Claims coverage reports "empty" when index has durable claims but active ledger is empty
        let non_caveated_index = ovp_index::IndexModel {
            schema: ovp_index::INDEX_SCHEMA.into(),
            date: "2026-09-01".into(),
            built_at: Some("2026-09-01T00:00:00Z".into()),
            run_id: Some("test-run".into()),
            totals: ovp_index::Totals::default(),
            sources: vec![],
            packs: vec![],
            claims: vec![ovp_index::ClaimRow {
                claim_id: "c-durable-1".into(),
                claim_key: None,
                claim: "Durable claim in index only".into(),
                theme: Some("Theme".into()),
                theme_id: None,
                status: ovp_index::ClaimStatus::Durable,
                sources: vec![],
                strength: None,
                run_id: None,
                run_date: None,
                lane: None,
                patched_by: None,
            }],
            runs: vec![],
            ops: ovp_index::OpsState::default(),
        };
        let tmp_non_caveated = tempfile::tempdir().unwrap();
        ovp_index::write_index(tmp_non_caveated.path(), &non_caveated_index).unwrap();
        let state_non_caveated = McpState {
            vault_root: tmp_non_caveated.path().to_path_buf(),
            layout: VaultLayout,
            ask_client: None,
        };
        let search_non_cav = call(
            &state_non_caveated,
            "ovp_search",
            serde_json::json!({ "query": "Durable", "kind": "claims" }),
        )
        .unwrap();
        let search_non_cav_val: Value = serde_json::from_str(&text_of(&search_non_cav)).unwrap();
        assert_eq!(search_non_cav_val["coverage"]["claims"]["status"], "empty");
        assert_eq!(search_non_cav_val["coverage"]["claims"]["available"], false);
    }
}
