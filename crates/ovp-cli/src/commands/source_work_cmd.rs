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
    theme_page_en_hash, translate_card, translate_claim, translate_theme_page, CardsZhFile,
    ClaimsZhFile, GlossaryFile, ThemePagesZhFile,
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

pub fn claims_zh(args: ClaimsZhArgs) -> Result<(), CliError> {
    use ovp_domain::crystal::{fold_ledger, CrystalStatus, StoreEvent};
    let ledger = args.vault_root.join(".ovp/crystal/ledger.jsonl");
    let events: Vec<StoreEvent> = if ledger.is_file() {
        let raw = std::fs::read_to_string(&ledger)
            .map_err(|e| CliError::Io(format!("reading ledger: {e}")))?;
        let mut out = Vec::new();
        for (i, line) in raw.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let ev: StoreEvent = serde_json::from_str(line).map_err(|e| {
                CliError::Io(format!("ledger line {}: {e}", i + 1))
            })?;
            out.push(ev);
        }
        out
    } else {
        Vec::new()
    };
    let pairs: Vec<(String, String)> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .map(|r| (r.claim_key, r.claim))
        .collect();
    sayln!("claims-zh: {} active durable claim(s)", pairs.len());
    if pairs.is_empty() {
        return Ok(());
    }

    let cache = args
        .cache_dir
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/bilingual"));
    let mut client = build_client(args.client_kind, &cache)?;
    let model = ovp_memory::ask::AskArgs::default().model_name;
    let existing = ClaimsZhFile::load(&args.vault_root).map_err(CliError::Io)?;
    let mut done = 0usize;
    let mut skipped = 0usize;
    let mut errors = Vec::new();
    for (key, en) in &pairs {
        if args.max > 0 && done >= args.max {
            break;
        }
        if !args.force && existing.get_fresh(key, en).is_some() {
            skipped += 1;
            continue;
        }
        match translate_claim(
            &args.vault_root,
            key,
            en,
            client.as_mut(),
            &model,
            args.force,
        ) {
            Ok(_) => {
                done += 1;
                sayln!("  ok {key}");
            }
            Err(e) => {
                errors.push(format!("{key}: {e}"));
                sayln!("  FAIL {key}: {e}");
            }
        }
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
        let existing = CardsZhFile::load(&args.vault_root).map_err(CliError::Io)?;
        sayln!("memory-zh cards: {} in evidence", evidence.cards.len());
        for card in &evidence.cards {
            if budget == 0 {
                break;
            }
            if !args.force && existing.get_fresh(&card.id, &card.title, &card.content).is_some() {
                skipped += 1;
                continue;
            }
            match translate_card(
                &args.vault_root,
                &card.id,
                &card.title,
                &card.content,
                client.as_mut(),
                &model_name,
                args.force,
            ) {
                Ok(_) => {
                    done += 1;
                    budget = budget.saturating_sub(1);
                    sayln!("  card ok {}", card.id);
                }
                Err(e) => sayln!("  card FAIL {}: {e}", card.id),
            }
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
        let existing = ThemePagesZhFile::load(&args.vault_root).map_err(CliError::Io)?;
        sayln!("memory-zh theme_pages: {} page(s)", pages.pages.len());
        for page in &pages.pages {
            if budget == 0 {
                break;
            }
            let sections: Vec<(String, String)> = page
                .sections
                .iter()
                .map(|s| (s.heading.clone(), s.body.clone()))
                .collect();
            let en_hash = theme_page_en_hash(&sections);
            if !args.force && existing.get_fresh(page.community_id, &en_hash).is_some() {
                skipped += 1;
                continue;
            }
            match translate_theme_page(
                &args.vault_root,
                page.community_id,
                &sections,
                client.as_mut(),
                &model_name,
                args.force,
            ) {
                Ok(_) => {
                    done += 1;
                    budget = budget.saturating_sub(1);
                    sayln!("  theme ok community={}", page.community_id);
                }
                Err(e) => sayln!("  theme FAIL {}: {e}", page.community_id),
            }
        }
    }

    sayln!("memory-zh done: translated={done} skipped={skipped}");
    Ok(())
}

fn today_iso() -> String {
    // Local civil day — same as `ovp2 daily` default date.
    chrono::Local::now().format("%Y-%m-%d").to_string()
}
