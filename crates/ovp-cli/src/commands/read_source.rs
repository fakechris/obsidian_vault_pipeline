//! `read-source` — the M17 Grounded Reader Trunk command. End-to-end:
//!   Source → Grounded Units (v5) → Critic Repair (v1) → Reader Cards (card_synth/v3)
//!   → Reader Pack (collapsible HTML + flat MD + provenance artifacts).
//! Fail-loud on truth-layer errors (parse / 0 units / accepted_without_quote>0 / 0
//! cards). NOT wired to canonical store / evergreen / RAG / Referent.
//!
//! M30: the pipeline body lives in `ovp_domain::reader::pipeline` (shared with the
//! `daily` loop); this command is the single-source shim plus `--render-only`.
//!
//! `--render-only` (with `--units-json` + `--cards-json`) renders a pack from
//! existing artifacts without any model call — used to inspect/validate at scale.

use std::path::PathBuf;

use ovp_domain::reader::{
    run_reader_pipeline, write_reader_pack, Card, CardReport, GroundingStatus,
    ReaderPipelineError,
};
use ovp_domain::units::{read_source_from_path, Unit};

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

    let mut base = build_client(args.client_kind, &args.cache_dir)?;
    let mut critic = build_client(args.client_kind, &args.critic_cache_dir)?;
    let mut cards = build_client(args.client_kind, &args.cache_dir)?;

    let run = run_reader_pipeline(
        &source,
        base.as_mut(),
        critic.as_mut(),
        cards.as_mut(),
        &args.out_dir,
    )
    .map_err(|e| match e {
        ReaderPipelineError::TruthLayer(_) => CliError::Io(format!("read-source: {e}")),
        ReaderPipelineError::Client(s) | ReaderPipelineError::Io(s) => CliError::Io(s),
    })?;

    print_summary(&source.title, &run.pack);
    for r in &run.json_repairs {
        println!("  json-repair[{}]: {}", r.stage, r.method);
    }

    // Card-layer fail-loud: the pack is on disk, but the run is not a success.
    if let Some(reason) = run.card_failure {
        return Err(CliError::Io(format!("read-source: {reason}")));
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

fn print_summary(title: &str, p: &ovp_domain::reader::ReaderPack) {
    println!("read-source: {title}");
    println!("  cards={} grounded_units={} (dropped_uncited={})", p.n_cards, p.n_accepted_units, p.cards_dropped_uncited);
    println!("  critic: trims={} adds={}", p.repair_trims, p.repair_adds);
    println!("  grounding: accepted_without_quote={} needs_review={} quote_not_found={}",
        p.accepted_without_quote, p.needs_review, p.quote_not_found);
    println!("  reader pack: reader.html / reader.md (+ source-support.md, cards.json, run-status.json)");
}
