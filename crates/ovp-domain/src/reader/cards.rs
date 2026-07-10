//! Rust card synthesis — the trunk-native port of the frozen `card_synth/v3`
//! prompt (M16.1). The LLM proposes cards; a deterministic post-check keeps only
//! cards that cite ≥1 real accepted Unit (truth-layer linkage), dropping the rest.
//! Mirrors `units::critic` structure (own prompt asset + cassette namespace).

use ovp_llm::{CallError, ModelClient, ModelMessage, ModelRequest};
use serde::{Deserialize, Deserializer, Serialize};

use crate::units::Unit;

use super::Card;

const CARD_TEMPLATE: &str = include_str!("../../prompts/card_synthesis.md");
/// Cassette namespace + version marker. v3 = modality-preserving (M16.1).
pub const CARD_PROMPT_ID: &str = "card_synth/v3";
const DEFAULT_MODEL: &str = "claude-sonnet-4-6";
/// The live client raises this via `OVP_LLM_MAX_TOKENS` for thinking headroom.
const DEFAULT_MAX_TOKENS: u32 = 8192;

/// The model's per-card shape before validation.
#[derive(Debug, Clone, Deserialize)]
pub struct RawCard {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub unit_type: Option<String>,
    #[serde(default, deserialize_with = "null_to_default")]
    pub cited_unit_ids: Vec<String>,
}

fn null_to_default<'de, D, T>(de: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(de)?.unwrap_or_default())
}

/// Fact-level metrics over one synthesis. No quality scoring — just the citation
/// invariant (a card with no real cited Unit is dropped, not rendered).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct CardReport {
    pub cards_returned: usize,
    pub cards_kept: usize,
    pub cards_dropped_uncited: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parse_error: Option<String>,
}

/// One end-to-end synthesis run: kept cards + report + the raw model reply.
#[derive(Debug, Clone, PartialEq)]
pub struct CardSynthesisRun {
    pub cards: Vec<Card>,
    pub report: CardReport,
    pub raw_reply: String,
    /// `Some` if the card JSON had to be salvaged (M19). Surfaced into run-status.
    pub json_repair: Option<crate::model_reply::RepairNote>,
}

/// Build the (system, user) card prompt: the frozen v3 instructions + the accepted
/// Units rendered as `id | kind/subtype | text | quote` lines.
pub fn build_card_prompt(accepted_units: &[Unit]) -> (String, String) {
    let marker = "## Accepted Units";
    let (system, _) = CARD_TEMPLATE.split_once(marker).unwrap_or((CARD_TEMPLATE, ""));
    let mut user = format!("{marker}\n\n");
    for u in accepted_units {
        let st = u.subtype.as_deref().unwrap_or("-");
        user.push_str(&format!(
            "{} | {:?}/{} | text=\"{}\" | quote=\"{}\"\n",
            u.id, u.kind, st, u.text, u.evidence.quote
        ));
    }
    (system.trim_end().to_string(), user)
}

pub fn card_model_request(accepted_units: &[Unit]) -> ModelRequest {
    let (system, user) = build_card_prompt(accepted_units);
    ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: DEFAULT_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(CARD_PROMPT_ID.to_string()),
    }
}

/// Parse the `{ "cards": [...] }` envelope tolerantly (M19): strip fence /
/// surrounding prose + parser-local backslash recovery via the shared
/// [`crate::model_reply`] util. Returns `Err(detail)` if no card array is found.
pub fn parse_cards(reply_text: &str) -> Result<Vec<RawCard>, String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    cards_from_value(&value)
}

/// Pull `RawCard`s out of an already-parsed `{ "cards": [...] }` envelope. One
/// malformed card is skipped (not fatal); a missing array is an error.
fn cards_from_value(value: &serde_json::Value) -> Result<Vec<RawCard>, String> {
    let arr = value.get("cards").and_then(|c| c.as_array()).ok_or("missing `cards` array")?;
    let mut out = Vec::with_capacity(arr.len());
    for item in arr {
        if let Ok(rc) = serde_json::from_value::<RawCard>(item.clone()) {
            out.push(rc);
        }
    }
    Ok(out)
}

/// Keep only cards that cite ≥1 real accepted Unit id (exact or unique `u-NNN`
/// prefix, tolerating model truncation). Cards citing none are dropped + counted.
pub fn validate_cards(raw: &[RawCard], accepted_units: &[Unit]) -> (Vec<Card>, CardReport) {
    let mut kept: Vec<Card> = Vec::new();
    let mut dropped = 0usize;
    for rc in raw {
        let mut cites: Vec<String> = Vec::new();
        for cid in &rc.cited_unit_ids {
            if let Some(u) = resolve_unit(accepted_units, cid)
                && !cites.contains(&u.id) {
                    cites.push(u.id.clone());
                }
        }
        if cites.is_empty() || rc.content.trim().is_empty() {
            dropped += 1;
            continue;
        }
        kept.push(Card {
            title: rc.title.trim().to_string(),
            content: rc.content.trim().to_string(),
            unit_type: rc.unit_type.clone().filter(|s| !s.trim().is_empty()),
            cited_unit_ids: cites,
        });
    }
    let report = CardReport {
        cards_returned: raw.len(),
        cards_kept: kept.len(),
        cards_dropped_uncited: dropped,
        parse_error: None,
    };
    (kept, report)
}

