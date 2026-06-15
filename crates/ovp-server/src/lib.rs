//! `ovp-server` — synchronous localhost HTTP server for OVP console and API.
//!
//! Serves static console HTML from `.ovp/console/` and JSON API endpoints
//! (`/api/find`, `/api/search`, `/api/doctor`). Uses `tiny_http` to avoid
//! any async runtime dependency.

use std::path::PathBuf;
use std::sync::{Arc, RwLock};

use ovp_domain::VaultLayout;
use ovp_index::{read_index, run_query, IndexModel, Query, QueryKind};
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
    // Simple SSE endpoint: sends one "ready" event then closes.
    // A real impl would keep-alive; here we just confirm connectivity.
    let body = "event: ready\ndata: {}\n\n";
    let data = body.as_bytes().to_vec();
    let header =
        Header::from_bytes("Content-Type", "text/event-stream").unwrap();
    Response::from_data(data).with_header(header).with_status_code(200)
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
    match std::fs::read(&file_path) {
        Ok(content) => {
            let ct = content_type_for(relative);
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
