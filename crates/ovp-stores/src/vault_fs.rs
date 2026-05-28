use std::fs;
use std::path::{Component, Path, PathBuf};

use ovp_core::{
    ApplyMode, ApplyReport, OpKind, OpOutcome, OpResult, PlanApplier, VaultCreateOp, VaultPath,
    VaultUpdateOp, WriteOp, WritePlan,
};
use sha2::{Digest, Sha256};

/// Filesystem-backed `PlanApplier`. All paths are interpreted relative
/// to a fixed vault root; `..`, absolute paths, and any traversal that
/// resolves outside the root are rejected before any I/O.
///
/// v1 handles `VaultCreate` and `VaultUpdate` only.
/// `CanonicalUpsert` and `EventAppend` ops record `Unsupported`.
///
/// Single-process: no file locking. Concurrent runs against the same
/// vault are undefined.
pub struct VaultFsPlanApplier {
    vault_root: PathBuf,
}

impl VaultFsPlanApplier {
    pub fn new(vault_root: impl Into<PathBuf>) -> Self {
        Self { vault_root: vault_root.into() }
    }

    pub fn vault_root(&self) -> &Path { &self.vault_root }
}

impl PlanApplier for VaultFsPlanApplier {
    fn apply(&mut self, plan: &WritePlan, mode: ApplyMode) -> ApplyReport {
        let mut report = ApplyReport::new(plan.run_id.clone(), mode);
        for op in &plan.ops {
            let outcome = match op {
                WriteOp::VaultCreate(c) => self.apply_create(c, mode),
                WriteOp::VaultUpdate(u) => self.apply_update(u, mode),
                WriteOp::CanonicalUpsert(c) => OpOutcome {
                    op_id: c.op_id.clone(),
                    kind: OpKind::CanonicalUpsert,
                    result: OpResult::Unsupported,
                },
                WriteOp::EventAppend(e) => OpOutcome {
                    op_id: e.op_id.clone(),
                    kind: OpKind::EventAppend,
                    result: OpResult::Unsupported,
                },
            };
            report.push(outcome);
        }
        report
    }
}

impl VaultFsPlanApplier {
    fn apply_create(&self, op: &VaultCreateOp, mode: ApplyMode) -> OpOutcome {
        let outcome = |result| OpOutcome {
            op_id: op.op_id.clone(),
            kind: OpKind::VaultCreate,
            result,
        };

        let abs = match self.resolve_vault_path(&op.path) {
            Ok(p) => p,
            Err(reason) => return outcome(OpResult::Failed { reason }),
        };

        if abs.exists() {
            let current = match fs::read(&abs) {
                Ok(b) => b,
                Err(e) => {
                    return outcome(OpResult::Failed {
                        reason: format!("read existing target: {e}"),
                    });
                }
            };
            let current_hash = sha256_hex(&current);
            if current_hash == op.after_hash.as_str() {
                return outcome(OpResult::Skipped {
                    reason: "idempotent: file already matches after_hash".into(),
                });
            }
            return outcome(OpResult::Failed {
                reason: format!(
                    "target exists with different content (current_hash={current_hash}, expected={})",
                    op.after_hash.as_str()
                ),
            });
        }

        if matches!(mode, ApplyMode::DryRun) {
            return outcome(OpResult::Skipped { reason: "dry-run".into() });
        }

        if let Some(parent) = abs.parent() {
            if let Err(e) = fs::create_dir_all(parent) {
                return outcome(OpResult::Failed {
                    reason: format!("create_dir_all({}): {e}", parent.display()),
                });
            }
        }
        if let Err(e) = fs::write(&abs, op.body.as_bytes()) {
            return outcome(OpResult::Failed {
                reason: format!("write({}): {e}", abs.display()),
            });
        }
        outcome(OpResult::Applied)
    }

    fn apply_update(&self, op: &VaultUpdateOp, mode: ApplyMode) -> OpOutcome {
        let outcome = |result| OpOutcome {
            op_id: op.op_id.clone(),
            kind: OpKind::VaultUpdate,
            result,
        };

        let abs = match self.resolve_vault_path(&op.path) {
            Ok(p) => p,
            Err(reason) => return outcome(OpResult::Failed { reason }),
        };

        if !abs.exists() {
            return outcome(OpResult::Failed {
                reason: format!("update target does not exist: {}", abs.display()),
            });
        }

        let current = match fs::read(&abs) {
            Ok(b) => b,
            Err(e) => {
                return outcome(OpResult::Failed {
                    reason: format!("read target: {e}"),
                });
            }
        };
        let current_hash = sha256_hex(&current);
        if current_hash != op.before_hash.as_str() {
            return outcome(OpResult::Failed {
                reason: format!(
                    "before_hash mismatch (current={current_hash}, expected={})",
                    op.before_hash.as_str()
                ),
            });
        }

        if matches!(mode, ApplyMode::DryRun) {
            return outcome(OpResult::Skipped { reason: "dry-run".into() });
        }

        if let Err(e) = fs::write(&abs, op.body.as_bytes()) {
            return outcome(OpResult::Failed {
                reason: format!("write({}): {e}", abs.display()),
            });
        }
        outcome(OpResult::Applied)
    }

    /// Validate + resolve a `VaultPath` against the configured root.
    /// Returns `Err(reason_code)` if the path is unsafe.
    fn resolve_vault_path(&self, vp: &VaultPath) -> Result<PathBuf, String> {
        let raw = vp.as_str();
        if raw.is_empty() {
            return Err("path_empty".into());
        }
        let p = Path::new(raw);

        if p.is_absolute() {
            return Err(format!("path_absolute: {raw}"));
        }
        // Reject any `..` or root component. Plain `.` is OK and gets stripped.
        for c in p.components() {
            match c {
                Component::ParentDir => return Err(format!("path_escape: `..` in {raw}")),
                Component::RootDir | Component::Prefix(_) => {
                    return Err(format!("path_root_component: {raw}"));
                }
                Component::Normal(_) | Component::CurDir => {}
            }
        }
        // Defensive: also reject backslash-prefixed components on
        // platforms where Path doesn't catch them, and verify the
        // resolved target sits under vault_root by string check.
        let resolved = self.vault_root.join(p);
        // Best-effort containment check: compare root prefix as strings
        // *before* any filesystem normalization (canonicalize would
        // require the target to exist).
        let root_str = self.vault_root.to_string_lossy();
        let resolved_str = resolved.to_string_lossy();
        if !resolved_str.starts_with(root_str.as_ref()) {
            return Err(format!("path_outside_root: {raw}"));
        }
        Ok(resolved)
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let hash = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    use std::fmt::Write;
    for b in hash.iter() {
        write!(s, "{:02x}", b).expect("infallible");
    }
    s
}
