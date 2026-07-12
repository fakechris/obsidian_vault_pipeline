use std::collections::BTreeSet;
use std::path::PathBuf;

use ovp_domain::crystal::synth::{collect_catalog, parse_strength_verdicts, strength_request};
use ovp_domain::crystal::themes::{ThemesFile, UNCLASSIFIED_ID};
use ovp_domain::crystal::{
    apply_decisions, defer_fired, strength_coverage, triviality_containment,
    ClaimStrengthVerdict, CrystalCandidate, CrystalClaim, CrystalHeader, DeferState,
    DeferTrigger, ReviewAction, ReviewDecision, ReviewEntry,
};
use ovp_domain::vault_layout::VaultLayout;
use ovp_index::model::IndexModel;

use crate::commands::client::{build_client, ClientKind};
use crate::commands::crystal_synth::{call_and_parse, RepairLog, MAX_STRENGTH_CLAIMS_PER_CALL};
use crate::commands::crystal_write::{
    build_grounding_index, merge_review_queue, read_review_queue, write_durable,
    write_review_queue, WriteInputs,
};
use crate::commands::{console_cmd, index_cmd, project};
use crate::CliError;

pub struct CrystalReviewSessionPrepareArgs {
    pub vault_root: PathBuf,
    pub batch: usize,
    pub out: PathBuf,
    /// Also emit zero-LLM backfill suggestions per selected entry (top-k
    /// corpus units from the evidence sidecar that could become second
    /// sources). Productizes the R0 manual probe.
    pub suggest: bool,
}

/// The reader-pack case id is its dir basename (== themes.json packs key).
fn pack_case_id(pack_dir: &str) -> &str {
    pack_dir.rsplit('/').next().unwrap_or(pack_dir)
}

/// Community id of a pack in the projection. Packs ABSENT from `packs` are
/// new-since-generation (the themes.json contract: "a pack missing from this
/// map is by definition NEW") and count toward the unclassified pool — new
/// packs land there until the next `crystal-themes` run.
fn community_of(themes: &ThemesFile, case_id: &str) -> i64 {
    themes.packs.get(case_id).copied().unwrap_or(UNCLASSIFIED_ID)
}

/// How many read-model packs are "in the entry's theme" — the count
/// `new_sources_in_theme` defers are measured against. Membership derives
/// from STABLE identity, never display-label text: labels change under
/// `--client live` relabels and clustering refreshes (which would orphan a
/// label-keyed defer silently), and fallback themes like "Unthemed batch N"
/// or "Unclassified" match no community label at all (count stuck at 0 → the
/// trigger could never fire, violating the M36 closed-vocabulary contract).
///
///   - themes.json + a cited entry: the union of the communities the entry's
///     cited packs belong to (community id via `packs`, unclassified pool
///     included) — the defer fires as packs join those communities.
///   - otherwise (no themes.json, or a pre-M35 entry without citations):
///     the entry's own cited-pack set plus the legacy case-insensitive
///     title⊇theme containment, so keyword-era queues keep firing.
fn theme_pack_count(model: &IndexModel, themes: Option<&ThemesFile>, entry: &ReviewEntry) -> usize {
    let cited: BTreeSet<&str> = entry.citations.iter().map(|c| c.case_id.as_str()).collect();
    if let Some(t) = themes
        && !cited.is_empty()
    {
        let communities: BTreeSet<i64> =
            cited.iter().map(|case_id| community_of(t, case_id)).collect();
        return model
            .packs
            .iter()
            .filter(|p| communities.contains(&community_of(t, pack_case_id(&p.pack_dir))))
            .count();
    }
    let theme = entry.theme.to_lowercase();
    model
        .packs
        .iter()
        .filter(|p| {
            cited.contains(pack_case_id(&p.pack_dir)) || p.title.to_lowercase().contains(&theme)
        })
        .count()
}

/// The count a defer trigger is measured against, from the read model.
fn trigger_count(
    model: &IndexModel,
    themes: Option<&ThemesFile>,
    trigger: DeferTrigger,
    entry: &ReviewEntry,
) -> usize {
    match trigger {
        DeferTrigger::CorpusGrowsBy => model.packs.len(),
        DeferTrigger::NewSourcesInTheme => theme_pack_count(model, themes, entry),
    }
}

pub fn run_prepare(args: CrystalReviewSessionPrepareArgs) -> Result<(), CliError> {
    let review_path = args.vault_root.join(".ovp/crystal/review.json");
    let mut review = read_review_queue(&review_path)?;
    // Human sessions review the Review lane only — source-scoped insights
    // (single-source + Supported) are parked, not human debt (M35).
    let parked = review
        .iter()
        .filter(|e| e.lane == ovp_domain::crystal::ReviewLane::SourceInsight)
        .count();
    review.retain(|e| e.lane == ovp_domain::crystal::ReviewLane::Review);
    if parked > 0 {
        println!("  ({parked} source-insight entr(ies) parked outside the human queue)");
    }
    // Deferred entries stay skipped until their trigger fires (M36 R1). If the
    // read model is unavailable we INCLUDE them — deferral must never hide
    // items just because the index was not built.
    let has_defers = review.iter().any(|e| e.defer.is_some());
    if has_defers {
        let themes = match ThemesFile::load(&args.vault_root.join(".ovp/crystal/themes.json")) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("  warning: ignoring themes.json for defer triggers ({e})");
                None
            }
        };
        match ovp_index::read_index(&args.vault_root) {
            Ok(model) => {
                let before = review.len();
                review.retain(|e| match &e.defer {
                    Some(d) => {
                        defer_fired(d, trigger_count(&model, themes.as_ref(), d.trigger, e))
                    }
                    None => true,
                });
                let skipped = before - review.len();
                if skipped > 0 {
                    println!("  ({skipped} deferred entr(ies) skipped — trigger not fired)");
                }
            }
            Err(e) => eprintln!(
                "  warning: cannot check defer triggers ({e}); including deferred entries"
            ),
        }
    }
    review.sort_by(|a, b| {
        (a.theme.as_str(), a.claim_id.as_str()).cmp(&(b.theme.as_str(), b.claim_id.as_str()))
    });
    review.truncate(args.batch);

    std::fs::create_dir_all(&args.out)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", args.out.display())))?;
    std::fs::write(
        args.out.join("selected-claim-ids.txt"),
        selected_ids(&review),
    )
    .map_err(|e| CliError::Io(format!("writing selected ids: {e}")))?;
    std::fs::write(
        args.out.join("review-sheet.md"),
        render_review_sheet(&review),
    )
    .map_err(|e| CliError::Io(format!("writing review sheet: {e}")))?;
    std::fs::write(
        args.out.join("decisions.template.json"),
        render_decisions_template(&review)?,
    )
    .map_err(|e| CliError::Io(format!("writing decisions template: {e}")))?;

    println!(
        "crystal-review session prepare: {} claim(s) -> {}",
        review.len(),
        args.out.display()
    );
    println!("  review-sheet.md");
    println!("  decisions.template.json");
    println!("  selected-claim-ids.txt");
    if args.suggest {
        let n = write_backfill_suggestions(&args.vault_root, &review, &args.out)?;
        println!("  backfill-candidates.md ({n} candidate unit(s) across the batch)");
    }
    Ok(())
}

