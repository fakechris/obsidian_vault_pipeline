use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::*;

/// Canonical recursively sorted JSON, even when another workspace crate enables
/// serde_json/preserve_order. Credential values and environment are never read.
pub fn decision_key(
    profile: &DecisionProfile,
    request: &DecisionRequest,
) -> Result<String, DecisionError> {
    fn canonical(v: Value) -> Value {
        match v {
            Value::Object(m) => Value::Object(
                m.into_iter()
                    .collect::<BTreeMap<_, _>>()
                    .into_iter()
                    .map(|(k, v)| (k, canonical(v)))
                    .collect(),
            ),
            Value::Array(a) => Value::Array(a.into_iter().map(canonical).collect()),
            v => v,
        }
    }
    let value = serde_json::json!({"schema":"ovp.decision/v1", "provider": profile.identity(), "request": request});
    let bytes = serde_json::to_vec(&canonical(value))
        .map_err(|_| DecisionError::InvalidRequest("serialization failed"))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

pub struct FixtureDecisionClient {
    profile: DecisionProfile,
    capabilities: DecisionCapabilities,
    replies: BTreeMap<String, DecisionReply>,
}
impl FixtureDecisionClient {
    pub fn new(
        profile: DecisionProfile,
        capabilities: DecisionCapabilities,
    ) -> Result<Self, DecisionError> {
        profile.validate()?;
        Ok(Self {
            profile,
            capabilities,
            replies: BTreeMap::new(),
        })
    }
    pub fn insert(
        &mut self,
        request: &DecisionRequest,
        mut reply: DecisionReply,
    ) -> Result<(), DecisionError> {
        request.validate(self.capabilities)?;
        reply.validate(&self.profile, request)?;
        reply.receipt.origin = DecisionOrigin::Fixture;
        reply.receipt.network_attempts = 0;
        self.replies
            .insert(decision_key(&self.profile, request)?, reply);
        Ok(())
    }
}
impl DecisionClient for FixtureDecisionClient {
    fn profile(&self) -> &DecisionProfile {
        &self.profile
    }
    fn capabilities(&self) -> DecisionCapabilities {
        self.capabilities
    }
    fn decide(&mut self, request: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
        request.validate(self.capabilities)?;
        let key = decision_key(&self.profile, request)?;
        self.replies
            .get(&key)
            .cloned()
            .ok_or(DecisionError::CacheMiss { key })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecisionCacheMode {
    Record,
    Replay,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cassette {
    schema: String,
    key: String,
    reply: DecisionReply,
}

/// Replay owns no live client and needs no credentials. Corrupt evidence fails
/// explicitly; it is never silently overwritten with a new provider result.
pub struct CachedDecisionClient {
    profile: DecisionProfile,
    capabilities: DecisionCapabilities,
    inner: Option<Box<dyn DecisionClient>>,
    directory: PathBuf,
    mode: DecisionCacheMode,
}
impl CachedDecisionClient {
    pub fn record(
        inner: Box<dyn DecisionClient>,
        directory: impl Into<PathBuf>,
    ) -> Result<Self, DecisionError> {
        inner.profile().validate()?;
        Ok(Self {
            profile: inner.profile().clone(),
            capabilities: inner.capabilities(),
            inner: Some(inner),
            directory: directory.into(),
            mode: DecisionCacheMode::Record,
        })
    }
    pub fn replay(
        profile: DecisionProfile,
        capabilities: DecisionCapabilities,
        directory: impl Into<PathBuf>,
    ) -> Result<Self, DecisionError> {
        profile.validate()?;
        Ok(Self {
            profile,
            capabilities,
            inner: None,
            directory: directory.into(),
            mode: DecisionCacheMode::Replay,
        })
    }
    fn store(&self, key: &str, reply: &DecisionReply) -> Result<(), DecisionError> {
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        fs::create_dir_all(&self.directory).map_err(|_| DecisionError::CacheIo)?;
        let path = self.directory.join(format!("{key}.json"));
        let temp = self.directory.join(format!(
            ".{key}.{}.{}.part",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let result = (|| {
            let mut opts = OpenOptions::new();
            opts.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                opts.mode(0o600);
            }
            let mut file = opts.open(&temp).map_err(|_| DecisionError::CacheIo)?;
            let data = serde_json::to_vec_pretty(&Cassette {
                schema: "ovp.decision.cassette/v1".into(),
                key: key.into(),
                reply: reply.clone(),
            })
            .map_err(|_| DecisionError::CacheIo)?;
            file.write_all(&data)
                .and_then(|_| file.sync_all())
                .map_err(|_| DecisionError::CacheIo)?;
            // Persist once. Concurrent recordings cannot overwrite the evidence
            // another run observed. A racing call fails explicitly rather than
            // reporting a successful result that cannot be replayed from disk.
            match fs::hard_link(&temp, &path) {
                Ok(()) => Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    Err(DecisionError::CacheConflict)
                }
                Err(_) => Err(DecisionError::CacheIo),
            }
        })();
        let _ = fs::remove_file(temp);
        result
    }
    fn load(
        &self,
        key: &str,
        request: &DecisionRequest,
    ) -> Result<Option<DecisionReply>, DecisionError> {
        let path = self.directory.join(format!("{key}.json"));
        let bytes = match fs::read(path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(DecisionError::CacheIo),
        };
        let value =
            super::typesafe::strict_json(&bytes).map_err(|_| DecisionError::CorruptCassette)?;
        let cassette: Cassette =
            serde_json::from_value(value).map_err(|_| DecisionError::CorruptCassette)?;
        if cassette.schema != "ovp.decision.cassette/v1" || cassette.key != key {
            return Err(DecisionError::CorruptCassette);
        }
        cassette
            .reply
            .validate(&self.profile, request)
            .map_err(|_| DecisionError::CorruptCassette)?;
        Ok(Some(cassette.reply))
    }
}
impl DecisionClient for CachedDecisionClient {
    fn profile(&self) -> &DecisionProfile {
        &self.profile
    }
    fn capabilities(&self) -> DecisionCapabilities {
        self.capabilities
    }
    fn decide(&mut self, request: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
        request.validate(self.capabilities)?;
        let key = decision_key(&self.profile, request)?;
        if let Some(mut reply) = self.load(&key, request)? {
            reply.receipt.origin = if self.mode == DecisionCacheMode::Replay {
                DecisionOrigin::Replay
            } else {
                DecisionOrigin::Cache
            };
            reply.receipt.network_attempts = 0;
            return Ok(reply);
        }
        let inner = self
            .inner
            .as_mut()
            .ok_or(DecisionError::CacheMiss { key: key.clone() })?;
        // Detect a client changing its profile behind the cache wrapper.
        if inner.profile().identity() != self.profile.identity() {
            return Err(DecisionError::InvalidRequest("client profile changed"));
        }
        let reply = inner.decide(request)?;
        reply.validate(&self.profile, request)?;
        self.store(&key, &reply)?;
        Ok(reply)
    }
}
