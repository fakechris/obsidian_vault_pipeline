//! `read-source` — the M17 Grounded Reader Trunk command. End-to-end:
//!   Source → Grounded Units (v5) → Critic Repair (v1) → Reader Cards (card_synth/v3)
//!   → Reader Pack (collapsible HTML + flat MD + provenance artifacts).
//! Fail-loud on truth-layer errors (parse / 0 units / accepted_without_quote>0 / 0
//! cards). NOT wired to canonical store / evergreen / RAG / Referent.
//!
//! `--render-only` (with `--units-json` + `--cards-json`) renders a pack from
//! existing artifacts without any model call — used to inspect/validate at scale.

use std::path::PathBuf;

use ovp_domain::reader::{
    run_card_synthesis, write_reader_pack, Card, CardReport, GroundingStatus,
};
use ovp_domain::units::{
    read_source_from_path, run_unit_extraction_repaired, Unit,
};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct ReadSourceArgs {
    pub input_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub critic_cache_dir: PathBuf,
    pub client_kind: ClientKind,
    /// Render-only: skip extraction/synthesis, render a pack from these artifacts.
    pub render_only: bool,
    pub units_json: Option<PathBuf>,
    pub cards_json: Option<PathBuf>,
}

pub fn run(args: ReadSourceArgs) -> Result<(), CliError> {
    let source = read_source_from_path(&args.input_path)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.input_path.display())))?;

    if args.render_only {
        return run_render_only(&args, &source.title);
    }

    // 1. Grounded Units (v5) + 2. Critic Repair (v1). Base extracts via the chosen
    //    client (live for a new article); critic likewise.
    let mut base = build_client(args.client_kind, &args.cache_dir)?;
    let mut critic = build_client(args.client_kind, &args.critic_cache_dir)?;
    let run = run_unit_extraction_repaired(&source, base.as_mut(), critic.as_mut())
        .map_err(|e| CliError::Io(format!("grounded extraction/repair failed: {e}")))?;
    let ex = &run.extraction;

    // Fail loud on truth-layer errors BEFORE spending a card-synthesis call.
    if let Some(reason) = truth_layer_failure(ex.report.parse_error.as_deref(), ex.report.total, ex.report.accepted_without_quote) {
        write_audit(&args.out_dir, &run.base_reply, &run.critic_reply, "");
        return Err(CliError::Io(format!("read-source: truth-layer error: {reason}")));
    }

    let accepted: Vec<Unit> = ex.accepted().cloned().collect();

    // 3. Reader Cards (frozen card_synth/v3).
    let mut card_client = build_client(args.client_kind, &args.cache_dir)?;
    let synth = run_card_synthesis(&accepted, card_client.as_mut())
        .map_err(|e| CliError::Io(format!("card synthesis call failed: {e}")))?;

    // 4. Reader Pack.
    let grounding = GroundingStatus {
        accepted_without_quote: ex.report.accepted_without_quote,
        needs_review: ex.report.needs_review,
        quote_not_found: ex.report.quote_not_found,
        parse_error: ex.report.parse_error.clone(),
    };
    let pack = write_reader_pack(&args.out_dir, &source.title, &accepted, &synth.cards,
        &synth.report, Some(&run.repair_log), &grounding)
        .map_err(|e| CliError::Io(format!("writing reader pack: {e}")))?;
    write_audit(&args.out_dir, &run.base_reply, &run.critic_reply, &synth.raw_reply);
    write_units(&args.out_dir, &accepted);

    print_summary(&source.title, &pack);

    // Card-layer fail-loud: units extracted but no cards survived synthesis.
    if let Some(e) = &synth.report.parse_error {
        return Err(CliError::Io(format!("read-source: card synthesis did not parse: {e} (pack written)")));
    }
    if !accepted.is_empty() && synth.cards.is_empty() {
        return Err(CliError::Io("read-source: 0 reader cards produced from accepted units (pack written)".into()));
    }
    Ok(())
}

fn run_render_only(args: &ReadSourceArgs, title: &str) -> Result<(), CliError> {
    let up = args.units_json.as_ref().ok_or_else(|| CliError::Io("--render-only requires --units-json".into()))?;
    let cp = args.cards_json.as_ref().ok_or_else(|| CliError::Io("--render-only requires --cards-json".into()))?;
    let units: Vec<Unit> = serde_json::from_str(&std::fs::read_to_string(up)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", up.display())))?)
        .map_err(|e| CliError::Io(format!("parsing units {}: {e}", up.display())))?;
    let cards: Vec<Card> = serde_json::from_str(&std::fs::read_to_string(cp)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", cp.display())))?)
        .map_err(|e| CliError::Io(format!("parsing cards {}: {e}", cp.display())))?;
    let report = CardReport { cards_returned: cards.len(), cards_kept: cards.len(), cards_dropped_uncited: 0, parse_error: None };
    let pack = write_reader_pack(&args.out_dir, title, &units, &cards, &report, None, &GroundingStatus::default())
        .map_err(|e| CliError::Io(format!("writing reader pack: {e}")))?;
    print_summary(title, &pack);
    Ok(())
}

/// `Some(reason)` if the truth layer is unusable (mirrors `extraction_failure`).
fn truth_layer_failure(parse_error: Option<&str>, total: usize, accepted_without_quote: usize) -> Option<String> {
    if let Some(e) = parse_error {
        return Some(format!("unit extraction did not parse: {e}"));
    }
    if total == 0 {
        return Some("0 units extracted".into());
    }
    if accepted_without_quote > 0 {
        return Some(format!("{accepted_without_quote} accepted unit(s) without a located quote — grounding violated"));
    }
    None
}

fn write_audit(out: &std::path::Path, base: &str, critic: &str, cards: &str) {
    let _ = std::fs::create_dir_all(out);
    let _ = std::fs::write(out.join("model-reply.units.txt"), base);
    let _ = std::fs::write(out.join("model-reply.critic.txt"), critic);
    if !cards.is_empty() {
        let _ = std::fs::write(out.join("model-reply.cards.txt"), cards);
    }
}

fn write_units(out: &std::path::Path, accepted: &[Unit]) {
    if let Ok(s) = serde_json::to_string_pretty(accepted) {
        let _ = std::fs::write(out.join("units.accepted.json"), format!("{s}\n"));
    }
}

fn print_summary(title: &str, p: &ovp_domain::reader::ReaderPack) {
    println!("read-source: {title}");
    println!("  cards={} grounded_units={} (dropped_uncited={})", p.n_cards, p.n_accepted_units, p.cards_dropped_uncited);
    println!("  critic: trims={} adds={}", p.repair_trims, p.repair_adds);
    println!("  grounding: accepted_without_quote={} needs_review={} quote_not_found={}",
        p.accepted_without_quote, p.needs_review, p.quote_not_found);
    println!("  reader pack: reader.html / reader.md (+ source-support.md, cards.json, run-status.json)");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truth_layer_failure_cases() {
        assert!(truth_layer_failure(Some("bad json"), 0, 0).is_some());
        assert!(truth_layer_failure(None, 0, 0).is_some());
        assert!(truth_layer_failure(None, 5, 1).is_some());
        assert!(truth_layer_failure(None, 5, 0).is_none());
    }
}
