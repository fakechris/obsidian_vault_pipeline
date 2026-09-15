//! M37 — Human Patch Ledger for Crystal Knowledge Claims.
//!
//! Inspired by WeKnora's Chunk Editing with Revision History and one-click rollback.
//! In OVP2, knowledge crystal claims are produced deterministically through multi-source
//! synthesis and citation linter gates. If an operator discovers wording overreach, typos,
//! or wants to add a human caveat to an assertion, directly editing downstream Markdown
//! files would cause state drift from the upstream ledger and get overwritten on rebuild.
//!
//! This module introduces an **append-only Human Patch Ledger** stored at
//! `.ovp/crystal/patches.jsonl`.
//!
//! Core Invariants:
//! 1. **Immutable Ground Truth**: `ledger.jsonl` and vault notes are NEVER mutated in-place.
//! 2. **Reproducible Projection**: Rebuilding the index (`ovp2 index`) or loading active records
//!    folds the patch ledger and overlays effective patches deterministically.
//! 3. **Auditable Revisions**: Every edit is an append-only `HumanPatchRecord` capturing
//!    `base_text`, `base_hash`, `patched_text`, author, reason, and timestamp.
//! 4. **One-Click Rollback**: Rolling back a patch is ALSO an append-only event (`PatchOp::Rollback`)
//!    which deactivates the target patch and cleanly falls back to the previous revision or original raw truth.
//! 5. **Drift Detection**: Patches record `base_hash` (SHA-256 of the baseline assertion) so conflicting
//!    concurrent patches or upstream assertion rewrites are explicitly detected.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::crystal::DurableRecord;

/// Operation recorded in the patch ledger.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PatchOp {
    /// Apply a human patch or revise an existing patch.
    Apply,
    /// Roll back a previously applied patch.
    Rollback,
}

/// The effective status of a patch in the folded state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PatchStatus {
    /// Currently active and effective.
    Active,
    /// Superseded by a newer patch for the same target.
    Superseded,
    /// Deactivated by a rollback operation.
    RolledBack,
    /// Rejected at fold time: a concurrent `Apply` based on a revision that
    /// was already superseded (lost-update conflict). It never becomes
    /// effective; the earlier correction wins and the conflict is visible
    /// in `audit` instead of silently dropping one operator's edit.
    Conflicted,
}

/// One append-only record in `.ovp/crystal/patches.jsonl`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HumanPatchRecord {
    /// Deterministic patch identity (e.g. `hp-<hash>`).
    pub patch_id: String,
    /// Operation: Apply or Rollback.
    pub op: PatchOp,
    /// Target claim identifier (claim_id such as "c01" or claim_key such as "ck-abc...").
    pub target_id: String,
    /// Optional target claim_key when known (for exact version binding).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_claim_key: Option<String>,
    /// Base assertion text before this patch was applied (for diff & drift verification).
    pub base_text: String,
    /// SHA-256 hash of `base_text`.
    pub base_hash: String,
    /// Revised assertion text. Empty if op == Rollback.
    #[serde(default)]
    pub patched_text: String,
    /// Optional theme override.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub patched_theme: Option<String>,
    /// Optional caveat override.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub patched_caveat: Option<String>,
    /// Target patch_id to roll back (when op == Rollback).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rollback_patch_id: Option<String>,
    /// Author / operator attribution (e.g. "human:operator", "admin").
    pub author: String,
    /// Reason or motivation for this patch.
    pub reason: String,
    /// ISO-8601 timestamp string when the patch was authored.
    pub created_at: String,
}

impl HumanPatchRecord {
    /// Create a new `Apply` patch record.
    #[allow(clippy::too_many_arguments)]
    pub fn new_apply(
        target_id: impl Into<String>,
        target_claim_key: Option<String>,
        base_text: impl Into<String>,
        patched_text: impl Into<String>,
        patched_theme: Option<String>,
        patched_caveat: Option<String>,
        author: impl Into<String>,
        reason: impl Into<String>,
        created_at: Option<String>,
    ) -> Self {
        let target_id = target_id.into();
        let base_text = base_text.into();
        let patched_text = patched_text.into();
        let base_hash = compute_text_hash(&base_text);
        let created_at = created_at.unwrap_or_else(current_iso_timestamp);
        let patch_id = compute_patch_id(&target_id, &base_hash, &patched_text, &created_at);
        Self {
            patch_id,
            op: PatchOp::Apply,
            target_id,
            target_claim_key,
            base_text,
            base_hash,
            patched_text,
            patched_theme,
            patched_caveat,
            rollback_patch_id: None,
            author: author.into(),
            reason: reason.into(),
            created_at,
        }
    }

    /// Create a new `Rollback` patch record.
    pub fn new_rollback(
        target_id: impl Into<String>,
        rollback_patch_id: Option<String>,
        target_claim_key: Option<String>,
        base_text: impl Into<String>,
        author: impl Into<String>,
        reason: impl Into<String>,
        created_at: Option<String>,
    ) -> Self {
        let target_id = target_id.into();
        let base_text = base_text.into();
        let base_hash = compute_text_hash(&base_text);
        let created_at = created_at.unwrap_or_else(current_iso_timestamp);
        let target_ref = rollback_patch_id.as_deref().unwrap_or(&target_id);
        let patch_id = compute_patch_id(
            &format!("rb-{}", target_ref),
            &base_hash,
            "rollback",
            &created_at,
        );
        Self {
            patch_id,
            op: PatchOp::Rollback,
            target_id,
            target_claim_key,
            base_text,
            base_hash,
            patched_text: String::new(),
            patched_theme: None,
            patched_caveat: None,
            rollback_patch_id,
            author: author.into(),
            reason: reason.into(),
            created_at,
        }
    }

