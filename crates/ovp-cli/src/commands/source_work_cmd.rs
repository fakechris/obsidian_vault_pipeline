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
    CACHE_REL, CORRUPT_PROJECTION_MARKER, GlossaryFile, topup_cards_zh, topup_theme_pages_zh,
    translate_claims_batch,
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
    sayln!(
        "  auto_claim_zh_max_per_run: {}",
        cfg.auto_claim_zh_max_per_run
    );
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
    if args.dry_run && report.enqueued > 0 {
        let est = crate::commands::usage_cmd::estimate_source_work(
            &args.vault_root,
            report.enqueued_translate,
            report.enqueued_summarize,
        );
        let basis = if est.cold_start {
            "cold-start output-token ceilings — excludes input tokens & retries, typically 3-5x over"
        } else {
            "source-work lane average from .ovp/usage"
        };
        sayln!(
            "  estimate: {} item(s) ≈ {} call(s), ~{} tokens ({basis})",
            report.enqueued, est.calls, est.tokens
        );
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
        let lines: Vec<(usize, &str)> = raw
            .lines()
            .enumerate()
            .map(|(i, l)| (i, l.trim()))
            .filter(|(_, l)| !l.is_empty())
            .collect();
        for (pos, (i, line)) in lines.iter().enumerate() {
            match serde_json::from_str::<StoreEvent>(line) {
                Ok(ev) => events.push(ev),
                // The serve worker appends without a read/write lock, so a
                // read can catch a half-written FINAL line — recognizable by
                // the missing terminating newline. Tolerate just that case
                // (it will be complete next run). A malformed line that IS
                // newline-terminated is a complete, permanently bad record —
                // silently dropping it could lose the latest write/retract
                // and corrupt the claims projection (codex P2), so it stays
                // a loud, line-numbered error like mid-file garbage.
                Err(_) if pos + 1 == lines.len() && !raw.ends_with('\n') => {}
                Err(e) => return Err(format!("ledger line {}: {e}", i + 1)),
            }
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
        .unwrap_or_else(|| args.vault_root.join(CACHE_REL));
    let usage_ledger = args.vault_root.join(crate::commands::usage_cmd::USAGE_LEDGER_REL);
    let mut client = build_client(args.client_kind, &cache, Some(&usage_ledger))?;
    let model = ovp_memory::ask::AskArgs::default().model_name;
    let (done, skipped, errors) = translate_claims_batch(
        &args.vault_root,
        &pairs,
        client.as_mut(),
        &model,
        args.force,
        args.max,
    );
    // A corrupt projection fails the MANUAL command loud — pre-refactor the
    // initial `ClaimsZhFile::load?` did exactly that, and a silent Ok would
    // let automation report success while nothing was repaired (codex P2).
    // Only the automatic tails degrade this to a warning.
    if let Some(e) = errors.iter().find(|e| e.contains(CORRUPT_PROJECTION_MARKER)) {
        return Err(CliError::Io(format!("claims-zh: {e}")));
    }
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
    // Corrupt projections already failed loud above, so an Err here is an IO
    // surprise — report it instead of guessing a backlog number.
    let remaining = match ovp_memory::bilingual::remaining_untranslated(&args.vault_root, &pairs) {
        Ok(r) => r.to_string(),
        Err(e) => format!("unknown ({e})"),
    };
    sayln!(
        "claims-zh done: translated={} skipped={} errors={} remaining={remaining}",
        done,
        skipped,
        errors.len(),
    );
    Ok(())
}

pub fn memory_zh(args: MemoryZhArgs) -> Result<(), CliError> {
    let do_cards = args.cards || (!args.cards && !args.theme_pages);
    let do_pages = args.theme_pages || (!args.cards && !args.theme_pages);

    let date = today_iso();
    let cache = args
        .cache_dir
        .unwrap_or_else(|| args.vault_root.join(CACHE_REL));
    let usage_ledger = args.vault_root.join(crate::commands::usage_cmd::USAGE_LEDGER_REL);
    let mut client = build_client(args.client_kind, &cache, Some(&usage_ledger))?;
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
        // First phase: the budget is never exhausted yet, so this never skips.
        let max = remaining_max(budget).unwrap_or(0);
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
        // Same corrupt-projection loud failure as claims-zh (codex P2).
        if let Some(e) = errors.iter().find(|e| e.contains(CORRUPT_PROJECTION_MARKER)) {
            return Err(CliError::Io(format!("memory-zh: {e}")));
        }
        for e in &errors {
            sayln!("  card FAIL {e}");
        }
    }

    if do_pages {
        // An exhausted --max budget must NOT become unlimited: the helpers
        // treat max=0 as "no cap", so forwarding a drained budget would
        // translate EVERY theme page.
        let Some(max) = remaining_max(budget) else {
            sayln!("memory-zh theme_pages: skipped (--max budget exhausted by cards)");
            sayln!("memory-zh done: translated={done} skipped={skipped}");
            return Ok(());
        };
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
        if let Some(e) = errors.iter().find(|e| e.contains(CORRUPT_PROJECTION_MARKER)) {
            return Err(CliError::Io(format!("memory-zh: {e}")));
        }
        for e in &errors {
            sayln!("  theme FAIL {e}");
        }
    }

    sayln!("memory-zh done: translated={done} skipped={skipped}");
    Ok(())
}

