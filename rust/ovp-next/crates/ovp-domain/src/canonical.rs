use std::fmt;

use serde::{Deserialize, Serialize};

use crate::canonical_slug::CanonicalSlug;
use crate::vault_layout::VaultLayout;

/// The typed canonical record for an evergreen concept — the data a
/// `CanonicalUpsert` op carries. `EvergreenSink` builds one and serializes
/// it into the op's payload; a canonical-store reader deserializes it
/// back. The `WriteOp` payload stays a `String` in domain-blind `ovp-core`
/// (a serialization boundary); the *type* lives here, so the payload is
/// no longer an untyped blob.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonicalConcept {
    pub slug: String,
    pub title: String,
    /// Vault-relative path of the evergreen page this concept owns.
    pub evergreen_path: String,
    /// Source URL of the document that first surfaced this concept.
    pub provenance_source_url: String,
}

impl CanonicalConcept {
    /// Serialize to the JSON form carried in a `CanonicalUpsertOp.payload`.
    /// Compact + field-order-stable for content hashing.
    pub fn to_payload(&self) -> String {
        serde_json::to_string(self).expect("CanonicalConcept serializes")
    }

    /// Parse from a `CanonicalUpsertOp.payload`.
    pub fn from_payload(s: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(s)
    }

    /// Parse `(key, payload)` pairs into concepts, **failing loudly** on the
    /// first record that is unsound — returning a [`CanonicalParseError`]
    /// that names the offending key. This is the parser for derived-state
    /// rebuilds (MOC, knowledge index): an unsound canonical record must
    /// abort the rebuild, not silently shrink or misdirect the index
    /// (invariant #11).
    ///
    /// A record is sound iff:
    /// 1. its payload parses as a `CanonicalConcept`;
    /// 2. `payload.slug` is itself a valid [`CanonicalSlug`] — a rebuild must
    ///    never propagate an unsafe/divergent slug into derived views;
    /// 3. the store `key` equals `payload.slug` — identity discipline, since
    ///    rebuilds key off the slug; a divergent key would point
    ///    backlinks/MOC entries at an identity the store can't resolve;
    /// 4. `payload.evergreen_path` is exactly the slug's canonical path
    ///    (`VaultLayout::evergreen_note(slug)`) — a stray path would be
    ///    propagated into the knowledge index.
    pub fn try_parse_pairs<I, S>(pairs: I) -> Result<Vec<CanonicalConcept>, CanonicalParseError>
    where
        I: IntoIterator<Item = (S, S)>,
        S: AsRef<str>,
    {
        let mut out = Vec::new();
        for (key, payload) in pairs {
            let key = key.as_ref();
            let concept = Self::from_payload(payload.as_ref()).map_err(|e| CanonicalParseError {
                key: key.to_string(),
                message: e.to_string(),
            })?;
            if let Err(e) = CanonicalSlug::parse(&concept.slug) {
                return Err(CanonicalParseError {
                    key: key.to_string(),
                    message: format!(
                        "payload slug `{}` is not a valid canonical slug: {e}",
                        concept.slug
                    ),
                });
            }
            if concept.slug != key {
                return Err(CanonicalParseError {
                    key: key.to_string(),
                    message: format!(
                        "key/slug mismatch: store key `{key}` != payload slug `{}`",
                        concept.slug
                    ),
                });
            }
            // The evergreen page this concept owns must be the slug's canonical
            // path — otherwise a derived rebuild (knowledge index) would
            // propagate a backlink/path pointing at the wrong note.
            let expected_path = VaultLayout::new().evergreen_note(&concept.slug);
            if concept.evergreen_path != expected_path.as_str() {
                return Err(CanonicalParseError {
                    key: key.to_string(),
                    message: format!(
                        "evergreen_path `{}` does not match the slug's canonical path `{}`",
                        concept.evergreen_path,
                        expected_path.as_str()
                    ),
                });
            }
            out.push(concept);
        }
        Ok(out)
    }

    /// Permissive parse: `(key, payload)` pairs into concepts, **skipping**
    /// any record that fails to parse.
    ///
    /// DIAGNOSTICS ONLY. Do not use in rebuild paths — silently dropping an
    /// unparseable record would shrink a derived index without any signal.
    /// Rebuilds must use [`Self::try_parse_pairs`]. This stays for tooling
    /// that wants a best-effort view of a partially-corrupt store.
    pub fn parse_pairs<I, S>(pairs: I) -> Vec<CanonicalConcept>
    where
        I: IntoIterator<Item = (S, S)>,
        S: AsRef<str>,
    {
        pairs
            .into_iter()
            .filter_map(|(_key, payload)| Self::from_payload(payload.as_ref()).ok())
            .collect()
    }
}