    /// Check if the claim text matches the recorded `base_hash` (drift check).
    pub fn matches_base(&self, claim_text: &str) -> bool {
        compute_text_hash(claim_text) == self.base_hash
    }

    /// Overlay this patch onto a `DurableRecord`. Returns true if modified.
    pub fn overlay_onto_durable(&self, record: &mut DurableRecord) -> bool {
        if self.op != PatchOp::Apply {
            return false;
        }
        let mut modified = false;
        if !self.patched_text.trim().is_empty() && record.claim != self.patched_text {
            record.claim = self.patched_text.clone();
            modified = true;
        }
        if let Some(theme) = self.patched_theme.as_deref().filter(|&t| t != record.theme) {
            record.theme = theme.to_string();
            modified = true;
        }
        modified
    }

    /// Overlay this patch onto assertion text and theme option. Returns true if modified.
    pub fn overlay_onto_claim_parts(&self, claim: &mut String, theme: &mut Option<String>) -> bool {
        if self.op != PatchOp::Apply {
            return false;
        }
        let mut modified = false;
        if !self.patched_text.trim().is_empty() && claim.as_str() != self.patched_text {
            *claim = self.patched_text.clone();
            modified = true;
        }
        if let Some(new_theme) = self
            .patched_theme
            .as_deref()
            .filter(|&t| theme.as_deref() != Some(t))
        {
            *theme = Some(new_theme.to_string());
            modified = true;
        }
        modified
    }
}

/// Compute SHA-256 hash of a text span for drift verification.
pub fn compute_text_hash(text: &str) -> String {
    let mut h = Sha256::new();
    h.update(text.trim().as_bytes());
    format!("{:x}", h.finalize())[..16].to_string()
}

/// Compute a deterministic patch ID: `hp-<sha256[..16]>`.
pub fn compute_patch_id(
    target_id: &str,
    base_hash: &str,
    patched_text: &str,
    created_at: &str,
) -> String {
    let mut h = Sha256::new();
    h.update(target_id.trim().as_bytes());
    h.update(b"|");
    h.update(base_hash.trim().as_bytes());
    h.update(b"|");
    h.update(patched_text.trim().as_bytes());
    h.update(b"|");
    h.update(created_at.trim().as_bytes());
    format!("hp-{:x}", h.finalize())[..19].to_string()
}

/// Formats current UTC timestamp as ISO-8601 without external dependencies.
pub fn current_iso_timestamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now();
    let duration = now.duration_since(UNIX_EPOCH).unwrap_or_default();
    let secs = duration.as_secs();
    let days = secs / 86400;
    let rem_secs = secs % 86400;
    let hours = rem_secs / 3600;
    let mins = (rem_secs % 3600) / 60;
    let s = rem_secs % 60;

    let z = days as i64 + 719468;
    let era = (if z >= 0 { z } else { z - 146096 }) / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };

    format!("{y:04}-{m:02}-{d:02}T{hours:02}:{mins:02}:{s:02}Z")
}

/// Folded state of the patch ledger.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct PatchLedgerState {
    /// Active effective patches indexed by `target_id`.
    pub active_by_target: BTreeMap<String, HumanPatchRecord>,
    /// Active effective patches indexed by `target_claim_key`.
    pub active_by_claim_key: BTreeMap<String, HumanPatchRecord>,
    /// Root (FIRST) record of each target's currently effective chain — the
    /// apply whose `base_hash` upstream truth must still match for the whole
    /// chain to be grounded. After a full rollback a fresh chain may start
    /// from NEWER upstream text than the target's first-ever apply, so
    /// grounding must consult this, not the head of history.
    pub active_chain_root: BTreeMap<String, HumanPatchRecord>,
    /// Chronological audit history of all patch operations per `target_id`.
    pub history_by_target: BTreeMap<String, Vec<HumanPatchRecord>>,
    /// Status of each patch ID.
    pub patch_statuses: BTreeMap<String, PatchStatus>,
    /// Total records in the ledger.
    pub total_records: usize,
    /// Number of currently active patches.
    pub active_count: usize,
    /// Number of rolled back patches.
    pub rolled_back_count: usize,
    /// Number of applies rejected as concurrent lost-update conflicts.
    pub conflicted_count: usize,
}

impl PatchLedgerState {
    /// Target-level lookup (CLI semantics): find the active patch for a
    /// target addressed by `claim_id` and/or `claim_key`. The claim-id
    /// fallback matches the patch that is active FOR that target.
    pub fn get_active_patch(
        &self,
        claim_id: &str,
        claim_key: Option<&str>,
    ) -> Option<&HumanPatchRecord> {
        if let Some(key) = claim_key {
            if let Some(p) = self.active_by_claim_key.get(key) {
                return Some(p);
            }
            // Patch targeted by claim key directly (`--target ck-...`).
            if let Some(p) = self.active_by_target.get(key) {
                return Some(p);
            }
        }
        self.active_by_target.get(claim_id)
    }

