//! The end-to-end reader-trunk pipeline for ONE source:
//!   Source → Grounded Units (v5) → Critic Repair (v1) → Reader Cards (card_synth/v3)
//!   → Reader Pack (collapsible HTML + flat MD + provenance artifacts).
//!
//! Extracted from the `read-source` CLI command (M30) so the daily loop and the
//! single-shot command share ONE fail-loud path: the truth-layer gate, the audit
//! artifact writes, and the card-layer checks cannot drift between callers.
//! Behavior is the M17–M20 validated sequence, unchanged.

use std::path::Path;

use ovp_llm::ModelClient;

use crate::model_reply::RepairNote;
use crate::source_doc::SourceDoc;
use crate::units::{run_unit_extraction_repaired, Unit};

use super::cards::run_card_synthesis;
use super::pack::{write_reader_pack, GroundingStatus, ReaderPack};

/// Why a reader-pipeline run could not produce a pack.
#[derive(Debug, Clone, PartialEq)]
pub enum ReaderPipelineError {
    /// Model transport / cache-miss failure on any stage. Nothing was written.
    Client(String),
    /// The truth layer is unusable (parse failure / 0 units / grounding
    /// violated). The model-reply audit artifacts ARE written to `out_dir`.
    TruthLayer(String),
    /// The pack itself could not be written.
    Io(String),
}

impl std::fmt::Display for ReaderPipelineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReaderPipelineError::Client(s) => write!(f, "{s}"),
            ReaderPipelineError::TruthLayer(s) => write!(f, "truth-layer error: {s}"),
            ReaderPipelineError::Io(s) => write!(f, "{s}"),
        }
    }
}

/// A completed run. `card_failure` is the card-layer fail-loud condition (the
/// pack and audit artifacts are already on disk): the caller decides whether
/// that is a hard error (`read-source`) or a failed ledger record (`daily`).
#[derive(Debug)]
pub struct ReaderPipelineRun {
    pub pack: ReaderPack,
    /// M19 JSON salvage notes across unit base + card synthesis.
    pub json_repairs: Vec<RepairNote>,
    /// `Some(reason)` when card synthesis did not parse or produced 0 cards
    /// from a non-empty accepted-unit set.
    pub card_failure: Option<String>,
}

/// Run the full reader trunk for one source and write the pack to `out_dir`.
/// `base_client` drives unit extraction, `critic_client` the independent
/// critic, `card_client` card synthesis (callers may pass three handles onto
/// the same cassette root — requests are key-disambiguated).
pub fn run_reader_pipeline(
    source: &SourceDoc,
    base_client: &mut dyn ModelClient,
    critic_client: &mut dyn ModelClient,
    card_client: &mut dyn ModelClient,
    out_dir: &Path,
) -> Result<ReaderPipelineRun, ReaderPipelineError> {
    // 1. Grounded Units (v5) + 2. Critic Repair (v1).
    let run = run_unit_extraction_repaired(source, base_client, critic_client)
        .map_err(|e| ReaderPipelineError::Client(format!("grounded extraction/repair failed: {e}")))?;
    let ex = &run.extraction;

    // Fail loud on truth-layer errors BEFORE spending a card-synthesis call.
    if let Some(reason) = truth_layer_failure(
        ex.report.parse_error.as_deref(),
        ex.report.total,
        ex.report.accepted_without_quote,
    ) {
        write_audit(out_dir, &run.base_reply, &run.critic_reply, "");
        return Err(ReaderPipelineError::TruthLayer(reason));
    }

    let accepted: Vec<Unit> = ex.accepted().cloned().collect();

    // 3. Reader Cards (frozen card_synth/v3).
    let synth = run_card_synthesis(&accepted, card_client)
        .map_err(|e| ReaderPipelineError::Client(format!("card synthesis call failed: {e}")))?;

    // 4. Reader Pack. M19: collect JSON salvage notes (unit base + card
    //    synthesis) so a repaired pack is auditable in run-status.json.
    let mut json_repairs = run.json_repair.clone();
    json_repairs.extend(synth.json_repair.clone());
    let grounding = GroundingStatus {
        accepted_without_quote: ex.report.accepted_without_quote,
        needs_review: ex.report.needs_review,
        quote_not_found: ex.report.quote_not_found,
        parse_error: ex.report.parse_error.clone(),
        json_repairs: json_repairs.clone(),
    };

    let pack = write_reader_pack(
        out_dir,
        &source.title,
        &accepted,
        &synth.cards,
        &synth.report,
        Some(&run.repair_log),
        &grounding,
    )
    .map_err(|e| ReaderPipelineError::Io(format!("writing reader pack: {e}")))?;
    write_audit(out_dir, &run.base_reply, &run.critic_reply, &synth.raw_reply);
    write_units(out_dir, &accepted);

    // Card-layer fail-loud: units extracted but no usable cards.
    let card_failure = if let Some(e) = &synth.report.parse_error {
        Some(format!("card synthesis did not parse: {e} (pack written)"))
    } else if !accepted.is_empty() && synth.cards.is_empty() {
        Some("0 reader cards produced from accepted units (pack written)".to_string())
    } else {
        None
    };

    Ok(ReaderPipelineRun { pack, json_repairs, card_failure })
}

