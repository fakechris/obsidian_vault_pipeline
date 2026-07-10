//! Parse the classifier reply into raw referent candidates.
//!
//! Two-step like `units/parser.rs`: [`parse_referent_envelope`] extracts the
//! `referents[]` array as opaque JSON values; the validator deserializes each
//! into a [`RawReferent`] individually, so one malformed candidate becomes a
//! rejected candidate (carrying the serde error) instead of failing the case.

use serde::{Deserialize, Deserializer};

use super::{Boundary, ReferentKind};

/// The classifier's per-candidate shape, before validation. `evidence_refs` and
/// `confidence` are NOT taken from the model — the validator derives them from
/// the real supporting units. `kind` is a closed enum: a bad value fails THIS
/// candidate's deserialize → a rejected (malformed) candidate, not the run.
#[derive(Debug, Clone, Deserialize)]
pub struct RawReferent {
    #[serde(default, deserialize_with = "null_to_default")]
    pub surface_names: Vec<String>,
    pub kind: ReferentKind,
    #[serde(default)]
    pub subtype: Option<String>,
    #[serde(default, deserialize_with = "null_to_default")]
    pub support_unit_ids: Vec<String>,
    #[serde(default)]
    pub rationale: String,
    #[serde(default)]
    pub boundary: Option<Boundary>,
}

fn null_to_default<'de, D, T>(de: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(de)?.unwrap_or_default())
}

/// Why the envelope itself could not be parsed (vs. a single bad candidate).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferentParseError {
    pub detail: String,
}

/// Extract the `referents[]` array as opaque JSON values. Strips a ```json fence
/// and tolerates leading prose by scanning to the first balanced object.
pub fn parse_referent_envelope(reply_text: &str) -> Result<Vec<serde_json::Value>, ReferentParseError> {
    let raw = extract_object(reply_text)
        .ok_or_else(|| ReferentParseError { detail: "no JSON object in reply".into() })?;
    let value: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| ReferentParseError { detail: format!("not JSON: {e}") })?;
    let referents = value
        .get("referents")
        .ok_or_else(|| ReferentParseError { detail: "missing `referents` array".into() })?;
    match referents {
        serde_json::Value::Array(items) => Ok(items.clone()),
        _ => Err(ReferentParseError { detail: "`referents` is not an array".into() }),
    }
}

/// Strip a code fence, else return the first balanced `{...}` (string-aware).
fn extract_object(text: &str) -> Option<String> {
    let t = text.trim();
    let t = t.strip_prefix("```json").or_else(|| t.strip_prefix("```")).unwrap_or(t);
    let t = t.trim_start_matches('\n').trim_end_matches("```").trim();
    if t.starts_with('{') && serde_json::from_str::<serde_json::Value>(t).is_ok() {
        return Some(t.to_string());
    }
    let bytes = t.as_bytes();
    let start = bytes.iter().position(|&b| b == b'{')?;
    let (mut depth, mut in_str, mut esc) = (0i32, false, false);
    for (i, &b) in bytes.iter().enumerate().skip(start) {
        if in_str {
            match b {
                _ if esc => esc = false,
                b'\\' => esc = true,
                b'"' => in_str = false,
                _ => {}
            }
            continue;
        }
        match b {
            b'"' => in_str = true,
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(t[start..=i].to_string());
                }
            }
            _ => {}
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_referents_array() {
        let j = r#"{"referents":[{"kind":"entity","surface_names":["IdeaBlock"]},{"kind":"noise"}]}"#;
        assert_eq!(parse_referent_envelope(j).unwrap().len(), 2);
    }

    #[test]
    fn strips_fence_and_prose() {
        let j = "Here:\n```json\n{\"referents\":[]}\n```";
        assert_eq!(parse_referent_envelope(j).unwrap().len(), 0);
    }

    #[test]
    fn missing_referents_is_error() {
        assert!(parse_referent_envelope(r#"{"foo":1}"#).is_err());
        assert!(parse_referent_envelope("not json").is_err());
    }

    #[test]
    fn raw_referent_tolerates_null_lists_and_bad_kind_fails_that_item() {
        let v: serde_json::Value = serde_json::from_str(
            r#"{"kind":"concept","surface_names":null,"support_unit_ids":null}"#,
        )
        .unwrap();
        let r: RawReferent = serde_json::from_value(v).unwrap();
        assert!(r.surface_names.is_empty() && r.support_unit_ids.is_empty());
        // A bad kind enum fails just this candidate's deserialize.
        let bad: serde_json::Value =
            serde_json::from_str(r#"{"kind":"thingy","surface_names":["x"]}"#).unwrap();
        assert!(serde_json::from_value::<RawReferent>(bad).is_err());
    }
}
