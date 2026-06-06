//! Parse the model's unit-extraction reply into raw units.
//!
//! Two-step on purpose: [`parse_envelope`] only extracts the `units[]` array as
//! opaque JSON values; the validator deserializes each value into a [`RawUnit`]
//! individually, so one malformed unit becomes a rejected unit (with the serde
//! error as its reason) instead of failing the whole document. This mirrors the
//! M13.3 lesson — a single bad field should not silently lose every unit.

use serde::{Deserialize, Deserializer};

use super::{Argument, Attribution, Modality, UnitKind};

/// The model's per-unit shape, before validation. `kind` / `text` /
/// `evidence_quote` / `attribution` / `modality` are required — a missing or
/// invalid value fails THIS unit's deserialize (→ a rejected unit), not the run.
#[derive(Debug, Clone, Deserialize)]
pub struct RawUnit {
    pub kind: UnitKind,
    #[serde(default)]
    pub subtype: Option<String>,
    pub text: String,
    /// The `pNNN` paragraph id the unit is anchored to (M14a.1). Required — a
    /// missing ref fails this unit's deserialize → a rejected (malformed) unit.
    pub evidence_ref: String,
    pub evidence_quote: String,
    pub attribution: Attribution,
    pub modality: Modality,
    #[serde(default, deserialize_with = "null_to_default")]
    pub arguments: Vec<Argument>,
}

/// Tolerate an explicit JSON `null` for list fields (models emit it despite the
/// schema) by mapping it to the default, the way `#[serde(default)]` alone does
/// NOT (it only fills a *missing* field).
fn null_to_default<'de, D, T>(de: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(de)?.unwrap_or_default())
}

/// Why the envelope itself could not be parsed (vs. a single bad unit).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub detail: String,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.detail)
    }
}

/// Extract the `units[]` array as opaque JSON values. Tolerant (M19): strips a
/// ```json fence / surrounding prose and applies parser-local recovery
/// (unescaped-backslash) via [`crate::model_reply::parse_reply_value`]. The
/// recovery NOTE is discarded here; callers that record repairs (the harness)
/// drive recovery+repair themselves and then call [`units_from_value`].
pub fn parse_envelope(reply_text: &str) -> Result<Vec<serde_json::Value>, ParseError> {
    let (value, _note) = crate::model_reply::parse_reply_value(reply_text)
        .map_err(|d| ParseError { detail: d.to_string() })?;
    units_from_value(&value)
}

/// Pull the `units[]` array (opaque values) out of an already-parsed envelope.
pub fn units_from_value(value: &serde_json::Value) -> Result<Vec<serde_json::Value>, ParseError> {
    let units = value
        .get("units")
        .ok_or_else(|| ParseError { detail: "missing `units` array".into() })?;
    match units {
        serde_json::Value::Array(items) => Ok(items.clone()),
        other => Err(ParseError {
            detail: format!("`units` is not an array (got {})", kind_name(other)),
        }),
    }
}

fn kind_name(v: &serde_json::Value) -> &'static str {
    match v {
        serde_json::Value::Null => "null",
        serde_json::Value::Bool(_) => "bool",
        serde_json::Value::Number(_) => "number",
        serde_json::Value::String(_) => "string",
        serde_json::Value::Array(_) => "array",
        serde_json::Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_units_array() {
        let json = r#"{"units":[{"kind":"assertion"},{"kind":"question"}]}"#;
        let items = parse_envelope(json).unwrap();
        assert_eq!(items.len(), 2);
    }

    #[test]
    fn strips_code_fence() {
        let json = "```json\n{\"units\":[]}\n```";
        assert_eq!(parse_envelope(json).unwrap().len(), 0);
    }

    #[test]
    fn non_json_is_envelope_error() {
        assert!(parse_envelope("not json at all").is_err());
    }

    #[test]
    fn missing_units_is_envelope_error() {
        let err = parse_envelope(r#"{"foo":1}"#).unwrap_err();
        assert!(err.detail.contains("units"));
    }

    #[test]
    fn raw_unit_tolerates_null_arguments() {
        let v: serde_json::Value = serde_json::from_str(
            r#"{"kind":"assertion","text":"t","evidence_ref":"p001","evidence_quote":"q","attribution":"author","modality":"asserted","arguments":null}"#,
        )
        .unwrap();
        let u: RawUnit = serde_json::from_value(v).unwrap();
        assert!(u.arguments.is_empty());
        assert_eq!(u.evidence_ref, "p001");
    }

    #[test]
    fn raw_unit_missing_evidence_ref_fails_that_unit() {
        // No `evidence_ref` → this single unit's deserialize fails (the validator
        // turns that into a rejected/malformed unit; the envelope still parses).
        let v: serde_json::Value = serde_json::from_str(
            r#"{"kind":"assertion","text":"t","evidence_quote":"q","attribution":"author","modality":"asserted"}"#,
        )
        .unwrap();
        assert!(serde_json::from_value::<RawUnit>(v).is_err());
    }
}
