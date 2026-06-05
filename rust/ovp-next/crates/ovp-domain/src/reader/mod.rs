//! M17 — Grounded Reader Trunk: the VIEW layer.
//!
//! `reader::cards` compiles ACCEPTED Units (the truth layer, from M14a.8) into
//! readable **cards** via the frozen `card_synth/v3` prompt. `reader::pack` renders
//! the human-usable **reader pack** (a collapsible HTML workbench + a flat Markdown
//! view) where provenance (card → unit → verbatim quote/span) is collapsed VISUALLY
//! but never removed from the artifacts.
//!
//! Hard line: this is the VIEW layer only. It does NOT canonicalize, NOT mint
//! concepts, NOT use Referent/Resolver. Truth (Units) and view (Cards) stay
//! separate — a Card never invents a fact a cited Unit does not carry.

use serde::{Deserialize, Serialize};

pub mod cards;
pub mod pack;

pub use cards::{
    build_card_prompt, card_model_request, parse_cards, run_card_synthesis, validate_cards,
    CardReport, CardSynthesisRun, RawCard, CARD_PROMPT_ID,
};
pub use pack::{write_reader_pack, GroundingStatus, ReaderPack};

/// A reader card (view layer). `cited_unit_ids` are the truth-layer Units this card
/// is compiled from; each resolves (downstream) to a verbatim quote + source span.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Card {
    pub title: String,
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unit_type: Option<String>,
    #[serde(default)]
    pub cited_unit_ids: Vec<String>,
}

impl Card {
    /// Split `content` into a one-sentence takeaway + the remaining body, for the
    /// title/takeaway/body reader layout. Deterministic, render-only.
    pub fn takeaway_and_body(&self) -> (String, String) {
        let c = self.content.trim();
        // First sentence terminator (. ! ? 。！？) followed by space/end.
        let bytes = c.as_bytes();
        let mut end = None;
        for (i, ch) in c.char_indices() {
            if matches!(ch, '.' | '!' | '?' | '。' | '！' | '？') {
                let after = i + ch.len_utf8();
                if after >= c.len() || c[after..].starts_with(char::is_whitespace) {
                    end = Some(after);
                    break;
                }
            }
            let _ = bytes;
        }
        match end {
            Some(e) if e < c.len() => (c[..e].trim().to_string(), c[e..].trim().to_string()),
            _ => (c.to_string(), String::new()),
        }
    }
}
