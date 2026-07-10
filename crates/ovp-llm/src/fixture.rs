use std::collections::HashMap;

use crate::client::{CallError, ModelClient};
use crate::key::request_key;
use crate::reply::ModelReply;
use crate::request::ModelRequest;

/// In-memory map from request-key to canned reply. Tests `insert` known
/// (request, reply) pairs and the client serves them. Unknown requests
/// surface as `CallError::CacheMiss` — no network, no surprise.
pub struct FixtureModelClient {
    replies: HashMap<String, ModelReply>,
}

impl FixtureModelClient {
    pub fn new() -> Self {
        Self { replies: HashMap::new() }
    }

    /// Pre-register a reply for the given request.
    pub fn insert(&mut self, request: &ModelRequest, reply: ModelReply) {
        let key = request_key(request);
        self.replies.insert(key, reply);
    }

    pub fn len(&self) -> usize { self.replies.len() }
    pub fn is_empty(&self) -> bool { self.replies.is_empty() }
}

impl Default for FixtureModelClient {
    fn default() -> Self { Self::new() }
}

impl ModelClient for FixtureModelClient {
    fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
        let key = request_key(request);
        self.replies
            .get(&key)
            .cloned()
            .ok_or(CallError::CacheMiss { key })
    }
}
