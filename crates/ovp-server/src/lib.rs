//! `ovp-server` — synchronous localhost HTTP server for OVP console and API.
//!
//! Serves static console HTML from `.ovp/console/` and JSON API endpoints
//! (`/api/find`, `/api/search`, `/api/graph`, `/api/claim/:id`, `/api/flow`).
//! Uses `tiny_http` to avoid any async runtime dependency.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, RwLock};

use ovp_domain::crystal::{fold_ledger, CrystalStatus, DurableRecord, StoreEvent};
use ovp_domain::units::Unit;
use ovp_domain::VaultLayout;
use ovp_index::{read_index, run_query, IndexModel, Query, QueryKind};
use ovp_intake::read_jsonl;
use tiny_http::{Header, Method, Response, Server};

pub struct ServeConfig {
    pub vault_root: PathBuf,
    pub host: String,
    pub port: u16,
}

struct AppState {
    vault_root: PathBuf,
    layout: VaultLayout,
    model: RwLock<Option<IndexModel>>,
}

impl AppState {
    fn load_model(&self) -> Option<IndexModel> {
        read_index(&self.vault_root).ok()
    }

    fn current_model(&self) -> Option<IndexModel> {
        {
            let guard = self.model.read().unwrap();
            if guard.is_some() {
                return guard.clone();
            }
        }
        let fresh = self.load_model()?;
        let mut guard = self.model.write().unwrap();
        *guard = Some(fresh.clone());
        Some(fresh)
    }

    fn refresh_model(&self) {
        if let Ok(m) = read_index(&self.vault_root) {
            let mut guard = self.model.write().unwrap();
            *guard = Some(m);
        }
    }

    fn console_dir(&self) -> PathBuf {
        self.vault_root.join(self.layout.console_dir())
    }
}

pub fn run_server(config: ServeConfig) -> Result<(), String> {
    let bind = format!("{}:{}", config.host, config.port);
    let server =
        Server::http(&bind).map_err(|e| format!("failed to bind {bind}: {e}"))?;

    let state = Arc::new(AppState {
        vault_root: config.vault_root,
        layout: VaultLayout::new(),
        model: RwLock::new(None),
    });

    // Pre-load model
    state.refresh_model();

    eprintln!("ovp-server listening on http://{bind}");
    eprintln!("  console: http://{bind}/");
    eprintln!("  API:     http://{bind}/api/find?term=...");
    eprintln!("  reload:  http://{bind}/api/refresh");

    for request in server.incoming_requests() {
        let path = request.url().to_string();
        let method = request.method().clone();

        let resp = match (method, path.as_str()) {
            (Method::Get, "/api/refresh") => {
                state.refresh_model();
                json_response(200, r#"{"ok":true}"#)
            }
            (Method::Get, p) if p.starts_with("/api/find") => {
                handle_find(&state, &path)
            }
            (Method::Get, p) if p.starts_with("/api/search") => {
                handle_search(&state, &path)
            }
            (Method::Get, "/api/model") => handle_model(&state),
            (Method::Get, "/api/graph") => handle_graph(&state),
            (Method::Get, "/api/flow") => handle_flow(&state),
            (Method::Get, p) if p.starts_with("/api/claim/") => {
                handle_claim(&state, &path)
            }
            (Method::Get, "/api/sse") => handle_sse(&state),
            (Method::Get, _) => serve_static(&state, &path),
            _ => text_response(405, "Method Not Allowed"),
        };

        let _ = request.respond(resp);
    }

    Ok(())
}

fn handle_find(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let params = parse_query_string(url);
    let query = Query {
        kind: params.get("kind").and_then(|k| match k.as_str() {
            "sources" => Some(QueryKind::Sources),
            "packs" => Some(QueryKind::Packs),
            "claims" => Some(QueryKind::Claims),
            "runs" => Some(QueryKind::Runs),
            _ => None,
        }),
        status: params.get("status").cloned(),
        date: params.get("date").cloned(),
        term: params.get("term").cloned(),
    };

    let hits = run_query(&model, &query);
    let body = serde_json::to_string(&hits).unwrap_or_else(|_| "[]".into());
    json_response(200, &body)
}

fn handle_search(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let params = parse_query_string(url);
    let term = params.get("q").or_else(|| params.get("term")).cloned();
    let query = Query { kind: None, status: None, date: None, term };
    let hits = run_query(&model, &query);
    let body = serde_json::to_string(&hits).unwrap_or_else(|_| "[]".into());
    json_response(200, &body)
}

fn handle_model(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };
    let body = serde_json::to_string(&model).unwrap_or_else(|_| "{}".into());
    json_response(200, &body)
}

