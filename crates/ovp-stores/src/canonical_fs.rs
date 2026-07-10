use std::fs;
use std::path::{Component, Path, PathBuf};

use ovp_core::{
    ApplyMode, ApplyReport, CanonicalUpsertOp, OpKind, OpOutcome, OpResult, PlanApplier, WriteOp,
    WritePlan,
};
use sha2::{Digest, Sha256};

/// Filesystem-backed canonical store applier. Persists `CanonicalUpsert`
/// ops as `<store_root>/<key>.json` (one record per canonical key). The
/// store is **domain-blind**: it transports the op's `payload` string
/// verbatim (the typed shape, `ovp-domain::CanonicalConcept`, is the
/// producer/reader's concern). Vault/event ops record `Unsupported`.
///
/// Upsert semantics: write-or-replace, idempotent on matching content.
/// `before_hash`, when `Some`, is an optimistic-concurrency guard — the
/// current record must match it or the op `Failed`s (a conflicting writer
/// moved the record). `None` (a fresh registration) skips the guard.
///
/// Path safety mirrors `VaultFsPlanApplier`: keys are validated to stay
/// under `store_root` (no `..`, absolute, or root components) before any
/// I/O.
pub struct CanonicalFsStoreApplier {
    store_root: PathBuf,
}

impl CanonicalFsStoreApplier {
    pub fn new(store_root: impl Into<PathBuf>) -> Self {
        Self { store_root: store_root.into() }
    }

    pub fn store_root(&self) -> &Path {
        &self.store_root
    }

    /// Read every canonical record as `(key, payload)` pairs, sorted by
    /// key for determinism. Domain-blind: payloads are returned verbatim;
    /// the caller parses them (e.g. into `ovp-domain::CanonicalConcept`).
    /// Used by derived-state rebuilds (MOC, knowledge index). A missing
    /// store root yields an empty list, not an error.
    pub fn read_all(&self) -> std::io::Result<Vec<(String, String)>> {
        if !self.store_root.exists() {
            return Ok(Vec::new());
        }
        let mut out: Vec<(String, String)> = Vec::new();
        for entry in fs::read_dir(&self.store_root)? {
            let path = entry?.path();
            if path.extension().and_then(|e| e.to_str()) == Some("json")
                && let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                    let payload = fs::read_to_string(&path)?;
                    out.push((stem.to_string(), payload));
                }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(out)
    }
}

impl PlanApplier for CanonicalFsStoreApplier {
    fn apply(&mut self, plan: &WritePlan, mode: ApplyMode) -> ApplyReport {
        let mut report = ApplyReport::new(plan.run_id.clone(), mode);
        for op in &plan.ops {
            let outcome = match op {
                WriteOp::CanonicalUpsert(c) => self.apply_upsert(c, mode),
                WriteOp::VaultCreate(o) => OpOutcome {
                    op_id: o.op_id.clone(),
                    kind: OpKind::VaultCreate,
                    result: OpResult::Unsupported,
                },
                WriteOp::VaultUpdate(o) => OpOutcome {
                    op_id: o.op_id.clone(),
                    kind: OpKind::VaultUpdate,
                    result: OpResult::Unsupported,
                },
                WriteOp::EventAppend(o) => OpOutcome {
                    op_id: o.op_id.clone(),
                    kind: OpKind::EventAppend,
                    result: OpResult::Unsupported,
                },
            };
            report.push(outcome);
        }
        report
    }
}

