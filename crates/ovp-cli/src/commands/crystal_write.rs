//! `crystal-write` — M23 minimal durable Crystal store writer. Runs the FULL
//! pre-write gate (citation linter + provenance + claim-strength) and, only if the
//! candidate is durable-eligible, appends the `Durable` claims to an append-only
//! ledger (idempotent by `claim_key`), records the `caveated`/`reject` claims in a
//! review file (NEVER durable truth), and renders a human-readable `crystal.md`.
//! No model call, no vault write, no graph.

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

fn build_index(packs_dir: &std::path::Path) -> Result<GroundingIndex, CliError> {
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

fn read_ledger(path: &std::path::Path) -> Result<Vec<StoreEvent>, CliError> {
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

    let index = build_index(&args.packs_dir)?;
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
    let run_id = args.run_id.clone().unwrap_or_else(|| {
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
                });
            }
        }
    }

    // --- Append-only, idempotent write. ---
    std::fs::create_dir_all(&args.store)
        .map_err(|e| CliError::Io(format!("creating store {}: {e}", args.store.display())))?;
    let ledger_path = args.store.join("ledger.jsonl");
    let existing = read_ledger(&ledger_path)?;
    let active = active_keys(&existing);
    let mut events = existing.clone();
    let mut appended = 0usize;
    let mut appended_lines = String::new();
    for r in &new_records {
        if active.contains(&r.claim_key) {
            continue; // idempotent: already active, no duplicate append
        }
        let ev = StoreEvent { op: StoreOp::Write, record: r.clone(), supersedes: None, reason: None };
        appended_lines.push_str(&serde_json::to_string(&ev).map_err(|e| CliError::Io(e.to_string()))?);
        appended_lines.push('\n');
        events.push(ev);
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
    let header = CrystalHeader {
        title: args.title.clone().unwrap_or_else(|| "Crystal".into()),
        scope: args.scope.clone().unwrap_or_default(),
        not_claiming: args.not_claiming.clone().unwrap_or_default(),
    };
    let md = render_crystal_md(&header, &active_now, &review);
    std::fs::write(args.store.join("crystal.md"), md)
        .map_err(|e| CliError::Io(format!("writing crystal.md: {e}")))?;
    std::fs::write(
        args.store.join("review.json"),
        serde_json::to_string_pretty(&serde_json::json!({"review": review})).unwrap() + "\n",
    )
    .map_err(|e| CliError::Io(format!("writing review.json: {e}")))?;

    println!("crystal-write: run_id={run_id}");
    println!(
        "  eligible: {} durable claim(s) considered, {appended} newly appended ({} already active)",
        new_records.len(),
        new_records.len() - appended
    );
    println!("  store: {} active durable claim(s) total", active_now.len());
    println!("  review (NOT durable): {} caveated/reject claim(s)", review.len());
    println!("  ledger: {}", ledger_path.display());
    println!("  view:   {}", args.store.join("crystal.md").display());
    Ok(())
}