    /// Record-level binding used by PROJECTION overlays (P1): two runs CAN
    /// emit active records that share a `claim_id` while carrying distinct
    /// claim keys — a patch bound to one record's `target_claim_key` must
    /// never overlay onto the other record. Key-bound patches apply only to
    /// the exact key; patches recorded against a bare claim id (no key
    /// binding, e.g. a `--force` apply) fall back to claim-id matching.
    pub fn get_active_patch_for_record(
        &self,
        claim_id: &str,
        claim_key: Option<&str>,
    ) -> Option<&HumanPatchRecord> {
        if let Some(key) = claim_key {
            if let Some(p) = self.active_by_claim_key.get(key) {
                return Some(p);
            }
            if let Some(p) = self.active_by_target.get(key) {
                return Some(p);
            }
        }
        self.active_by_target
            .get(claim_id)
            .filter(|p| p.target_claim_key.is_none() || p.target_claim_key.as_deref() == claim_key)
    }

    /// Resolve a user-supplied target (claim id OR claim key) to the
    /// canonical `target_id` used in this ledger, if any patch history exists
    /// for it. Returns `None` when the ledger has never seen this target.
    pub fn resolve_target_id(&self, target: &str) -> Option<String> {
        if self.history_by_target.contains_key(target) {
            return Some(target.to_string());
        }
        self.history_by_target
            .values()
            .flatten()
            .find(|r| r.target_claim_key.as_deref() == Some(target))
            .map(|r| r.target_id.clone())
    }

    /// Drift gate for projection (P1): the root of the target's CURRENTLY
    /// EFFECTIVE chain (see `active_chain_root`) must have been based on
    /// exactly the record's current upstream text — otherwise the upstream
    /// assertion was rewritten after the chain was authored and overlaying
    /// would replace newer truth with a stale human edit. Returns `false` on
    /// drift: the overlay must be skipped.
    ///
    /// Two deliberate properties:
    /// - After a FULL rollback the next apply starts a fresh chain rooted in
    ///   whatever upstream text is current then — the retired chain's root
    ///   must not veto it.
    /// - A forced apply (`--force`, empty `base_text`) skipped base
    ///   verification at authoring time by explicit operator choice, so its
    ///   chain stays grounded.
    pub fn chain_grounded_on(&self, patch: &HumanPatchRecord, current_text: &str) -> bool {
        let Some(root) = self.active_chain_root.get(&patch.target_id) else {
            return false;
        };
        if root.base_text.is_empty() {
            return true;
        }
        root.matches_base(current_text)
    }

    /// Check if a claim has an active patch.
    pub fn has_active_patch(&self, claim_id: &str, claim_key: Option<&str>) -> bool {
        self.get_active_patch(claim_id, claim_key).is_some()
    }

    /// Retrieve audit history for a specific target.
    pub fn history_for_target(&self, target_id: &str) -> &[HumanPatchRecord] {
        self.history_by_target
            .get(target_id)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }
}

/// Fold append-only patch ledger records into current effective state.
///
/// Deterministic reduction:
/// - Records are processed in chronological order.
/// - Each target maintains an applied stack.
/// - An `Apply` pushes to the stack ONLY if its `base_hash` matches the
///   current top of stack's effective text — an apply based on an already
///   superseded revision is a concurrent lost-update conflict, rejected and
///   marked [`PatchStatus::Conflicted`] rather than silently stacked.
/// - A `Rollback` pops or removes the targeted patch from the stack, marking it `RolledBack`.
/// - After all operations for a target: the top of the stack is `Active`, preceding items are `Superseded`.
pub fn fold_patch_ledger(records: &[HumanPatchRecord]) -> PatchLedgerState {
    let mut history_by_target: BTreeMap<String, Vec<HumanPatchRecord>> = BTreeMap::new();
    let mut target_order: Vec<String> = Vec::new();
    let mut target_keys: BTreeMap<String, String> = BTreeMap::new();

    for r in records {
        if !history_by_target.contains_key(&r.target_id) {
            target_order.push(r.target_id.clone());
        }
        history_by_target
            .entry(r.target_id.clone())
            .or_default()
            .push(r.clone());
        if let Some(ref k) = r.target_claim_key {
            target_keys.insert(r.target_id.clone(), k.clone());
        }
    }

    let mut active_by_target: BTreeMap<String, HumanPatchRecord> = BTreeMap::new();
    let mut active_by_claim_key: BTreeMap<String, HumanPatchRecord> = BTreeMap::new();
    let mut active_chain_root: BTreeMap<String, HumanPatchRecord> = BTreeMap::new();
    let mut patch_statuses: BTreeMap<String, PatchStatus> = BTreeMap::new();
    let mut rolled_back_count = 0;
    let mut conflicted_count = 0;

    for target_id in &target_order {
        let history = history_by_target.get(target_id).unwrap();
        let mut stack: Vec<HumanPatchRecord> = Vec::new();
        let mut rolled_back_ids: BTreeSet<String> = BTreeSet::new();
        let mut conflicted_ids: BTreeSet<String> = BTreeSet::new();

        for ev in history {
            match ev.op {
                PatchOp::Apply => {
                    // Lost-update gate: an apply must be based on the
                    // revision that is still effective. The first apply of a
                    // chain (empty stack) is verified against UPSTREAM truth
                    // at projection time instead (chain_grounded_on).
                    if let Some(top) = stack.last()
                        && ev.base_hash != compute_text_hash(&top.patched_text)
                    {
                        conflicted_ids.insert(ev.patch_id.clone());
                        continue;
                    }
                    stack.push(ev.clone());
                }
                PatchOp::Rollback => {
                    // Rolling back a patch retires it AND every revision
                    // stacked on top of it: each later apply was based on the
                    // retired text, so leaving them would produce a chain whose
                    // root no longer matches upstream (silently skipped as
                    // drift at projection time). Truncating is loud instead —
                    // every retired revision is reported RolledBack.
                    if let Some(ref target_patch_id) = ev.rollback_patch_id {
                        if let Some(pos) = stack.iter().position(|p| &p.patch_id == target_patch_id)
                        {
                            for popped in stack.drain(pos..) {
                                rolled_back_ids.insert(popped.patch_id);
                            }
                        } else {
                            rolled_back_ids.insert(target_patch_id.clone());
                        }
                    } else if let Some(popped) = stack.pop() {
                        rolled_back_ids.insert(popped.patch_id);
                    }
                }
            }
        }

        for (i, p) in stack.iter().enumerate() {
            let status = if i + 1 == stack.len() {
                PatchStatus::Active
            } else {
                PatchStatus::Superseded
            };
            patch_statuses.insert(p.patch_id.clone(), status);
        }

        for r_id in &rolled_back_ids {
            patch_statuses.insert(r_id.clone(), PatchStatus::RolledBack);
            rolled_back_count += 1;
        }
        for r_id in &conflicted_ids {
            patch_statuses.insert(r_id.clone(), PatchStatus::Conflicted);
            conflicted_count += 1;
        }

        if let Some(top) = stack.last() {
            active_by_target.insert(target_id.clone(), top.clone());
            if let Some(k) = top
                .target_claim_key
                .as_ref()
                .or_else(|| target_keys.get(target_id))
            {
                active_by_claim_key.insert(k.clone(), top.clone());
            }
            // Grounding root: the FIRST apply of the chain that is still
            // effective (its base_hash pins the upstream text the whole
            // chain is grounded in).
            if let Some(root) = stack.first() {
                active_chain_root.insert(target_id.clone(), root.clone());
            }
        }
    }

    let active_count = active_by_target.len();
    PatchLedgerState {
        active_by_target,
        active_by_claim_key,
        active_chain_root,
        history_by_target,
        patch_statuses,
        total_records: records.len(),
        active_count,
        rolled_back_count,
        conflicted_count,
    }
}

