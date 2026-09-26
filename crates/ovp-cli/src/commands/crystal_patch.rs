//! `crystal-patch` — human patch ledger operations over Crystal knowledge claims (M37).
//!
//! Provides CLI access to:
//! - apply: Apply an append-only human modification or caveat to a knowledge assertion.
//! - rollback: One-click append-only rollback of a patch to previous revision or baseline truth.
//! - list: List active effective patches.
//! - diff: Inspect structured unified diffs between baseline and patched text.
//! - audit: Display complete chronological history of patch applications and rollbacks.

use std::path::{Path, PathBuf};

use clap::Subcommand;
use ovp_domain::VaultLayout;
use ovp_domain::crystal::patch::{
    HumanPatchRecord, PatchOp, append_patch_record, audit_patches, compute_text_hash, diff_patch,
    fold_patch_ledger, format_diff, read_patch_ledger,
};
use ovp_domain::crystal::{CrystalStatus, StoreEvent, fold_ledger};
use ovp_intake::read_jsonl_strict;

use crate::CliError;

#[derive(Subcommand, Debug, Clone)]
pub enum CrystalPatchSubcommand {
    /// Apply a human patch (edit or caveat) to a crystal claim.
    Apply {
        /// Target claim ID (e.g. "c01") or claim key (e.g. "ck-...").
        #[arg(long)]
        target: String,
        /// New revised assertion text.
        #[arg(long)]
        claim: String,
        /// Optional theme override.
        #[arg(long)]
        theme: Option<String>,
        /// Optional caveat override.
        #[arg(long)]
        caveat: Option<String>,
        /// Author attribution (defaults to "operator:human").
        #[arg(long, default_value = "operator:human")]
        author: String,
        /// Reason or justification for the patch.
        #[arg(long)]
        reason: String,
        /// Expected SHA-256 base hash (optional drift check).
        #[arg(long)]
        base_hash: Option<String>,
        /// Apply even if target cannot be resolved in existing ledger records.
        #[arg(long)]
        force: bool,
    },
    /// Roll back an applied patch cleanly using an append-only rollback record.
    Rollback {
        /// Target claim ID or claim key.
        #[arg(long)]
        target: String,
        /// Specific patch ID to roll back (defaults to currently active patch).
        #[arg(long)]
        patch_id: Option<String>,
        /// Author attribution (defaults to "operator:human").
        #[arg(long, default_value = "operator:human")]
        author: String,
        /// Reason or motivation for rolling back.
        #[arg(long)]
        reason: String,
    },
    /// List active effective patches.
    List {
        /// Optional filter by target claim ID or key.
        #[arg(long)]
        target: Option<String>,
        /// Output as JSON.
        #[arg(long)]
        json: bool,
    },
    /// Inspect unified diff between baseline and patched assertion text.
    Diff {
        /// Optional filter by target claim ID or key.
        #[arg(long)]
        target: Option<String>,
        /// Specific patch ID to inspect.
        #[arg(long)]
        patch_id: Option<String>,
    },
    /// Display full audit trail of all patch operations (applies and rollbacks).
    Audit {
        /// Optional filter by target claim ID or key.
        #[arg(long)]
        target: Option<String>,
        /// Output as JSON.
        #[arg(long)]
        json: bool,
    },
}

pub struct CrystalPatchArgs {
    pub vault_root: PathBuf,
    pub action: CrystalPatchSubcommand,
}