/// Zero-LLM backfill suggestions: for each selected entry, the top corpus
/// units (evidence sidecar) from cases the entry does NOT already cite —
/// candidate second sources for a `narrow`/`rewrite` with unioned citations.
/// This productizes the R0 manual probe (4/10 strict hit rate by hand).
fn write_backfill_suggestions(
    vault_root: &std::path::Path,
    review: &[ReviewEntry],
    out: &std::path::Path,
) -> Result<usize, CliError> {
    const PER_ENTRY: usize = 5;
    let evidence = ovp_index::read_evidence(vault_root)
        .map_err(|e| CliError::Io(format!("reading evidence sidecar: {e}")))?;
    let mut md = String::from(
        "# Backfill candidates (zero-LLM, evidence-sidecar retrieval)\n\n\
         For each entry: top corpus units from NOT-yet-cited cases. A hit becomes a\n\
         `narrow`/`rewrite` decision whose citations are the union of old + new (the R0\n\
         backfill pattern: 4 promotions, all durable). Judge relevance yourself — the\n\
         ranking is lexical.\n\n",
    );
    let mut total = 0usize;
    let mut json_rows = Vec::new();
    for e in review {
        let cited: BTreeSet<&str> = e.citations.iter().map(|c| c.case_id.as_str()).collect();
        let mut scored: Vec<(f64, &ovp_index::evidence::UnitEvidenceRow)> = evidence
            .units
            .iter()
            .filter(|u| !cited.contains(u.pack_dir.as_str()))
            .map(|u| {
                (
                    ovp_index::score::lexical_score(&e.claim, &[&u.text, &u.quote]),
                    u,
                )
            })
            .filter(|(s, _)| *s > 0.0)
            .collect();
        scored.sort_by(|a, b| {
            b.0.partial_cmp(&a.0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| (a.1.pack_dir.as_str(), a.1.unit_id.as_str())
                    .cmp(&(b.1.pack_dir.as_str(), b.1.unit_id.as_str())))
        });
        scored.truncate(PER_ENTRY);
        md.push_str(&format!("## `{}` [{}]\n\n{}\n\n", e.claim_id, e.theme, e.claim.trim()));
        if scored.is_empty() {
            md.push_str("_no candidate units found in uncited cases_\n\n");
            continue;
        }
        for (score, u) in &scored {
            md.push_str(&format!(
                "- ({score:.1}) `{}` · `{}`{} — \"{}\"\n",
                u.pack_dir,
                u.unit_id,
                u.line.map(|l| format!(" · line {l}")).unwrap_or_default(),
                u.quote.trim().chars().take(180).collect::<String>()
            ));
            json_rows.push(serde_json::json!({
                "claim_id": e.claim_id,
                "case_id": u.pack_dir,
                "unit_id": u.unit_id,
                "quote": u.quote,
                "score": score,
            }));
            total += 1;
        }
        md.push('\n');
    }
    std::fs::write(out.join("backfill-candidates.md"), md)
        .map_err(|e| CliError::Io(format!("writing backfill candidates: {e}")))?;
    std::fs::write(
        out.join("backfill-candidates.json"),
        serde_json::to_string_pretty(&json_rows).unwrap() + "\n",
    )
    .map_err(|e| CliError::Io(format!("writing backfill candidates json: {e}")))?;
    Ok(total)
}

fn selected_ids(review: &[ReviewEntry]) -> String {
    let mut out = String::new();
    for entry in review {
        out.push_str(&entry.claim_id);
        out.push('\n');
    }
    out
}

pub struct CrystalReviewSessionApplyArgs {
    pub vault_root: PathBuf,
    pub decisions: PathBuf,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub run_id: Option<String>,
    pub title: Option<String>,
    pub refresh: bool,
    pub date: Option<String>,
}

