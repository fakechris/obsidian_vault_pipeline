use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use crate::client::{CallError, ModelClient};
use crate::key::request_key;
use crate::reply::ModelReply;
use crate::request::ModelRequest;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheMode {
    /// Look up the cache first; on miss, delegate to the inner client and
    /// persist the result to disk.
    Record,
    /// Look up the cache; on miss, return `CacheMiss` without ever
    /// touching the inner client. Use this in tests (with
    /// `NeverCallsClient` inside) to guarantee zero network.
    ReplayOnly,
}

/// File-backed cache layered over another `ModelClient`. Files live at
/// `<cache_dir>/<namespace>/<sha256-hex>.json`; one cassette per unique
/// (prompt namespace, request) pair.
///
/// The namespace is conventionally `<prompt_id>/v<schema_version>`
/// (e.g. `article_interpret/v1`). It is chosen **per request**: a request
/// carrying `cache_namespace` (set by `LLMInvoker` from its `prompt_id`)
/// uses that; otherwise the client's constructor namespace is the
/// fallback. This lets one client — shared by a unified pipeline's single
/// `LLMInvoker` — file article and paper cassettes under their own prompt
/// namespaces without collision.
///
/// When the prompt asset is revised and the schema version bumps, the
/// namespace changes, the cassette dir changes, and old cassettes don't
/// masquerade as new-schema responses.
///
/// In `Record` mode the cache backfills from the inner client and saves
/// each response as it's recorded. In `ReplayOnly` mode cache misses are
/// errors — the inner client is never consulted.
pub struct CachedModelClient<C: ModelClient> {
    inner: C,
    cache_dir: PathBuf,
    /// Fallback namespace when a request carries no `cache_namespace`.
    namespace: String,
    mode: CacheMode,
    memo: HashMap<String, ModelReply>,
}

impl<C: ModelClient> CachedModelClient<C> {
    /// Open a cache at `<cache_dir>/<namespace>/`. v1's namespace
    /// for the article path is `article_interpret/v1`. Empty namespace
    /// is allowed (`<cache_dir>/<hash>.json` directly) but strongly
    /// discouraged in production — it's a footgun for schema bumps.
    pub fn new(
        inner: C,
        cache_dir: impl Into<PathBuf>,
        namespace: impl Into<String>,
        mode: CacheMode,
    ) -> std::io::Result<Self> {
        let cache_dir = cache_dir.into();
        let namespace = namespace.into();
        // Namespace subdirs are created lazily per request in `store`, so
        // a client serving multiple prompt namespaces only materializes
        // the ones it actually writes.
        if mode == CacheMode::Record {
            fs::create_dir_all(&cache_dir)?;
        }
        Ok(Self { inner, cache_dir, namespace, mode, memo: HashMap::new() })
    }

    pub fn mode(&self) -> CacheMode { self.mode }
    pub fn cache_dir(&self) -> &PathBuf { &self.cache_dir }
    /// The fallback namespace (used when a request carries no hint).
    pub fn namespace(&self) -> &str { &self.namespace }

    /// Effective namespace for a request: its own hint, else the client's
    /// fallback.
    fn namespace_for<'a>(&'a self, request: &'a ModelRequest) -> &'a str {
        request.cache_namespace.as_deref().unwrap_or(&self.namespace)
    }

    fn cassette_dir(&self, namespace: &str) -> PathBuf {
        if namespace.is_empty() {
            self.cache_dir.clone()
        } else {
            self.cache_dir.join(namespace)
        }
    }

    fn cassette_path(&self, namespace: &str, key: &str) -> PathBuf {
        self.cassette_dir(namespace).join(format!("{key}.json"))
    }

    /// Memo key combines namespace + request hash. Request hashes already
    /// differ across prompt kinds (different system text), so this is
    /// belt-and-suspenders against any future hash collision.
    fn memo_key(namespace: &str, key: &str) -> String {
        format!("{namespace}\u{1f}{key}")
    }

    fn load(&mut self, namespace: &str, key: &str) -> Option<ModelReply> {
        let memo_key = Self::memo_key(namespace, key);
        if let Some(r) = self.memo.get(&memo_key) {
            return Some(r.clone());
        }
        let path = self.cassette_path(namespace, key);
        if !path.exists() {
            return None;
        }
        let raw = fs::read_to_string(&path).ok()?;
        let reply: ModelReply = serde_json::from_str(&raw).ok()?;
        self.memo.insert(memo_key, reply.clone());
        Some(reply)
    }

    fn store(&mut self, namespace: &str, key: &str, reply: &ModelReply) -> Result<(), CallError> {
        let dir = self.cassette_dir(namespace);
        fs::create_dir_all(&dir)
            .map_err(|e| CallError::Transport { detail: format!("mkdir {}: {e}", dir.display()) })?;
        let path = self.cassette_path(namespace, key);
        let raw = serde_json::to_string_pretty(reply)
            .map_err(|e| CallError::Transport { detail: format!("serialize reply: {e}") })?;
        fs::write(&path, raw)
            .map_err(|e| CallError::Transport { detail: format!("write {}: {e}", path.display()) })?;
        self.memo.insert(Self::memo_key(namespace, key), reply.clone());
        Ok(())
    }
}

impl<C: ModelClient> ModelClient for CachedModelClient<C> {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
        let namespace = self.namespace_for(request).to_string();
        let key = request_key(request);
        if let Some(reply) = self.load(&namespace, &key) {
            return Ok(reply);
        }
        match self.mode {
            CacheMode::ReplayOnly => Err(CallError::CacheMiss { key }),
            CacheMode::Record => {
                let reply = self.inner.call(request)?;
                self.store(&namespace, &key, &reply)?;
                Ok(reply)
            }
        }
    }
}