/// Apply effective patches to a slice of `DurableRecord`s in-place.
/// Returns the number of records modified.
///
/// Patches whose base no longer matches the record's current (upstream) text
/// are SKIPPED with a warning — upstream rewrites win over stale human edits,
/// and the drift is surfaced instead of silently rebinding.
pub fn apply_patches_to_durable_records(
    records: &mut [DurableRecord],
    state: &PatchLedgerState,
) -> usize {
    let mut modified_count = 0;
    let mut drift_skips: Vec<(&str, &str)> = Vec::new();
    for rec in records {
        let Some(patch) = state.get_active_patch_for_record(&rec.claim_id, Some(&rec.claim_key))
        else {
            continue;
        };
        if !state.chain_grounded_on(patch, &rec.claim) {
            drift_skips.push((&patch.patch_id, &rec.claim_id));
            continue;
        }
        if patch.overlay_onto_durable(rec) {
            modified_count += 1;
        }
    }
    for (patch_id, claim_id) in drift_skips {
        eprintln!(
            "warning: human patch {patch_id} skipped for claim {claim_id}: upstream text drifted from the patch base"
        );
    }
    modified_count
}

// ---- Diff & Inspection ----

/// Kind of a diff line.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiffKind {
    Unchanged,
    Addition,
    Deletion,
}

/// A line in a patch diff.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DiffLine {
    pub kind: DiffKind,
    pub text: String,
}

/// Structured diff between base state and patched state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PatchDiff {
    pub patch_id: String,
    pub target_id: String,
    pub base_text: String,
    pub patched_text: String,
    pub theme_changed: bool,
    pub old_theme: Option<String>,
    pub new_theme: Option<String>,
    pub caveat_changed: bool,
    pub old_caveat: Option<String>,
    pub new_caveat: Option<String>,
    pub diff_lines: Vec<DiffLine>,
}

/// Generate structured diff for a patch record against base context.
pub fn diff_patch(
    patch: &HumanPatchRecord,
    current_theme: Option<&str>,
    current_caveat: Option<&str>,
) -> PatchDiff {
    let theme_changed =
        patch.patched_theme.is_some() && patch.patched_theme.as_deref() != current_theme;
    let caveat_changed =
        patch.patched_caveat.is_some() && patch.patched_caveat.as_deref() != current_caveat;

    let mut diff_lines = Vec::new();
    if patch.op == PatchOp::Rollback {
        diff_lines.push(DiffLine {
            kind: DiffKind::Deletion,
            text: format!(
                "[ROLLBACK patch {}]",
                patch.rollback_patch_id.as_deref().unwrap_or("latest")
            ),
        });
    } else if patch.base_text == patch.patched_text {
        diff_lines.push(DiffLine {
            kind: DiffKind::Unchanged,
            text: patch.base_text.clone(),
        });
    } else {
        if !patch.base_text.is_empty() {
            diff_lines.push(DiffLine {
                kind: DiffKind::Deletion,
                text: patch.base_text.clone(),
            });
        }
        if !patch.patched_text.is_empty() {
            diff_lines.push(DiffLine {
                kind: DiffKind::Addition,
                text: patch.patched_text.clone(),
            });
        }
    }

    PatchDiff {
        patch_id: patch.patch_id.clone(),
        target_id: patch.target_id.clone(),
        base_text: patch.base_text.clone(),
        patched_text: patch.patched_text.clone(),
        theme_changed,
        old_theme: current_theme.map(|s| s.to_string()),
        new_theme: patch.patched_theme.clone(),
        caveat_changed,
        old_caveat: current_caveat.map(|s| s.to_string()),
        new_caveat: patch.patched_caveat.clone(),
        diff_lines,
    }
}

