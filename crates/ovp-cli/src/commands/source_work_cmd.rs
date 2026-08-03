//! `source-work` — config, historical backfill, and bilingual projection CLIs.
//!
//! ```text
//! ovp2 source-work show-config --vault-root …
//! ovp2 source-work init-config --vault-root …
//! ovp2 source-work backfill --vault-root … [--max N] [--translate] [--summarize]
//! ovp2 source-work claims-zh --vault-root … [--max N] [--force] --client live
//! ovp2 source-work memory-zh --vault-root … [--max N] [--force] --client live
//! ```

use std::path::PathBuf;

use ovp_index::build_index;
use ovp_memory::bilingual::{
    topup_cards_zh, topup_theme_pages_zh, translate_claims_batch, GlossaryFile,
};
use ovp_memory::source_work_auto::candidates_from_index;
use ovp_memory::source_work_config::SourceWorkConfig;

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct ShowConfigArgs {
    pub vault_root: PathBuf,
}

pub struct InitConfigArgs {
    pub vault_root: PathBuf,
}

pub struct BackfillArgs {
    pub vault_root: PathBuf,
    pub max: usize,
    pub translate: bool,
    pub summarize: bool,
    pub force: bool,
    pub dry_run: bool,
}

pub struct ClaimsZhArgs {
    pub vault_root: PathBuf,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub max: usize,
    pub force: bool,
}

pub struct MemoryZhArgs {
    pub vault_root: PathBuf,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub max: usize,
    pub force: bool,
    pub cards: bool,
    pub theme_pages: bool,
}

pub fn show_config(args: ShowConfigArgs) -> Result<(), CliError> {
    let cfg = SourceWorkConfig::load(&args.vault_root).map_err(CliError::Io)?;
    let path = args.vault_root.join(ovp_memory::source_work_config::CONFIG_REL);
    sayln!("config: {}", path.display());
    sayln!("  exists: {}", path.is_file());
    sayln!("  auto_summarize: {}", cfg.auto_summarize);
    sayln!("  auto_translate: {}", cfg.auto_translate);
    sayln!("  auto_notify: {}", cfg.auto_notify);
    sayln!("  auto_max_per_run: {}", cfg.auto_max_per_run);
    sayln!("  auto_claim_zh: {}", cfg.auto_claim_zh);
    sayln!("  auto_memory_zh: {}", cfg.auto_memory_zh);
    Ok(())
}

pub fn init_config(args: InitConfigArgs) -> Result<(), CliError> {
    let created =
        SourceWorkConfig::ensure_template(&args.vault_root).map_err(CliError::Io)?;
    let path = args.vault_root.join(ovp_memory::source_work_config::CONFIG_REL);
    if created {
        sayln!("wrote {}", path.display());
    } else {
        sayln!("already exists: {}", path.display());
    }
    Ok(())
}

pub fn backfill(args: BackfillArgs) -> Result<(), CliError> {
    let date = today_iso();
    let model = build_index(&args.vault_root, &date, None).map_err(CliError::Io)?;
    let mut cands = candidates_from_index(&args.vault_root, &model, !args.force);
    if args.max > 0 && cands.len() > args.max {
        cands.truncate(args.max);
    }
    // CLI flags: if neither set, use config; if either set, pin those kinds.
    let (ot, os) = if args.translate || args.summarize {
        (Some(args.translate), Some(args.summarize))
    } else {
        (None, None)
    };
    // Cap for this CLI pass uses --max already applied to candidate list;
    // temporarily raise config cap so we don't double-limit.
    let mut cfg = SourceWorkConfig::load(&args.vault_root).map_err(CliError::Io)?;
    if args.max > 0 {
        cfg.auto_max_per_run = args.max;
    } else {
        cfg.auto_max_per_run = 0;
    }
    // Persist temporary cap via enqueue path: use overrides + run with loaded cfg
    // by writing through enqueue_candidates after patching.
    let queue = ovp_memory::source_work_queue::SourceWorkQueue::open(&args.vault_root);
    let report = ovp_memory::source_work_auto::enqueue_candidates(
        &args.vault_root,
        &queue,
        &cands,
        &cfg,
        args.force,
        ot,
        os,
        args.dry_run,
    );
    sayln!(
        "backfill: considered={} enqueued={} complete={} not_en={} cap={} dry={} err={}",
        report.considered,
        report.enqueued,
        report.skipped_complete,
        report.skipped_not_english,
        report.skipped_cap,
        args.dry_run,
        report.errors.len()
    );
    for e in &report.errors {
        sayln!("  err {e}");
    }
    if report.enqueued > 0 && !args.dry_run {
        sayln!("  note: jobs sit in .ovp/source-work-queue.json — run `ovp2 serve` / open OVP2.app to execute");
    }
    Ok(())
}