/// `Some(reason)` if the truth layer is unusable (mirrors `extraction_failure`).
fn truth_layer_failure(
    parse_error: Option<&str>,
    total: usize,
    accepted_without_quote: usize,
) -> Option<String> {
    if let Some(e) = parse_error {
        return Some(format!("unit extraction did not parse: {e}"));
    }
    if total == 0 {
        return Some("0 units extracted".into());
    }
    if accepted_without_quote > 0 {
        return Some(format!(
            "{accepted_without_quote} accepted unit(s) without a located quote — grounding violated"
        ));
    }
    None
}

fn write_audit(out: &Path, base: &str, critic: &str, cards: &str) {
    let _ = std::fs::create_dir_all(out);
    let _ = std::fs::write(out.join("model-reply.units.txt"), base);
    let _ = std::fs::write(out.join("model-reply.critic.txt"), critic);
    if !cards.is_empty() {
        let _ = std::fs::write(out.join("model-reply.cards.txt"), cards);
    }
}

fn write_units(out: &Path, accepted: &[Unit]) {
    if let Ok(s) = serde_json::to_string_pretty(accepted) {
        let _ = std::fs::write(out.join("units.accepted.json"), format!("{s}\n"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_llm::{CallError, ModelReply, ModelRequest, StopReason, Usage};

    #[test]
    fn truth_layer_failure_cases() {
        assert!(truth_layer_failure(Some("bad json"), 0, 0).is_some());
        assert!(truth_layer_failure(None, 0, 0).is_some());
        assert!(truth_layer_failure(None, 5, 1).is_some());
        assert!(truth_layer_failure(None, 5, 0).is_none());
    }

    /// Returns a fixed reply for any request — stands in for one model stage.
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

    const BODY: &str = "A chunk is a structurally neutral container. It knows nothing about ownership.";

    fn source() -> SourceDoc {
        SourceDoc::article("T", "https://e/x", None, None, vec![], BODY)
    }

    fn units_reply() -> String {
        r#"{"units":[{"kind":"assertion","text":"A chunk is structurally neutral.",
            "evidence_ref":"p001","evidence_quote":"A chunk is a structurally neutral container.",
            "attribution":"author","modality":"asserted","arguments":[]}]}"#
            .to_string()
    }

    #[test]
    fn end_to_end_writes_pack_and_units() {
        let src = source();
        // Learn the deterministic accepted-unit id, then cite it from the card.
        let ex = crate::units::extract_units(&units_reply(), &src);
        let unit_id = ex.accepted().next().expect("one accepted unit").id.clone();
        let cards = format!(
            r#"{{"cards":[{{"title":"Chunks are neutral","content":"A chunk is structurally neutral.","unit_type":"definition","cited_unit_ids":["{unit_id}"]}}]}}"#
        );

        let dir = tempfile::tempdir().unwrap();
        let run = run_reader_pipeline(
            &src,
            &mut Canned(units_reply()),
            &mut Canned("{}".into()),
            &mut Canned(cards),
            dir.path(),
        )
        .expect("pipeline runs");
        assert_eq!(run.pack.n_cards, 1);
        assert_eq!(run.pack.n_accepted_units, 1);
        assert!(run.card_failure.is_none());
        for f in ["reader.md", "reader.html", "units.accepted.json", "model-reply.units.txt"] {
            assert!(dir.path().join(f).exists(), "missing {f}");
        }
    }

    #[test]
    fn truth_layer_gate_writes_audit_and_errors() {
        let dir = tempfile::tempdir().unwrap();
        let err = run_reader_pipeline(
            &source(),
            &mut Canned("not json".into()),
            &mut Canned("{}".into()),
            &mut Canned("UNUSED".into()),
            dir.path(),
        )
        .expect_err("truth layer must fail loud");
        assert!(matches!(err, ReaderPipelineError::TruthLayer(_)), "got {err:?}");
        assert!(dir.path().join("model-reply.units.txt").exists());
        assert!(!dir.path().join("reader.md").exists(), "no pack on truth-layer failure");
    }

    #[test]
    fn zero_cards_is_a_card_failure_with_pack_written() {
        let src = source();
        let dir = tempfile::tempdir().unwrap();
        let run = run_reader_pipeline(
            &src,
            &mut Canned(units_reply()),
            &mut Canned("{}".into()),
            // Cites an unknown unit → the card is dropped → 0 cards survive.
            &mut Canned(r#"{"cards":[{"title":"x","content":"y","cited_unit_ids":["u-999-deadbeef"]}]}"#.into()),
            dir.path(),
        )
        .expect("pipeline runs");
        assert!(run.card_failure.is_some());
        assert!(dir.path().join("reader.md").exists(), "pack written even on card failure");
    }
}
