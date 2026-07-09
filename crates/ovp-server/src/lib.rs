//! `ovp-server` — synchronous localhost HTTP server for the OVP2 portal
//! and API.
//!
//! Serves the portal SPA at the site root (deployed `.ovp/console/app/` or
//! the `--viz-dir` overlay; see `resolve_static` for the precedence rule),
//! legacy generated console pages by exact filename, and JSON API endpoints
//! (`/api/find`, `/api/search`, `/api/graph`, `/api/claim/:id`,
//! `/api/source/:sha`, `/api/flow`).
//! Uses `tiny_http` to avoid any async runtime dependency.

mod graph;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, RwLock};

use ovp_domain::VaultLayout;
use ovp_domain::crystal::{CrystalStatus, DurableRecord, StoreEvent, fold_ledger};
use ovp_domain::units::Unit;
use ovp_index::{
    EvidenceModel, IndexModel, Query, QueryKind, read_evidence, read_index, run_query,
};
use ovp_intake::read_jsonl;
use tiny_http::{Header, Method, Response, Server};

/// Cap for source markdown shipped in the /api/source payload — beyond this
/// the response truncates with an explicit flag instead of shipping megabytes
/// of JSON (same limit the v1 server-rendered page used).
pub const MAX_SOURCE_DOC_BYTES: usize = 200 * 1024;

pub struct ServeConfig {
    pub vault_root: PathBuf,
    pub host: String,
    pub port: u16,
    /// Fallback directory for the portal SPA build (`console-ui/dist`).
    /// When the vault's deployed `.ovp/console/app/` misses, files are
    /// served from here — so a dev checkout can serve ANY vault without
    /// copying the build in.
    pub viz_dir: Option<PathBuf>,
}

struct AppState {
    vault_root: PathBuf,
    layout: VaultLayout,
    model: RwLock<Option<IndexModel>>,
    /// Card/unit bodies for the /api/source/:sha memory layer — lazy-loaded
    /// like the index model, refreshed together on /api/refresh.
    evidence: RwLock<Option<EvidenceModel>>,
    viz_dir: Option<PathBuf>,
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

    fn current_evidence(&self) -> Option<EvidenceModel> {
        {
            let guard = self.evidence.read().unwrap();
            if guard.is_some() {
                return guard.clone();
            }
        }
        let fresh = read_evidence(&self.vault_root).ok()?;
        let mut guard = self.evidence.write().unwrap();
        *guard = Some(fresh.clone());
        Some(fresh)
    }

    fn refresh_model(&self) {
        if let Ok(m) = read_index(&self.vault_root) {
            let mut guard = self.model.write().unwrap();
            *guard = Some(m);
        }
        // Reload the evidence sidecar too; it may legitimately be absent
        // (pre-M31 vaults) — the source API then reports it as unavailable.
        *self.evidence.write().unwrap() = read_evidence(&self.vault_root).ok();
    }

    fn console_dir(&self) -> PathBuf {
        self.vault_root.join(self.layout.console_dir())
    }
}

