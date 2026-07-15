//! Filesystem readers shared by the live server and the static publisher.
//!
//! These do I/O (unlike the pure `bodies` builders) and take an explicit
//! `vault_root` + `VaultLayout` so neither `ovp-server`'s `AppState` nor the
//! publisher has to reimplement the reads (which is where drift creeps in).

use std::path::{Path, PathBuf};

use ovp_domain::VaultLayout;
use ovp_domain::crystal::themes::{ThemesFile, UNCLASSIFIED_THEME};
use ovp_domain::crystal::{CrystalStatus, DurableRecord, StoreEvent, fold_ledger};
use ovp_intake::read_jsonl;

use crate::{MAX_SOURCE_DOC_BYTES, is_plain_relative};

/// Fold the crystal ledger to its ACTIVE durable records and relabel each with
/// the semantic display theme from `themes.json` — mirroring
/// `ovp-index::build_claims` so every surface (themes, graph, claim pages)
/// shows the same themes. A missing/corrupt `themes.json` degrades to
/// passthrough (the reader must keep working; `ovp2 index` is where corruption
/// fails loud).
pub fn load_active_records(vault_root: &Path, layout: &VaultLayout) -> Vec<DurableRecord> {
    load_active_records_strict(vault_root, layout).unwrap_or_default()
}

/// Fallible variant for the PUBLISHER: an unreadable/malformed crystal ledger
/// is an ERROR, not an empty list. `load_active_records`'s "graceful degrade to
/// empty" is right for a live server (keep serving) but wrong for publishing —
/// it would deploy a site that silently removes every claim. A genuinely
/// MISSING ledger (fresh vault) is still `Ok(empty)`.
pub fn load_active_records_strict(
    vault_root: &Path,
    layout: &VaultLayout,
) -> Result<Vec<DurableRecord>, String> {
    let store = vault_root.join(layout.crystal_store_dir());
    let ledger = store.join("ledger.jsonl");
    // `read_jsonl` returns Ok(empty) for a missing file (fresh vault) and Err
    // for a present-but-corrupt one — propagate the latter.
    let events: Vec<StoreEvent> =
        read_jsonl(&ledger).map_err(|e| format!("crystal ledger {}: {e}", ledger.display()))?;
    let mut records: Vec<DurableRecord> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect();
    match ThemesFile::load(&store.join("themes.json")) {
        Ok(Some(themes)) => {
            for r in records.iter_mut() {
                r.theme = themes
                    .majority_label(&r.source_cases)
                    .unwrap_or_else(|| UNCLASSIFIED_THEME.to_string());
            }
        }
        Ok(None) => {}
        Err(e) => eprintln!("warning: ignoring themes.json ({e})"),
    }
    Ok(records)
}

/// Read a source's markdown from the vault, capped at `MAX_SOURCE_DOC_BYTES`.
/// Returns `(markdown, truncated, error)` — every failure mode becomes an
/// explicit error string so the endpoint always answers. Traversal-safe.
pub fn read_source_doc(
    vault_root: &Path,
    layout: &VaultLayout,
    rel_path: Option<&str>,
) -> (Option<String>, bool, Option<String>) {
    let Some(rel) = rel_path else {
        return (None, false, None);
    };
    if !is_plain_relative(rel) {
        return (None, false, Some("source path rejected".into()));
    }
    let recorded = vault_root.join(rel);
    let path = if recorded.is_file() {
        recorded
    } else if let Some(moved) = lifecycle_moved_path(vault_root, layout, rel) {
        moved
    } else {
        recorded
    };
    match std::fs::read_to_string(&path) {
        Ok(mut text) => {
            let truncated = text.len() > MAX_SOURCE_DOC_BYTES;
            if truncated {
                let mut cut = MAX_SOURCE_DOC_BYTES;
                while cut > 0 && !text.is_char_boundary(cut) {
                    cut -= 1;
                }
                text.truncate(cut);
            }
            (Some(text), truncated, None)
        }
        Err(e) => (None, false, Some(format!("{rel}: {e}"))),
    }
}

/// Lifecycle-move fallback: `SourceRow.rel_path` records the INTAKE location
/// (`50-Inbox/01-Raw/<month>/…`), but the daily lifecycle step moves processed
/// sources to `50-Inbox/03-Processed/<month>/…` keeping the trailing subpath.
/// When the recorded path misses and sits under the raw inbox dir, retry the
/// processed dir. `rel` is already traversal-checked by the caller.
pub fn lifecycle_moved_path(vault_root: &Path, layout: &VaultLayout, rel: &str) -> Option<PathBuf> {
    let raw_prefix = format!("{}/", layout.inbox_raw_dir());
    let rest = rel.strip_prefix(&raw_prefix)?;
    let (month, file) = rest.split_once('/')?;
    let candidate = vault_root.join(layout.processed_dir(month)).join(file);
    candidate.is_file().then_some(candidate)
}
