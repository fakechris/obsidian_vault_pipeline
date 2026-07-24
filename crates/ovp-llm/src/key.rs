use sha2::{Digest, Sha256};

use crate::request::ModelRequest;

/// Deterministic SHA-256 hex string keyed on the canonicalized JSON of
/// a `ModelRequest`. Used by `FixtureModelClient` and `CachedModelClient`
/// to look up canned replies. Two requests with the same key are
/// considered identical for replay purposes.
pub fn request_key(request: &ModelRequest) -> String {
    let bytes = serde_json::to_vec(request).expect("ModelRequest serializes deterministically");
    let hash = Sha256::digest(&bytes);
    hex_lower(&hash)
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    use std::fmt::Write;
    for b in bytes {
        write!(s, "{:02x}", b).expect("write to String is infallible");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::request::{ModelMessage, ToolDef};
    use serde_json::json;

    fn req() -> ModelRequest {
        ModelRequest {
            model: "test".into(),
            system: None,
            messages: vec![ModelMessage::User { content: "hi".into() }],
            max_tokens: 100,
            temperature: None,
            tools: None,
            cache_namespace: None,
        }
    }

    #[test]
    fn key_is_stable_across_calls() {
        let r = req();
        assert_eq!(request_key(&r), request_key(&r));
    }

    #[test]
    fn key_differs_on_content_change() {
        let a = req();
        let mut b = req();
        b.messages = vec![ModelMessage::User { content: "different".into() }];
        assert_ne!(request_key(&a), request_key(&b));
    }

    #[test]
    fn cache_namespace_does_not_affect_key() {
        // cache_namespace is #[serde(skip)] — it picks the cassette dir,
        // never the hash. Two requests differing only in namespace must
        // share a key (so existing cassettes stay valid).
        let a = req();
        let b = req().with_cache_namespace("article_interpret/v1");
        assert_eq!(request_key(&a), request_key(&b));
    }

    #[test]
    fn key_is_64_hex_chars() {
        let k = request_key(&req());
        assert_eq!(k.len(), 64);
        assert!(k.chars().all(|c| c.is_ascii_hexdigit() && (c.is_ascii_digit() || c.is_ascii_lowercase())));
    }

    #[test]
    fn tool_less_key_matches_pre_protocol_request_json() {
        assert_eq!(
            request_key(&req()),
            "5c9b43757b8975b8b1cc2f52ebf02d981c4665278eb24e71a2bc97efc8f2e558"
        );
    }

    #[test]
    fn tool_key_uses_only_name_and_version() {
        let mut baseline = req();
        baseline.tools = Some(vec![ToolDef {
            name: "vault_search".into(),
            version: "v1".into(),
            description: "Search the vault".into(),
            input_schema: json!({"type": "object", "properties": {"query": {"type": "string"}}}),
        }]);

        let mut docs_changed = baseline.clone();
        let tool = docs_changed.tools.as_mut().unwrap().first_mut().unwrap();
        tool.description = "Search every indexed vault document".into();
        tool.input_schema = json!({
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search text"}}
        });
        assert_eq!(request_key(&baseline), request_key(&docs_changed));

        let mut name_changed = baseline.clone();
        name_changed.tools.as_mut().unwrap()[0].name = "vault_lookup".into();
        assert_ne!(request_key(&baseline), request_key(&name_changed));

        let mut version_changed = baseline.clone();
        version_changed.tools.as_mut().unwrap()[0].version = "v2".into();
        assert_ne!(request_key(&baseline), request_key(&version_changed));
    }
}