/// Apply a filled decisions file against the vault-local review queue:
/// decisions → revised claims → strength gate → durable write (the reviewed
/// queue entries retire) → optional project/index/console refresh. Human
/// decisions NEVER bypass the gate. Malformed decisions and revisions with
/// defective citations fail LOUD before anything is mutated (fix
/// decisions.json and re-run — silent routing would discard the reviewer's
/// citation work); revisions that pass grounding but fail the strength gate
/// route back into the review queue with their rationale.
pub fn run_apply(args: CrystalReviewSessionApplyArgs) -> Result<(), CliError> {
    if args.refresh && args.date.is_none() {
        return Err(CliError::Io(
            "crystal-review-session-apply: --refresh requires --date <YYYY-MM-DD>".into(),
        ));
    }
    let layout = VaultLayout::new();
    let store = args.vault_root.join(layout.crystal_store_dir());
    let review_path = store.join("review.json");
    let entries = read_review_queue(&review_path)?;
    if entries.is_empty() {
        return Err(CliError::Io(format!(
            "crystal-review-session-apply: empty review queue at {}",
            review_path.display()
        )));
    }
    let text = std::fs::read_to_string(&args.decisions)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.decisions.display())))?;
    let decisions: Vec<ReviewDecision> = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing {}: {e}", args.decisions.display())))?;

    // Validate decision cardinality BEFORE anything mutates: a rewrite/split
    // left with empty revisions would otherwise retire the entry as a silent
    // reject (a template edited halfway is the common mistake).
    let mut malformed: Vec<String> = Vec::new();
    for d in &decisions {
        match d.action {
            ReviewAction::Rewrite | ReviewAction::Narrow if d.revisions.len() != 1 => {
                malformed.push(format!(
                    "{}: {:?} requires exactly 1 revision (got {})",
                    d.claim_id,
                    d.action,
                    d.revisions.len()
                ))
            }
            ReviewAction::Split | ReviewAction::SplitByEvidence if d.revisions.len() < 2 => {
                malformed.push(format!(
                    "{}: {:?} requires >=2 revisions (got {})",
                    d.claim_id,
                    d.action,
                    d.revisions.len()
                ))
            }
            ReviewAction::DeferUntil if d.defer.is_none() => malformed.push(format!(
                "{}: defer_until requires `defer: {{trigger, n}}`",
                d.claim_id
            )),
            ReviewAction::KeepCaveated
            | ReviewAction::Reject
            | ReviewAction::RejectAsNoise
            | ReviewAction::DemoteToSourceInsight
            | ReviewAction::DeferUntil
                if !d.revisions.is_empty() =>
            {
                malformed.push(format!(
                    "{}: {:?} must not carry revisions (got {})",
                    d.claim_id,
                    d.action,
                    d.revisions.len()
                ))
            }
            _ => {}
        }
    }
    if !malformed.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: malformed decisions — {} — nothing was changed; \
             fix the decisions file and re-run",
            malformed.join("; ")
        )));
    }

    // The queue entries ARE the original candidate for id-matching purposes
    // (rewrite/split revisions carry their own full claims + citations).
    let original = CrystalCandidate {
        items: entries
            .iter()
            .map(|e| CrystalClaim {
                id: e.claim_id.clone(),
                claim: e.claim.clone(),
                theme: e.theme.clone(),
                citations: Vec::new(),
                caveat: None,
            })
            .collect(),
    };
    let outcome = apply_decisions(&original, &decisions);
    if !outcome.unknown.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: decisions reference unknown claim ids {:?} — \
             refusing to proceed (stale decisions file?)",
            outcome.unknown
        )));
    }
    // Reviewed ids leave the queue: rewrite/narrow/split (replaced by
    // revisions that re-enter the gate) and reject. keep_caveated stays, and
    // the queue-state ops (demote/defer) MUTATE their entry in place.
    let processed: BTreeSet<String> = outcome
        .log
        .iter()
        .filter(|(_, action, _)| {
            matches!(
                action,
                ReviewAction::Rewrite
                    | ReviewAction::Narrow
                    | ReviewAction::Split
                    | ReviewAction::SplitByEvidence
                    | ReviewAction::Reject
                    | ReviewAction::RejectAsNoise
            )
        })
        .map(|(id, _, _)| id.clone())
        .collect();
    for (id, action, n) in &outcome.log {
        println!("  decision {id}: {action:?} -> {n} revision(s)");
    }

    // Pre-validate EVERYTHING before any mutation (codex P2: a mixed decisions
    // file must not persist its queue ops and then fail the revision lint —
    // "nothing was changed" must be literally true on every error path).
    let revised = outcome.revised;
    let regate = if revised.items.is_empty() {
        None
    } else {
        // Build the grounding context + pre-lint the revisions BEFORE any
        // model spend or mutation: write_durable fails loud on citation
        // defects, so one typo'd citation would abort the whole batch. Failing
        // here keeps decisions.json the single thing to fix and never discards
        // the reviewer's citation work.
        let reader_root = args.vault_root.join(layout.reader_root());
        let index = build_grounding_index(&reader_root)?;
        let catalog = collect_catalog(&reader_root)
            .map_err(|e| CliError::Io(format!("crystal-review-session-apply: {e}")))?;
        let lint = ovp_domain::crystal::lint_candidate(&revised, &index);
        let defective: Vec<String> = lint
            .claims
            .iter()
            .filter(|c| !c.fully_grounded)
            .map(|c| {
                let details: Vec<String> = c
                    .citations
                    .iter()
                    .filter_map(|cc| cc.defect.as_ref().map(|d| format!("{}:{d:?}", cc.unit_id)))
                    .collect();
                let detail =
                    if details.is_empty() { "no citations".to_string() } else { details.join(", ") };
                format!("{} ({detail})", c.claim_id)
            })
            .collect();
        if !defective.is_empty() {
            return Err(CliError::Gate(format!(
                "crystal-review-session-apply: {} revision(s) have citation defects — {} — \
                 nothing was changed; fix the revisions' citations and re-run",
                defective.len(),
                defective.join("; ")
            )));
        }
        Some((index, catalog))
    };

    // Queue-state operations (M36 R1): all validation has passed — persist
    // them now, BEFORE any model call, so a later transport failure cannot
    // lose a human decision that needs no gate.
    let mut entries = entries;
    let mut n_queue_ops = 0usize;
    let mut index_model: Option<IndexModel> = None;
    let themes = match ThemesFile::load(&args.vault_root.join(".ovp/crystal/themes.json")) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("  warning: ignoring themes.json for defer baselines ({e})");
            None
        }
    };
    for d in &decisions {
        match d.action {
            ReviewAction::DemoteToSourceInsight => {
                if let Some(e) = entries.iter_mut().find(|e| e.claim_id == d.claim_id) {
                    e.lane = ovp_domain::crystal::ReviewLane::SourceInsight;
                    n_queue_ops += 1;
                }
            }
            ReviewAction::DeferUntil => {
                let spec = d.defer.expect("validated above");
                if index_model.is_none() {
                    index_model = Some(ovp_index::read_index(&args.vault_root).map_err(|e| {
                        CliError::Io(format!(
                            "defer_until needs the read model for its baseline: {e}"
                        ))
                    })?);
                }
                let model = index_model.as_ref().expect("just loaded");
                if let Some(e) = entries.iter_mut().find(|e| e.claim_id == d.claim_id) {
                    let baseline = trigger_count(model, themes.as_ref(), spec.trigger, e);
                    e.defer = Some(DeferState { trigger: spec.trigger, n: spec.n, baseline });
                    n_queue_ops += 1;
                }
            }
            _ => {}
        }
    }
    if n_queue_ops > 0 {
        write_review_queue(&review_path, &entries)?;
        println!("  queue ops persisted: {n_queue_ops} entr(ies) demoted/deferred");
    }

    let Some((index, catalog)) = regate else {
        // reject/keep/queue-op-only session: no model call, no ledger change —
        // just retire the processed entries. crystal.md's review section
        // refreshes on the next durable write.
        let merged = merge_review_queue(entries, &processed, Vec::new());
        write_review_queue(&review_path, &merged)?;
        println!(
            "crystal-review-session-apply: no revisions to re-gate; retired {} entr(ies), \
             {} remain in {}",
            processed.len(),
            merged.len(),
            review_path.display()
        );
        refresh_views(&args)?;
        return Ok(());
    };

    // M36 anti-gaming warnings (R1: warn-only, never blocks). (a) triviality:
    // a revision that mostly restates its own quotes adds no synthesis value;
    // (b) cross-source loss: a repair that dropped the parent's cross-source
    // property may have narrowed away the claim's whole point.
    for item in &revised.items {
        let containment = triviality_containment(&item.claim, &item.citations);
        if containment >= 0.8 {
            eprintln!(
                "  WARNING: {} restates its evidence (containment {:.2}) — \
                 consider whether it still synthesizes anything",
                item.id, containment
            );
        }
        let parent_id = item.id.trim_end_matches(char::is_numeric);
        let parent_id = parent_id.strip_suffix('s').unwrap_or(parent_id);
        let parent_id = parent_id.strip_suffix('r').unwrap_or(parent_id);
        if let Some(parent) = entries.iter().find(|e| e.claim_id == parent_id) {
            let parent_sources: BTreeSet<&str> =
                parent.citations.iter().map(|c| c.case_id.as_str()).collect();
            let rev_sources: BTreeSet<&str> =
                item.citations.iter().map(|c| c.case_id.as_str()).collect();
            if parent_sources.len() >= 2 && rev_sources.len() < 2 {
                eprintln!(
                    "  WARNING: {} lost the parent's cross-source property \
                     ({} sources -> {})",
                    item.id,
                    parent_sources.len(),
                    rev_sources.len()
                );
            }
        }
    }
    let cache_dir = args
        .cache_dir
        .clone()
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/crystal"));
    let mut client = build_client(args.client_kind, &cache_dir)?;
    let mut verdicts: Vec<ClaimStrengthVerdict> = Vec::new();
    let mut repairs: Vec<RepairLog> = Vec::new();
    // Each chunk is one live re-gate LLM call; stream a flushed heartbeat so a
    // watched apply never looks hung.
    let n_regate_chunks = revised
        .items
        .len()
        .div_ceil(MAX_STRENGTH_CLAIMS_PER_CALL)
        .max(1);
    for (ci, chunk) in revised.items.chunks(MAX_STRENGTH_CLAIMS_PER_CALL).enumerate() {
        sayln!(
            "  [{}/{n_regate_chunks}] re-gating {} revision(s)",
            ci + 1,
            chunk.len()
        );
        let sub = CrystalCandidate { items: chunk.to_vec() };
        let req = strength_request(&sub, &catalog);
        let (chunk_verdicts, log): (Vec<ClaimStrengthVerdict>, Option<RepairLog>) =
            call_and_parse(client.as_mut(), &req, "strength", parse_strength_verdicts)?;
        verdicts.extend(chunk_verdicts);
        if let Some(l) = log {
            repairs.push(l);
        }
    }
    let ids: Vec<String> = revised.items.iter().map(|c| c.id.clone()).collect();
    let coverage = strength_coverage(&ids, &verdicts);
    if !coverage.complete() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: strength verdicts incomplete — missing={:?} \
             duplicate={:?} unknown={:?}",
            coverage.missing, coverage.duplicate, coverage.unknown
        )));
    }

    let header = CrystalHeader {
        title: args.title.clone().unwrap_or_else(|| "Crystal".into()),
        scope: String::new(),
        not_claiming: String::new(),
    };
    let n_revised = revised.items.len();
    let out = write_durable(WriteInputs {
        candidate: revised,
        verdicts,
        index,
        store,
        run_id: args.run_id.clone(),
        header,
        processed_review_ids: processed,
    })?;
    println!("crystal-review-session-apply: run_id={}", out.run_id);
    println!(
        "  re-gated {n_revised} revision(s): {} newly durable ({} already active), \
         {} routed back to review",
        out.appended,
        out.considered.saturating_sub(out.appended),
        out.review
    );
    for r in &repairs {
        println!("  json-repair[{}]: {}", r.stage, r.method);
    }
    println!("  store: {} active durable claim(s) total", out.active_total);
    println!("  ledger: {}", out.ledger_path.display());

    refresh_views(&args)?;
    Ok(())
}

