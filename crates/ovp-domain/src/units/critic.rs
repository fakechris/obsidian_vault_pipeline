//! M14a.8 — critic-assisted bounded repair.
//!
//! The single-pass extractor must jointly satisfy four objectives (grounded,
//! faithful, covered, concise); prompt-tuning them against each other oscillated
//! (v4→v5→v6).
//! This step FREEZES the v5 generator and adds ONE independent audit pass that
//! never extracts freely: it (1) flags `text` that asserts more than the source
//! supports and proposes a verbatim-substring TRIM, and (2) flags central points
//! no unit covers and proposes a grounded ADD. Both repairs are then re-run
//! through the UNCHANGED [`validator`] exactly once, so grounding, the
//! `accepted_without_quote = 0` invariant, and the no-fuzzy-accept rule are
//! untouched — a repair can only land as a verbatim-grounded accepted unit or be
//! dropped.
//!
//! Why repairs are SOUND despite faithfulness being un-gateable deterministically
//! (a `text` is a paraphrase, not a substring of its quote — 97% of real v5 units
//! are not literal substrings, so there is no deterministic text⊆quote gate):
//!
//! - TRIM sets `text` to a verbatim substring of the unit's OWN already-grounded
//!   `quote` (checked with [`deterministic_contains`]). The result asserts only
//!   what the quote says ⇒ faithful by construction. Over-flagging costs at worst
//!   readability, never correctness; `quote`/location are never touched, so
//!   coverage is unaffected by a trim.
//! - ADD's quote is re-grounded by the validator; a non-verbatim proposal is
//!   rejected, never accepted.
//!
//! Conservative floor: no defects and no gaps ⇒ the merged raw set equals the
//! base raw set ⇒ the extraction is byte-identical to frozen v5. The protocol
//! can never score below the committed baseline.

use ovp_llm::{CallError, ModelClient, ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::source_doc::SourceDoc;

use super::source_map::{annotate_rendered, rendered_view};
use super::validator::deterministic_contains;
use super::Unit;

const CRITIC_TEMPLATE: &str = include_str!("../../prompts/unit_critic.md");
/// Cassette namespace for the critic call. Separate from `unit_extract/*` so the
/// frozen v5 base replays from its own cassette and the critic records into its.
pub const CRITIC_PROMPT_ID: &str = "unit_critic/v1";
const DEFAULT_MODEL: &str = "claude-sonnet-4-6";
/// Default budget; the live client raises it via `OVP_LLM_MAX_TOKENS` (thinking
/// models need headroom to emit text after their reasoning blocks).
const DEFAULT_MAX_TOKENS: u32 = 8192;

/// The critic's reply: defects to trim + central points to add. Every field is
/// `#[serde(default)]` so a partial reply degrades to "fewer repairs", never an
/// error (a malformed critic reply ⇒ no repairs ⇒ frozen-v5 floor).
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct CriticReply {
    #[serde(default)]
    pub faithfulness_defects: Vec<FaithDefect>,
    #[serde(default)]
    pub coverage_gaps: Vec<CoverageGap>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct FaithDefect {
    #[serde(default)]
    pub unit_id: String,
    #[serde(default)]
    pub unsupported_claim: String,
    /// A proposed replacement `text`; honoured ONLY if it is a verbatim substring
    /// of the unit's own quote, else the whole quote is used.
    #[serde(default)]
    pub suggested_text: String,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct CoverageGap {
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub evidence_ref: String,
    #[serde(default)]
    pub evidence_quote: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub subtype: Option<String>,
}

/// One applied repair, for the inspectable pack. No status here — status is the
/// validator's job after re-validation.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RepairAction {
    /// `trim` | `add`.
    pub action: String,
    /// The base unit id a trim hit, or the critic `label` an add came from.
    pub target: String,
    pub detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub before: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub after: Option<String>,
}

/// What the repair pass did. `adds_proposed` is pre-validation; how many ADDs
/// actually became accepted is read from the re-validated report.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct RepairLog {
    pub trims: usize,
    pub trims_to_full_quote: usize,
    pub adds_proposed: usize,
    pub defects_unmatched: usize,
    pub actions: Vec<RepairAction>,
}

/// Build the (system, user) critic prompt: the rendered span view (same one the
/// extractor saw) followed by the base units as an audit checklist.
pub fn build_critic_prompt(source: &SourceDoc, base_units: &[Unit]) -> (String, String) {
    let marker = "## Source spans";
    let (system, _) = CRITIC_TEMPLATE.split_once(marker).unwrap_or((CRITIC_TEMPLATE, ""));
    let view = annotate_rendered(&rendered_view(&source.body_markdown));
    let mut user = format!("{marker}\n\n{view}\n## Existing units (id | quote | text)\n\n");
    for u in base_units {
        user.push_str(&format!(
            "{} | quote=\"{}\" | text=\"{}\"\n",
            u.id, u.evidence.quote, u.text
        ));
    }
    (system.trim_end().to_string(), user)
}

/// Build the provider-neutral critic request under the `unit_critic/v1` cassette
/// namespace.
pub fn critic_model_request(source: &SourceDoc, base_units: &[Unit]) -> ModelRequest {
    let (system, user) = build_critic_prompt(source, base_units);
    ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: DEFAULT_MAX_TOKENS,
        temperature: None,
        tools: None,
        cache_namespace: Some(CRITIC_PROMPT_ID.to_string()),
    }
}

