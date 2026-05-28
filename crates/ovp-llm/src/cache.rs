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
/// `<cache_dir>/<sha256-hex>.json`; one cassette per unique request.
///
/// In `Record` mode the cache backfills from the inner client and saves
/// each response as it's recorded. In `ReplayOnly` mode cache misses are
/// errors — the inner client is never consulted.
pub struct CachedModelClient<C: ModelClient> {
    inner: C,
    cache_dir: PathBuf,
    mode: CacheMode,
    memo: HashMap<String, ModelReply>,
}

impl<C: ModelClient> CachedModelClient<C> {
    pub fn new(inner: C, cache_dir: impl Into<PathBuf>, mode: CacheMode) -> std::io::Result<Self> {
        let cache_dir = cache_dir.into();
        if mode == CacheMode::Record {
            fs::create_dir_all(&cache_dir)?;
        }
        Ok(Self { inner, cache_dir, mode, memo: HashMap::new() })
    }

    pub fn mode(&self) -> CacheMode { self.mode }
    pub fn cache_dir(&self) -> &PathBuf { &self.cache_dir }

    fn cassette_path(&self, key: &str) -> PathBuf {
        self.cache_dir.join(format!("{key}.json"))
    }

    fn load(&mut self, key: &str) -> Option<ModelReply> {
        if let Some(r) = self.memo.get(key) {
            return Some(r.clone());
        }
        let path = self.cassette_path(key);
        if !path.exists() {
            return None;
        }
        let raw = fs::read_to_string(&path).ok()?;
        let reply: ModelReply = serde_json::from_str(&raw).ok()?;
        self.memo.insert(key.to_string(), reply.clone());
        Some(reply)
    }

    fn store(&mut self, key: &str, reply: &ModelReply) -> Result<(), CallError> {
        let path = self.cassette_path(key);
        let raw = serde_json::to_string_pretty(reply)
            .map_err(|e| CallError::Transport { detail: format!("serialize reply: {e}") })?;
        fs::write(&path, raw)
            .map_err(|e| CallError::Transport { detail: format!("write {}: {e}", path.display()) })?;
        self.memo.insert(key.to_string(), reply.clone());
        Ok(())
    }
}

impl<C: ModelClient> ModelClient for CachedModelClient<C> {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
        let key = request_key(request);
        if let Some(reply) = self.load(&key) {
            return Ok(reply);
        }
        match self.mode {
            CacheMode::ReplayOnly => Err(CallError::CacheMiss { key }),
            CacheMode::Record => {
                let reply = self.inner.call(request)?;
                self.store(&key, &reply)?;
                Ok(reply)
            }
        }
    }
}
