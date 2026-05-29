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
}

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
    fn payload_is_compact_field_ordered() {
        let payload = sample().to_payload();
        assert!(payload.starts_with("{\"slug\":\"agent-native-pm\""));
        assert!(payload.contains("\"evergreen_path\":\"10-Knowledge/Evergreen/agent-native-pm.md\""));
        assert!(!payload.contains(": ")); // compact, no spaces
    }
}
