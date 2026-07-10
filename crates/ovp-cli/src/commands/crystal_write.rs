//! `crystal-write` — M23 minimal durable Crystal store writer. Runs the FULL
//! pre-write gate (citation linter + provenance + claim-strength) and, only if the
//! candidate is durable-eligible, appends the `Durable` claims to an append-only
//! ledger (idempotent by `claim_key`), records the `caveated`/`reject` claims in a
//! review file (NEVER durable truth), and renders a human-readable `crystal.md`.
//! No model call, no vault write, no graph.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use ovp_domain::crystal::{
    active_keys, build_durable_record, default_run_id, fold_ledger, lint_candidate,
    render_crystal_md, score_candidate, strength_coverage, ClaimStrengthVerdict, CrystalCandidate,
    CrystalHeader, FinalClass, GroundingIndex, ReviewEntry, StoreEvent, StoreOp,
};
use ovp_domain::units::Unit;

use crate::CliError;

pub struct CrystalWriteArgs {
    pub candidate: PathBuf,
    pub packs_dir: PathBuf,
    /// Claim-strength verdicts (REQUIRED for a durable write — a full pre-write run).
    pub strength: PathBuf,
    /// Durable store directory (append-only `ledger.jsonl` + rendered views).
    pub store: PathBuf,
    /// Optional explicit run id; defaults to a deterministic hash of the written keys.
    pub run_id: Option<String>,
    /// Crystal view header (scope/policy framing for crystal.md).
    pub title: Option<String>,
    pub scope: Option<String>,
    pub not_claiming: Option<String>,
}

/// In-memory inputs for a durable write — the shared core `crystal-write` and
/// `crystal-synth` both call, so neither can drift from the frozen gate/store
/// logic. The candidate, verdicts, and index are already loaded (no file paths,
/// no re-parsing); the header/store/run_id carry the write framing.
pub struct WriteInputs {
    pub candidate: CrystalCandidate,
    pub verdicts: Vec<ClaimStrengthVerdict>,
    pub index: GroundingIndex,
    pub store: PathBuf,
    pub run_id: Option<String>,
    pub header: CrystalHeader,
    pub processed_review_ids: BTreeSet<String>,
}

/// What a durable write produced (returned so callers can print their own summary).
pub struct WriteOutcome {
    pub run_id: String,
    /// Durable-class records considered this run (some may already be active).
    pub considered: usize,
    /// Newly-appended ledger lines this run.
    pub appended: usize,
    /// Total active durable claims in the store after the write.
    pub active_total: usize,
    /// Caveated/reject claims routed to review (never durable).
    pub review: usize,
    pub ledger_path: PathBuf,
    pub crystal_md_path: PathBuf,
}

pub fn merge_review_queue(
    existing: Vec<ReviewEntry>,
    processed_ids: &BTreeSet<String>,
    new_entries: Vec<ReviewEntry>,
) -> Vec<ReviewEntry> {
    let mut by_id: BTreeMap<String, ReviewEntry> = BTreeMap::new();
    for entry in existing {
        if !processed_ids.contains(&entry.claim_id) {
            by_id.insert(entry.claim_id.clone(), entry);
        }
    }
    for entry in new_entries {
        by_id.insert(entry.claim_id.clone(), entry);
    }
    let mut merged: Vec<_> = by_id.into_values().collect();
    merged.sort_by(|a, b| {
        (a.theme.as_str(), a.claim_id.as_str()).cmp(&(b.theme.as_str(), b.claim_id.as_str()))
    });
    merged
}

/// Build the grounding index by reading `<packs_dir>/<case>/units.accepted.json`.
/// Shared by `crystal-lint`, `crystal-write`, and `crystal-synth` so all three
/// resolve citations against exactly the same keying (directory name == case_id).
pub fn build_grounding_index(packs_dir: &std::path::Path) -> Result<GroundingIndex, CliError> {
    let mut index = GroundingIndex::new();
    let entries = std::fs::read_dir(packs_dir)
        .map_err(|e| CliError::Io(format!("reading packs dir {}: {e}", packs_dir.display())))?;
    for entry in entries.flatten() {
        if !entry.path().is_dir() {
            continue;
        }
        let units_path = entry.path().join("units.accepted.json");
        if !units_path.exists() {
            continue;
        }
        let text = std::fs::read_to_string(&units_path)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", units_path.display())))?;
        let units: Vec<Unit> = serde_json::from_str(&text)
            .map_err(|e| CliError::Io(format!("parsing {}: {e}", units_path.display())))?;
        index.insert(entry.file_name().to_string_lossy().to_string(), units);
    }
    if index.is_empty() {
        return Err(CliError::Io(format!("no units.accepted.json under {}", packs_dir.display())));
    }
    Ok(index)
}

