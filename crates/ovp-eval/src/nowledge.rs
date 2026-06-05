//! The Nowledge Mem HTTP adapter — the ONLY networked code in the comparator,
//! and the only place `reqwest` is used. Everything is behind the
//! [`NowledgeClient`] trait so the orchestrator is testable with an injected
//! fake (canned JSON) and never needs the network.
//!
//! **Fail loud.** Every call returns `Err(NowledgeError)` on a non-2xx status, a
//! timeout, a transport error, malformed JSON, or a missing expected field. The
//! adapter never silently degrades — a hidden empty result would mask exactly
//! the pipeline problems this comparator exists to surface. The *comparator*
//! decides what a failure means (it still writes a partial pack); the *adapter*
//! only reports the truth.
//!
//! Blocking + rustls (the workspace transport): no async runtime.

use std::time::Duration;

use serde::{Deserialize, Serialize};

/// A loud failure from the Nowledge Mem service.
#[derive(Debug, Clone)]
pub enum NowledgeError {
    /// Non-2xx HTTP response.
    Http { status: u16, op: String, detail: String },
    /// The request timed out.
    Timeout { op: String },
    /// Connection refused / DNS / TLS / other transport failure.
    Transport { op: String, detail: String },
    /// 2xx but the body was not the JSON shape we expected.
    Malformed { op: String, detail: String },
    /// A valid response that lacks an expected piece of state (e.g. no
    /// `source_id`, or extraction never reached a terminal state).
    MissingState { op: String, detail: String },
}

impl std::fmt::Display for NowledgeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NowledgeError::Http { status, op, detail } => {
                write!(f, "nowledge {op}: HTTP {status}: {detail}")
            }
            NowledgeError::Timeout { op } => write!(f, "nowledge {op}: request timed out"),
            NowledgeError::Transport { op, detail } => {
                write!(f, "nowledge {op}: transport error: {detail}")
            }
            NowledgeError::Malformed { op, detail } => {
                write!(f, "nowledge {op}: malformed response: {detail}")
            }
            NowledgeError::MissingState { op, detail } => {
                write!(f, "nowledge {op}: missing expected state: {detail}")
            }
        }
    }
}

impl std::error::Error for NowledgeError {}

// --- Wire DTOs (only the fields the comparator reads; unknown fields ignored) ---

/// `POST /sources/ingest/{url,file-path}` → the created source handle.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestResponse {
    pub source_id: String,
    #[serde(default)]
    pub original_name: String,
    #[serde(default)]
    pub lifecycle_state: String,
    #[serde(default)]
    pub is_duplicate: bool,
}

/// `GET /sources/{id}` → the source record plus the memories extracted from it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceDetail {
    pub source: SourceInfo,
    #[serde(default)]
    pub memories: Vec<SourceMemory>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceInfo {
    pub id: String,
    #[serde(default)]
    pub source_url: String,
    #[serde(default)]
    pub original_name: String,
    #[serde(default)]
    pub lifecycle_state: String,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub section_tree: Option<String>,
    #[serde(default)]
    pub memory_count: u32,
    #[serde(default)]
    pub error_message: Option<String>,
}

/// One memory in `SourceDetail.memories` — an atomic extracted fact. (This
/// per-source shape differs from the global `/memories` list shape, so it gets
/// its own minimal struct.)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceMemory {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub unit_type: String,
}

/// One page of `GET /sources/{id}/content` — the FULL parsed markdown (paged;
/// the endpoint caps `limit` at 50000). This is the real "what Nowledge
/// extracted" text; `SourceInfo.summary` is only a short snippet.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceContentResponse {
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub offset: usize,
    #[serde(default)]
    pub returned_length: usize,
    #[serde(default)]
    pub total_length: usize,
    #[serde(default)]
    pub has_more: bool,
}

/// One hit from `POST /memories/search`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemorySearchResult {
    #[serde(default)]
    pub memory: Option<SearchMemory>,
    #[serde(default)]
    pub similarity_score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchMemory {
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub is_crystal: bool,
}

/// One crystallized memory (`GET /memories?is_crystal=true`). Global (whole
/// store), not scoped to a single source — surfaced only as context.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrystalInfo {
    #[serde(default)]
    pub title: String,
}

/// The Nowledge Mem operations the comparator needs. Object-safe so the
/// orchestrator takes `&dyn NowledgeClient` and tests inject a fake.
pub trait NowledgeClient {
    /// Ingest a remote URL (the service fetches + parses it).
    fn ingest_url(&self, url: &str, space_id: &str) -> Result<IngestResponse, NowledgeError>;
    /// Ingest a local file by absolute path (the service reads it directly).
    fn ingest_file_path(&self, path: &str, space_id: &str) -> Result<IngestResponse, NowledgeError>;
    /// Trigger KG/memory extraction for an ingested source.
    fn trigger_extract(&self, source_id: &str) -> Result<(), NowledgeError>;
    /// Read the source record + its extracted memories.
    fn get_source(&self, source_id: &str) -> Result<SourceDetail, NowledgeError>;
    /// Read one page of the source's FULL parsed content. `limit` is capped at
    /// 50000 by the service; the caller pages until `has_more` is false.
    fn get_source_content(
        &self,
        source_id: &str,
        offset: usize,
        limit: usize,
    ) -> Result<SourceContentResponse, NowledgeError>;
    /// Semantic memory search over the whole store (global, not source-scoped).
    fn search_memories(&self, query: &str, limit: usize)
        -> Result<Vec<MemorySearchResult>, NowledgeError>;
    /// List crystallized memories (global). Best-effort context only.
    fn list_crystals(&self, limit: usize) -> Result<Vec<CrystalInfo>, NowledgeError>;
}