/// Optional post-write refresh: Crystal Notes projection + index + console.
/// Runs for BOTH the durable-write path and the reject/keep-only path — a
/// queue-only session still changes what the console's review page must show.
fn refresh_views(args: &CrystalReviewSessionApplyArgs) -> Result<(), CliError> {
    if !args.refresh {
        return Ok(());
    }
    let date = args.date.clone().expect("checked at entry");
    project::run(project::ProjectArgs {
        vault_root: args.vault_root.clone(),
        lane: project::LaneFilter::Durable,
        verbose: false,
        write: true,
        rebuild: false,
    })?;
    index_cmd::run_index(index_cmd::IndexArgs {
        vault_root: args.vault_root.clone(),
        date: date.clone(),
    })?;
    console_cmd::run(console_cmd::ConsoleArgs { vault_root: args.vault_root.clone(), date })
}

fn render_review_sheet(review: &[ReviewEntry]) -> String {
    let mut out = String::from("# Crystal Review Session\n\n");
    if review.is_empty() {
        out.push_str("_No review entries selected._\n");
        return out;
    }
    for (idx, entry) in review.iter().enumerate() {
        out.push_str(&format!(
            "## {}. `{}` [{}] - {}\n\n{}\n\n_strength: {:?} | evidence_sufficient: {}_\n\n{}\n\n",
            idx + 1,
            entry.claim_id,
            entry.theme,
            final_class_label(entry),
            entry.claim.trim(),
            entry.strength,
            entry.evidence_sufficient,
            entry.rationale.trim()
        ));
        if entry.citations.is_empty() {
            out.push_str(
                "_No citations on this entry (pre-M35 queue) — regenerate the queue with a \
                 replay `crystal-synth` run to populate them._\n\n",
            );
        } else {
            out.push_str("Citations (copy into `revisions` for rewrite/split):\n\n");
            for c in &entry.citations {
                out.push_str(&format!(
                    "- `{}` · `{}` — \"{}\"\n",
                    c.case_id,
                    c.unit_id,
                    c.quote.trim()
                ));
            }
            out.push('\n');
        }
    }
    out
}

