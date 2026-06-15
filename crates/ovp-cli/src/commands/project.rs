//! `project` — Projection Lanes view: list claims by final routing lane
//! (durable / review / reject) and surface the human review queue.
//!
//! This is a READ-ONLY view over the Crystal ledger and review state. It does
//! NOT perform vault projection (writing notes) — that is Phase 2b.

use std::path::PathBuf;

use ovp_domain::crystal::{
    fold_ledger, CrystalStatus, DurableRecord, FinalClass, ReviewEntry, StoreEvent,
};

use crate::CliError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LaneFilter {
    All,
    Durable,
    Review,
}

pub struct ProjectArgs {
    pub vault_root: PathBuf,
    pub lane: LaneFilter,
    pub verbose: bool,
}

pub fn run(args: ProjectArgs) -> Result<(), CliError> {
    let store_dir = args.vault_root.join(".ovp/crystal");
    if !store_dir.exists() {
        println!("project: no crystal store at {}", store_dir.display());
        println!("  (run `daily` or `crystal-write` first to populate the ledger)");
        return Ok(());
    }

    let ledger_path = store_dir.join("ledger.jsonl");
    let review_path = store_dir.join("review.json");

    let ledger_events = read_ledger(&ledger_path)?;
    let all_records = fold_ledger(&ledger_events);
    let active: Vec<&DurableRecord> = all_records
        .iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect();
    let review_entries = read_review(&review_path)?;

    match args.lane {
        LaneFilter::All => {
            print_summary(&active, &review_entries);
            println!();
            print_durable_lane(&active, args.verbose);
            println!();
            print_review_lane(&review_entries, args.verbose);
        }
        LaneFilter::Durable => {
            print_durable_lane(&active, args.verbose);
        }
        LaneFilter::Review => {
            print_review_lane(&review_entries, args.verbose);
        }
    }

    Ok(())
}

fn print_summary(active: &[&DurableRecord], review: &[ReviewEntry]) {
    let n_caveated = review.iter().filter(|r| r.final_class == FinalClass::Caveated).count();
    let n_reject = review.iter().filter(|r| r.final_class == FinalClass::Reject).count();
    println!("=== Projection Lanes ===");
    println!("  Durable (AUTO):     {} active claim(s)", active.len());
    println!("  Review (ESCALATE):  {} caveated claim(s) awaiting human review", n_caveated);
    println!("  Reject:             {} claim(s) rejected", n_reject);
}

fn print_durable_lane(active: &[&DurableRecord], verbose: bool) {
    println!("--- Durable Lane (AUTO projection eligible) ---");
    if active.is_empty() {
        println!("  (no durable claims yet)");
        return;
    }
    for (i, r) in active.iter().enumerate() {
        println!("  {}. [{}] {}", i + 1, r.theme, r.claim.trim());
        if verbose {
            println!(
                "     provenance={:.2} sources={} key={}",
                r.provenance_score,
                r.source_cases.join(","),
                &r.claim_key[..12]
            );
        }
    }
}

fn print_review_lane(review: &[ReviewEntry], verbose: bool) {
    let caveated: Vec<&ReviewEntry> = review
        .iter()
        .filter(|r| r.final_class == FinalClass::Caveated)
        .collect();
    println!("--- Review Lane (ESCALATE — awaiting human decision) ---");
    if caveated.is_empty() {
        println!("  (no caveated claims pending review)");
        return;
    }
    for (i, r) in caveated.iter().enumerate() {
        println!("  {}. [{}] {}", i + 1, r.theme, r.claim.trim());
        if verbose {
            println!(
                "     strength={:?} evidence_sufficient={} rationale={}",
                r.strength, r.evidence_sufficient, r.rationale
            );
        }
    }
    println!();
    println!(
        "  → {} claim(s) need human review. Use `crystal-review` to decide: rewrite/split/keep/reject.",
        caveated.len()
    );
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

fn read_review(path: &std::path::Path) -> Result<Vec<ReviewEntry>, CliError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| CliError::Io(format!("reading review {}: {e}", path.display())))?;
    let wrapper: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing review: {e}")))?;
    let arr = wrapper.get("review").and_then(|v| v.as_array());
    match arr {
        Some(items) => {
            let entries: Vec<ReviewEntry> = items
                .iter()
                .filter_map(|v| serde_json::from_value(v.clone()).ok())
                .collect();
            Ok(entries)
        }
        None => Ok(Vec::new()),
    }
}