fn handle_sse(_state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let body = "event: ready\ndata: {}\n\n";
    let data = body.as_bytes().to_vec();
    let header =
        Header::from_bytes("Content-Type", "text/event-stream").unwrap();
    Response::from_data(data).with_header(header).with_status_code(200)
}

fn load_active_records(state: &AppState) -> Vec<DurableRecord> {
    let ledger_path = state
        .vault_root
        .join(state.layout.crystal_store_dir())
        .join("ledger.jsonl");
    let events: Vec<StoreEvent> = match read_jsonl(&ledger_path) {
        Ok(e) => e,
        Err(_) => return Vec::new(),
    };
    fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect()
}

fn handle_graph(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let records = load_active_records(state);
    let model = state.current_model();

    let source_lookup: HashMap<String, &ovp_index::SourceRow> = model
        .as_ref()
        .map(|m| {
            m.sources.iter().map(|s| (s.sha256.clone(), s)).collect()
        })
        .unwrap_or_default();
    let pack_lookup: HashMap<String, &ovp_index::PackRow> = model
        .as_ref()
        .map(|m| {
            m.packs
                .iter()
                .filter_map(|p| {
                    let case = p.pack_dir.rsplit('/').next()?;
                    Some((case.to_string(), p))
                })
                .collect()
        })
        .unwrap_or_default();

    #[derive(serde::Serialize)]
    struct GNode {
        id: String,
        #[serde(rename = "type")]
        node_type: String,
        label: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        theme: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        strength: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        url: Option<String>,
        degree: usize,
    }

    #[derive(serde::Serialize)]
    struct GEdge {
        source: String,
        target: String,
        #[serde(rename = "type")]
        edge_type: String,
    }

    let mut nodes: HashMap<String, GNode> = HashMap::new();
    let mut edges: Vec<GEdge> = Vec::new();

    for rec in &records {
        let claim_id = format!("claim:{}", rec.claim_key);
        nodes.entry(claim_id.clone()).or_insert_with(|| GNode {
            id: claim_id.clone(),
            node_type: "claim".into(),
            label: if rec.claim.len() > 80 {
                format!("{}…", &rec.claim[..77])
            } else {
                rec.claim.clone()
            },
            theme: Some(rec.theme.clone()),
            strength: Some(format!("{:?}", rec.strength).to_lowercase()),
            url: None,
            degree: 0,
        });

        for cit in &rec.citations {
            let unit_id = format!("unit:{}", cit.unit_id);
            nodes.entry(unit_id.clone()).or_insert_with(|| GNode {
                id: unit_id.clone(),
                node_type: "unit".into(),
                label: if cit.quote.len() > 60 {
                    format!("{}…", &cit.quote[..57])
                } else {
                    cit.quote.clone()
                },
                theme: None,
                strength: None,
                url: None,
                degree: 0,
            });

            edges.push(GEdge {
                source: claim_id.clone(),
                target: unit_id.clone(),
                edge_type: "cites".into(),
            });

            let source_node_id =
                if let Some(pack) = pack_lookup.get(cit.case_id.as_str()) {
                    let sha = pack.source_sha256.as_deref().unwrap_or(&cit.case_id);
                    let sid = format!("source:{}", sha);
                    let src = source_lookup.get(sha);
                    nodes.entry(sid.clone()).or_insert_with(|| GNode {
                        id: sid.clone(),
                        node_type: "source".into(),
                        label: src
                            .and_then(|s| s.title.clone())
                            .unwrap_or_else(|| pack.title.clone()),
                        theme: None,
                        strength: None,
                        url: src.and_then(|s| s.url.clone()),
                        degree: 0,
                    });
                    sid
                } else {
                    let sid = format!("source:{}", cit.case_id);
                    nodes.entry(sid.clone()).or_insert_with(|| GNode {
                        id: sid.clone(),
                        node_type: "source".into(),
                        label: cit.case_id.clone(),
                        theme: None,
                        strength: None,
                        url: None,
                        degree: 0,
                    });
                    sid
                };

            edges.push(GEdge {
                source: unit_id,
                target: source_node_id,
                edge_type: "extracted_from".into(),
            });
        }
    }

    for edge in &edges {
        if let Some(n) = nodes.get_mut(&edge.source) {
            n.degree += 1;
        }
        if let Some(n) = nodes.get_mut(&edge.target) {
            n.degree += 1;
        }
    }

    let body = serde_json::json!({
        "nodes": nodes.into_values().collect::<Vec<_>>(),
        "edges": edges,
    });
    json_response(200, &body.to_string())
}

