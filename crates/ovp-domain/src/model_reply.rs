//! M19 — tolerant model-reply JSON handling for the grounded reader trunk.
//!
//! Live models (esp. non-Anthropic providers) occasionally emit *almost*-valid
//! JSON: a stray fence, an unescaped backslash copied verbatim from source text,
//! a dropped quote, a structural break in a long reply. M18 lost 3/20 packs to
//! exactly these. This module adds **two** bounded, safety-first layers:
//!
//! 1. **Parser-local recovery** ([`parse_reply_value`]): strip a markdown fence,
//!    locate the JSON envelope, and apply ONE well-defined syntactic fix —
//!    doubling backslashes that are not valid JSON escapes (the `tengu\session`
//!    class). Nothing else is guessed. Anything we cannot fix safely returns a
//!    classified [`JsonDefect`] so the caller fails loud (never silent-accepts).
//!
//! 2. **Bounded model repair** ([`json_repair_request`]): a single follow-up call
//!    asking the model to fix ONLY JSON syntax, preserving every field/string —
//!    no re-extraction. The repaired text goes back through the SAME parser +
//!    validator, so repair can never bypass grounding.
//!
//! Pure (this module): no I/O, no client. The repair *call* is wired by the
//! harness, which owns the `ModelClient`.

use ovp_llm::{ModelMessage, ModelRequest};

/// Why a model reply could not be turned into a JSON value — or how it was
/// recovered. Carries enough detail for a fail-loud error message that names the
/// defect class (never a bare "parse error").
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JsonDefect {
    /// Recovered locally by doubling backslashes that were not valid JSON
    /// escapes (unescaped `\` copied from source text). Not an error — the
    /// `Ok` recovery note.
    RepairedUnescapedBackslash,
    /// No `{`/`[` JSON envelope found after stripping fence/prose.
    NoEnvelope,
    /// serde rejected the located envelope for a reason we will NOT guess-fix
    /// (a dropped quote, a structural break, …). Carries the serde detail and is
    /// the signal to attempt a bounded model repair. Never silently accepted.
    Unrecoverable(String),
}

impl std::fmt::Display for JsonDefect {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JsonDefect::RepairedUnescapedBackslash => {
                write!(f, "unescaped-backslash (repaired locally)")
            }
            JsonDefect::NoEnvelope => write!(f, "no JSON object/array envelope in reply"),
            JsonDefect::Unrecoverable(d) => write!(f, "unrecoverable JSON: {d}"),
        }
    }
}

/// A record of how one stage's reply was salvaged — surfaced into the reader
/// pack's `run-status.json` so a repaired pack is auditable, never silent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepairNote {
    /// Which stage produced the reply: `"units"` or `"cards"`.
    pub stage: &'static str,
    /// Human-readable description of the salvage applied.
    pub method: String,
}

impl RepairNote {
    /// Recovered without a model call (the safe backslash fix).
    pub fn parser_local(stage: &'static str) -> Self {
        Self { stage, method: "parser-local: unescaped-backslash".into() }
    }

    /// Recovered via the bounded model JSON-repair call.
    pub fn model_repair(stage: &'static str, input_defect: &JsonDefect) -> Self {
        Self { stage, method: format!("model-repair (input defect: {input_defect})") }
    }
}

/// Valid characters that may follow a `\` inside a JSON string.
const VALID_ESCAPE: &[char] = &['"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u'];

/// Strip a leading ```json / ``` fence and a trailing ``` if the model wrapped
/// its reply. Returns the inner text (trimmed). Idempotent on un-fenced text.
pub fn strip_code_fence(text: &str) -> &str {
    let t = text.trim();
    if let Some(rest) = t.strip_prefix("```json") {
        return rest.trim_start_matches('\n').trim_end_matches("```").trim();
    }
    if let Some(rest) = t.strip_prefix("```") {
        return rest.trim_start_matches('\n').trim_end_matches("```").trim();
    }
    t
}

/// Locate the outermost balanced `{..}` or `[..]` envelope, skipping any prose
/// before/after it. Tracks JSON string state so braces inside strings don't
/// count. Returns `None` if there is no opener or the structure never balances
/// (e.g. a dropped quote desyncs string tracking) — the caller then fails loud
/// or attempts a model repair.
pub fn locate_envelope(text: &str) -> Option<&str> {
    let bytes = text.as_bytes();
    let start = bytes.iter().position(|&b| b == b'{' || b == b'[')?;
    let open = bytes[start];
    let close = if open == b'{' { b'}' } else { b']' };
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
            x if x == open => depth += 1,
            x if x == close => {
                depth -= 1;
                if depth == 0 {
                    return Some(&text[start..=i]);
                }
            }
            _ => {}
        }
    }
    None
}

