//! `project` — Projection Lanes view AND vault projection writer.
//!
//! **Read mode** (default): list claims by final routing lane
//! (durable / review / reject) and surface the human review queue.
//!
//! **Write mode** (`--write` or `--rebuild`): project Durable claims into
//! vault notes at `10-Knowledge/Crystal/<slug>.md`. Files are machine-owned,
//! marked with `<!-- crystal-managed -->` and frontmatter `crystal_key`.
//! Uses `write_new` — never overwrites existing files.

use std::collections::HashSet;
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
    /// Write durable claims as vault notes (incremental — skip already-projected).
    pub write: bool,
    /// Delete all projected notes and rebuild from the full ledger.
    pub rebuild: bool,
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

    if args.write || args.rebuild {
        return project_to_vault(&args.vault_root, &active, args.rebuild);
    }

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

// ---- Phase 2b: Vault Projection ----

const CRYSTAL_DIR: &str = "10-Knowledge/Crystal";
const MANAGED_MARKER: &str = "<!-- crystal-managed -->";

fn project_to_vault(
    vault_root: &std::path::Path,
    active: &[&DurableRecord],
    rebuild: bool,
) -> Result<(), CliError> {
    let crystal_dir = vault_root.join(CRYSTAL_DIR);

    if rebuild && crystal_dir.exists() {
        remove_managed_files(&crystal_dir)?;
    }

    std::fs::create_dir_all(&crystal_dir)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", crystal_dir.display())))?;

    let existing_keys = scan_existing_keys(&crystal_dir);

    let mut written = 0usize;
    let mut skipped = 0usize;
    for record in active {
        if existing_keys.contains(&record.claim_key) {
            skipped += 1;
            continue;
        }
        let slug = derive_slug(&record.theme, &record.claim_key);
        let target = crystal_dir.join(format!("{slug}.md"));
        let content = render_projection_note(record);
        ovp_intake::vaultops::write_new(&target, &content)
            .map_err(|e| CliError::Io(format!("projecting {slug}: {e}")))?;
        written += 1;
    }

    println!("project --write: {} active durable claims", active.len());
    println!("  written: {written} new vault note(s)");
    println!("  skipped: {skipped} (already projected)");
    println!("  dir:     {}", crystal_dir.display());
    Ok(())
}

fn derive_slug(theme: &str, claim_key: &str) -> String {
    let theme_part: String = theme
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == ' ' || *c == '-')
        .collect::<String>()
        .trim()
        .replace(' ', "-")
        .to_lowercase();
    let theme_part = if theme_part.len() > 40 {
        theme_part[..40].trim_end_matches('-').to_string()
    } else {
        theme_part
    };
    let key_prefix = &claim_key[..8.min(claim_key.len())];
    format!("{theme_part}-{key_prefix}")
}

fn render_projection_note(record: &DurableRecord) -> String {
    let mut note = String::new();
    note.push_str("---\n");
    note.push_str(&format!("crystal_key: {}\n", record.claim_key));
    note.push_str(&format!("theme: {}\n", record.theme));
    note.push_str(&format!("provenance_score: {:.2}\n", record.provenance_score));
    note.push_str(&format!("sources: [{}]\n", record.source_cases.join(", ")));
    note.push_str(&format!("strength: {:?}\n", record.strength));
    note.push_str("status: durable\n");
    note.push_str("---\n\n");
    note.push_str(MANAGED_MARKER);
    note.push('\n');
    note.push_str(&format!("# {}\n\n", record.claim.trim()));
    note.push_str(&format!("**Theme:** {}\n\n", record.theme));
    note.push_str("## Evidence\n\n");
    for cit in &record.citations {
        note.push_str(&format!(
            "- **{}:{}** — _{}_",
            cit.case_id, cit.unit_id, cit.quote
        ));
        if let Some(line) = cit.resolved_line {
            note.push_str(&format!(" (line {line})"));
        }
        note.push('\n');
    }
    note.push_str(&format!(
        "\n---\n_Provenance: {:.2} | Strength: {:?} — {}_ \n",
        record.provenance_score, record.strength, record.strength_rationale
    ));
    note
}

fn scan_existing_keys(dir: &std::path::Path) -> HashSet<String> {
    let mut keys = HashSet::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return keys;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("md") {
            continue;
        }
        if let Ok(content) = std::fs::read_to_string(&path) {
            if !content.contains(MANAGED_MARKER) {
                continue;
            }
            for line in content.lines() {
                if let Some(key) = line.strip_prefix("crystal_key: ") {
                    keys.insert(key.trim().to_string());
                    break;
                }
            }
        }
    }
    keys
}

fn remove_managed_files(dir: &std::path::Path) -> Result<(), CliError> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Ok(());
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("md") {
            continue;
        }
        if let Ok(content) = std::fs::read_to_string(&path) {
            if content.contains(MANAGED_MARKER) {
                std::fs::remove_file(&path).map_err(|e| {
                    CliError::Io(format!("removing managed file {}: {e}", path.display()))
                })?;
            }
        }
    }
    Ok(())
}
