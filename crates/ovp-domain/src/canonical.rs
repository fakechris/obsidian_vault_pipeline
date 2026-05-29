use std::fmt;

use serde::{Deserialize, Serialize};

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
    /// first record that doesn't parse — returning a [`CanonicalParseError`]
    /// that names the offending key. This is the parser for derived-state
    /// rebuilds (MOC, knowledge index): a corrupt canonical record must
    /// abort the rebuild, not silently shrink the index (which would drop a
    /// real concept from every downstream view, invariant #11).
    pub fn try_parse_pairs<I, S>(pairs: I) -> Result<Vec<CanonicalConcept>, CanonicalParseError>
    where
        I: IntoIterator<Item = (S, S)>,
        S: AsRef<str>,
    {
        let mut out = Vec::new();
        for (key, payload) in pairs {
            match Self::from_payload(payload.as_ref()) {
                Ok(c) => out.push(c),
                Err(e) => {
                    return Err(CanonicalParseError {
                        key: key.as_ref().to_string(),
                        message: e.to_string(),
                    });
                }
            }
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
            ("ai-agent".to_string(), sample().to_payload()),
            ("broken".to_string(), "not json".to_string()),
        ];
        let err = CanonicalConcept::try_parse_pairs(pairs).unwrap_err();
        assert_eq!(err.key, "broken", "names the offending key");
        // No silent shrink: the error short-circuits the whole rebuild.
        assert!(err.to_string().contains("corrupt canonical record `broken`"));
    }

    #[test]
    fn payload_is_compact_field_ordered() {
        let payload = sample().to_payload();
        assert!(payload.starts_with("{\"slug\":\"agent-native-pm\""));
        assert!(payload.contains("\"evergreen_path\":\"10-Knowledge/Evergreen/agent-native-pm.md\""));
        assert!(!payload.contains(": ")); // compact, no spaces
    }
}