/// Double any backslash *inside a JSON string* that is not the start of a valid
/// escape sequence — the `tengu\session\memory` defect where source text with
/// literal backslashes is copied into a JSON string without escaping. Safe and
/// idempotent on already-valid JSON (every `\` there is a valid escape, so
/// nothing changes). Does NOT touch backslashes outside strings (valid JSON has
/// none there). String tracking relies on balanced quotes; if quotes are
/// unbalanced this is a no-op-ish best effort and the serde retry simply fails.
pub fn escape_stray_backslashes(json: &str) -> String {
    let chars: Vec<char> = json.chars().collect();
    let mut out = String::with_capacity(json.len());
    let mut in_str = false;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if !in_str {
            out.push(c);
            if c == '"' {
                in_str = true;
            }
            i += 1;
            continue;
        }
        // inside a string
        if c == '\\' {
            let next = chars.get(i + 1).copied();
            match next {
                Some(n) if VALID_ESCAPE.contains(&n) => {
                    // valid escape — keep both chars verbatim
                    out.push(c);
                    out.push(n);
                    i += 2;
                }
                _ => {
                    // stray backslash → escape it; re-examine `next` next loop
                    out.push('\\');
                    out.push('\\');
                    i += 1;
                }
            }
            continue;
        }
        if c == '"' {
            in_str = false;
        }
        out.push(c);
        i += 1;
    }
    out
}

/// Parse a model reply into a `serde_json::Value`, tolerantly:
/// fence-strip → envelope-locate → `serde_json` parse; on failure, try the
/// backslash repair and re-parse once. Returns the value plus an optional
/// recovery note ([`JsonDefect::RepairedUnescapedBackslash`]), or a classified
/// [`JsonDefect`] the caller must surface (fail loud / attempt model repair).
pub fn parse_reply_value(text: &str) -> Result<(serde_json::Value, Option<JsonDefect>), JsonDefect> {
    let stripped = strip_code_fence(text);
    let candidate = locate_envelope(stripped).unwrap_or_else(|| stripped.trim());

    match serde_json::from_str::<serde_json::Value>(candidate) {
        Ok(v) => Ok((v, None)),
        Err(first_err) => {
            // One safe local fix: re-escape stray backslashes, then retry.
            let repaired = escape_stray_backslashes(candidate);
            if repaired != candidate
                && let Ok(v) = serde_json::from_str::<serde_json::Value>(&repaired) {
                    return Ok((v, Some(JsonDefect::RepairedUnescapedBackslash)));
                }
            // Classify the unrecoverable case for a precise fail-loud message.
            let has_envelope =
                locate_envelope(stripped).is_some() || stripped.trim_start().starts_with(['{', '[']);
            if has_envelope {
                Err(JsonDefect::Unrecoverable(first_err.to_string()))
            } else {
                Err(JsonDefect::NoEnvelope)
            }
        }
    }
}

/// System prompt for the bounded JSON-repair call. Deliberately narrow: fix
/// syntax ONLY, change no content. This is NOT content prompt tuning — it never
/// touches the extraction/critic/card prompts and cannot add or drop facts.
const REPAIR_SYSTEM: &str = "You are a strict JSON syntax repair tool. The user message contains text that was meant to be a single valid JSON document but has syntax errors (for example: a backslash that is not a valid JSON escape, a missing or extra quote, a missing comma, an unbalanced bracket or brace, or text/markdown around the JSON).\n\nReturn ONLY the corrected JSON document and nothing else — no explanation, no markdown fence.\n\nRules:\n- Preserve every field name, string value, number, boolean, null, and array element exactly as written.\n- Only fix JSON syntax. Do NOT add, remove, reorder, summarize, translate, or rephrase any content.\n- Do not invent fields or values. If a value looks truncated, keep it as-is; do not complete it.\n- Output must be a single parseable JSON value.";