/// Resolve a (possibly truncated) cited id to an accepted unit: exact, or the
/// UNIQUE unit whose id starts with `cid-`. Ambiguous → none.
fn resolve_unit<'a>(units: &'a [Unit], cid: &str) -> Option<&'a Unit> {
    let cid = cid.trim();
    if cid.is_empty() {
        return None;
    }
    if let Some(u) = units.iter().find(|u| u.id == cid) {
        return Some(u);
    }
    let pfx = format!("{cid}-");
    let mut it = units.iter().filter(|u| u.id.starts_with(&pfx));
    let first = it.next()?;
    if it.next().is_some() {
        return None;
    }
    Some(first)
}

/// Full run: synthesize cards from the accepted Units (replay or live), validate.
/// M19: on a JSON defect, attempt parser-local recovery, then ONE bounded model
/// JSON-repair call (`client`). The salvaged reply still goes through the SAME
/// `cards_from_value` + `validate_cards`, so repair cannot bypass the citation
/// invariant. A reply that still does not parse yields `Ok` with
/// `report.parse_error` set + 0 cards (fail-loud upstream).
pub fn run_card_synthesis(
    accepted_units: &[Unit],
    client: &mut dyn ModelClient,
) -> Result<CardSynthesisRun, CallError> {
    use crate::model_reply::{json_repair_request, parse_reply_value, RepairNote};

    let request = card_model_request(accepted_units);
    let reply = client.call(&request)?;

    // 1. Parser-local: parse (with backslash recovery). 2. Bounded model repair.
    let (parsed, json_repair): (Result<Vec<RawCard>, String>, Option<RepairNote>) =
        match parse_reply_value(&reply.text) {
            Ok((value, note)) => (cards_from_value(&value), note.map(|_| RepairNote::parser_local("cards"))),
            Err(defect) => {
                let repaired = client
                    .call(&json_repair_request(&reply.text))
                    .ok()
                    .and_then(|r| parse_reply_value(&r.text).ok())
                    .and_then(|(v, _)| cards_from_value(&v).ok());
                match repaired {
                    Some(raw) => (Ok(raw), Some(RepairNote::model_repair("cards", &defect))),
                    None => (Err(format!("cards: {defect}")), None),
                }
            }
        };

    match parsed {
        Ok(raw) => {
            let (cards, report) = validate_cards(&raw, accepted_units);
            Ok(CardSynthesisRun { cards, report, raw_reply: reply.text, json_repair })
        }
        Err(detail) => Ok(CardSynthesisRun {
            cards: Vec::new(),
            report: CardReport { parse_error: Some(detail), ..Default::default() },
            raw_reply: reply.text,
            json_repair: None,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;
    use ovp_llm::{ModelReply, StopReason, Usage};

    struct Canned(String);
    impl ModelClient for Canned {
        fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
            Ok(ModelReply {
                model: "canned".into(),
                text: self.0.clone(),
                stop_reason: StopReason::EndTurn,
                usage: Usage { input_tokens: 1, output_tokens: 1 },
            })
        }
    }

    fn units() -> Vec<Unit> {
        let raw = vec![
            serde_json::json!({"kind":"assertion","text":"IdeaBlocks replace prose chunks.",
              "evidence_ref":"p001.s001","evidence_quote":"IdeaBlocks replace prose chunks.",
              "attribution":"author","modality":"asserted","arguments":[]}),
            serde_json::json!({"kind":"assertion","text":"It knows nothing about ownership.",
              "evidence_ref":"p001.s002","evidence_quote":"It knows nothing about ownership.",
              "attribution":"author","modality":"asserted","arguments":[]}),
        ];
        validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![],
            "IdeaBlocks replace prose chunks. It knows nothing about ownership.")).units
    }

    #[test]
    fn synthesizes_and_keeps_cited_cards() {
        let u = units();
        let reply = format!(
            r#"{{"cards":[{{"title":"IdeaBlocks can replace prose chunks","content":"IdeaBlocks replace prose chunks for retrieval.","unit_type":"definition","cited_unit_ids":["{}"]}}]}}"#,
            u[0].id
        );
        let run = run_card_synthesis(&u, &mut Canned(reply)).unwrap();
        assert_eq!(run.report.cards_kept, 1);
        assert_eq!(run.cards[0].cited_unit_ids, vec![u[0].id.clone()]);
        assert!(run.report.parse_error.is_none());
    }

    #[test]
    fn drops_uncited_card() {
        let u = units();
        let reply = r#"{"cards":[{"title":"floating claim","content":"no citation here","cited_unit_ids":["u-999-deadbeef"]}]}"#;
        let run = run_card_synthesis(&u, &mut Canned(reply.into())).unwrap();
        assert_eq!(run.report.cards_kept, 0);
        assert_eq!(run.report.cards_dropped_uncited, 1);
    }

    #[test]
    fn truncated_citation_resolves_by_prefix() {
        let u = units();
        // cite "u-000" (truncated) → resolves to u-000-<hash>.
        let reply = r#"{"cards":[{"title":"t","content":"body","cited_unit_ids":["u-000"]}]}"#;
        let run = run_card_synthesis(&u, &mut Canned(reply.into())).unwrap();
        assert_eq!(run.report.cards_kept, 1);
        assert!(run.cards[0].cited_unit_ids[0].starts_with("u-000-"));
    }

    #[test]
    fn bad_reply_yields_parse_error_not_panic() {
        let run = run_card_synthesis(&units(), &mut Canned("the model refused".into())).unwrap();
        assert!(run.report.parse_error.is_some());
        assert!(run.cards.is_empty());
        assert!(run.json_repair.is_none());
    }

    /// Returns each scripted reply in turn, then repeats the last.
    struct Scripted {
        replies: Vec<String>,
        i: usize,
    }
    impl ModelClient for Scripted {
        fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
            let text = self.replies.get(self.i).or_else(|| self.replies.last()).cloned().unwrap_or_default();
            self.i += 1;
            Ok(ModelReply { model: "scripted".into(), text, stop_reason: StopReason::EndTurn,
                usage: Usage { input_tokens: 1, output_tokens: 1 } })
        }
    }

    #[test]
    fn card_json_recovered_by_bounded_model_repair() {
        // m18-06 class: first reply has a dropped opening quote on an id; the
        // bounded repair call returns valid JSON → cards kept, repair recorded.
        let u = units();
        let broken = r#"{"cards":[{"title":"t","content":"body", "cited_unit_ids":[ u-000-x"]}]}"#;
        let fixed = format!(
            r#"{{"cards":[{{"title":"t","content":"body","cited_unit_ids":["{}"]}}]}}"#, u[0].id);
        let run = run_card_synthesis(&u, &mut Scripted { replies: vec![broken.into(), fixed], i: 0 }).unwrap();
        assert_eq!(run.report.cards_kept, 1);
        assert!(run.report.parse_error.is_none());
        let n = run.json_repair.expect("repair note");
        assert_eq!(n.stage, "cards");
        assert!(n.method.starts_with("model-repair"));
    }

    #[test]
    fn unescaped_backslash_in_cards_recovered_parser_local() {
        let u = units();
        let reply = format!(
            r#"{{"cards":[{{"title":"path C:\Users","content":"body","cited_unit_ids":["{}"]}}]}}"#, u[0].id);
        let run = run_card_synthesis(&u, &mut Canned(reply)).unwrap();
        assert_eq!(run.report.cards_kept, 1);
        assert_eq!(run.json_repair.unwrap().method, "parser-local: unescaped-backslash");
    }

    #[test]
    fn repaired_cards_still_pass_citation_validator() {
        // Repair returns valid JSON but the card cites a non-existent unit →
        // dropped. Repair cannot bypass the citation invariant.
        let u = units();
        let broken = r#"{"cards":[ bad ]}"#;
        let ungrounded = r#"{"cards":[{"title":"t","content":"floating","cited_unit_ids":["u-999-nope"]}]}"#;
        let run = run_card_synthesis(&u, &mut Scripted { replies: vec![broken.into(), ungrounded.into()], i: 0 }).unwrap();
        assert_eq!(run.report.cards_kept, 0, "ungrounded card dropped even after repair");
        assert_eq!(run.report.cards_dropped_uncited, 1);
    }

    #[test]
    fn takeaway_splits_first_sentence() {
        let c = Card { title: "t".into(), content: "First takeaway. Then the body detail.".into(),
            unit_type: None, cited_unit_ids: vec!["u".into()] };
        let (t, b) = c.takeaway_and_body();
        assert_eq!(t, "First takeaway.");
        assert_eq!(b, "Then the body detail.");
    }

    #[test]
    fn prompt_carries_frozen_v3_policy_and_units() {
        let u = units();
        let (system, user) = build_card_prompt(&u);
        assert!(system.contains("MODALITY FIDELITY"));
        assert!(system.contains("card_synth/v3"));
        assert!(user.contains(&u[0].id));
    }
}
