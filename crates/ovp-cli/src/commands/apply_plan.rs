use std::path::PathBuf;

use ovp_core::{ApplyMode, PlanApplier, WritePlan};
use ovp_stores::VaultFsPlanApplier;

use crate::CliError;

pub struct ApplyPlanArgs {
    pub plan_path: PathBuf,
    pub vault_root: PathBuf,
    pub dry_run: bool,
    pub report_path: Option<PathBuf>,
}

pub fn run(args: ApplyPlanArgs) -> Result<(), CliError> {
    let raw = std::fs::read_to_string(&args.plan_path).map_err(|e| {
        CliError::Io(format!("reading plan `{}`: {e}", args.plan_path.display()))
    })?;
    let plan: WritePlan = serde_json::from_str(&raw)
        .map_err(|e| CliError::Io(format!("parsing plan: {e}")))?;

    let mode = if args.dry_run { ApplyMode::DryRun } else { ApplyMode::Apply };
    let mut applier = VaultFsPlanApplier::new(&args.vault_root);
    let report = applier.apply(&plan, mode);
    let counts = report.counts();

    println!("run_id:        {}", report.run_id.as_str());
    println!("mode:          {}", report.mode);
    println!("applied:       {}", counts.applied);
    println!("skipped:       {}", counts.skipped);
    println!("failed:        {}", counts.failed);
    println!("unsupported:   {}", counts.unsupported);

    if let Some(path) = &args.report_path {
        let json = serde_json::to_string_pretty(&report)
            .map_err(|e| CliError::Io(format!("serializing report: {e}")))?;
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    CliError::Io(format!("creating {}: {e}", parent.display()))
                })?;
            }
        }
        std::fs::write(path, json)
            .map_err(|e| CliError::Io(format!("writing report {}: {e}", path.display())))?;
        println!();
        println!("wrote {}", path.display());
    }

    // Unsupported ops are not hard failures, but they must never pass
    // silently: this applier was handed an op kind it can't perform, so
    // that work did NOT happen. Surface it loudly so an operator routing
    // CanonicalUpsert/EventAppend to a vault-only applier notices.
    if report.has_unsupported() {
        for o in &report.outcomes {
            if matches!(o.result, ovp_core::OpResult::Unsupported) {
                eprintln!(
                    "WARNING unsupported {} ({:?}): VaultFsPlanApplier does not handle this op kind; nothing was written for it",
                    o.op_id.as_str(),
                    o.kind
                );
            }
        }
    }

    if !report.all_ok() {
        for o in &report.outcomes {
            if let ovp_core::OpResult::Failed { reason } = &o.result {
                eprintln!("FAILED {} ({:?}): {reason}", o.op_id.as_str(), o.kind);
            }
        }
        return Err(CliError::Io(format!(
            "{} op(s) failed; vault left in partial state",
            counts.failed
        )));
    }

    Ok(())
}