fn handle_claim(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let id = url.strip_prefix("/api/claim/").unwrap_or("");
    let id = url_decode(id);
    if id.is_empty() {
        return json_response(400, r#"{"error":"missing claim id"}"#);
    }

    let records = load_active_records(state);
    let rec = records
        .iter()
        .find(|r| r.claim_key == id || r.claim_id == id);
    let rec = match rec {
        Some(r) => r,
        None => return json_response(404, r#"{"error":"claim not found"}"#),
    };

    let model = state.current_model();
    let source_lookup: HashMap<String, &ovp_index::SourceRow> = model
        .as_ref()
        .map(|m| m.sources.iter().map(|s| (s.sha256.clone(), s)).collect())
        .unwrap_or_default();
    let pack_lookup: HashMap<String, &ovp_index::PackRow> = model
        .as_ref()
        .map(|m| {
            m.packs
                .iter()
                .filter_map(|p| {
                    let case = p.pack_dir.rsplit('/').next()?;
                    Some((case.to_string(), p))
                })
                .collect()
        })
        .unwrap_or_default();

    let reader_root = state.vault_root.join(state.layout.reader_root());
    let mut citations = Vec::new();

    for cit in &rec.citations {
        let units_path = reader_root
            .join(&cit.case_id)
            .join("units.accepted.json");
        let unit_text = std::fs::read_to_string(&units_path)
            .ok()
            .and_then(|raw| serde_json::from_str::<Vec<Unit>>(&raw).ok())
            .and_then(|units| {
                units.into_iter().find(|u| u.id == cit.unit_id).map(|u| u.text)
            })
            .unwrap_or_default();

        let (source_title, source_url, source_sha) =
            if let Some(pack) = pack_lookup.get(cit.case_id.as_str()) {
                let sha = pack
                    .source_sha256
                    .as_deref()
                    .unwrap_or("")
                    .to_string();
                let src = source_lookup.get(&sha);
                (
                    src.and_then(|s| s.title.clone())
                        .unwrap_or_else(|| pack.title.clone()),
                    src.and_then(|s| s.url.clone()).unwrap_or_default(),
                    sha,
                )
            } else {
                (cit.case_id.clone(), String::new(), String::new())
            };

        citations.push(serde_json::json!({
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

    let body = serde_json::json!({
        "claim_id": rec.claim_key,
        "claim": rec.claim,
        "theme": rec.theme,
        "strength": format!("{:?}", rec.strength).to_lowercase(),
        "citations": citations,
    });
    json_response(200, &body.to_string())
}

fn handle_flow(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let t = &model.totals;
    let total_units: usize = model.packs.iter().map(|p| p.units).sum();
    let total_cards: usize = model.packs.iter().map(|p| p.cards).sum();

    let body = serde_json::json!({
        "stages": ["intake", "reader", "units", "cards", "crystal", "blocked", "needs_content"],
        "flows": [
            { "from": "intake", "to": "reader", "value": t.processed, "label": "processed" },
            { "from": "intake", "to": "blocked", "value": t.blocked, "label": "blocked" },
            { "from": "intake", "to": "needs_content", "value": t.needs_content, "label": "needs content" },
            { "from": "reader", "to": "units", "value": total_units, "label": "accepted units" },
            { "from": "units", "to": "cards", "value": total_cards, "label": "cards kept" },
            { "from": "cards", "to": "crystal", "value": t.claims_durable, "label": "durable claims" },
        ],
    });
    json_response(200, &body.to_string())
}

fn serve_static(state: &AppState, url_path: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let console_dir = state.console_dir();

    let relative = if url_path == "/" || url_path.is_empty() {
        "index.html"
    } else {
        url_path.trim_start_matches('/')
    };

    // Prevent directory traversal
    if relative.contains("..") {
        return text_response(400, "Bad Request");
    }

    let file_path = console_dir.join(relative);
    let file_path = if file_path.is_dir() {
        file_path.join("index.html")
    } else {
        file_path
    };
    match std::fs::read(&file_path) {
        Ok(content) => {
            let fname = file_path.to_string_lossy();
            let ct = content_type_for(&fname);
            let header = Header::from_bytes("Content-Type", ct).unwrap();
            Response::from_data(content).with_header(header).with_status_code(200)
        }
        Err(_) => text_response(404, "Not Found"),
    }
}

fn json_response(status: u16, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let data = body.as_bytes().to_vec();
    let header =
        Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap();
    Response::from_data(data).with_header(header).with_status_code(status)
}

fn text_response(status: u16, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let data = body.as_bytes().to_vec();
    let header =
        Header::from_bytes("Content-Type", "text/plain; charset=utf-8").unwrap();
    Response::from_data(data).with_header(header).with_status_code(status)
}

fn content_type_for(path: &str) -> &'static str {
    if path.ends_with(".html") {
        "text/html; charset=utf-8"
    } else if path.ends_with(".css") {
        "text/css; charset=utf-8"
    } else if path.ends_with(".js") {
        "application/javascript; charset=utf-8"
    } else if path.ends_with(".json") {
        "application/json; charset=utf-8"
    } else if path.ends_with(".svg") {
        "image/svg+xml"
    } else {
        "application/octet-stream"
    }
}

fn parse_query_string(url: &str) -> std::collections::HashMap<String, String> {
    let mut map = std::collections::HashMap::new();
    if let Some(qs) = url.split('?').nth(1) {
        for pair in qs.split('&') {
            let mut kv = pair.splitn(2, '=');
            if let (Some(k), Some(v)) = (kv.next(), kv.next()) {
                let key = url_decode(k);
                let val = url_decode(v);
                map.insert(key, val);
            }
        }
    }
    map
}

fn url_decode(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut chars = s.bytes();
    while let Some(b) = chars.next() {
        if b == b'%' {
            let hi = chars.next().unwrap_or(b'0');
            let lo = chars.next().unwrap_or(b'0');
            let byte = hex_val(hi) * 16 + hex_val(lo);
            result.push(byte as char);
        } else if b == b'+' {
            result.push(' ');
        } else {
            result.push(b as char);
        }
    }
    result
}

fn hex_val(b: u8) -> u8 {
    match b {
        b'0'..=b'9' => b - b'0',
        b'a'..=b'f' => b - b'a' + 10,
        b'A'..=b'F' => b - b'A' + 10,
        _ => 0,
    }
}