/// Map the remaining `--max` budget onto a phase's `max` argument.
/// `usize::MAX` (no `--max` given) means unlimited — the helpers take 0 for
/// that. An exhausted budget means the phase must NOT run at all (`None`):
/// forwarding 0 there would silently become unlimited.
fn remaining_max(budget: usize) -> Option<usize> {
    match budget {
        usize::MAX => Some(0),
        0 => None,
        n => Some(n),
    }
}

fn today_iso() -> String {
    // Local civil day — same as `ovp2 daily` default date.
    chrono::Local::now().format("%Y-%m-%d").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        CrystalStatus, DurableRecord, FinalClass, ProvenanceClass, StoreEvent, StoreOp,
        StrengthClass,
    };

    fn record(claim_key: &str, claim: &str) -> DurableRecord {
        DurableRecord {
            claim_key: claim_key.into(),
            claim_id: format!("cl-{claim_key}"),
            claim: claim.into(),
            theme: "t".into(),
            theme_id: None,
            source_cases: vec![],
            citations: vec![],
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "r".into(),
            final_class: FinalClass::Durable,
            run_id: "run-test".into(),
            status: CrystalStatus::Active,
        }
    }

    fn write_event(claim_key: &str, claim: &str) -> StoreEvent {
        StoreEvent {
            op: StoreOp::Write,
            record: record(claim_key, claim),
            supersedes: None,
            reason: None,
        }
    }

    fn write_ledger(vault: &std::path::Path, body: &str) {
        let dir = vault.join(".ovp/crystal");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("ledger.jsonl"), body).unwrap();
    }

    /// Regression (F1): an exhausted --max budget must map to "phase must not
    /// run", never to 0 — the topup helpers treat max=0 as UNLIMITED.
    #[test]
    fn remaining_max_maps_budget_to_phase_cap() {
        assert_eq!(remaining_max(usize::MAX), Some(0), "no --max = unlimited");
        assert_eq!(remaining_max(5), Some(5));
        assert_eq!(remaining_max(1), Some(1));
        assert_eq!(remaining_max(0), None, "exhausted budget = phase must not run");
    }

    #[test]
    fn active_claim_pairs_missing_ledger_is_empty() {
        let tmp = tempfile::tempdir().unwrap();
        assert_eq!(active_claim_pairs(tmp.path()).unwrap(), Vec::new());
    }

    #[test]
    fn active_claim_pairs_returns_only_active_records() {
        let tmp = tempfile::tempdir().unwrap();
        let active = serde_json::to_string(&write_event("ck-a", "Active claim.")).unwrap();
        let retract = serde_json::to_string(&StoreEvent {
            op: StoreOp::Retract,
            record: record("ck-r", "Retracted claim."),
            supersedes: None,
            reason: Some("stale".into()),
        })
        .unwrap();
        write_ledger(tmp.path(), &format!("{active}\n{retract}"));
        let pairs = active_claim_pairs(tmp.path()).unwrap();
        assert_eq!(
            pairs,
            vec![("ck-a".to_string(), "Active claim.".to_string())]
        );
    }

    #[test]
    fn active_claim_pairs_midfile_garbage_errors_with_line_number() {
        let tmp = tempfile::tempdir().unwrap();
        let good = serde_json::to_string(&write_event("ck-a", "Active claim.")).unwrap();
        write_ledger(tmp.path(), &format!("{good}\n{{garbage\n{good}"));
        let err = active_claim_pairs(tmp.path()).unwrap_err();
        assert!(err.contains("ledger line 2"), "{err}");
    }

    /// The serve worker appends without a read/write lock: a read can catch a
    /// half-written FINAL line. Skip just that line — it will be complete
    /// next run — and still fold the events before it.
    #[test]
    fn active_claim_pairs_tolerates_trailing_partial_line() {
        let tmp = tempfile::tempdir().unwrap();
        let good = serde_json::to_string(&write_event("ck-a", "Active claim.")).unwrap();
        write_ledger(tmp.path(), &format!("{good}\n{{\"op\":\"write\",\"rec"));
        let pairs = active_claim_pairs(tmp.path()).unwrap();
        assert_eq!(
            pairs,
            vec![("ck-a".to_string(), "Active claim.".to_string())]
        );
    }

    /// A malformed last line that IS newline-terminated is a complete bad
    /// record, not a half-written append — it must stay a loud error (codex
    /// P2), otherwise the latest write/retract would silently vanish.
    #[test]
    fn active_claim_pairs_rejects_terminated_malformed_last_line() {
        let tmp = tempfile::tempdir().unwrap();
        let good = serde_json::to_string(&write_event("ck-a", "Active claim.")).unwrap();
        write_ledger(tmp.path(), &format!("{good}\n{{not json\n"));
        let err = active_claim_pairs(tmp.path()).unwrap_err();
        assert!(err.contains("ledger line 2"), "{err}");
    }
}
