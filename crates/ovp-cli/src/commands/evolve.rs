use std::path::PathBuf;

use crate::CliError;

pub struct EvolveArgs {
    pub sub: EvolveSubcmd,
    pub registry_path: PathBuf,
}

pub enum EvolveSubcmd {
    Registry,
    Validate { candidate: PathBuf },
    Ledger { vault_root: PathBuf },
    Diagnose { run_id: String, source: String, symptoms: Vec<String> },
}

pub fn run(args: EvolveArgs) -> Result<(), CliError> {
    let registry = ovp_evolve::registry::ComponentRegistry::load(&args.registry_path)
        .map_err(|e| CliError::Io(format!("registry: {e}")))?;

    match args.sub {
        EvolveSubcmd::Registry => run_registry(&registry),
        EvolveSubcmd::Validate { candidate } => run_validate(&registry, &candidate),
        EvolveSubcmd::Ledger { vault_root } => run_ledger(&vault_root),
        EvolveSubcmd::Diagnose { run_id, source, symptoms } => {
            run_diagnose(&run_id, &source, symptoms)
        }
    }
}

fn run_registry(registry: &ovp_evolve::registry::ComponentRegistry) -> Result<(), CliError> {
    println!("=== Evolution Component Registry ===");
    println!("{} components registered:\n", registry.components.len());
    for c in &registry.components {
        let ver = c.current_version.as_deref().unwrap_or("-");
        println!(
            "  {} [{}] v{} → {}",
            c.id, c.surface, ver, c.file
        );
        let buckets: Vec<_> = c.quality_buckets.iter().map(|b| format!("{b:?}")).collect();
        println!("    buckets: {}", buckets.join(", "));
    }
    println!("\nRegistry valid ✓");
    Ok(())
}

fn run_validate(
    registry: &ovp_evolve::registry::ComponentRegistry,
    candidate_path: &std::path::Path,
) -> Result<(), CliError> {
    let spec = ovp_evolve::candidate::CandidateSpec::load(candidate_path)
        .map_err(|e| CliError::Io(format!("candidate: {e}")))?;

    match spec.validate(registry) {
        Ok(()) => {
            println!("Candidate '{}' is valid ✓", spec.id);
            println!("  surface: {}", spec.surface);
            println!("  component: {}", spec.component);
            println!("  {} → {}", spec.base_version, spec.target_version);
            println!("  hypothesis: {}", spec.hypothesis);
            Ok(())
        }
        Err(e) => {
            eprintln!("Candidate validation FAILED: {e}");
            Err(CliError::Gate(format!("candidate invalid: {e}")))
        }
    }
}

fn run_ledger(vault_root: &std::path::Path) -> Result<(), CliError> {
    let ledger_path = vault_root.join(".ovp/evolution-ledger.jsonl");
    let entries = ovp_evolve::ledger::read_entries(&ledger_path)
        .map_err(|e| CliError::Io(format!("ledger: {e}")))?;

    let summary = ovp_evolve::ledger::summary(&entries);
    println!("=== Evolution Ledger ===");
    println!("  path: {}", ledger_path.display());
    println!("  total entries: {}", summary.total);
    println!("  accepted: {}", summary.accepted);
    println!("  rejected: {}", summary.rejected);
    println!("  needs_ablation: {}", summary.needs_ablation);
    println!("  needs_review: {}", summary.needs_review);

    if !entries.is_empty() {
        println!("\n  Recent entries:");
        for e in entries.iter().rev().take(5) {
            println!(
                "    [{}] {} → {} ({})",
                e.timestamp, e.candidate_id, e.component, e.decision
            );
        }
    }
    Ok(())
}

fn run_diagnose(run_id: &str, source: &str, symptoms: Vec<String>) -> Result<(), CliError> {
    if symptoms.is_empty() {
        return Err(CliError::Io("at least one symptom required".into()));
    }
    let card = ovp_evolve::root_cause::diagnose(run_id, source, symptoms);

    println!("=== Root-Cause Card ===");
    println!("  run_id: {}", card.run_id);
    println!("  source: {}", card.source);
    println!("  suspected_surface: {:?}", card.suspected_surface);
    println!("  primary_bucket: {:?}", card.primary_bucket);
    println!("  confidence: {:.2}", card.confidence);
    println!("  symptoms:");
    for s in &card.symptoms {
        println!("    - {s}");
    }

    let json = serde_json::to_string_pretty(&card)
        .map_err(|e| CliError::Io(format!("json: {e}")))?;
    println!("\n{json}");
    Ok(())
}