/// Build the bounded repair request: ask the model to fix ONLY the JSON syntax
/// of `broken_reply`. The reply still goes back through the normal parser +
/// validator, so this cannot bypass grounding. `max_tokens` is generous (the
/// repaired doc may be as large as the original); the live client further raises
/// it via `OVP_LLM_MAX_TOKENS`.
pub fn json_repair_request(broken_reply: &str) -> ModelRequest {
    ModelRequest {
        model: "claude-sonnet-4-6".to_string(),
        system: Some(REPAIR_SYSTEM.to_string()),
        messages: vec![ModelMessage::User {
            content: format!("Repair this into valid JSON:\n\n{broken_reply}"),
        }],
        max_tokens: 16384,
        temperature: None,
        tools: None,
        cache_namespace: Some("json_repair/v1".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_json_fence() {
        assert_eq!(strip_code_fence("```json\n{\"a\":1}\n```"), "{\"a\":1}");
        assert_eq!(strip_code_fence("```\n{\"a\":1}\n```"), "{\"a\":1}");
        assert_eq!(strip_code_fence("{\"a\":1}"), "{\"a\":1}");
    }

    #[test]
    fn locates_envelope_skipping_prose() {
        assert_eq!(locate_envelope("here you go: {\"a\":1} done"), Some("{\"a\":1}"));
        assert_eq!(locate_envelope("[1,2,3]"), Some("[1,2,3]"));
        // brace inside a string does not close early
        assert_eq!(locate_envelope("{\"a\":\"}\"}"), Some("{\"a\":\"}\"}"));
        // dropped quote desyncs string tracking → no balanced envelope
        assert_eq!(locate_envelope("{\"a\": b\"}"), None);
    }

    #[test]
    fn escape_repair_fixes_source_backslashes() {
        // m18-04 class: a windows-ish path copied verbatim into a JSON string.
        let bad = r#"{"text":"path like tengu\session\memory here"}"#;
        assert!(serde_json::from_str::<serde_json::Value>(bad).is_err(), "precondition: invalid");
        let fixed = escape_stray_backslashes(bad);
        let v: serde_json::Value = serde_json::from_str(&fixed).expect("repaired parses");
        assert_eq!(v["text"], "path like tengu\\session\\memory here");
    }

    #[test]
    fn escape_repair_preserves_valid_escapes_and_is_idempotent() {
        let good = r#"{"text":"line1\nline2\t\"quoted\" \\ end","u":"é"}"#;
        let once = escape_stray_backslashes(good);
        assert_eq!(once, good, "valid escapes unchanged");
        let twice = escape_stray_backslashes(&once);
        assert_eq!(twice, once, "idempotent");
        // and it still parses to the same value
        let a: serde_json::Value = serde_json::from_str(good).unwrap();
        let b: serde_json::Value = serde_json::from_str(&once).unwrap();
        assert_eq!(a, b);
    }

    #[test]
    fn parse_reply_value_recovers_unescaped_backslash() {
        let bad = r#"```json
{"units":[{"text":"see tengu\session\memory"}]}
```"#;
        let (v, note) = parse_reply_value(bad).expect("recovered");
        assert_eq!(note, Some(JsonDefect::RepairedUnescapedBackslash));
        assert_eq!(v["units"][0]["text"], "see tengu\\session\\memory");
    }

    #[test]
    fn parse_reply_value_clean_has_no_note() {
        let (_v, note) = parse_reply_value(r#"{"units":[]}"#).unwrap();
        assert_eq!(note, None);
    }

    #[test]
    fn parse_reply_value_missing_quote_is_unrecoverable() {
        // m18-06 class: a dropped opening quote on an array element.
        let bad = r#"{"cards":[{"cited_unit_ids":["u-024-abc", u-025-def"]}]}"#;
        let err = parse_reply_value(bad).unwrap_err();
        assert!(
            matches!(err, JsonDefect::Unrecoverable(_) | JsonDefect::NoEnvelope),
            "missing-quote must NOT be silently accepted, got {err:?}"
        );
    }

    #[test]
    fn parse_reply_value_structural_break_is_unrecoverable() {
        // m18-19 class: a stray field outside an object in a long reply.
        let bad = r#"{"units":[{"a":1} , "surface":"x" ]}"#;
        let err = parse_reply_value(bad).unwrap_err();
        assert!(matches!(err, JsonDefect::Unrecoverable(_)));
    }

    #[test]
    fn parse_reply_value_no_envelope() {
        let err = parse_reply_value("the model refused to answer").unwrap_err();
        assert_eq!(err, JsonDefect::NoEnvelope);
    }

    #[test]
    fn repair_request_is_syntax_only() {
        let req = json_repair_request("{bad json}");
        let sys = req.system.unwrap();
        assert!(sys.contains("Only fix JSON syntax"));
        assert!(sys.contains("Do NOT add, remove"));
        assert_eq!(req.cache_namespace.as_deref(), Some("json_repair/v1"));
        assert!(req.max_tokens >= 16384);
    }
}