/// The live HTTP client. Blocking reqwest; no async runtime.
pub struct LiveNowledgeClient {
    base_url: String,
    http: reqwest::blocking::Client,
}

impl LiveNowledgeClient {
    /// Build a client against `base_url` (e.g. `http://127.0.0.1:14242`) with a
    /// per-request timeout. Trailing slash on `base_url` is trimmed.
    pub fn new(base_url: impl Into<String>, timeout: Duration) -> Result<Self, NowledgeError> {
        // `.no_proxy()`: the comparator talks to a local service. reqwest honors
        // HTTP(S)_PROXY env vars by default, which would route a 127.0.0.1
        // request through a proxy and fail — so disable proxying outright.
        let http = reqwest::blocking::Client::builder()
            .timeout(timeout)
            .no_proxy()
            .build()
            .map_err(|e| NowledgeError::Transport {
                op: "client-build".into(),
                detail: e.to_string(),
            })?;
        Ok(Self { base_url: base_url.into().trim_end_matches('/').to_string(), http })
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
    }

    /// Map a reqwest send error to the right loud variant.
    fn send_err(op: &str, e: reqwest::Error) -> NowledgeError {
        if e.is_timeout() {
            NowledgeError::Timeout { op: op.to_string() }
        } else {
            NowledgeError::Transport { op: op.to_string(), detail: e.to_string() }
        }
    }

    /// Turn a response into a deserialized `T`, failing loud on status + parse.
    fn read_json<T: serde::de::DeserializeOwned>(
        op: &str,
        resp: reqwest::blocking::Response,
    ) -> Result<T, NowledgeError> {
        let status = resp.status();
        let body = resp
            .text()
            .map_err(|e| NowledgeError::Transport { op: op.to_string(), detail: e.to_string() })?;
        if !status.is_success() {
            return Err(NowledgeError::Http {
                status: status.as_u16(),
                op: op.to_string(),
                detail: truncate(&body, 500),
            });
        }
        serde_json::from_str::<T>(&body).map_err(|e| NowledgeError::Malformed {
            op: op.to_string(),
            detail: format!("{e}; body starts: {}", truncate(&body, 200)),
        })
    }
}

impl NowledgeClient for LiveNowledgeClient {
    fn ingest_url(&self, url: &str, space_id: &str) -> Result<IngestResponse, NowledgeError> {
        let op = "ingest_url";
        let resp = self
            .http
            .post(self.url("/sources/ingest/url"))
            .json(&serde_json::json!({ "url": url, "space_id": space_id }))
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        let out: IngestResponse = Self::read_json(op, resp)?;
        if out.source_id.is_empty() {
            return Err(NowledgeError::MissingState {
                op: op.into(),
                detail: "ingest returned an empty source_id".into(),
            });
        }
        Ok(out)
    }

    fn ingest_file_path(&self, path: &str, space_id: &str) -> Result<IngestResponse, NowledgeError> {
        let op = "ingest_file_path";
        let resp = self
            .http
            .post(self.url("/sources/ingest/file-path"))
            .json(&serde_json::json!({ "file_path": path, "space_id": space_id }))
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        let out: IngestResponse = Self::read_json(op, resp)?;
        if out.source_id.is_empty() {
            return Err(NowledgeError::MissingState {
                op: op.into(),
                detail: "ingest returned an empty source_id".into(),
            });
        }
        Ok(out)
    }

    fn trigger_extract(&self, source_id: &str) -> Result<(), NowledgeError> {
        let op = "trigger_extract";
        let resp = self
            .http
            .post(self.url(&format!("/sources/{source_id}/extract")))
            .json(&serde_json::json!({}))
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().unwrap_or_default();
            return Err(NowledgeError::Http {
                status: status.as_u16(),
                op: op.into(),
                detail: truncate(&body, 500),
            });
        }
        Ok(())
    }

    fn get_source(&self, source_id: &str) -> Result<SourceDetail, NowledgeError> {
        let op = "get_source";
        let resp = self
            .http
            .get(self.url(&format!("/sources/{source_id}")))
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        Self::read_json(op, resp)
    }

    fn get_source_content(
        &self,
        source_id: &str,
        offset: usize,
        limit: usize,
    ) -> Result<SourceContentResponse, NowledgeError> {
        let op = "get_source_content";
        let resp = self
            .http
            .get(self.url(&format!("/sources/{source_id}/content")))
            .query(&[("offset", offset.to_string()), ("limit", limit.to_string())])
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        Self::read_json(op, resp)
    }

    fn search_memories(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<MemorySearchResult>, NowledgeError> {
        let op = "search_memories";
        let resp = self
            .http
            .post(self.url("/memories/search"))
            .json(&serde_json::json!({ "query": query, "limit": limit, "include_entities": false }))
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        Self::read_json(op, resp)
    }

    fn list_crystals(&self, limit: usize) -> Result<Vec<CrystalInfo>, NowledgeError> {
        let op = "list_crystals";
        let resp = self
            .http
            .get(self.url("/memories"))
            .query(&[("limit", limit.to_string()), ("is_crystal", "true".to_string())])
            .send()
            .map_err(|e| Self::send_err(op, e))?;
        // `/memories` wraps the list in `{ memories: [...] }`.
        #[derive(Deserialize)]
        struct Wrap {
            #[serde(default)]
            memories: Vec<CrystalInfo>,
        }
        let wrap: Wrap = Self::read_json(op, resp)?;
        Ok(wrap.memories)
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        return s.to_string();
    }
    let end = s.char_indices().nth(max).map(|(i, _)| i).unwrap_or(s.len());
    format!("{}…", &s[..end])
}