pub(crate) fn read_ledger(path: &std::path::Path) -> Result<Vec<StoreEvent>, CliError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| CliError::Io(format!("reading ledger {}: {e}", path.display())))?;
    let mut events = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let ev: StoreEvent = serde_json::from_str(line)
            .map_err(|e| CliError::Io(format!("ledger {} line {}: {e}", path.display(), i + 1)))?;
        events.push(ev);
    }
    Ok(events)
}

pub fn read_review_queue(path: &std::path::Path) -> Result<Vec<ReviewEntry>, CliError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| CliError::Io(format!("reading review queue {}: {e}", path.display())))?;
    let value: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing review queue {}: {e}", path.display())))?;
    let entries = value
        .get("review")
        .cloned()
        .unwrap_or_else(|| serde_json::Value::Array(Vec::new()));
    serde_json::from_value(entries)
        .map_err(|e| CliError::Io(format!("parsing review entries {}: {e}", path.display())))
}

pub(crate) fn write_review_queue(path: &std::path::Path, review: &[ReviewEntry]) -> Result<(), CliError> {
    write_review_queue_with_collapsed(path, review, &[])
}

/// Write the queue plus the record of near-duplicate collapses performed this
/// write (additive `collapsed` key; `read_review_queue` only reads `review`).
pub(crate) fn write_review_queue_with_collapsed(
    path: &std::path::Path,
    review: &[ReviewEntry],
    collapsed: &[ovp_domain::crystal::CollapsedDuplicate],
) -> Result<(), CliError> {
    let mut body = serde_json::json!({ "review": review });
    if !collapsed.is_empty() {
        body["collapsed"] = serde_json::json!(collapsed);
    }
    let body = serde_json::to_string_pretty(&body)
        .map_err(|e| CliError::Io(format!("serializing review queue: {e}")))?;
    std::fs::write(path, body + "\n")
        .map_err(|e| CliError::Io(format!("writing review.json: {e}")))
}

pub fn run(args: CrystalWriteArgs) -> Result<(), CliError> {
    let candidate: CrystalCandidate = serde_json::from_str(
        &std::fs::read_to_string(&args.candidate)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", args.candidate.display())))?,
    )
    .map_err(|e| CliError::Io(format!("parsing candidate: {e}")))?;
    let verdicts: Vec<ClaimStrengthVerdict> = serde_json::from_str(
        &std::fs::read_to_string(&args.strength)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", args.strength.display())))?,
    )
    .map_err(|e| CliError::Io(format!("parsing strength: {e}")))?;

    let index = build_grounding_index(&args.packs_dir)?;
    let header = CrystalHeader {
        title: args.title.clone().unwrap_or_else(|| "Crystal".into()),
        scope: args.scope.clone().unwrap_or_default(),
        not_claiming: args.not_claiming.clone().unwrap_or_default(),
    };
    let out = write_durable(WriteInputs {
        candidate,
        verdicts,
        index,
        store: args.store.clone(),
        run_id: args.run_id.clone(),
        header,
        processed_review_ids: BTreeSet::new(),
    })?;

    println!("crystal-write: run_id={}", out.run_id);
    println!(
        "  eligible: {} durable claim(s) considered, {} newly appended ({} already active)",
        out.considered,
        out.appended,
        out.considered - out.appended
    );
    println!("  store: {} active durable claim(s) total", out.active_total);
    println!("  review (NOT durable): {} caveated/reject claim(s)", out.review);
    println!("  ledger: {}", out.ledger_path.display());
    println!("  view:   {}", out.crystal_md_path.display());
    Ok(())
}