fn final_class_label(entry: &ReviewEntry) -> String {
    format!("{:?}", entry.final_class)
}

fn render_decisions_template(review: &[ReviewEntry]) -> Result<String, CliError> {
    let decisions: Vec<ReviewDecision> = review
        .iter()
        .map(|entry| ReviewDecision {
            claim_id: entry.claim_id.clone(),
            action: ReviewAction::KeepCaveated,
            revisions: Vec::new(),
            defer: None,
            note: "TODO: narrow | split_by_evidence | demote_to_source_insight | \
                   defer_until (+defer{trigger,n}) | reject_as_noise | keep_caveated"
                .into(),
        })
        .collect();
    serde_json::to_string_pretty(&decisions)
        .map(|body| format!("{body}\n"))
        .map_err(|e| CliError::Io(format!("serializing decisions template: {e}")))
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::json;

    use crate::commands::crystal_review_session::{run_prepare, CrystalReviewSessionPrepareArgs};

    #[test]
    fn crystal_review_session_prepare_writes_deterministic_batch_files() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            serde_json::to_string_pretty(&json!({
                "review": [
                    {
                        "claim_id": "z",
                        "claim": "z claim",
                        "theme": "zeta",
                        "final_class": "caveated",
                        "strength": "supported",
                        "evidence_sufficient": true,
                        "rationale": "z rationale"
                    },
                    {
                        "claim_id": "a",
                        "claim": "a claim",
                        "theme": "alpha",
                        "final_class": "caveated",
                        "strength": "supported",
                        "evidence_sufficient": true,
                        "rationale": "a rationale"
                    }
                ]
            }))
            .unwrap(),
        )
        .unwrap();
        let out = tmp.path().join("session");

        run_prepare(CrystalReviewSessionPrepareArgs {
            vault_root: vault,
            batch: 1,
            out: out.clone(),
            suggest: false,
        })
        .unwrap();

        assert_eq!(
            fs::read_to_string(out.join("selected-claim-ids.txt")).unwrap(),
            "a\n"
        );
        let sheet = fs::read_to_string(out.join("review-sheet.md")).unwrap();
        assert!(sheet.contains("a claim"), "{sheet}");
        assert!(!sheet.contains("z claim"), "{sheet}");
        let template = fs::read_to_string(out.join("decisions.template.json")).unwrap();
        assert!(template.contains(r#""claim_id": "a""#), "{template}");
        assert!(
            template.contains(r#""action": "keep_caveated""#),
            "{template}"
        );
    }

    fn review_json(entries: serde_json::Value) -> String {
        serde_json::to_string_pretty(&json!({ "review": entries })).unwrap()
    }

    fn caveated_entry(id: &str, claim: &str, theme: &str) -> serde_json::Value {
        json!({
            "claim_id": id, "claim": claim, "theme": theme,
            "final_class": "caveated", "strength": "supported",
            "evidence_sufficient": true, "rationale": "needs review"
        })
    }

    #[test]
    fn apply_reject_and_keep_only_retires_entries_without_model_calls() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            review_json(json!([
                caveated_entry("c1", "too broad", "memory"),
                caveated_entry("c2", "still deciding", "agents"),
            ])),
        )
        .unwrap();
        let decisions = tmp.path().join("decisions.json");
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "reject", "revisions": [], "note": "dup" }
            ]))
            .unwrap(),
        )
        .unwrap();

        super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect("reject-only session applies");

        let queue = fs::read_to_string(store.join("review.json")).unwrap();
        assert!(!queue.contains("\"c1\""), "rejected entry retired: {queue}");
        assert!(queue.contains("\"c2\""), "undecided entry stays: {queue}");
        assert!(!store.join("ledger.jsonl").exists(), "no durable write happened");
    }

    #[test]
    fn apply_rejects_rewrite_with_empty_revisions_before_mutating_anything() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        let queue_before = review_json(json!([caveated_entry("c1", "too broad", "memory")]));
        fs::write(store.join("review.json"), &queue_before).unwrap();
        let decisions = tmp.path().join("decisions.json");
        // The common template mistake: action flipped to rewrite, revisions left empty.
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "rewrite", "revisions": [], "note": "oops" }
            ]))
            .unwrap(),
        )
        .unwrap();

        let err = super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect_err("empty rewrite must fail loud, not silently reject");
        assert!(matches!(err, crate::CliError::Gate(_)), "got {err:?}");
        assert_eq!(
            fs::read_to_string(store.join("review.json")).unwrap(),
            queue_before,
            "queue untouched on malformed decisions"
        );
    }

    #[test]
    fn apply_fails_loud_on_citation_defects_without_mutating_the_queue() {
        use ovp_domain::crystal::{Citation, CrystalClaim};
        use ovp_domain::source_doc::SourceDoc;
        use ovp_domain::units::{validate, Unit};

        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let reader = vault.join("40-Resources/Reader");
        let case_dir = reader.join("m18-01");
        fs::create_dir_all(&case_dir).unwrap();
        let raw = vec![json!({
            "kind": "assertion", "text": "t0", "evidence_ref": "p001",
            "evidence_quote": "Memory is scarce working memory in systems.",
            "attribution": "author", "modality": "asserted", "arguments": []
        })];
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![],
                "Memory is scarce working memory in systems."),
        );
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        fs::write(case_dir.join("units.accepted.json"), serde_json::to_string_pretty(&units).unwrap()).unwrap();
        fs::write(case_dir.join("reader.md"), "# T\n\nbody\n").unwrap();

        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        let queue_before = review_json(json!([caveated_entry("c1", "too broad", "memory")]));
        fs::write(store.join("review.json"), &queue_before).unwrap();

        // Revision cites a unit that does not exist → citation defect.
        let bad = CrystalClaim {
            id: String::new(),
            claim: "narrowed".into(),
            theme: "memory".into(),
            citations: vec![Citation {
                case_id: "m18-01".into(),
                unit_id: "u-nope".into(),
                quote: "nope".into(),
                claimed_line: None,
            }],
            caveat: None,
        };
        let decisions = tmp.path().join("decisions.json");
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "rewrite", "revisions": [bad], "note": "typo" }
            ]))
            .unwrap(),
        )
        .unwrap();

        let err = super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect_err("citation defect must fail loud before any model call or write");
        assert!(matches!(err, crate::CliError::Gate(_)), "got {err:?}");
        assert_eq!(
            fs::read_to_string(store.join("review.json")).unwrap(),
            queue_before,
            "queue untouched on defective revision"
        );
        assert!(!store.join("ledger.jsonl").exists(), "no durable write happened");
    }

    #[test]
    fn apply_demote_and_defer_mutate_the_queue_without_model_or_ledger() {
        use ovp_domain::crystal::{DeferTrigger, ReviewLane};
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            review_json(json!([
                caveated_entry("c1", "impl detail, true but narrow", "agents"),
                caveated_entry("c2", "needs more sources someday", "memory"),
            ])),
        )
        .unwrap();
        // defer_until needs the read model for its baseline — build a minimal
        // empty-vault index (0 packs → baseline 0).
        let model = ovp_index::build_index(&vault, "2026-07-07", None).unwrap();
        ovp_index::write_index(&vault, &model).unwrap();

        let decisions = tmp.path().join("decisions.json");
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "demote_to_source_insight", "revisions": [], "note": "reader material" },
                { "claim_id": "c2", "action": "defer_until", "revisions": [],
                  "defer": { "trigger": "corpus_grows_by", "n": 5 }, "note": "wait for corpus" }
            ]))
            .unwrap(),
        )
        .unwrap();

        super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect("queue-op session applies with no model calls");

        assert!(!store.join("ledger.jsonl").exists(), "no durable write");
        let queue = crate::commands::crystal_write::read_review_queue(&store.join("review.json")).unwrap();
        let c1 = queue.iter().find(|e| e.claim_id == "c1").expect("c1 stays");
        assert_eq!(c1.lane, ReviewLane::SourceInsight, "demoted in place");
        let c2 = queue.iter().find(|e| e.claim_id == "c2").expect("c2 stays");
        let d = c2.defer.expect("defer stamped");
        assert_eq!(d.trigger, DeferTrigger::CorpusGrowsBy);
        assert_eq!((d.n, d.baseline), (5, 0), "baseline from the (empty) read model");

        // prepare skips the unfired defer AND the demoted insight.
        let out = tmp.path().join("session");
        super::run_prepare(super::CrystalReviewSessionPrepareArgs {
            vault_root: vault.clone(),
            batch: 20,
            out: out.clone(),
            suggest: false,
        })
        .unwrap();
        let ids = fs::read_to_string(out.join("selected-claim-ids.txt")).unwrap();
        assert!(ids.trim().is_empty(), "nothing left for the human queue: {ids}");
    }

    /// Read-model fixture: packs by `(pack_dir, title)`, everything else empty.
    fn model_with_packs(packs: &[(&str, &str)]) -> ovp_index::model::IndexModel {
        ovp_index::model::IndexModel {
            schema: "test".into(),
            date: "2026-07-07".into(),
            built_at: None,
            run_id: None,
            totals: Default::default(),
            sources: vec![],
            packs: packs
                .iter()
                .map(|(dir, title)| ovp_index::model::PackRow {
                    pack_dir: (*dir).into(),
                    title: (*title).into(),
                    date: None,
                    units: 0,
                    cards: 0,
                    json_repaired: false,
                    card_titles: vec![],
                    source_sha256: None,
                })
                .collect(),
            claims: vec![],
            runs: vec![],
            ops: Default::default(),
        }
    }

    fn entry_citing(theme: &str, cited: &[&str]) -> ovp_domain::crystal::ReviewEntry {
        let citations: Vec<_> = cited
            .iter()
            .map(|c| json!({"case_id": c, "unit_id": "u-0", "quote": "q"}))
            .collect();
        serde_json::from_value(json!({
            "claim_id": "c1", "claim": "x", "theme": theme,
            "final_class": "caveated", "strength": "supported",
            "evidence_sufficient": true, "rationale": "r",
            "citations": citations
        }))
        .unwrap()
    }

    fn themes_with(
        packs: &[(&str, i64)],
        label: &str,
    ) -> ovp_domain::crystal::themes::ThemesFile {
        use ovp_domain::crystal::themes::{
            LabelsProvenance, ThemeCommunity, ThemeParams, ThemesFile,
        };
        ThemesFile {
            schema: "ovp.themes/v1".into(),
            model: "test".into(),
            params: ThemeParams {
                k: 10,
                cosine_threshold: 0.5,
                resolution: 1.5,
                seed: 42,
                text_prefix: String::new(),
                head_chars: 1500,
            },
            generated_from: "x".into(),
            packs: packs.iter().map(|(id, c)| ((*id).to_string(), *c)).collect(),
            communities: vec![ThemeCommunity {
                id: 0,
                label: label.into(),
                label_zh: label.into(),
                keywords: vec![],
                size: packs.iter().filter(|(_, c)| *c == 0).count(),
            }],
            labels_provenance: LabelsProvenance::Keyword,
        }
    }

    #[test]
    fn unclassified_defer_fires_when_new_packs_join_the_pool() {
        use ovp_domain::crystal::{defer_fired, DeferState, DeferTrigger};
        // The entry's cited pack is noise (community -1); its display theme is
        // the label-less "Unclassified" — which matches NO community label,
        // the exact stuck-at-0 failure of label-keyed membership.
        let themes = themes_with(&[("m18-01", -1), ("m18-02", 0)], "Agent memory");
        let e = entry_citing("Unclassified", &["m18-01"]);
        let before = super::theme_pack_count(
            &model_with_packs(&[("R/m18-01", "A"), ("R/m18-02", "B")]),
            Some(&themes),
            &e,
        );
        assert_eq!(before, 1, "only the noise pack is in the entry's pool");
        // A NEW pack (absent from themes.packs) lands in the unclassified pool.
        let after = super::theme_pack_count(
            &model_with_packs(&[("R/m18-01", "A"), ("R/m18-02", "B"), ("R/m18-09", "New")]),
            Some(&themes),
            &e,
        );
        assert_eq!(after, 2);
        let d = DeferState { trigger: DeferTrigger::NewSourcesInTheme, n: 1, baseline: before };
        assert!(defer_fired(&d, after), "unclassified-pool growth must fire the defer");
    }

    #[test]
    fn relabeling_or_refresh_does_not_orphan_a_defer() {
        // Membership is keyed by community id via the cited packs, so neither
        // the entry's stored theme text nor the community's display label
        // participates — an LLM relabel cannot orphan the defer.
        let themes = themes_with(&[("m18-01", 0), ("m18-02", 0)], "Agent memory systems");
        let model = model_with_packs(&[("R/m18-01", "A"), ("R/m18-02", "B")]);
        // Stored theme text is the OLD label; entry cites a community-0 pack.
        let e = entry_citing("Agent memory systems", &["m18-01"]);
        assert_eq!(super::theme_pack_count(&model, Some(&themes), &e), 2);
        // `--client live` renames the community: count unchanged.
        let mut relabeled = themes.clone();
        relabeled.communities[0].label = "记忆与上下文".into();
        relabeled.communities[0].label_zh = "记忆与上下文".into();
        assert_eq!(super::theme_pack_count(&model, Some(&relabeled), &e), 2);
        // A themes refresh adds a pack to the same community: count grows —
        // the defer can fire even though every label changed.
        relabeled.packs.insert("m18-03".into(), 0);
        let grown = model_with_packs(&[("R/m18-01", "A"), ("R/m18-02", "B"), ("R/m18-03", "C")]);
        assert_eq!(super::theme_pack_count(&grown, Some(&relabeled), &e), 3);
        // Same for a fallback-labeled batch theme ("Unthemed batch 1"):
        // membership still resolves via the cited pack's community.
        let batch_entry = entry_citing("Unthemed batch 1", &["m18-01"]);
        assert_eq!(super::theme_pack_count(&grown, Some(&relabeled), &batch_entry), 3);
    }

    #[test]
    fn no_themes_fallback_counts_cited_packs_and_title_matches() {
        let model = model_with_packs(&[
            ("R/m18-01", "Unrelated title"),
            ("R/m18-02", "Agent MEMORY design"),
            ("R/m18-03", "Something else"),
        ]);
        // Cited pack + case-insensitive title⊇theme containment (legacy).
        let e = entry_citing("memory", &["m18-01"]);
        assert_eq!(super::theme_pack_count(&model, None, &e), 2);
        // Pre-M35 entry without citations: pure legacy title containment.
        let legacy = entry_citing("memory", &[]);
        assert_eq!(super::theme_pack_count(&model, None, &legacy), 1);
        // A cited entry with NO themes.json never gets stuck below its own
        // citation count, even for fallback batch labels.
        let batch = entry_citing("Unthemed batch 1", &["m18-01", "m18-02"]);
        assert_eq!(super::theme_pack_count(&model, None, &batch), 2);
    }

    #[test]
    fn mixed_file_with_defective_revision_persists_no_queue_ops() {
        use ovp_domain::crystal::ReviewLane;
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        // A real pack so the grounding index builds; the revision cites a
        // NONEXISTENT unit → pre-lint must fail before ANY mutation.
        use ovp_domain::source_doc::SourceDoc;
        use ovp_domain::units::{validate, Unit};
        let reader = vault.join("40-Resources/Reader");
        let case_dir = reader.join("m18-01");
        fs::create_dir_all(&case_dir).unwrap();
        let raw = vec![json!({
            "kind": "assertion", "text": "t0", "evidence_ref": "p001",
            "evidence_quote": "Memory is scarce.",
            "attribution": "author", "modality": "asserted", "arguments": []
        })];
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![], "Memory is scarce."),
        );
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        fs::write(
            case_dir.join("units.accepted.json"),
            serde_json::to_string_pretty(&units).unwrap(),
        )
        .unwrap();
        fs::write(case_dir.join("reader.md"), "# T\n\nbody\n").unwrap();
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        let queue_before = review_json(json!([
            caveated_entry("c1", "to demote", "agents"),
            caveated_entry("c2", "to rewrite badly", "memory"),
        ]));
        fs::write(store.join("review.json"), &queue_before).unwrap();

        let decisions = tmp.path().join("decisions.json");
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "demote_to_source_insight", "revisions": [], "note": "" },
                { "claim_id": "c2", "action": "narrow", "revisions": [{
                    "id": "", "claim": "bad", "theme": "memory",
                    "citations": [{"case_id": "m18-01", "unit_id": "u-nope", "quote": "nope"}]
                }], "note": "" }
            ]))
            .unwrap(),
        )
        .unwrap();

        let err = super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect_err("defective revision fails the whole file");
        assert!(matches!(err, crate::CliError::Gate(_)), "got {err:?}");
        // codex P2: "nothing was changed" must be literally true — the demote
        // in the same file must NOT have been persisted.
        let queue = crate::commands::crystal_write::read_review_queue(&store.join("review.json")).unwrap();
        let c1 = queue.iter().find(|e| e.claim_id == "c1").unwrap();
        assert_eq!(c1.lane, ReviewLane::Review, "queue op not persisted on failed validation");
    }

    #[test]
    fn legacy_crystal_review_rejects_queue_state_actions() {
        let tmp = tempfile::tempdir().unwrap();
        let cand = tmp.path().join("candidate.json");
        fs::write(
            &cand,
            serde_json::to_string_pretty(&json!({"items":[{
                "id":"c1","claim":"x","theme":"t",
                "citations":[{"case_id":"a","unit_id":"u","quote":"q"}]}]}))
            .unwrap(),
        )
        .unwrap();
        let dec = tmp.path().join("decisions.json");
        fs::write(
            &dec,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "demote_to_source_insight", "revisions": [], "note": "" }
            ]))
            .unwrap(),
        )
        .unwrap();
        let out = tmp.path().join("revised.json");
        let err = crate::commands::crystal_review::run(crate::commands::crystal_review::CrystalReviewArgs {
            candidate: cand,
            decisions: dec,
            out: out.clone(),
        })
        .expect_err("queue-state action must be refused by the candidate workbench");
        assert!(matches!(err, crate::CliError::Gate(_)), "got {err:?}");
        assert!(!out.exists(), "nothing written");
    }

    #[test]
    fn suggest_lists_units_only_from_uncited_cases() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        // Entry cites m18-01 and mentions "scarce memory budget".
        fs::write(
            store.join("review.json"),
            review_json(json!([{
                "claim_id": "c1",
                "claim": "Memory is a scarce budget for agent systems.",
                "theme": "memory",
                "final_class": "caveated",
                "strength": "supported",
                "evidence_sufficient": true,
                "rationale": "single source",
                "lane": "review",
                "citations": [
                    {"case_id": "m18-01", "unit_id": "u-0", "quote": "Memory is scarce."}
                ]
            }])),
        )
        .unwrap();
        // Hand-built evidence sidecar: one unit in the cited case, one in another.
        let ev = ovp_index::EvidenceModel {
            schema: ovp_index::evidence::EVIDENCE_SCHEMA.into(),
            date: "2026-07-07".into(),
            cards: vec![],
            units: vec![
                ovp_index::evidence::UnitEvidenceRow {
                    id: "u:m18-01:u-0".into(),
                    pack_dir: "m18-01".into(),
                    source_sha256: None,
                    source_title: "Working memory systems".into(),
                    unit_id: "u-0".into(),
                    text: "Memory is a scarce budget.".into(),
                    quote: "Memory is scarce.".into(),
                    line: Some(3),
                    attribution: "author".into(),
                    modality: "asserted".into(),
                },
                ovp_index::evidence::UnitEvidenceRow {
                    id: "u:m18-02:u-1".into(),
                    pack_dir: "m18-02".into(),
                    source_sha256: None,
                    source_title: "Context budgets".into(),
                    unit_id: "u-1".into(),
                    text: "Context windows are a scarce memory budget for agents.".into(),
                    quote: "Context windows are a scarce budget.".into(),
                    line: Some(7),
                    attribution: "author".into(),
                    modality: "asserted".into(),
                },
            ],
            warnings: vec![],
        };
        ovp_index::write_evidence(&vault, &ev).unwrap();

        let out = tmp.path().join("session");
        super::run_prepare(super::CrystalReviewSessionPrepareArgs {
            vault_root: vault.clone(),
            batch: 20,
            out: out.clone(),
            suggest: true,
        })
        .unwrap();
        let md = fs::read_to_string(out.join("backfill-candidates.md")).unwrap();
        assert!(md.contains("m18-02"), "uncited case suggested: {md}");
        assert!(!md.contains("u:m18-01"), "cited case excluded");
        let rows: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(out.join("backfill-candidates.json")).unwrap()).unwrap();
        assert_eq!(rows.as_array().unwrap().len(), 1);
        assert_eq!(rows[0]["case_id"], "m18-02");
    }

    #[test]
    fn apply_rewrite_regates_to_durable_and_retires_the_old_entry() {
        use ovp_domain::crystal::synth::{collect_catalog, strength_request};
        use ovp_domain::crystal::{Citation, CrystalCandidate, CrystalClaim};
        use ovp_domain::source_doc::SourceDoc;
        use ovp_domain::units::{validate, Unit};

        fn write_pack(dir: &std::path::Path, case_id: &str, title: &str, body: &str, quotes: &[&str]) -> String {
            let case_dir = dir.join(case_id);
            fs::create_dir_all(&case_dir).unwrap();
            let raw: Vec<_> = quotes
                .iter()
                .enumerate()
                .map(|(i, q)| {
                    json!({
                        "kind": "assertion", "text": format!("t{i}"), "evidence_ref": "p001",
                        "evidence_quote": q, "attribution": "author", "modality": "asserted", "arguments": []
                    })
                })
                .collect();
            let ex = validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![], body));
            let units: Vec<Unit> = ex.accepted().cloned().collect();
            let uid = units[0].id.clone();
            fs::write(
                case_dir.join("units.accepted.json"),
                serde_json::to_string_pretty(&units).unwrap(),
            )
            .unwrap();
            fs::write(case_dir.join("reader.md"), format!("# {title}\n\nbody\n")).unwrap();
            uid
        }

        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let reader = vault.join("40-Resources/Reader");
        let u1 = write_pack(&reader, "m18-01", "Working memory systems",
            "Memory is scarce working memory in systems. It must be curated.",
            &["Memory is scarce working memory in systems."]);
        let u2 = write_pack(&reader, "m18-02", "Context and retrieval",
            "Context windows are a scarce budget for retrieval.",
            &["Context windows are a scarce budget for retrieval."]);

        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            review_json(json!([caveated_entry("c1", "memory is everything", "memory")])),
        )
        .unwrap();

        // The human rewrite: narrower claim, verbatim cross-source citations.
        let revised_claim = CrystalClaim {
            id: "c1r".into(), // apply_decisions derives this from an empty revision id
            claim: "Memory and context are treated as a scarce budget across systems.".into(),
            theme: "memory".into(),
            citations: vec![
                Citation { case_id: "m18-01".into(), unit_id: u1, quote: "scarce working memory in systems".into(), claimed_line: None },
                Citation { case_id: "m18-02".into(), unit_id: u2, quote: "scarce budget for retrieval".into(), claimed_line: None },
            ],
            caveat: None,
        };
        let decisions = tmp.path().join("decisions.json");
        let mut rev_json = serde_json::to_value(&revised_claim).unwrap();
        rev_json["id"] = json!(""); // template users leave the id empty
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "rewrite", "revisions": [rev_json], "note": "narrowed" }
            ]))
            .unwrap(),
        )
        .unwrap();

        // Seed the strength cassette for the re-gate call (replay = zero network).
        let catalog = collect_catalog(&reader).unwrap();
        let req = strength_request(
            &CrystalCandidate { items: vec![revised_claim.clone()] },
            &catalog,
        );
        let cache = vault.join(".ovp/cassettes/crystal");
        let ns = req.cache_namespace.as_deref().unwrap();
        let dir = cache.join(ns);
        fs::create_dir_all(&dir).unwrap();
        let reply = ovp_llm::ModelReply {
            model: "canned".into(),
            text: r#"[{"claim_id":"c1r","strength":"supported","evidence_sufficient":true,"rationale":"both quotes state a scarce budget"}]"#.into(),
            stop_reason: ovp_llm::StopReason::EndTurn,
            usage: ovp_llm::Usage { input_tokens: 1, output_tokens: 1 },
        };
        fs::write(
            dir.join(format!("{}.json", ovp_llm::request_key(&req))),
            serde_json::to_string_pretty(&reply).unwrap(),
        )
        .unwrap();

        super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: Some("Test Crystal".into()),
            refresh: false,
            date: None,
        })
        .expect("rewrite session re-gates and writes");

        let ledger = fs::read_to_string(store.join("ledger.jsonl")).unwrap();
        assert_eq!(
            ledger.lines().filter(|l| !l.trim().is_empty()).count(),
            1,
            "one revised claim written durable"
        );
        assert!(ledger.contains("c1r"), "{ledger}");
        let queue = fs::read_to_string(store.join("review.json")).unwrap();
        assert!(!queue.contains("\"c1\""), "reviewed entry retired: {queue}");
    }
}