impl CanonicalFsStoreApplier {
    fn apply_upsert(&self, op: &CanonicalUpsertOp, mode: ApplyMode) -> OpOutcome {
        let outcome = |result| OpOutcome {
            op_id: op.op_id.clone(),
            kind: OpKind::CanonicalUpsert,
            result,
        };

        // Declared-hash integrity: the payload must actually hash to the
        // op's `after_hash` before we touch the store. A mismatch means the
        // op is corrupt or mislabeled — fail before any I/O so a bad write
        // never lands (and idempotence checks below stay trustworthy).
        let computed = sha256_hex(op.payload.as_bytes());
        if computed != op.after_hash.as_str() {
            return outcome(OpResult::Failed {
                reason: format!(
                    "after_hash mismatch (declared={}, computed={computed})",
                    op.after_hash.as_str()
                ),
            });
        }

        let abs = match self.resolve_key_path(op.key.as_str()) {
            Ok(p) => p,
            Err(reason) => return outcome(OpResult::Failed { reason }),
        };

        let existing = if abs.exists() {
            match fs::read(&abs) {
                Ok(b) => Some(b),
                Err(e) => {
                    return outcome(OpResult::Failed {
                        reason: format!("read existing canonical record: {e}"),
                    });
                }
            }
        } else {
            None
        };

        // Optimistic-concurrency guard.
        if let Some(expected) = &op.before_hash {
            match &existing {
                Some(bytes) => {
                    let current = sha256_hex(bytes);
                    if current != expected.as_str() {
                        return outcome(OpResult::Failed {
                            reason: format!(
                                "before_hash mismatch (current={current}, expected={})",
                                expected.as_str()
                            ),
                        });
                    }
                }
                None => {
                    return outcome(OpResult::Failed {
                        reason: "before_hash given but no existing record".into(),
                    });
                }
            }
        }

        // Idempotent: existing content already equals the desired payload.
        if let Some(bytes) = &existing
            && sha256_hex(bytes) == op.after_hash.as_str() {
                return outcome(OpResult::Skipped {
                    reason: "idempotent: canonical record already matches after_hash".into(),
                });
            }

        if matches!(mode, ApplyMode::DryRun) {
            return outcome(OpResult::Skipped { reason: "dry-run".into() });
        }

        if let Some(parent) = abs.parent()
            && let Err(e) = fs::create_dir_all(parent) {
                return outcome(OpResult::Failed {
                    reason: format!("create_dir_all({}): {e}", parent.display()),
                });
            }
        if let Err(e) = fs::write(&abs, op.payload.as_bytes()) {
            return outcome(OpResult::Failed {
                reason: format!("write({}): {e}", abs.display()),
            });
        }
        outcome(OpResult::Applied)
    }

    /// Resolve a canonical key to `<store_root>/<key>.json`, rejecting any
    /// key that would escape the store root.
    fn resolve_key_path(&self, key: &str) -> Result<PathBuf, String> {
        if key.is_empty() {
            return Err("key_empty".into());
        }
        let rel = Path::new(key);
        if rel.is_absolute() {
            return Err(format!("key_absolute: {key}"));
        }
        for c in rel.components() {
            match c {
                Component::ParentDir => return Err(format!("key_escape: `..` in {key}")),
                Component::RootDir | Component::Prefix(_) => {
                    return Err(format!("key_root_component: {key}"));
                }
                Component::Normal(_) | Component::CurDir => {}
            }
        }
        // A canonical key must be exactly ONE path segment so it maps to
        // `<root>/<key>.json` at the top level. A key with a separator (e.g.
        // `a/b`) would nest the record in a subdirectory that `read_all`
        // (top-level `*.json` only) never scans — silently dropping it from
        // every derived rebuild. Reject so every key the store can WRITE is
        // a key it can READ back. Checked after the component scan so `..`
        // still reports `key_escape`.
        if key.contains('/') || key.contains('\\') {
            return Err(format!("key_nested: {key}"));
        }
        let file = format!("{key}.json");
        // Independent round-trip guard, decoupled from the domain slug rule:
        // `read_all` recovers a key via `Path::file_stem` over `<key>.json`
        // and only sees files whose extension is `json`. If this key would
        // not recover to itself (e.g. a trailing dot, or stem/extension
        // splitting that eats part of the key), the store would persist a
        // record it can never read back. Reject so the store's write-set
        // always equals its read-set, whatever the producer's key rule is.
        let fname = Path::new(&file);
        let round_trips = fname.extension().and_then(|e| e.to_str()) == Some("json")
            && fname.file_stem().and_then(|s| s.to_str()) == Some(key);
        if !round_trips {
            return Err(format!("key_not_roundtrippable: {key}"));
        }
        let resolved = self.store_root.join(&file);
        if resolved.strip_prefix(&self.store_root).is_err() {
            return Err(format!("key_outside_root: {key}"));
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