/// Format unified-style diff text for CLI / audit output.
pub fn format_diff(diff: &PatchDiff) -> String {
    let mut out = format!(
        "--- target: {}\n+++ patch: {}\n",
        diff.target_id, diff.patch_id
    );
    for line in &diff.diff_lines {
        match line.kind {
            DiffKind::Unchanged => out.push_str(&format!("  {}\n", line.text)),
            DiffKind::Deletion => out.push_str(&format!("- {}\n", line.text)),
            DiffKind::Addition => out.push_str(&format!("+ {}\n", line.text)),
        }
    }
    if diff.theme_changed {
        out.push_str(&format!(
            " [theme: {} -> {}]\n",
            diff.old_theme.as_deref().unwrap_or("(none)"),
            diff.new_theme.as_deref().unwrap_or("(none)")
        ));
    }
    if diff.caveat_changed {
        out.push_str(&format!(
            " [caveat: {} -> {}]\n",
            diff.old_caveat.as_deref().unwrap_or("(none)"),
            diff.new_caveat.as_deref().unwrap_or("(none)")
        ));
    }
    out
}

// ---- Audit Entry ----

/// Comprehensive audit row for patch tracking.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PatchAuditEntry {
    pub patch_id: String,
    pub op: PatchOp,
    pub status: PatchStatus,
    pub target_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_claim_key: Option<String>,
    pub author: String,
    pub reason: String,
    pub created_at: String,
    pub base_text: String,
    pub patched_text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rollback_patch_id: Option<String>,
}

/// Collect audit entries from folded patch ledger state.
pub fn audit_patches(state: &PatchLedgerState, target_id: Option<&str>) -> Vec<PatchAuditEntry> {
    let mut entries = Vec::new();
    let targets: Vec<&String> = match target_id {
        Some(t) => state
            .history_by_target
            .keys()
            .filter(|k| k.as_str() == t)
            .collect(),
        None => state.history_by_target.keys().collect(),
    };

    for target in targets {
        if let Some(history) = state.history_by_target.get(target) {
            for rec in history {
                // A rollback record is itself never "active" or "superseded";
                // it is displayed as the operation it performed.
                let status = if rec.op == PatchOp::Rollback {
                    PatchStatus::RolledBack
                } else {
                    state
                        .patch_statuses
                        .get(&rec.patch_id)
                        .copied()
                        .unwrap_or(PatchStatus::Superseded)
                };
                entries.push(PatchAuditEntry {
                    patch_id: rec.patch_id.clone(),
                    op: rec.op,
                    status,
                    target_id: rec.target_id.clone(),
                    target_claim_key: rec.target_claim_key.clone(),
                    author: rec.author.clone(),
                    reason: rec.reason.clone(),
                    created_at: rec.created_at.clone(),
                    base_text: rec.base_text.clone(),
                    patched_text: rec.patched_text.clone(),
                    rollback_patch_id: rec.rollback_patch_id.clone(),
                });
            }
        }
    }
    entries
}

// ---- I/O Helpers ----

/// Read all `HumanPatchRecord`s from a `.ovp/crystal/patches.jsonl` file.
/// If the file does not exist, returns `Ok(vec![])`.
pub fn read_patch_ledger(path: &Path) -> Result<Vec<HumanPatchRecord>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let file = std::fs::File::open(path)
        .map_err(|e| format!("reading patch ledger {}: {e}", path.display()))?;
    let reader = BufReader::new(file);
    let mut records = Vec::new();
    for (i, line) in reader.lines().enumerate() {
        let line = line.map_err(|e| format!("{}:{}: {e}", path.display(), i + 1))?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let record: HumanPatchRecord = serde_json::from_str(trimmed)
            .map_err(|e| format!("{}:{}: malformed patch record: {e}", path.display(), i + 1))?;
        records.push(record);
    }
    Ok(records)
}

/// Append a single `HumanPatchRecord` line to `.ovp/crystal/patches.jsonl`.
///
/// Durability mirrors `ovp_intake::vaultops::append_jsonl` (which ovp-domain
/// cannot reuse without inverting the dependency): `flush()` on a `File` is a
/// no-op, so the record is `sync_data`-ed before returning — a power loss
/// right after the CLI reports success must not drop a human correction —
/// and a freshly created ledger's parent directory is fsynced so the new
/// directory entry itself survives.
pub fn append_patch_record(path: &Path, record: &HumanPatchRecord) -> Result<(), String> {
    if let Some(parent) = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty() && !p.exists())
    {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating directory {}: {e}", parent.display()))?;
    }
    // Creation is derived from the ATOMIC open, not an exists() probe — a
    // concurrent creator between probe and open would otherwise skip the
    // directory fsync exactly when a new entry needed it (TOCTOU).
    let (mut file, created) = match OpenOptions::new().create_new(true).append(true).open(path) {
        Ok(f) => (f, true),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (
            OpenOptions::new()
                .append(true)
                .open(path)
                .map_err(|e| format!("opening patch ledger {}: {e}", path.display()))?,
            false,
        ),
        Err(e) => return Err(format!("opening patch ledger {}: {e}", path.display())),
    };
    let serialized = serde_json::to_string(record)
        .map_err(|e| format!("serializing patch record {}: {e}", record.patch_id))?;
    writeln!(file, "{serialized}")
        .map_err(|e| format!("appending to patch ledger {}: {e}", path.display()))?;
    file.sync_data()
        .map_err(|e| format!("syncing patch ledger {}: {e}", path.display()))?;
    if created && let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
        sync_dir(parent)?;
    }
    Ok(())
}