pub fn run_server(config: ServeConfig) -> Result<(), String> {
    let bind = format!("{}:{}", config.host, config.port);
    let server = Server::http(&bind).map_err(|e| format!("failed to bind {bind}: {e}"))?;

    let state = Arc::new(AppState {
        vault_root: config.vault_root,
        layout: VaultLayout::new(),
        model: RwLock::new(None),
        evidence: RwLock::new(None),
        viz_dir: config.viz_dir,
    });

    // Pre-load model
    state.refresh_model();

    eprintln!("ovp-server listening on http://{bind}");
    eprintln!("  console: http://{bind}/");
    eprintln!("  API:     http://{bind}/api/find?term=...");
    eprintln!("  reload:  http://{bind}/api/refresh");
    match &state.viz_dir {
        Some(dir) => eprintln!("  portal:  overlay from {}", dir.display()),
        None => {
            if !state.console_dir().join("app").join("index.html").exists() {
                eprintln!(
                    "  portal:  NOT DEPLOYED in this vault — pass --viz-dir \
                     <repo>/console-ui/dist to serve the SPA build \
                     (legacy console pages still served)"
                );
            }
        }
    }

    for request in server.incoming_requests() {
        let path = request.url().to_string();
        let method = request.method().clone();

        let resp = match (method, path.as_str()) {
            (Method::Get, "/api/refresh") => {
                state.refresh_model();
                json_response(200, r#"{"ok":true}"#)
            }
            (Method::Get, p) if p.starts_with("/api/find") => handle_find(&state, &path),
            (Method::Get, p) if p.starts_with("/api/search") => handle_search(&state, &path),
            (Method::Get, "/api/model") => handle_model(&state),
            (Method::Get, p) if p.starts_with("/api/graph") => handle_graph(&state, &path),
            (Method::Get, "/api/flow") => handle_flow(&state),
            (Method::Get, "/api/themes") => handle_themes(&state),
            (Method::Get, p) if p.starts_with("/api/claim/") => handle_claim(&state, &path),
            (Method::Get, p) if p.starts_with("/api/source/") => handle_source_api(&state, &path),
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
    let params = parse_query_string(url);
    let term = params.get("q").or_else(|| params.get("term")).cloned();

    // Graph search mode: return a hit-flagged subgraph instead of text hits
    // (the ≤40-node tight-layout scenario in the console).
    if params.get("subgraph").map(String::as_str) == Some("1") {
        let Some(term) = term.filter(|t| !t.trim().is_empty()) else {
            return json_response(400, r#"{"error":"subgraph search requires q"}"#);
        };
        let records = load_active_records(state);
        let model = state.current_model();
        let resp = graph::search_subgraph(&records, model.as_ref(), term.trim());
        let body = serde_json::to_string(&resp).unwrap_or_else(|_| "{}".into());
        return json_response(200, &body);
    }

    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };
    let query = Query {
        kind: None,
        status: None,
        date: None,
        term,
    };
    let hits = run_query(&model, &query);
    let body = serde_json::to_string(&hits).unwrap_or_else(|_| "[]".into());
    json_response(200, &body)
}

fn handle_themes(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let records = load_active_records(state);
    let themes: Vec<serde_json::Value> = graph::theme_counts(&records)
        .into_iter()
        .map(|(theme, count)| serde_json::json!({ "theme": theme, "count": count }))
        .collect();
    let body = serde_json::to_string(&themes).unwrap_or_else(|_| "[]".into());
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

fn handle_graph(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let query = parse_query_string(url);

    // Portal v2 scoped-component API (design §4): `scope=neighborhood&
    // source=<sha>` returns the source-centric neighborhood (this source →
    // claims citing it → sibling sources). `scope=global|theme` land in B3 —
    // unknown scopes fail loud, never guess.
    if let Some(scope) = query.get("scope") {
        if scope != "neighborhood" {
            let body = serde_json::json!({
                "error": format!("unknown scope: {scope} (only scope=neighborhood is available; global/theme land in B3)"),
            });
            return json_response(400, &body.to_string());
        }
        let Some(sha) = query.get("source").filter(|s| !s.is_empty()) else {
            return json_response(
                400,
                r#"{"error":"scope=neighborhood requires source=<sha256>"}"#,
            );
        };
        let records = load_active_records(state);
        let model = state.current_model();
        return match graph::source_neighborhood(&records, model.as_ref(), sha) {
            Ok(resp) => {
                let body = serde_json::to_string(&resp).unwrap_or_else(|_| "{}".into());
                json_response(200, &body)
            }
            Err(e) => {
                let body = serde_json::json!({ "error": e.message });
                json_response(e.status, &body.to_string())
            }
        };
    }

    let params = match graph::GraphParams::from_query(&query) {
        Ok(p) => p,
        Err(e) => {
            let body = serde_json::json!({ "error": e.message });
            return json_response(e.status, &body.to_string());
        }
    };

    let records = load_active_records(state);
    let model = state.current_model();

    match graph::build_graph(&records, model.as_ref(), &params) {
        Ok(resp) => {
            let body = serde_json::to_string(&resp).unwrap_or_else(|_| "{}".into());
            json_response(200, &body)
        }
        Err(e) => {
            let body = serde_json::json!({ "error": e.message });
            json_response(e.status, &body.to_string())
        }
    }
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
                    let case = graph::last_path_segment(&p.pack_dir)?;
                    Some((case.to_string(), p))
                })
                .collect()
        })
        .unwrap_or_default();

    let reader_root = state.vault_root.join(state.layout.reader_root());
    let mut citations = Vec::new();

    for cit in &rec.citations {
        let units_path = reader_root.join(&cit.case_id).join("units.accepted.json");
        let unit_text = std::fs::read_to_string(&units_path)
            .ok()
            .and_then(|raw| serde_json::from_str::<Vec<Unit>>(&raw).ok())
            .and_then(|units| {
                units
                    .into_iter()
                    .find(|u| u.id == cit.unit_id)
                    .map(|u| u.text)
            })
            .unwrap_or_default();

        let (source_title, source_url, source_sha) =
            if let Some(pack) = pack_lookup.get(cit.case_id.as_str()) {
                let sha = pack.source_sha256.as_deref().unwrap_or("").to_string();
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

/// GET /api/source/<sha256> — JSON for the portal's three-layer source
/// detail page (B2): full SourceRow meta, the memory layer (cards + grounded
/// units from the evidence sidecar), crystal claims citing this source, and
/// the raw source markdown (size-capped, traversal-safe). The markdown is
/// DATA in a JSON string — the client renders it safely; nothing here emits
/// HTML.
fn handle_source_api(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let raw = url.split('?').next().unwrap_or(url);
    let sha = url_decode(
        raw.strip_prefix("/api/source/")
            .unwrap_or("")
            .trim_end_matches('/'),
    );
    if sha.is_empty() {
        return json_response(400, r#"{"error":"missing source sha"}"#);
    }

    let Some(source) = model.sources.iter().find(|s| s.sha256 == sha) else {
        let body = serde_json::json!({ "error": format!("source not found: {sha}") });
        return json_response(404, &body.to_string());
    };

    // Memory layer: evidence rows keyed by the source sha or its pack dir.
    let evidence = state.current_evidence();
    let evidence_available = evidence.is_some();
    let pack_dir = source.pack_dir.as_deref();
    let belongs = |row_sha: Option<&str>, row_pack: &str| {
        row_sha == Some(sha.as_str()) || pack_dir == Some(row_pack)
    };
    let cards: Vec<serde_json::Value> = evidence
        .as_ref()
        .map(|ev| {
            ev.cards
                .iter()
                .filter(|c| belongs(c.source_sha256.as_deref(), &c.pack_dir))
                .map(|c| serde_json::json!({ "title": c.title, "content": c.content }))
                .collect()
        })
        .unwrap_or_default();
    let units: Vec<serde_json::Value> = evidence
        .as_ref()
        .map(|ev| {
            ev.units
                .iter()
                .filter(|u| belongs(u.source_sha256.as_deref(), &u.pack_dir))
                .map(|u| {
                    serde_json::json!({
                        "unit_id": u.unit_id,
                        "text": u.text,
                        "quote": u.quote,
                        "line": u.line,
                        "attribution": u.attribution,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    // Crystal layer: ClaimRow.sources holds case ids (last pack_dir segment).
    let case_id = pack_dir.and_then(graph::last_path_segment);
    let mut citing: Vec<&ovp_index::ClaimRow> = match case_id {
        Some(case) => model
            .claims
            .iter()
            .filter(|c| c.sources.iter().any(|s| s == case))
            .collect(),
        None => Vec::new(),
    };
    citing.sort_by_key(|c| {
        (
            match c.status {
                ovp_index::ClaimStatus::Durable => 0u8,
                ovp_index::ClaimStatus::Caveated => 1,
                _ => 2,
            },
            c.claim_id.clone(),
        )
    });

    let (markdown, truncated, doc_error) = read_source_doc(state, source.rel_path.as_deref());

    let body = serde_json::json!({
        "source": source,
        "memory": {
            "evidence_available": evidence_available,
            "cards": cards,
            "units": units,
        },
        "citing_claims": citing,
        "doc": {
            "markdown": markdown,
            "truncated": truncated,
            "error": doc_error,
        },
    });
    json_response(200, &body.to_string())
}

/// Read the source markdown from the vault, capped at MAX_SOURCE_DOC_BYTES.
/// All failure modes become an explicit error string — the endpoint always
/// answers.
fn read_source_doc(
    state: &AppState,
    rel_path: Option<&str>,
) -> (Option<String>, bool, Option<String>) {
    let Some(rel) = rel_path else {
        return (None, false, None);
    };
    // rel_path comes from our own index, but never trust a path with parent
    // components or an absolute root anyway.
    if rel.contains("..") || std::path::Path::new(rel).is_absolute() {
        return (None, false, Some("source path rejected".into()));
    }
    match std::fs::read_to_string(state.vault_root.join(rel)) {
        Ok(mut text) => {
            let truncated = text.len() > MAX_SOURCE_DOC_BYTES;
            if truncated {
                let mut cut = MAX_SOURCE_DOC_BYTES;
                while cut > 0 && !text.is_char_boundary(cut) {
                    cut -= 1;
                }
                text.truncate(cut);
            }
            (Some(text), truncated, None)
        }
        Err(e) => (None, false, Some(format!("{rel}: {e}"))),
    }
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

/// Result of static-path resolution — kept separate from `Response` so the
/// routing precedence is testable on content, not just status codes.
enum Resolved {
    File {
        body: Vec<u8>,
        content_type: &'static str,
    },
    BadRequest,
    NotFound,
}

fn serve_static(state: &AppState, url_path: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    match resolve_static(state, url_path) {
        Resolved::File { body, content_type } => {
            let header = Header::from_bytes("Content-Type", content_type).unwrap();
            Response::from_data(body)
                .with_header(header)
                .with_status_code(200)
        }
        Resolved::BadRequest => text_response(400, "Bad Request"),
        Resolved::NotFound => text_response(404, "Not Found"),
    }
}

/// Static routing precedence (portal v2 B1) — the SPA owns the site root,
/// legacy generated pages stay reachable by exact filename:
///
/// 1. `/api/*` never reaches here (dispatched in `run_server` first).
/// 2. `/legacy-index.html` → the OLD generated console index
///    (`<vault>/.ovp/console/index.html`), kept reachable after the SPA
///    took over `/`.
/// 3. SPA app build, exact file: deployed `<vault>/.ovp/console/app/`
///    first, then the `--viz-dir` overlay. `/` maps to `index.html`, so
///    the portal is the root whenever an app build is present.
/// 4. Legacy console file under `<vault>/.ovp/console/` by exact filename
///    (`ops.html`, `audit.html`, `candidates.html`, pre-B1 `/viz/*`
///    assets, …). Without any app build this also serves the old console
///    index at `/` — backward compatible.
/// 5. Extensionless paths are SPA client routes (`/library`,
///    `/library/:sha`, `/search`, old `/viz/graph` deep links) → the SPA
///    `index.html`; the router takes over. Paths WITH an extension that
///    missed on disk are plain 404s.
fn resolve_static(state: &AppState, url_path: &str) -> Resolved {
    let console_dir = state.console_dir();

    // Deep links like /library?c=pinboard carry a query string; file
    // lookup (and client-route detection) must see the path only.
    let url_path = url_path.split('?').next().unwrap_or(url_path);
    let relative = if url_path == "/" || url_path.is_empty() {
        "index.html"
    } else {
        url_path.trim_start_matches('/')
    };

    // Prevent directory traversal
    if relative.contains("..") {
        return Resolved::BadRequest;
    }

    if relative == "legacy-index.html" {
        return match std::fs::read(console_dir.join("index.html")) {
            Ok(body) => Resolved::File {
                body,
                content_type: "text/html; charset=utf-8",
            },
            Err(_) => Resolved::NotFound,
        };
    }

    if let Some(body) = read_app_file(state, relative) {
        return Resolved::File {
            body,
            content_type: content_type_for(relative),
        };
    }

    let file_path = console_dir.join(relative);
    let file_path = if file_path.is_dir() {
        file_path.join("index.html")
    } else {
        file_path
    };
    if let Ok(body) = std::fs::read(&file_path) {
        let fname = file_path.to_string_lossy().to_string();
        return Resolved::File {
            body,
            content_type: content_type_for(&fname),
        };
    }

    if is_client_route(relative) {
        if let Some(body) = read_app_file(state, "index.html") {
            return Resolved::File {
                body,
                content_type: "text/html; charset=utf-8",
            };
        }
    }

    Resolved::NotFound
}

/// Read a root-relative asset from the SPA app build: the deployed
/// `<vault>/.ovp/console/app/` wins, then the `--viz-dir` overlay — so a
/// dev checkout can serve ANY vault without copying the build in. `..` is
/// already rejected by resolve_static, but `Path::join` DISCARDS the base
/// for an absolute RHS — so only plain relative components pass.
fn read_app_file(state: &AppState, rest: &str) -> Option<Vec<u8>> {
    let rel = std::path::Path::new(rest);
    if rel.as_os_str().is_empty()
        || rel.is_absolute()
        || rel
            .components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
    {
        return None;
    }
    let deployed = state.console_dir().join("app").join(rel);
    if let Ok(body) = std::fs::read(&deployed) {
        return Some(body);
    }
    let dir = state.viz_dir.as_ref()?;
    std::fs::read(dir.join(rel)).ok()
}

/// Extensionless path = SPA client route. Malformed paths (leading slash
/// remnants, empty segments) are not client routes — they must 404, never
/// get a 200 SPA shell.
fn is_client_route(relative: &str) -> bool {
    if relative.is_empty() || relative.starts_with('/') || relative.contains("//") {
        return false;
    }
    let last = relative.rsplit('/').next().unwrap_or(relative);
    !last.contains('.')
}

fn json_response(status: u16, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let data = body.as_bytes().to_vec();
    let header = Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap();
    Response::from_data(data)
        .with_header(header)
        .with_status_code(status)
}

fn text_response(status: u16, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let data = body.as_bytes().to_vec();
    let header = Header::from_bytes("Content-Type", "text/plain; charset=utf-8").unwrap();
    Response::from_data(data)
        .with_header(header)
        .with_status_code(status)
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
    } else if path.ends_with(".woff2") {
        "font/woff2"
    } else if path.ends_with(".png") {
        "image/png"
    } else if path.ends_with(".txt") {
        "text/plain; charset=utf-8"
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

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("ovp-server-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn state(vault: PathBuf, viz_dir: Option<PathBuf>) -> AppState {
        AppState {
            vault_root: vault,
            layout: VaultLayout::new(),
            model: RwLock::new(None),
            evidence: RwLock::new(None),
            viz_dir,
        }
    }

    /// Unwrap the resolved body for content assertions.
    fn body(r: Resolved) -> Vec<u8> {
        match r {
            Resolved::File { body, .. } => body,
            Resolved::BadRequest => panic!("expected file, got 400"),
            Resolved::NotFound => panic!("expected file, got 404"),
        }
    }

    fn is_not_found(r: Resolved) -> bool {
        matches!(r, Resolved::NotFound)
    }

    #[test]
    fn spa_owns_root_and_client_routes_legacy_by_exact_filename() {
        let root = temp_root("precedence");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console")).unwrap();
        std::fs::write(vault.join(".ovp/console/index.html"), "legacy-index").unwrap();
        std::fs::write(vault.join(".ovp/console/ops.html"), "legacy-ops").unwrap();
        let overlay = root.join("dist");
        std::fs::create_dir_all(overlay.join("assets")).unwrap();
        std::fs::write(overlay.join("index.html"), "spa").unwrap();
        std::fs::write(overlay.join("assets/app.js"), "js").unwrap();

        let st = state(vault.clone(), Some(overlay));

        // The SPA owns the portal root and /index.html…
        assert_eq!(body(resolve_static(&st, "/")), b"spa");
        assert_eq!(body(resolve_static(&st, "/index.html")), b"spa");
        // …and every client route (query strings stripped).
        assert_eq!(body(resolve_static(&st, "/library")), b"spa");
        assert_eq!(body(resolve_static(&st, "/library/84fbf6dc")), b"spa");
        assert_eq!(body(resolve_static(&st, "/search?lang=zh")), b"spa");
        // Pre-B1 deep links are client routes too (router redirects).
        assert_eq!(body(resolve_static(&st, "/viz/graph")), b"spa");
        // Hashed assets come from the overlay.
        assert_eq!(body(resolve_static(&st, "/assets/app.js")), b"js");
        // Legacy generated pages stay reachable by exact filename, and the
        // old console index moves to /legacy-index.html.
        assert_eq!(body(resolve_static(&st, "/ops.html")), b"legacy-ops");
        assert_eq!(
            body(resolve_static(&st, "/legacy-index.html")),
            b"legacy-index"
        );
        // A missed path WITH an extension is a plain 404, not the SPA shell.
        assert!(is_not_found(resolve_static(&st, "/nope.js")));
        assert!(is_not_found(resolve_static(&st, "/nope.html")));
        // Traversal / malformed paths never resolve.
        assert!(matches!(
            resolve_static(&st, "/../secret.txt"),
            Resolved::BadRequest
        ));
        std::fs::write(root.join("secret.txt"), "nope").unwrap();
        let abs = format!("/viz/{}", root.join("secret.txt").display());
        assert!(is_not_found(resolve_static(&st, &abs)));
        assert!(is_not_found(resolve_static(&st, "/viz//etc/hosts")));

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn deployed_app_dir_wins_over_overlay() {
        let root = temp_root("app-dir");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console/app")).unwrap();
        std::fs::write(vault.join(".ovp/console/app/index.html"), "deployed").unwrap();
        let overlay = root.join("dist");
        std::fs::create_dir_all(&overlay).unwrap();
        std::fs::write(overlay.join("index.html"), "overlay").unwrap();

        let st = state(vault.clone(), Some(overlay));
        assert_eq!(body(resolve_static(&st, "/")), b"deployed");
        assert_eq!(body(resolve_static(&st, "/library")), b"deployed");

        // Deployed app also works with no overlay configured at all.
        let st = state(vault, None);
        assert_eq!(body(resolve_static(&st, "/")), b"deployed");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn without_app_build_legacy_console_stays_root() {
        let root = temp_root("no-app");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console")).unwrap();
        std::fs::write(vault.join(".ovp/console/index.html"), "legacy-index").unwrap();
        std::fs::write(vault.join(".ovp/console/ops.html"), "legacy-ops").unwrap();

        let st = state(vault, None);
        // Backward compatible: the old console remains the root…
        assert_eq!(body(resolve_static(&st, "/")), b"legacy-index");
        assert_eq!(body(resolve_static(&st, "/ops.html")), b"legacy-ops");
        assert_eq!(
            body(resolve_static(&st, "/legacy-index.html")),
            b"legacy-index"
        );
        // …and client routes have no SPA to fall back to.
        assert!(is_not_found(resolve_static(&st, "/library")));
        assert!(is_not_found(resolve_static(&st, "/viz/graph")));

        let _ = std::fs::remove_dir_all(&root);
    }

    fn body_json(resp: Response<std::io::Cursor<Vec<u8>>>) -> serde_json::Value {
        use std::io::Read;
        let mut out = Vec::new();
        resp.into_reader().read_to_end(&mut out).unwrap();
        serde_json::from_slice(&out).expect("response body must be valid JSON")
    }

    /// Vault with one processed source (hostile markdown body), its pack,
    /// evidence sidecar (one card + one grounded unit) and one claim citing
    /// the case — the /api/source three-layer fixture.
    fn portal_vault(name: &str, rel_path: &str, body: &str) -> PathBuf {
        use ovp_index::evidence::{CardEvidenceRow, UnitEvidenceRow};
        use ovp_index::{
            ClaimRow, ClaimStatus, EvidenceModel, OpsState, PackRow, SourceRow, SourceStatus,
            Totals,
        };
        let root = temp_root(name);
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join("50-Inbox/03-Processed")).unwrap();
        std::fs::write(vault.join("50-Inbox/03-Processed/good.md"), body).unwrap();

        let model = IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-07-09".into(),
            run_id: None,
            totals: Totals {
                sources: 1,
                processed: 1,
                packs: 1,
                ..Default::default()
            },
            sources: vec![SourceRow {
                sha256: "aaaa1111".into(),
                status: SourceStatus::Processed,
                title: Some("Good Article".into()),
                url: Some("https://example.com/good".into()),
                rel_path: Some(rel_path.into()),
                date: Some("2026-07-09".into()),
                last_run_id: None,
                pack_dir: Some("40-Resources/Reader/good".into()),
                fail_count: 0,
                last_reason: None,
            }],
            packs: vec![PackRow {
                pack_dir: "40-Resources/Reader/good".into(),
                title: "Good Article".into(),
                date: Some("2026-07-09".into()),
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec!["Card One".into()],
                source_sha256: Some("aaaa1111".into()),
            }],
            claims: vec![ClaimRow {
                claim_id: "c01".into(),
                claim: "Filesystem works as memory.".into(),
                theme: Some("memory".into()),
                status: ClaimStatus::Durable,
                sources: vec!["good".into()],
                strength: Some("supported".into()),
                run_id: None,
                lane: None,
            }],
            runs: vec![],
            ops: OpsState::default(),
        };
        ovp_index::write_index(&vault, &model).unwrap();

        let evidence = EvidenceModel {
            schema: "ovp.index.evidence/v1".into(),
            date: "2026-07-09".into(),
            cards: vec![CardEvidenceRow {
                id: "card:40-Resources/Reader/good:0".into(),
                pack_dir: "40-Resources/Reader/good".into(),
                source_sha256: Some("aaaa1111".into()),
                source_title: "Good Article".into(),
                title: "Card One".into(),
                content: "Body of card one.".into(),
                unit_type: None,
                cited_unit_ids: vec!["u-001".into()],
            }],
            units: vec![UnitEvidenceRow {
                id: "unit:40-Resources/Reader/good:u-001".into(),
                pack_dir: "40-Resources/Reader/good".into(),
                source_sha256: Some("aaaa1111".into()),
                source_title: "Good Article".into(),
                unit_id: "u-001".into(),
                text: "The unit text.".into(),
                quote: "the exact quote".into(),
                line: Some(14),
                attribution: "author".into(),
                modality: "asserted".into(),
            }],
            warnings: vec![],
        };
        ovp_index::write_evidence(&vault, &evidence).unwrap();
        vault
    }

    #[test]
    fn source_api_returns_three_layers_as_json_data() {
        // Hostile markdown must pass through as DATA in the JSON payload —
        // never HTML-escaped (the client renders it safely), never live.
        let vault = portal_vault(
            "source-api",
            "50-Inbox/03-Processed/good.md",
            "# Heading\n\nbody with <script>alert(1)</script>\n",
        );
        let st = state(vault.clone(), None);

        let resp = handle_source_api(&st, "/api/source/aaaa1111");
        assert_eq!(resp.status_code(), 200);
        let ct = resp
            .headers()
            .iter()
            .find(|h| {
                h.field
                    .as_str()
                    .as_str()
                    .eq_ignore_ascii_case("content-type")
            })
            .map(|h| h.value.as_str().to_string());
        assert_eq!(ct.as_deref(), Some("application/json; charset=utf-8"));

        let v = body_json(resp);
        assert_eq!(v["source"]["sha256"], "aaaa1111");
        assert_eq!(v["source"]["title"], "Good Article");
        assert_eq!(v["memory"]["evidence_available"], true);
        assert_eq!(v["memory"]["cards"][0]["title"], "Card One");
        assert_eq!(v["memory"]["units"][0]["unit_id"], "u-001");
        assert_eq!(v["memory"]["units"][0]["line"], 14);
        assert_eq!(v["citing_claims"][0]["claim_id"], "c01");
        assert_eq!(v["citing_claims"][0]["status"], "durable");
        // The XSS payload survives as a JSON string, exactly as written.
        let md = v["doc"]["markdown"].as_str().unwrap();
        assert!(md.contains("<script>alert(1)</script>"));
        assert!(!md.contains("&lt;script&gt;"));
        assert_eq!(v["doc"]["truncated"], false);
        assert!(v["doc"]["error"].is_null());

        // Unknown sha → JSON 404, not HTML.
        let missing = handle_source_api(&st, "/api/source/deadbeef");
        assert_eq!(missing.status_code(), 404);
        let v = body_json(missing);
        assert!(v["error"].as_str().unwrap().contains("deadbeef"));

        // Missing sha segment → 400.
        assert_eq!(handle_source_api(&st, "/api/source/").status_code(), 400);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_rejects_traversal_paths() {
        let vault = portal_vault("source-traversal", "../secret.md", "body\n");
        // A secret OUTSIDE the vault that `..` would reach.
        std::fs::write(vault.parent().unwrap().join("secret.md"), "TOP SECRET").unwrap();
        let st = state(vault.clone(), None);

        let resp = handle_source_api(&st, "/api/source/aaaa1111");
        assert_eq!(resp.status_code(), 200); // meta still served
        let v = body_json(resp);
        assert!(v["doc"]["markdown"].is_null());
        assert_eq!(v["doc"]["error"], "source path rejected");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_truncates_oversized_markdown() {
        let big = "x".repeat(MAX_SOURCE_DOC_BYTES + 100);
        let vault = portal_vault("source-big", "50-Inbox/03-Processed/good.md", &big);
        let st = state(vault.clone(), None);

        let v = body_json(handle_source_api(&st, "/api/source/aaaa1111"));
        assert_eq!(v["doc"]["truncated"], true);
        assert_eq!(
            v["doc"]["markdown"].as_str().unwrap().len(),
            MAX_SOURCE_DOC_BYTES
        );

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_without_evidence_reports_unavailable() {
        let vault = portal_vault(
            "source-no-evidence",
            "50-Inbox/03-Processed/good.md",
            "body\n",
        );
        std::fs::remove_file(vault.join(".ovp/index/evidence.json")).unwrap();
        let st = state(vault.clone(), None);

        let v = body_json(handle_source_api(&st, "/api/source/aaaa1111"));
        assert_eq!(v["memory"]["evidence_available"], false);
        assert!(v["memory"]["cards"].as_array().unwrap().is_empty());
        assert!(v["memory"]["units"].as_array().unwrap().is_empty());

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn client_route_detection() {
        assert!(is_client_route("library"));
        assert!(is_client_route("library/84fbf6dc"));
        assert!(is_client_route("search"));
        assert!(is_client_route("viz/graph"));
        // Extensions (missed files) and malformed paths are not routes.
        assert!(!is_client_route("index.html"));
        assert!(!is_client_route("assets/app.js"));
        assert!(!is_client_route("library/file.md"));
        assert!(!is_client_route(""));
        assert!(!is_client_route("/etc/hosts"));
        assert!(!is_client_route("viz//etc"));
    }
}