pub fn run(args: CrystalPatchArgs) -> Result<(), CliError> {
    let layout = VaultLayout;
    let patches_file = args.vault_root.join(layout.crystal_patches_ledger());
    let store_dir = args.vault_root.join(layout.crystal_store_dir());

    match args.action {
        CrystalPatchSubcommand::Apply {
            target,
            claim,
            theme,
            caveat,
            author,
            reason,
            base_hash,
            force,
        } => run_apply(
            &args.vault_root,
            &patches_file,
            &store_dir,
            &target,
            &claim,
            theme,
            caveat,
            &author,
            &reason,
            base_hash.as_deref(),
            force,
        ),
        CrystalPatchSubcommand::Rollback {
            target,
            patch_id,
            author,
            reason,
        } => run_rollback(
            &args.vault_root,
            &patches_file,
            &target,
            patch_id.as_deref(),
            &author,
            &reason,
        ),
        CrystalPatchSubcommand::List { target, json } => {
            run_list(&patches_file, target.as_deref(), json)
        }
        CrystalPatchSubcommand::Diff { target, patch_id } => {
            run_diff(&patches_file, target.as_deref(), patch_id.as_deref())
        }
        CrystalPatchSubcommand::Audit { target, json } => {
            run_audit(&patches_file, target.as_deref(), json)
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn run_apply(
    vault_root: &Path,
    patches_file: &Path,
    store_dir: &Path,
    target: &str,
    new_claim: &str,
    new_theme: Option<String>,
    new_caveat: Option<String>,
    author: &str,
    reason: &str,
    expected_base_hash: Option<&str>,
    force: bool,
) -> Result<(), CliError> {
    // 1. Check existing patches for target
    let existing_records = read_patch_ledger(patches_file)
        .map_err(|e| CliError::Io(format!("reading patch ledger: {e}")))?;
    let patch_state = fold_patch_ledger(&existing_records);

    let mut base_text = String::new();
    let mut target_claim_key: Option<String> = None;
    let mut resolved = false;
    // The ledger is keyed by claim id. A claim key supplied on the command
    // line is an alias: it must resolve to the same `target_id` as the id
    // form, or two histories would fold independently for one claim.
    let mut target: String = patch_state
        .resolve_target_id(target)
        .unwrap_or_else(|| target.to_string());

    if let Some(active_patch) = patch_state.get_active_patch(&target, None) {
        base_text = active_patch.patched_text.clone();
        target_claim_key = active_patch.target_claim_key.clone();
        resolved = true;
    }

    if !resolved {
        // Look up in crystal ledger.jsonl
        let ledger_file = store_dir.join("ledger.jsonl");
        if ledger_file.exists() {
            let events: Vec<StoreEvent> = read_jsonl_strict(&ledger_file)
                .map_err(|e| CliError::Io(format!("reading crystal ledger: {e}")))?;
            let durable_records = fold_ledger(&events);
            for rec in &durable_records {
                if rec.status == CrystalStatus::Active
                    && (rec.claim_id == target || rec.claim_key == target)
                {
                    base_text = rec.claim.clone();
                    target_claim_key = Some(rec.claim_key.clone());
                    target = rec.claim_id.clone();
                    resolved = true;
                    break;
                }
            }
        }
    }

    if !resolved {
        // Look up in review.json
        let review_file = store_dir.join("review.json");
        if review_file.exists()
            && let Ok(raw) = std::fs::read_to_string(&review_file)
        {
            #[derive(serde::Deserialize)]
            struct ReviewFile {
                #[serde(default)]
                review: Vec<ovp_domain::crystal::ReviewEntry>,
            }
            if let Ok(rf) = serde_json::from_str::<ReviewFile>(&raw) {
                for entry in rf.review {
                    if entry.claim_id == target {
                        base_text = entry.claim;
                        target_claim_key = None;
                        resolved = true;
                        break;
                    }
                }
            }
        }
    }

    if !resolved && !force {
        return Err(CliError::Io(format!(
            "Target claim '{target}' not found in crystal store. Use --force to apply without base verification."
        )));
    }

    let actual_base_hash = compute_text_hash(&base_text);
    if let Some(expected) = expected_base_hash
        && actual_base_hash != expected
    {
        return Err(CliError::Io(format!(
            "Drift detected for target '{target}': expected base hash {expected}, got {actual_base_hash}"
        )));
    }

    let patch = HumanPatchRecord::new_apply(
        target.as_str(),
        target_claim_key,
        &base_text,
        new_claim,
        new_theme,
        new_caveat,
        author,
        reason,
        None,
    );

    append_patch_record(patches_file, &patch)
        .map_err(|e| CliError::Io(format!("writing patch: {e}")))?;

    println!(
        "✓ Applied human patch {} for claim {}",
        patch.patch_id, target
    );
    println!("  Base text:    {base_text}");
    println!("  Patched text: {new_claim}");
    println!("  Reason:       {reason}");
    println!("  Author:       {author}");
    println!();
    println!(
        "Run `ovp2 index --vault-root {}` to rebuild index and update portals.",
        vault_root.display()
    );

    Ok(())
}

fn run_rollback(
    vault_root: &Path,
    patches_file: &Path,
    target: &str,
    patch_id: Option<&str>,
    author: &str,
    reason: &str,
) -> Result<(), CliError> {
    let existing_records = read_patch_ledger(patches_file)
        .map_err(|e| CliError::Io(format!("reading patch ledger: {e}")))?;
    let patch_state = fold_patch_ledger(&existing_records);

    let target_patch = match patch_id {
        Some(pid) => {
            // Find specific patch
            let mut found = None;
            for records in patch_state.history_by_target.values() {
                for r in records {
                    if r.patch_id == pid {
                        found = Some(r.clone());
                        break;
                    }
                }
            }
            found.ok_or_else(|| CliError::Io(format!("Patch ID '{pid}' not found")))?
        }
        None => {
            // Find active patch for target (claim id or claim-key alias)
            let target = patch_state
                .resolve_target_id(target)
                .unwrap_or_else(|| target.to_string());
            patch_state
                .get_active_patch(&target, None)
                .cloned()
                .ok_or_else(|| {
                    CliError::Io(format!(
                        "No active patch found for target '{target}' to roll back"
                    ))
                })?
        }
    };

    // The rollback record is grouped under the patch's OWN target: a valid
    // `--patch-id` may belong to a different target than `--target`, and
    // folding is per-target — grouping it under the user-supplied name would
    // leave the selected patch active while reporting success.
    let rollback_patch = HumanPatchRecord::new_rollback(
        &target_patch.target_id,
        Some(target_patch.patch_id.clone()),
        target_patch.target_claim_key.clone(),
        &target_patch.patched_text,
        author,
        reason,
        None,
    );

    append_patch_record(patches_file, &rollback_patch)
        .map_err(|e| CliError::Io(format!("writing rollback patch: {e}")))?;

    println!(
        "✓ Rolled back patch {} for claim {}",
        target_patch.patch_id, target_patch.target_id
    );
    println!("  Author: {author}");
    println!("  Reason: {reason}");
    println!();
    println!(
        "Run `ovp2 index --vault-root {}` to rebuild index and update portals.",
        vault_root.display()
    );

    Ok(())
}

fn run_list(patches_file: &Path, target: Option<&str>, json: bool) -> Result<(), CliError> {
    let records = read_patch_ledger(patches_file)
        .map_err(|e| CliError::Io(format!("reading patch ledger: {e}")))?;
    let state = fold_patch_ledger(&records);

    let target = target.map(|t| state.resolve_target_id(t).unwrap_or_else(|| t.to_string()));
    let active_patches: Vec<&HumanPatchRecord> = match target.as_deref() {
        Some(t) => state
            .active_by_target
            .values()
            .filter(|p| p.target_id == t)
            .collect(),
        None => state.active_by_target.values().collect(),
    };

    if json {
        let serialized = serde_json::to_string_pretty(&active_patches)
            .map_err(|e| CliError::Io(format!("formatting json: {e}")))?;
        println!("{serialized}");
        return Ok(());
    }

    if active_patches.is_empty() {
        println!(
            "No active human patches found in {}.",
            patches_file.display()
        );
        return Ok(());
    }

    println!(
        "ACTIVE HUMAN PATCHES (active: {}, rolled back: {}, total records: {})",
        state.active_count, state.rolled_back_count, state.total_records
    );
    println!("{:-<100}", "");
    println!(
        "{:<10} {:<22} {:<18} {:<22} PATCHED TEXT",
        "TARGET", "PATCH ID", "AUTHOR", "CREATED"
    );
    println!("{:-<100}", "");

    for p in active_patches {
        let truncated = if p.patched_text.len() > 32 {
            let mut s = p.patched_text.chars().take(29).collect::<String>();
            s.push_str("...");
            s
        } else {
            p.patched_text.clone()
        };
        println!(
            "{:<10} {:<22} {:<18} {:<22} {}",
            p.target_id, p.patch_id, p.author, p.created_at, truncated
        );
    }

    Ok(())
}

fn run_diff(
    patches_file: &Path,
    target: Option<&str>,
    patch_id: Option<&str>,
) -> Result<(), CliError> {
    let records = read_patch_ledger(patches_file)
        .map_err(|e| CliError::Io(format!("reading patch ledger: {e}")))?;
    let state = fold_patch_ledger(&records);

    let patches_to_diff: Vec<&HumanPatchRecord> = if let Some(pid) = patch_id {
        let mut found = Vec::new();
        for list in state.history_by_target.values() {
            for r in list {
                if r.patch_id == pid {
                    found.push(r);
                    break;
                }
            }
        }
        found
    } else if let Some(t) = target {
        let t = state.resolve_target_id(t).unwrap_or_else(|| t.to_string());
        state.active_by_target.get(&t).into_iter().collect()
    } else {
        state.active_by_target.values().collect()
    };

    if patches_to_diff.is_empty() {
        println!("No patches found to diff.");
        return Ok(());
    }

    for patch in patches_to_diff {
        if patch.op == PatchOp::Rollback {
            println!("Patch {} is a Rollback record.", patch.patch_id);
            continue;
        }
        let diff = diff_patch(patch, None, None);
        println!("{}", format_diff(&diff));
    }

    Ok(())
}

fn run_audit(patches_file: &Path, target: Option<&str>, json: bool) -> Result<(), CliError> {
    let records = read_patch_ledger(patches_file)
        .map_err(|e| CliError::Io(format!("reading patch ledger: {e}")))?;
    let state = fold_patch_ledger(&records);
    let target = target.map(|t| state.resolve_target_id(t).unwrap_or_else(|| t.to_string()));
    let audit_entries = audit_patches(&state, target.as_deref());

    if json {
        let serialized = serde_json::to_string_pretty(&audit_entries)
            .map_err(|e| CliError::Io(format!("formatting json: {e}")))?;
        println!("{serialized}");
        return Ok(());
    }

    if audit_entries.is_empty() {
        println!(
            "No patch audit entries found in {}.",
            patches_file.display()
        );
        return Ok(());
    }

    println!("PATCH AUDIT TRAIL ({} entries)", audit_entries.len());
    println!("{:-<100}", "");

    for entry in audit_entries {
        let op_str = match entry.op {
            PatchOp::Apply => "APPLY",
            PatchOp::Rollback => "ROLLBACK",
        };
        let status_str = match entry.status {
            ovp_domain::crystal::PatchStatus::Active => "active",
            ovp_domain::crystal::PatchStatus::Superseded => "superseded",
            ovp_domain::crystal::PatchStatus::RolledBack => "rolled_back",
            ovp_domain::crystal::PatchStatus::Conflicted => "conflicted",
        };
        println!(
            "[{}] {} ({}) target: {} | patch: {} | author: {}",
            entry.created_at, op_str, status_str, entry.target_id, entry.patch_id, entry.author
        );
        if !entry.base_text.is_empty() {
            println!("  base:    {}", entry.base_text);
        }
        if !entry.patched_text.is_empty() {
            println!("  patched: {}", entry.patched_text);
        }
        if let Some(ref rb) = entry.rollback_patch_id {
            println!("  rollback_target: {rb}");
        }
        println!("  reason:  {}", entry.reason);
        println!();
    }

    Ok(())
}