/// fsync a directory so a newly created file's directory entry survives a
/// power loss (syncing file contents does not persist the entry itself).
/// On non-Unix platforms (e.g. Windows), opening a directory via File::open
/// fails with ERROR_ACCESS_DENIED, so directory fsync is skipped.
fn sync_dir(dir: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        let f = std::fs::File::open(dir)
            .map_err(|e| format!("opening directory {}: {e}", dir.display()))?;
        f.sync_all()
            .map_err(|e| format!("syncing directory {}: {e}", dir.display()))?;
    }
    #[cfg(not(unix))]
    {
        let _ = dir;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_patch_apply_fold_and_diff() {
        let rec1 = HumanPatchRecord::new_apply(
            "c01",
            Some("ck-123".into()),
            "Knowledge graph enables fast retrieval.",
            "Knowledge graph enables deterministic fast retrieval.",
            Some("Architecture".into()),
            None,
            "operator",
            "clarify determinism",
            Some("2026-09-11T08:00:00Z".into()),
        );

        assert!(rec1.matches_base("Knowledge graph enables fast retrieval."));
        assert!(!rec1.matches_base("Something else"));

        let diff = diff_patch(&rec1, Some("OldArchitecture"), None);
        let formatted = format_diff(&diff);
        assert!(formatted.contains("- Knowledge graph enables fast retrieval."));
        assert!(formatted.contains("+ Knowledge graph enables deterministic fast retrieval."));
        assert!(formatted.contains("[theme: OldArchitecture -> Architecture]"));

        let records = vec![rec1.clone()];
        let state = fold_patch_ledger(&records);
        assert_eq!(state.active_count, 1);
        let active = state
            .get_active_patch("c01", Some("ck-123"))
            .expect("active patch found");
        assert_eq!(
            active.patched_text,
            "Knowledge graph enables deterministic fast retrieval."
        );
        assert_eq!(
            state.patch_statuses.get(&rec1.patch_id),
            Some(&PatchStatus::Active)
        );
    }

    #[test]
    fn test_patch_revision_supersede_and_rollback() {
        // Step 1: Initial apply
        let p1 = HumanPatchRecord::new_apply(
            "c01",
            None,
            "Claim text v0",
            "Claim text v1",
            None,
            None,
            "alice",
            "first edit",
            Some("2026-09-11T08:01:00Z".into()),
        );

        // Step 2: Second apply (revising v1 -> v2)
        let p2 = HumanPatchRecord::new_apply(
            "c01",
            None,
            "Claim text v1",
            "Claim text v2",
            None,
            None,
            "bob",
            "second edit",
            Some("2026-09-11T08:02:00Z".into()),
        );

        let records_step2 = vec![p1.clone(), p2.clone()];
        let state_step2 = fold_patch_ledger(&records_step2);
        assert_eq!(state_step2.active_count, 1);
        assert_eq!(
            state_step2.patch_statuses.get(&p1.patch_id),
            Some(&PatchStatus::Superseded)
        );
        assert_eq!(
            state_step2.patch_statuses.get(&p2.patch_id),
            Some(&PatchStatus::Active)
        );
        assert_eq!(
            state_step2
                .get_active_patch("c01", None)
                .unwrap()
                .patched_text,
            "Claim text v2"
        );

        // Step 3: Rollback p2 -> should restore p1
        let p3 = HumanPatchRecord::new_rollback(
            "c01",
            Some(p2.patch_id.clone()),
            None,
            "Claim text v2",
            "carol",
            "revert p2",
            Some("2026-09-11T08:03:00Z".into()),
        );

        let records_step3 = vec![p1.clone(), p2.clone(), p3.clone()];
        let state_step3 = fold_patch_ledger(&records_step3);
        assert_eq!(state_step3.active_count, 1);
        assert_eq!(
            state_step3.patch_statuses.get(&p2.patch_id),
            Some(&PatchStatus::RolledBack)
        );
        assert_eq!(
            state_step3.patch_statuses.get(&p1.patch_id),
            Some(&PatchStatus::Active)
        );
        assert_eq!(
            state_step3
                .get_active_patch("c01", None)
                .unwrap()
                .patched_text,
            "Claim text v1"
        );

        // Step 4: Rollback p1 -> should leave no active patch (fallback to base truth)
        let p4 = HumanPatchRecord::new_rollback(
            "c01",
            Some(p1.patch_id.clone()),
            None,
            "Claim text v1",
            "carol",
            "revert p1 back to base",
            Some("2026-09-11T08:04:00Z".into()),
        );

        let records_step4 = vec![p1.clone(), p2.clone(), p3.clone(), p4.clone()];
        let state_step4 = fold_patch_ledger(&records_step4);
        assert_eq!(state_step4.active_count, 0);
        assert_eq!(state_step4.get_active_patch("c01", None), None);
        assert_eq!(
            state_step4.patch_statuses.get(&p1.patch_id),
            Some(&PatchStatus::RolledBack)
        );
    }

    #[test]
    fn test_patch_audit_trail() {
        let p1 = HumanPatchRecord::new_apply(
            "c02",
            None,
            "Base quote",
            "Patched quote",
            None,
            None,
            "alice",
            "fix typo",
            Some("2026-09-11T08:00:00Z".into()),
        );
        let p2 = HumanPatchRecord::new_rollback(
            "c02",
            Some(p1.patch_id.clone()),
            None,
            "Patched quote",
            "alice",
            "undo fix",
            Some("2026-09-11T08:05:00Z".into()),
        );
        let records = vec![p1.clone(), p2.clone()];
        let state = fold_patch_ledger(&records);
        let audit = audit_patches(&state, Some("c02"));
        assert_eq!(audit.len(), 2);
        assert_eq!(audit[0].op, PatchOp::Apply);
        assert_eq!(audit[0].status, PatchStatus::RolledBack);
        assert_eq!(audit[1].op, PatchOp::Rollback);
    }

    #[test]
    fn test_keyed_patch_never_leaks_across_shared_claim_id() {
        // Two active records share claim_id "c01" but carry distinct keys
        // (possible across runs). A patch bound to ck-a must not reach ck-B.
        let p = HumanPatchRecord::new_apply(
            "c01",
            Some("ck-a".into()),
            "Shared base text",
            "Patched for record A only",
            None,
            None,
            "alice",
            "edit record A",
            Some("2026-09-11T09:00:00Z".into()),
        );
        let state = fold_patch_ledger(std::slice::from_ref(&p));

        // Exact-key binding applies.
        assert!(
            state
                .get_active_patch_for_record("c01", Some("ck-a"))
                .is_some()
        );
        // A different record sharing the claim id must NOT receive it …
        assert!(
            state
                .get_active_patch_for_record("c01", Some("ck-b"))
                .is_none()
        );
        // … and neither may an un-keyed row (binding exists, it just is not ours).
        assert!(state.get_active_patch_for_record("c01", None).is_none());

        // Target-level lookup (CLI `--target c01`) still finds it — the patch
        // IS the active patch for that target; the strictness only governs
        // which RECORD it may overlay onto.
        assert!(state.get_active_patch("c01", None).is_some());

        // An un-keyed (forced) patch still falls back to bare claim-id matching.
        let forced = HumanPatchRecord::new_apply(
            "c09",
            None,
            "Forced base",
            "Forced patch",
            None,
            None,
            "bob",
            "force",
            Some("2026-09-11T09:01:00Z".into()),
        );
        let state2 = fold_patch_ledger(&[forced]);
        assert!(
            state2
                .get_active_patch_for_record("c09", Some("ck-z"))
                .is_some()
        );
        assert!(state2.get_active_patch_for_record("c09", None).is_some());
    }

    #[test]
    fn test_rollback_of_non_head_retires_whole_chain_above_it() {
        let p1 = HumanPatchRecord::new_apply(
            "c01",
            Some("ck-1".into()),
            "upstream",
            "A",
            None,
            None,
            "a",
            "e",
            Some("2026-09-11T12:00:00Z".into()),
        );
        let p2 = HumanPatchRecord::new_apply(
            "c01",
            Some("ck-1".into()),
            "A",
            "B",
            None,
            None,
            "a",
            "e",
            Some("2026-09-11T12:00:01Z".into()),
        );
        let rb = HumanPatchRecord::new_rollback(
            "c01",
            Some(p1.patch_id.clone()),
            None,
            "A",
            "a",
            "undo p1",
            Some("2026-09-11T12:00:02Z".into()),
        );
        let state = fold_patch_ledger(&[p1.clone(), p2.clone(), rb.clone()]);
        assert_eq!(
            state.patch_statuses.get(&p1.patch_id),
            Some(&PatchStatus::RolledBack)
        );
        assert_eq!(
            state.patch_statuses.get(&p2.patch_id),
            Some(&PatchStatus::RolledBack)
        );
        assert!(state.get_active_patch("c01", None).is_none());
        assert!(!state.active_chain_root.contains_key("c01"));
        // Exactly the two applies count; the rollback record itself does not.
        assert_eq!(state.rolled_back_count, 2);
        let audit = audit_patches(&state, Some("c01"));
        assert_eq!(audit.len(), 3);
        assert_eq!(audit[2].op, PatchOp::Rollback);
        assert_eq!(audit[2].status, PatchStatus::RolledBack);
        // Claim-key alias resolves to the canonical target id.
        assert_eq!(state.resolve_target_id("ck-1").as_deref(), Some("c01"));
        assert_eq!(state.resolve_target_id("c01").as_deref(), Some("c01"));
        assert!(state.resolve_target_id("nope").is_none());
    }

    #[test]
    fn test_concurrent_apply_on_superseded_revision_is_conflicted() {
        // Two operators both load v0. Alice saves first; Bob's apply is still
        // based on v0, i.e. on a revision that is no longer effective.
        let alice = HumanPatchRecord::new_apply(
            "c01",
            None,
            "v0",
            "v1-alice",
            None,
            None,
            "alice",
            "edit",
            Some("2026-09-11T10:00:00Z".into()),
        );
        let bob = HumanPatchRecord::new_apply(
            "c01",
            None,
            "v0",
            "v1-bob",
            None,
            None,
            "bob",
            "edit",
            Some("2026-09-11T10:00:01Z".into()),
        );
        let state = fold_patch_ledger(&[alice.clone(), bob.clone()]);

        assert_eq!(
            state.patch_statuses.get(&alice.patch_id),
            Some(&PatchStatus::Active)
        );
        assert_eq!(
            state.patch_statuses.get(&bob.patch_id),
            Some(&PatchStatus::Conflicted)
        );
        assert_eq!(state.conflicted_count, 1);
        assert_eq!(state.active_count, 1);
        assert_eq!(
            state.get_active_patch("c01", None).unwrap().patched_text,
            "v1-alice"
        );
        // The conflict stays visible in the audit history.
        assert_eq!(state.history_by_target["c01"].len(), 2);

        // A revision correctly based on the effective text still stacks.
        let carol = HumanPatchRecord::new_apply(
            "c01",
            None,
            "v1-alice",
            "v2-carol",
            None,
            None,
            "carol",
            "edit",
            Some("2026-09-11T10:00:02Z".into()),
        );
        let state = fold_patch_ledger(&[alice.clone(), bob, carol.clone()]);
        assert_eq!(
            state.patch_statuses.get(&alice.patch_id),
            Some(&PatchStatus::Superseded)
        );
        assert_eq!(
            state.patch_statuses.get(&carol.patch_id),
            Some(&PatchStatus::Active)
        );
        assert_eq!(state.active_chain_root["c01"].patch_id, alice.patch_id);
    }

    #[test]
    fn test_fresh_chain_after_full_rollback_grounds_on_newer_upstream() {
        // Chain 1 was authored against old upstream text, then fully rolled
        // back. Upstream is later rewritten; chain 2 is authored against the
        // NEW text. Grounding must consult chain 2's root, not chain 1's.
        let old = HumanPatchRecord::new_apply(
            "c01",
            None,
            "old upstream",
            "old fix",
            None,
            None,
            "alice",
            "e",
            Some("2026-09-11T11:00:00Z".into()),
        );
        let rb = HumanPatchRecord::new_rollback(
            "c01",
            Some(old.patch_id.clone()),
            None,
            "old fix",
            "alice",
            "undo",
            Some("2026-09-11T11:00:01Z".into()),
        );
        let fresh = HumanPatchRecord::new_apply(
            "c01",
            None,
            "new upstream",
            "new fix",
            None,
            None,
            "alice",
            "e",
            Some("2026-09-11T11:00:02Z".into()),
        );
        let state = fold_patch_ledger(&[old.clone(), rb, fresh.clone()]);

        assert_eq!(state.active_chain_root["c01"].patch_id, fresh.patch_id);
        assert!(state.chain_grounded_on(&fresh, "new upstream"));
        assert!(!state.chain_grounded_on(&fresh, "old upstream"));
        assert_eq!(
            state.patch_statuses.get(&old.patch_id),
            Some(&PatchStatus::RolledBack)
        );
    }

    #[test]
    fn test_drift_skips_overlay_and_grounded_chain_applies() {
        fn record(claim: &str, key: &str) -> DurableRecord {
            DurableRecord {
                claim_key: key.into(),
                claim_id: "c01".into(),
                claim: claim.into(),
                theme: "t".into(),
                theme_id: None,
                source_cases: vec!["case1".into()],
                citations: Vec::new(),
                provenance_score: 0.9,
                provenance_class: crate::crystal::ProvenanceClass::Durable,
                strength: crate::crystal::StrengthClass::Supported,
                strength_rationale: "good".into(),
                final_class: crate::crystal::FinalClass::Durable,
                run_id: "r1".into(),
                status: crate::crystal::CrystalStatus::Active,
            }
        }

        let p = HumanPatchRecord::new_apply(
            "c01",
            Some("ck-a".into()),
            "Original upstream text",
            "Human corrected text",
            None,
            None,
            "alice",
            "fix overreach",
            Some("2026-09-11T09:10:00Z".into()),
        );
        let state = fold_patch_ledger(&[p]);

        // Grounded: record text still matches the patch base → overlay applies.
        let mut grounded = vec![record("Original upstream text", "ck-a")];
        assert_eq!(apply_patches_to_durable_records(&mut grounded, &state), 1);
        assert_eq!(grounded[0].claim, "Human corrected text");

        // Drift: upstream rewrote the claim → the stale patch is skipped.
        let mut drifted = vec![record("Rewritten upstream text", "ck-a")];
        assert_eq!(apply_patches_to_durable_records(&mut drifted, &state), 0);
        assert_eq!(drifted[0].claim, "Rewritten upstream text");

        // Forced patch (empty base text): operator skipped verification at
        // apply time, so the overlay stays grounded even after rewrites.
        let forced = HumanPatchRecord::new_apply(
            "c01",
            None,
            "",
            "Forced text",
            None,
            None,
            "bob",
            "force",
            Some("2026-09-11T09:11:00Z".into()),
        );
        let state2 = fold_patch_ledger(&[forced]);
        let mut any_text = vec![record("Whatever the upstream now says", "ck-q")];
        assert_eq!(apply_patches_to_durable_records(&mut any_text, &state2), 1);
        assert_eq!(any_text[0].claim, "Forced text");
    }
}