/// Shared durable-write core: run the FULL pre-write gate over already-loaded
/// inputs and, only if durable-eligible, append `Durable` claims to the store
/// (idempotent by `claim_key`), record caveated/reject in `review.json`, and
/// render `crystal.md`. Refuses loudly on any gate gap. Reused by `crystal-synth`.
pub fn write_durable(inputs: WriteInputs) -> Result<WriteOutcome, CliError> {
    let WriteInputs {
        candidate,
        verdicts,
        index,
        store,
        run_id: run_id_override,
        header,
        processed_review_ids,
    } = inputs;
    let report = lint_candidate(&candidate, &index);
    let scores = score_candidate(&report);
    let claim_ids: Vec<String> = candidate.items.iter().map(|c| c.id.clone()).collect();
    let coverage = strength_coverage(&claim_ids, &verdicts);

    // --- Durable-write eligibility: refuse outright, write nothing, on any gap. ---
    let mut blockers: Vec<String> = Vec::new();
    if !coverage.complete() {
        blockers.push(format!(
            "incomplete strength verdicts (missing={:?} duplicate={:?} unknown={:?})",
            coverage.missing, coverage.duplicate, coverage.unknown
        ));
    }
    if report.n_with_defects > 0 {
        blockers.push(format!("{} claim(s) with citation defects (quarantine)", report.n_with_defects));
    }
    // final routing per claim
    let final_of = |claim_id: &str| -> FinalClass {
        let score = scores.iter().find(|s| s.claim_id == claim_id).map(|s| s.class);
        let v = verdicts.iter().find(|v| v.claim_id == claim_id);
        match score {
            Some(c) => ovp_domain::crystal::final_routing(c, v),
            None => FinalClass::Reject,
        }
    };
    let routes: Vec<(String, FinalClass)> =
        candidate.items.iter().map(|c| (c.id.clone(), final_of(&c.id))).collect();
    let n_reject = routes.iter().filter(|(_, f)| *f == FinalClass::Reject).count();
    if n_reject > 0 {
        blockers.push(format!("{n_reject} claim(s) routed to reject"));
    }
    if !blockers.is_empty() {
        return Err(CliError::Gate(format!(
            "durable write REFUSED — {}. Nothing written.",
            blockers.join("; ")
        )));
    }

    // --- Assemble durable records for the Durable-class claims. ---
    let run_id = run_id_override.clone().unwrap_or_else(|| {
        let durable_ids: Vec<String> = candidate
            .items
            .iter()
            .filter(|c| final_of(&c.id) == FinalClass::Durable)
            .map(|c| c.id.clone())
            .collect();
        default_run_id(&durable_ids)
    });

    let lint_of = |id: &str| report.claims.iter().find(|c| c.claim_id == id).unwrap();
    let score_of = |id: &str| scores.iter().find(|s| s.claim_id == id).unwrap();
    let verdict_of = |id: &str| verdicts.iter().find(|v| v.claim_id == id).unwrap();

    let mut new_records = Vec::new();
    let mut review: Vec<ReviewEntry> = Vec::new();
    for item in &candidate.items {
        match final_of(&item.id) {
            FinalClass::Durable => {
                new_records.push(build_durable_record(
                    item,
                    lint_of(&item.id),
                    score_of(&item.id),
                    verdict_of(&item.id),
                    FinalClass::Durable,
                    &run_id,
                ));
            }
            fc => {
                let v = verdict_of(&item.id);
                review.push(ReviewEntry {
                    claim_id: item.id.clone(),
                    claim: item.claim.clone(),
                    theme: item.theme.clone(),
                    final_class: fc,
                    strength: v.strength,
                    evidence_sufficient: v.evidence_sufficient,
                    rationale: v.rationale.clone(),
                    citations: item.citations.clone(),
                    lane: ovp_domain::crystal::review_lane(
                        lint_of(&item.id).distinct_sources,
                        Some(v),
                    ),
                    defer: None,
                });
            }
        }
    }

    // --- Append-only, idempotent write. ---
    std::fs::create_dir_all(&store)
        .map_err(|e| CliError::Io(format!("creating store {}: {e}", store.display())))?;
    let ledger_path = store.join("ledger.jsonl");
    let existing = read_ledger(&ledger_path)?;
    // Track keys already active AND keys appended earlier in THIS batch, so two
    // records with the same claim_key in one run (e.g. the synth model emitting
    // a duplicate claim) append only once — idempotency holds within a batch too.
    let mut active = active_keys(&existing);
    let mut events = existing.clone();
    let mut appended = 0usize;
    let mut appended_lines = String::new();
    for r in &new_records {
        if active.contains(&r.claim_key) {
            continue; // idempotent: already active (or already appended this run)
        }
        let ev = StoreEvent { op: StoreOp::Write, record: r.clone(), supersedes: None, reason: None };
        appended_lines.push_str(&serde_json::to_string(&ev).map_err(|e| CliError::Io(e.to_string()))?);
        appended_lines.push('\n');
        events.push(ev);
        active.insert(r.claim_key.clone());
        appended += 1;
    }
    if !appended_lines.is_empty() {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&ledger_path)
            .map_err(|e| CliError::Io(format!("opening ledger {}: {e}", ledger_path.display())))?;
        f.write_all(appended_lines.as_bytes())
            .map_err(|e| CliError::Io(format!("appending ledger: {e}")))?;
    }

    // --- Render current state + review. ---
    let state = fold_ledger(&events);
    let active_now: Vec<_> = state
        .iter()
        .filter(|r| r.status == ovp_domain::crystal::CrystalStatus::Active)
        .cloned()
        .collect();
    let review_path = store.join("review.json");
    let existing_review = read_review_queue(&review_path)?;
    let merged_review = merge_review_queue(existing_review, &processed_review_ids, review.clone());
    // Near-duplicate collapse BEFORE the queue is written (decidable signals
    // only; recorded in review.json, never silent). Pre-M35 entries without
    // citations can never match and are left alone.
    let (merged_review, collapsed) =
        ovp_domain::crystal::collapse_review_duplicates(merged_review);
    for c in &collapsed {
        println!("  review-queue: collapsed near-duplicate {} into {} ({})", c.dropped, c.kept, c.reason);
    }

    let md = render_crystal_md(&header, &active_now, &merged_review);
    let crystal_md_path = store.join("crystal.md");
    std::fs::write(&crystal_md_path, md)
        .map_err(|e| CliError::Io(format!("writing crystal.md: {e}")))?;
    write_review_queue_with_collapsed(&review_path, &merged_review, &collapsed)?;

    Ok(WriteOutcome {
        run_id,
        considered: new_records.len(),
        appended,
        active_total: active_now.len(),
        review: review.len(),
        ledger_path,
        crystal_md_path,
    })
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use ovp_domain::crystal::{FinalClass, StrengthClass};

    use super::{merge_review_queue, ReviewEntry};

    fn entry(id: &str, theme: &str) -> ReviewEntry {
        ReviewEntry {
            claim_id: id.into(),
            claim: format!("claim {id}"),
            theme: theme.into(),
            final_class: FinalClass::Caveated,
            strength: StrengthClass::Supported,
            evidence_sufficient: true,
            rationale: format!("rationale {id}"),
            citations: Vec::new(),
            lane: Default::default(),
            defer: None,
        }
    }

    #[test]
    fn crystal_review_preserves_unprocessed_queue() {
        let existing = vec![entry("a", "t1"), entry("b", "t1"), entry("c", "t2")];
        let processed = BTreeSet::from(["a".to_string()]);
        let new_entries = vec![entry("d", "t3")];

        let merged = merge_review_queue(existing, &processed, new_entries);
        let ids: Vec<_> = merged.iter().map(|entry| entry.claim_id.as_str()).collect();

        assert_eq!(ids, vec!["b", "c", "d"]);
    }

    #[test]
    fn crystal_review_merge_prefers_new_entries_on_duplicate_ids() {
        let existing = vec![entry("a", "old")];
        let processed = BTreeSet::new();
        let new_entries = vec![entry("a", "new")];

        let merged = merge_review_queue(existing, &processed, new_entries);

        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].claim_id, "a");
        assert_eq!(merged[0].theme, "new");
    }
}
