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

use crate::model_reply::{json_repair_request, RepairNote};
use crate::source_doc::SourceDoc;
use crate::units::{critic_model_request, run_unit_extraction_repaired, unit_model_request, Unit};

use super::cards::{card_model_request, run_card_synthesis};
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
        // The replies transported fine but their CONTENT is unusable. Under a
        // recording cache they are already cassetted, and request keys are
        // pure content hashes — every retry (tomorrow's daily, --retry-blocked)
        // would replay the identical failure forever. Forget the whole
        // extraction exchange (base + any JSON-repair keyed on the bad reply +
        // critic) so the next attempt genuinely re-asks the model. No-op for
        // replay/fake clients.
        base_client.invalidate(&unit_model_request(source));
        base_client.invalidate(&json_repair_request(&run.base_reply));
        critic_client.invalidate(&critic_model_request(source, &run.base.units));
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
    if card_failure.is_some() {
        // Same forgetting as the truth-layer gate: callers treat a card
        // failure as a failed source and retry it — a cassetted bad card
        // reply must not pin every retry to the identical failure.
        card_client.invalidate(&card_model_request(&accepted));
        card_client.invalidate(&json_repair_request(&synth.raw_reply));
    }

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
                blocks: None,
                raw_stop_reason: None,
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

    /// The dogfood retry scenario: day 1 the model returns unusable units
    /// JSON, which a Record cache cassettes; day 2's retry must NOT replay the
    /// pinned bad reply — the truth-layer gate invalidates it, so the retry
    /// re-asks the model and succeeds. (Without the invalidation this test
    /// fails: the second run replays "not json" and errors identically.)
    #[test]
    fn truth_layer_failure_invalidates_cassette_so_retry_reasks() {
        use ovp_llm::{CacheMode, CachedModelClient};

        /// Unit-extract requests get scripted replies in order; JSON-repair
        /// requests always fail to repair (stay unparseable).
        struct Scripted {
            unit_replies: Vec<String>,
            calls: usize,
        }
        impl ModelClient for Scripted {
            fn call(&mut self, r: &ModelRequest) -> Result<ModelReply, CallError> {
                let ns = r.cache_namespace.as_deref().unwrap_or("");
                let text = if ns.starts_with("json_repair") {
                    "still not json".to_string()
                } else {
                    let t = self.unit_replies[self.calls.min(self.unit_replies.len() - 1)].clone();
                    self.calls += 1;
                    t
                };
                Ok(ModelReply {
                    model: "scripted".into(),
                    text,
                    stop_reason: StopReason::EndTurn,
                    usage: Usage { input_tokens: 1, output_tokens: 1 },
                    blocks: None,
                    raw_stop_reason: None,
                })
            }
        }

        let src = source();
        let ex = crate::units::extract_units(&units_reply(), &src);
        let unit_id = ex.accepted().next().expect("one accepted unit").id.clone();
        let cards = format!(
            r#"{{"cards":[{{"title":"Chunks are neutral","content":"A chunk is structurally neutral.","unit_type":"definition","cited_unit_ids":["{unit_id}"]}}]}}"#
        );

        let cache = tempfile::tempdir().unwrap();
        let inner = Scripted { unit_replies: vec!["not json".into(), units_reply()], calls: 0 };
        let mut base =
            CachedModelClient::new(inner, cache.path(), "", CacheMode::Record).unwrap();
        let mut critic = Canned("{}".into());
        let mut cards_client = Canned(cards);

        // Day 1: bad reply recorded → truth-layer failure (+ invalidation).
        let dir1 = tempfile::tempdir().unwrap();
        let err = run_reader_pipeline(&src, &mut base, &mut critic, &mut cards_client, dir1.path())
            .expect_err("day 1 fails on unusable units JSON");
        assert!(matches!(err, ReaderPipelineError::TruthLayer(_)), "got {err:?}");

        // Day 2 (same cassette dir): the retry re-asks and succeeds.
        let dir2 = tempfile::tempdir().unwrap();
        let run = run_reader_pipeline(&src, &mut base, &mut critic, &mut cards_client, dir2.path())
            .expect("day 2 retry re-asks the model instead of replaying the pin");
        assert_eq!(run.pack.n_cards, 1);
        assert!(run.card_failure.is_none());
    }

    /// Same pinning fix for the card stage: a card reply that yields 0 usable
    /// cards is forgotten, so the retry gets a fresh card synthesis.
    #[test]
    fn card_failure_invalidates_cassette_so_retry_reasks() {
        use ovp_llm::{CacheMode, CachedModelClient};

        struct Scripted {
            replies: Vec<String>,
            calls: usize,
        }
        impl ModelClient for Scripted {
            fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
                let text = self.replies[self.calls.min(self.replies.len() - 1)].clone();
                self.calls += 1;
                Ok(ModelReply {
                    model: "scripted".into(),
                    text,
                    stop_reason: StopReason::EndTurn,
                    usage: Usage { input_tokens: 1, output_tokens: 1 },
                    blocks: None,
                    raw_stop_reason: None,
                })
            }
        }

        let src = source();
        let ex = crate::units::extract_units(&units_reply(), &src);
        let unit_id = ex.accepted().next().expect("one accepted unit").id.clone();
        let good_cards = format!(
            r#"{{"cards":[{{"title":"Chunks are neutral","content":"A chunk is structurally neutral.","unit_type":"definition","cited_unit_ids":["{unit_id}"]}}]}}"#
        );
        // Parses fine but cites an unknown unit → 0 cards survive → card failure.
        let bad_cards =
            r#"{"cards":[{"title":"x","content":"y","cited_unit_ids":["u-999-deadbeef"]}]}"#
                .to_string();

        let cache = tempfile::tempdir().unwrap();
        let inner = Scripted { replies: vec![bad_cards, good_cards], calls: 0 };
        let mut cards_client =
            CachedModelClient::new(inner, cache.path(), "", CacheMode::Record).unwrap();
        let mut base = Canned(units_reply());
        let mut critic = Canned("{}".into());

        let dir1 = tempfile::tempdir().unwrap();
        let run1 = run_reader_pipeline(&src, &mut base, &mut critic, &mut cards_client, dir1.path())
            .expect("pipeline runs");
        assert!(run1.card_failure.is_some(), "day 1: bad card reply is a card failure");

        let dir2 = tempfile::tempdir().unwrap();
        let run2 = run_reader_pipeline(&src, &mut base, &mut critic, &mut cards_client, dir2.path())
            .expect("pipeline runs");
        assert!(run2.card_failure.is_none(), "day 2: retry re-asked and got usable cards");
        assert_eq!(run2.pack.n_cards, 1);
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