/// Active durable (claim_key, claim) pairs folded from the crystal ledger.
/// Shared by `claims-zh` and the crystal-synth bilingual tail. Read-only —
/// the ledger is the English authority and is never opened for write here.
pub(crate) fn active_claim_pairs(
    vault_root: &std::path::Path,
) -> Result<Vec<(String, String)>, String> {
    use ovp_domain::crystal::{CrystalStatus, StoreEvent, fold_ledger};
    let ledger = vault_root.join(".ovp/crystal/ledger.jsonl");
    let mut events: Vec<StoreEvent> = Vec::new();
    if ledger.is_file() {
        let raw = std::fs::read_to_string(&ledger).map_err(|e| format!("reading ledger: {e}"))?;
        for (i, line) in raw.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let ev: StoreEvent =
                serde_json::from_str(line).map_err(|e| format!("ledger line {}: {e}", i + 1))?;
            events.push(ev);
        }
    }
    Ok(fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .map(|r| (r.claim_key, r.claim))
        .collect())
}

pub fn claims_zh(args: ClaimsZhArgs) -> Result<(), CliError> {
    let pairs = active_claim_pairs(&args.vault_root).map_err(CliError::Io)?;
    sayln!("claims-zh: {} active durable claim(s)", pairs.len());
    if pairs.is_empty() {
        return Ok(());
    }

    let cache = args
        .cache_dir
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/bilingual"));
    let mut client = build_client(args.client_kind, &cache)?;
    let model = ovp_memory::ask::AskArgs::default().model_name;
    let (done, skipped, errors) = translate_claims_batch(
        &args.vault_root,
        &pairs,
        client.as_mut(),
        &model,
        args.force,
        args.max,
    );
    for e in &errors {
        sayln!("  FAIL {e}");
    }
    // Ensure glossary file exists for operators to extend.
    let _ = GlossaryFile::load(&args.vault_root)
        .and_then(|g| {
            if g.terms.is_empty() {
                g.save(&args.vault_root)
            } else {
                Ok(())
            }
        });
    sayln!(
        "claims-zh done: translated={} skipped={} errors={}",
        done,
        skipped,
        errors.len()
    );
    Ok(())
}

pub fn memory_zh(args: MemoryZhArgs) -> Result<(), CliError> {
    let do_cards = args.cards || (!args.cards && !args.theme_pages);
    let do_pages = args.theme_pages || (!args.cards && !args.theme_pages);

    let date = today_iso();
    let cache = args
        .cache_dir
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/bilingual"));
    let mut client = build_client(args.client_kind, &cache)?;
    let model_name = ovp_memory::ask::AskArgs::default().model_name;

    let mut budget = if args.max == 0 { usize::MAX } else { args.max };
    let mut done = 0usize;
    let mut skipped = 0usize;

    if do_cards {
        let evidence = ovp_index::build_evidence(
            &args.vault_root,
            &date,
            &build_index(&args.vault_root, &date, None).map_err(CliError::Io)?,
        )
        .map_err(CliError::Io)?;
        let cards: Vec<(String, String, String)> = evidence
            .cards
            .iter()
            .map(|c| (c.id.clone(), c.title.clone(), c.content.clone()))
            .collect();
        sayln!("memory-zh cards: {} in evidence", cards.len());
        let max = if budget == usize::MAX { 0 } else { budget };
        let (d, s, errors) = topup_cards_zh(
            &args.vault_root,
            &cards,
            client.as_mut(),
            &model_name,
            args.force,
            max,
        );
        done += d;
        skipped += s;
        budget = budget.saturating_sub(d);
        for e in &errors {
            sayln!("  card FAIL {e}");
        }
    }

    if do_pages {
        use ovp_domain::crystal::theme_pages::ThemePagesFile;
        let path = args.vault_root.join(".ovp/crystal/theme_pages.json");
        let pages = ThemePagesFile::load(&path).map_err(CliError::Io)?;
        let Some(pages) = pages else {
            sayln!("memory-zh theme_pages: none (run crystal-theme-pages first)");
            sayln!("memory-zh done: translated={done} skipped={skipped}");
            return Ok(());
        };
        sayln!("memory-zh theme_pages: {} page(s)", pages.pages.len());
        let page_inputs: Vec<(i64, Vec<(String, String)>)> = pages
            .pages
            .iter()
            .map(|p| {
                (
                    p.community_id,
                    p.sections
                        .iter()
                        .map(|s| (s.heading.clone(), s.body.clone()))
                        .collect(),
                )
            })
            .collect();
        let max = if budget == usize::MAX { 0 } else { budget };
        let (d, s, errors) = topup_theme_pages_zh(
            &args.vault_root,
            &page_inputs,
            client.as_mut(),
            &model_name,
            args.force,
            max,
        );
        done += d;
        skipped += s;
        for e in &errors {
            sayln!("  theme FAIL {e}");
        }
    }

    sayln!("memory-zh done: translated={done} skipped={skipped}");
    Ok(())
}

fn today_iso() -> String {
    // Local civil day — same as `ovp2 daily` default date.
    chrono::Local::now().format("%Y-%m-%d").to_string()
}