/// A canonical record failed to parse during a derived-state rebuild. Names
/// the store key so the operator can find the corrupt record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonicalParseError {
    pub key: String,
    pub message: String,
}

impl fmt::Display for CanonicalParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "corrupt canonical record `{}`: {}", self.key, self.message)
    }
}

impl std::error::Error for CanonicalParseError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> CanonicalConcept {
        CanonicalConcept {
            slug: "agent-native-pm".into(),
            title: "Agent Native Pm".into(),
            evergreen_path: "10-Knowledge/Evergreen/agent-native-pm.md".into(),
            provenance_source_url: "https://example.com/src".into(),
        }
    }

    #[test]
    fn payload_round_trips() {
        let c = sample();
        let payload = c.to_payload();
        assert_eq!(CanonicalConcept::from_payload(&payload).unwrap(), c);
    }

    #[test]
    fn parse_pairs_skips_bad_payloads() {
        let pairs = vec![
            ("ai-agent".to_string(), sample().to_payload()),
            ("broken".to_string(), "not json".to_string()),
        ];
        let concepts = CanonicalConcept::parse_pairs(pairs);
        assert_eq!(concepts.len(), 1, "bad payload skipped (diagnostics helper)");
        assert_eq!(concepts[0].slug, "agent-native-pm");
    }

    #[test]
    fn try_parse_pairs_ok_on_all_valid() {
        let pairs = vec![("agent-native-pm".to_string(), sample().to_payload())];
        let concepts = CanonicalConcept::try_parse_pairs(pairs).unwrap();
        assert_eq!(concepts.len(), 1);
        assert_eq!(concepts[0], sample());
    }

    #[test]
    fn try_parse_pairs_fails_loudly_on_corrupt_payload() {
        let pairs = vec![
            // First record is sound (key == slug); the second is corrupt.
            ("agent-native-pm".to_string(), sample().to_payload()),
            ("broken".to_string(), "not json".to_string()),
        ];
        let err = CanonicalConcept::try_parse_pairs(pairs).unwrap_err();
        assert_eq!(err.key, "broken", "names the offending key");
        // No silent shrink: the error short-circuits the whole rebuild.
        assert!(err.to_string().contains("corrupt canonical record `broken`"));
    }

    #[test]
    fn try_parse_pairs_rejects_key_slug_mismatch() {
        // Payload is valid, but it's filed under the wrong key.
        let pairs = vec![("wrong-key".to_string(), sample().to_payload())];
        let err = CanonicalConcept::try_parse_pairs(pairs).unwrap_err();
        assert_eq!(err.key, "wrong-key");
        assert!(err.to_string().contains("key/slug mismatch"), "got: {err}");
    }

    #[test]
    fn try_parse_pairs_rejects_invalid_slug_in_payload() {
        // A payload whose own slug is not a valid canonical slug.
        let bad = CanonicalConcept {
            slug: "a/b".into(),
            title: "x".into(),
            evergreen_path: "10-Knowledge/Evergreen/a b.md".into(),
            provenance_source_url: "u".into(),
        };
        let pairs = vec![("a/b".to_string(), bad.to_payload())];
        let err = CanonicalConcept::try_parse_pairs(pairs).unwrap_err();
        assert_eq!(err.key, "a/b");
        assert!(err.to_string().contains("not a valid canonical slug"), "got: {err}");
    }

    #[test]
    fn try_parse_pairs_rejects_evergreen_path_mismatch() {
        // Valid slug, key == slug, but evergreen_path points at the wrong note.
        let bad = CanonicalConcept {
            slug: "ai-agent".into(),
            title: "Ai Agent".into(),
            evergreen_path: "10-Knowledge/Evergreen/something-else.md".into(),
            provenance_source_url: "u".into(),
        };
        let pairs = vec![("ai-agent".to_string(), bad.to_payload())];
        let err = CanonicalConcept::try_parse_pairs(pairs).unwrap_err();
        assert_eq!(err.key, "ai-agent");
        assert!(err.to_string().contains("evergreen_path"), "got: {err}");
    }

    #[test]
    fn payload_is_compact_field_ordered() {
        let payload = sample().to_payload();
        assert!(payload.starts_with("{\"slug\":\"agent-native-pm\""));
        assert!(payload.contains("\"evergreen_path\":\"10-Knowledge/Evergreen/agent-native-pm.md\""));
        assert!(!payload.contains(": ")); // compact, no spaces
    }
}