/// Parse the critic reply tolerantly: strip a code fence, else extract the first
/// balanced `{...}` object. A reply we cannot parse yields an empty `CriticReply`
/// (⇒ no repairs ⇒ frozen-v5 floor), never an error.
pub fn parse_critic_reply(reply_text: &str) -> CriticReply {
    if let Some(obj) = extract_json_object(reply_text)
        && let Ok(r) = serde_json::from_str::<CriticReply>(&obj) {
            return r;
        }
    CriticReply::default()
}

fn extract_json_object(text: &str) -> Option<String> {
    let t = text.trim();
    let t = t.strip_prefix("```json").or_else(|| t.strip_prefix("```")).unwrap_or(t);
    let t = t.trim_start_matches('\n').trim_end_matches("```").trim();
    // Fast path: the whole thing is the object.
    if t.starts_with('{') && serde_json::from_str::<Value>(t).is_ok() {
        return Some(t.to_string());
    }
    // Else find the first '{' and its matching '}' (string-aware).
    let bytes = t.as_bytes();
    let start = bytes.iter().position(|&b| b == b'{')?;
    let mut depth = 0i32;
    let mut in_str = false;
    let mut esc = false;
    for (i, &b) in bytes.iter().enumerate().skip(start) {
        if in_str {
            if esc {
                esc = false;
            } else if b == b'\\' {
                esc = true;
            } else if b == b'"' {
                in_str = false;
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

/// Apply bounded repairs to the base RAW unit values, returning the merged raw
/// values to re-validate plus a log. Pure + deterministic.
///
/// `base_raw[i]` is index-aligned with `base_units[i]` (the validator preserves
/// order). A TRIM rewrites only `base_raw[i]["text"]`; an ADD appends a new raw
/// unit. Grounding is NOT checked here — the caller re-runs [`validator::validate`]
/// on the result, which is the single source of accept truth.
pub fn apply_repairs(
    base_raw: &[Value],
    base_units: &[Unit],
    reply: &CriticReply,
) -> (Vec<Value>, RepairLog) {
    let mut merged: Vec<Value> = base_raw.to_vec();
    let mut log = RepairLog::default();

    // TRIM: text := a verbatim substring of the unit's own quote (faithful by
    // construction). Fall back to the whole quote when the suggestion is not a
    // substring. Match a defect's unit_id to a base unit by id or id-prefix.
    for d in &reply.faithfulness_defects {
        let idx = base_units.iter().position(|u| id_matches(&u.id, &d.unit_id));
        let Some(idx) = idx else {
            log.defects_unmatched += 1;
            continue;
        };
        let quote = base_units[idx].evidence.quote.clone();
        if quote.trim().is_empty() {
            continue;
        }
        let suggestion = d.suggested_text.trim();
        let (new_text, full) = if !suggestion.is_empty() && deterministic_contains(&quote, suggestion) {
            (suggestion.to_string(), false)
        } else {
            (quote.clone(), true)
        };
        let before = merged[idx].get("text").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if before == new_text {
            continue; // no-op: already faithful to this text
        }
        if let Some(obj) = merged[idx].as_object_mut() {
            obj.insert("text".into(), json!(new_text));
        }
        log.trims += 1;
        if full {
            log.trims_to_full_quote += 1;
        }
        log.actions.push(RepairAction {
            action: "trim".into(),
            target: base_units[idx].id.clone(),
            detail: d.unsupported_claim.clone(),
            before: Some(before),
            after: Some(new_text),
        });
    }

    // ADD: append a grounded candidate per coverage gap. The validator decides if
    // it is accepted (verbatim quote in the ref span) or dropped (non-verbatim).
    for g in &reply.coverage_gaps {
        if g.evidence_quote.trim().is_empty() || g.evidence_ref.trim().is_empty() {
            continue;
        }
        // text must not assert beyond its own quote → substring or fall back.
        let text = if !g.text.trim().is_empty() && deterministic_contains(&g.evidence_quote, g.text.trim())
        {
            g.text.trim().to_string()
        } else {
            g.evidence_quote.trim().to_string()
        };
        let subtype = g.subtype.as_deref().filter(|s| !s.trim().is_empty() && *s != "null");
        merged.push(json!({
            "kind": "assertion",
            "subtype": subtype,
            "text": text,
            "evidence_ref": g.evidence_ref.trim(),
            "evidence_quote": g.evidence_quote.trim(),
            "attribution": "author",
            "modality": "asserted",
            "arguments": [],
        }));
        log.adds_proposed += 1;
        log.actions.push(RepairAction {
            action: "add".into(),
            target: g.label.clone(),
            detail: format!("ref {} :: {}", g.evidence_ref.trim(), g.label),
            before: None,
            after: Some(text),
        });
    }

    (merged, log)
}

/// True if `full_id` (e.g. `u-001-684a5c1e`) matches the critic's possibly
/// truncated `referenced` id (`u-001-684a5c` or `u-001`).
fn id_matches(full_id: &str, referenced: &str) -> bool {
    let r = referenced.trim();
    !r.is_empty() && (full_id == r || full_id.starts_with(r))
}

/// Run the critic call (replay or live) over `source` + the base accepted units.
/// Returns the parsed reply and the raw model text (for the pack).
pub fn run_unit_critique(
    source: &SourceDoc,
    base_units: &[Unit],
    client: &mut dyn ModelClient,
) -> Result<(CriticReply, String), CallError> {
    let request = critic_model_request(source, base_units);
    let reply = client.call(&request)?;
    Ok((parse_critic_reply(&reply.text), reply.text))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::units::{validate, UnitStatus};

    const BODY: &str = "# Why the chunk is a bad unit\n\nA chunk is a structurally neutral container. It knows nothing about ownership.\n\nBenchmark maxxing is for augmenting experts.";

    fn src() -> SourceDoc {
        SourceDoc::article("T", "https://e/x", None, None, vec![], BODY)
    }

    fn base() -> (Vec<Value>, Vec<Unit>) {
        // One faithful unit + one P0-shaped unit (text asserts beyond its quote).
        let raw = vec![
            json!({"kind":"assertion","text":"A chunk is structurally neutral.",
                   "evidence_ref":"p002.s001",
                   "evidence_quote":"A chunk is a structurally neutral container.",
                   "attribution":"author","modality":"asserted","arguments":[]}),
            json!({"kind":"assertion","subtype":"definition",
                   "text":"Benchmark maxxing is optimizing for performance metrics and test scores.",
                   "evidence_ref":"p003.s001",
                   "evidence_quote":"Benchmark maxxing is for augmenting experts.",
                   "attribution":"author","modality":"asserted","arguments":[]}),
        ];
        let ex = validate(&raw, &src());
        (raw, ex.units)
    }

    #[test]
    fn no_defects_no_gaps_is_byte_identical_floor() {
        let (raw, units) = base();
        let (merged, log) = apply_repairs(&raw, &units, &CriticReply::default());
        assert_eq!(merged, raw, "conservative floor: empty critic ⇒ unchanged raw");
        assert_eq!(log, RepairLog::default());
        // And re-validation equals the base extraction.
        assert_eq!(validate(&merged, &src()), validate(&raw, &src()));
    }

    #[test]
    fn trim_replaces_text_with_substring_of_quote() {
        let (raw, units) = base();
        let p0_id = units[1].id.clone();
        let reply = CriticReply {
            faithfulness_defects: vec![FaithDefect {
                unit_id: p0_id.clone(),
                unsupported_claim: "adds 'optimizing for performance metrics and test scores'".into(),
                suggested_text: "Benchmark maxxing is for augmenting experts.".into(),
            }],
            coverage_gaps: vec![],
        };
        let (merged, log) = apply_repairs(&raw, &units, &reply);
        assert_eq!(log.trims, 1);
        assert_eq!(log.trims_to_full_quote, 0, "the suggestion was a valid substring");
        let ex = validate(&merged, &src());
        // The repaired unit is still accepted (quote/location unchanged) and now
        // its text is faithful (a substring of the grounded quote).
        let u = ex.units.iter().find(|u| u.id == p0_id).unwrap();
        assert_eq!(u.status, UnitStatus::Accepted);
        assert_eq!(u.text, "Benchmark maxxing is for augmenting experts.");
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn trim_falls_back_to_full_quote_when_suggestion_not_a_substring() {
        let (raw, units) = base();
        let reply = CriticReply {
            faithfulness_defects: vec![FaithDefect {
                unit_id: units[1].id.clone(),
                unsupported_claim: "x".into(),
                suggested_text: "a fabricated suggestion not in the quote".into(),
            }],
            coverage_gaps: vec![],
        };
        let (merged, log) = apply_repairs(&raw, &units, &reply);
        assert_eq!(log.trims, 1);
        assert_eq!(log.trims_to_full_quote, 1, "bad suggestion ⇒ fall back to whole quote");
        assert_eq!(merged[1]["text"], json!("Benchmark maxxing is for augmenting experts."));
    }

    #[test]
    fn add_with_verbatim_quote_becomes_accepted() {
        let (raw, units) = base();
        let reply = CriticReply {
            faithfulness_defects: vec![],
            coverage_gaps: vec![CoverageGap {
                label: "chunk ignores ownership".into(),
                evidence_ref: "p002.s002".into(),
                evidence_quote: "It knows nothing about ownership.".into(),
                text: "It knows nothing about ownership.".into(),
                subtype: None,
            }],
        };
        let (merged, log) = apply_repairs(&raw, &units, &reply);
        assert_eq!(log.adds_proposed, 1);
        assert_eq!(merged.len(), 3);
        let ex = validate(&merged, &src());
        assert!(ex.accepted().any(|u| u.evidence.quote == "It knows nothing about ownership."));
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn add_with_fabricated_quote_is_dropped_by_validator() {
        let (raw, units) = base();
        let reply = CriticReply {
            faithfulness_defects: vec![],
            coverage_gaps: vec![CoverageGap {
                label: "made up".into(),
                evidence_ref: "p002.s001".into(),
                evidence_quote: "this sentence is not anywhere in the source".into(),
                text: "this sentence is not anywhere in the source".into(),
                subtype: None,
            }],
        };
        let (merged, _log) = apply_repairs(&raw, &units, &reply);
        let ex = validate(&merged, &src());
        // The fabricated add exists as a unit but is NOT accepted (no verbatim
        // quote) — no fuzzy accept, invariant intact.
        assert!(!ex.accepted().any(|u| u.evidence.quote.contains("not anywhere")));
        assert_eq!(ex.report.accepted_without_quote, 0);
    }

    #[test]
    fn unmatched_defect_is_counted_not_applied() {
        let (raw, units) = base();
        let reply = CriticReply {
            faithfulness_defects: vec![FaithDefect {
                unit_id: "u-999-deadbeef".into(),
                unsupported_claim: "x".into(),
                suggested_text: "y".into(),
            }],
            coverage_gaps: vec![],
        };
        let (merged, log) = apply_repairs(&raw, &units, &reply);
        assert_eq!(log.defects_unmatched, 1);
        assert_eq!(merged, raw, "an unmatched defect changes nothing");
    }

    #[test]
    fn parse_critic_reply_handles_fence_and_prose() {
        let r = parse_critic_reply("```json\n{\"faithfulness_defects\":[],\"coverage_gaps\":[]}\n```");
        assert_eq!(r, CriticReply::default());
        let r2 = parse_critic_reply(
            "Here is my audit:\n{\"coverage_gaps\":[{\"label\":\"a\",\"evidence_ref\":\"p1\",\"evidence_quote\":\"q\"}]}\nDone.",
        );
        assert_eq!(r2.coverage_gaps.len(), 1);
        assert_eq!(r2.coverage_gaps[0].evidence_ref, "p1");
    }

    #[test]
    fn unparseable_reply_yields_empty_floor() {
        assert_eq!(parse_critic_reply("the model refused"), CriticReply::default());
    }

    #[test]
    fn id_prefix_matches_truncated_reference() {
        assert!(id_matches("u-001-684a5c1e", "u-001-684a5c"));
        assert!(id_matches("u-001-684a5c1e", "u-001-684a5c1e"));
        assert!(!id_matches("u-001-684a5c1e", "u-002"));
        assert!(!id_matches("u-001-684a5c1e", ""));
    }

    #[test]
    fn prompt_carries_span_view_and_units() {
        let (_raw, units) = base();
        let (system, user) = build_critic_prompt(&src(), &units);
        assert!(system.contains("Faithfulness defects"));
        assert!(system.contains("Coverage gaps"));
        assert!(user.contains("[p002.s001] A chunk is a structurally neutral container."));
        assert!(user.contains(&units[0].id), "base unit ids listed for audit");
    }
}
