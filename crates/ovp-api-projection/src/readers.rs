//! Filesystem readers shared by the live server and the static publisher.
//!
//! These do I/O (unlike the pure `bodies` builders) and take an explicit
//! `vault_root` + `VaultLayout` so neither `ovp-server`'s `AppState` nor the
//! publisher has to reimplement the reads (which is where drift creeps in).

use std::path::{Path, PathBuf};

use ovp_domain::VaultLayout;
use ovp_domain::crystal::themes::{ThemesFile, UNCLASSIFIED_ID, UNCLASSIFIED_THEME};
use ovp_domain::crystal::lineage::{lineage_index, ClaimLineage};
use ovp_domain::crystal::{CrystalStatus, DurableRecord, StoreEvent, fold_ledger};
use ovp_intake::{read_jsonl, read_jsonl_strict};
use std::collections::BTreeMap;

use crate::{MAX_SOURCE_DOC_BYTES, is_plain_relative};

/// Load the full ledger events (missing file → empty). Live-portal readers use
/// the Skip policy (`read_jsonl`): a proven torn line is dropped with a warning
/// and the rest of the history keeps serving. The authoritative paths
/// (`ovp2 index`, the crystal patch / theme-page commands) read the same
/// ledger with `read_jsonl_strict` and fail loud, per this module's degrade
/// rule.
pub fn load_ledger_events(vault_root: &Path, layout: &VaultLayout) -> Vec<StoreEvent> {
    let ledger = vault_root
        .join(layout.crystal_store_dir())
        .join("ledger.jsonl");
    read_jsonl(&ledger).unwrap_or_default()
}

/// Lineage index for claim pages / graph detail (rebuildable projection).
pub fn load_lineage_index(vault_root: &Path, layout: &VaultLayout) -> BTreeMap<String, ClaimLineage> {
    lineage_index(&load_ledger_events(vault_root, layout))
}

/// Fold the crystal ledger to its ACTIVE durable records and relabel each with
/// the semantic display theme from `themes.json` — mirroring
/// `ovp-index::build_claims` so every surface (themes, graph, claim pages)
/// shows the same themes. A missing/corrupt `themes.json` degrades to
/// passthrough (the reader must keep working; `ovp2 index` is where corruption
/// fails loud).
pub fn load_active_records(vault_root: &Path, layout: &VaultLayout) -> Vec<DurableRecord> {
    let mut records = load_active_records_core(vault_root, layout, false).unwrap_or_default();
    // Live-server degrade: a corrupt human patch ledger keeps the unpatched
    // records serving (never an empty portal) — the corruption is reported,
    // and `ovp2 index` is where it fails loud.
    if let Err(e) = overlay_human_patches(&mut records, vault_root, layout) {
        eprintln!("warning: {e}");
    }
    records
}

/// Fallible variant for the PUBLISHER: an unreadable/malformed crystal ledger
/// OR human patch ledger is an ERROR, not an empty list.
/// `load_active_records`'s "graceful degrade" is right for a live server (keep
/// serving) but wrong for publishing — it would deploy a site that silently
/// removes every claim (or every human correction). A genuinely MISSING ledger
/// (fresh vault) is still `Ok(empty)`.
pub fn load_active_records_strict(
    vault_root: &Path,
    layout: &VaultLayout,
) -> Result<Vec<DurableRecord>, String> {
    let mut records = load_active_records_core(vault_root, layout, true)?;
    overlay_human_patches(&mut records, vault_root, layout)?;
    Ok(records)
}

/// Ledger + themes fold WITHOUT the human-patch overlay — the shared core of
/// both public loaders, which differ in how failures degrade. `strict`
/// selects the ledger reader: the strict loader rejects even a proven torn
/// `StoreEvent` (`read_jsonl_strict`). The live loader skips it with a warning
/// (`read_jsonl`).
fn load_active_records_core(
    vault_root: &Path,
    layout: &VaultLayout,
    strict: bool,
) -> Result<Vec<DurableRecord>, String> {
    let store = vault_root.join(layout.crystal_store_dir());
    let ledger = store.join("ledger.jsonl");
    // `read_jsonl` returns Ok(empty) for a missing file (fresh vault) and Err
    // for a present-but-corrupt one — propagate the latter.
    let events: Vec<StoreEvent> = if strict { read_jsonl_strict(&ledger) } else { read_jsonl(&ledger) }
        .map_err(|e| format!("crystal ledger {}: {e}", ledger.display()))?;
    let mut records: Vec<DurableRecord> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect();
    match ThemesFile::load(&store.join("themes.json")) {
        Ok(Some(themes)) => {
            for r in records.iter_mut() {
                // Route-by-id: keep the stable community id alongside the
                // mutable display label (see ovp-index::build_claims). Claims
                // with no mapped pack get the Unclassified sentinel so the
                // bucket stays routable.
                r.theme_id = Some(themes.majority_community(&r.source_cases).unwrap_or(UNCLASSIFIED_ID));
                r.theme = themes
                    .majority_label(&r.source_cases)
                    .unwrap_or_else(|| UNCLASSIFIED_THEME.to_string());
            }
        }
        Ok(None) => {}
        Err(e) => eprintln!("warning: ignoring themes.json ({e})"),
    }

    // Human patch overlay (M37): fold `.ovp/crystal/patches.jsonl` and overlay
    // active human adjustments onto active records. A missing ledger is a
    // no-op; a present-but-corrupt one is an error (see the public loaders
    // for how they degrade). Drift-gated inside `apply_patches_to_durable_records`.
    Ok(records)
}

fn overlay_human_patches(
    records: &mut [DurableRecord],
    vault_root: &Path,
    layout: &VaultLayout,
) -> Result<(), String> {
    let patches_file = vault_root.join(layout.crystal_patches_ledger());
    let patch_records = ovp_domain::crystal::read_patch_ledger(&patches_file)
        .map_err(|e| format!("human patch ledger {}: {e}", patches_file.display()))?;
    if patch_records.is_empty() {
        return Ok(());
    }
    let patch_state = ovp_domain::crystal::fold_patch_ledger(&patch_records);
    ovp_domain::crystal::apply_patches_to_durable_records(records, &patch_state);
    Ok(())
}

/// Read a source's markdown from the vault, capped at `MAX_SOURCE_DOC_BYTES`.
/// Returns `(markdown, truncated, error)` — every failure mode becomes an
/// explicit error string so the endpoint always answers. Traversal-safe.
///
/// The reserved `annotation:` frontmatter entry — the READER's own words
/// about the source, never the source's — is cut here. This text feeds
/// source-grounded chat, source summaries, the session glossary and the MCP
/// `ovp://source/` resource, all of which put it in front of a model, where
/// it would read as something the author wrote and could be quoted back as
/// evidence. The annotation still reaches the portal, which renders it as the
/// reader's own note through `SourceRow::annotation` rather than as body text.
pub fn read_source_doc(
    vault_root: &Path,
    layout: &VaultLayout,
    rel_path: Option<&str>,
    sha256: Option<&str>,
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
    } else if let Some(moved) = lifecycle_moved_path(vault_root, layout, rel, sha256) {
        moved
    } else {
        recorded
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => {
            // Cut BEFORE the cap, so the budget is spent on source text.
            let mut text =
                ovp_domain::sources::markdown_inbox::redact_annotation(&text).into_owned();
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

/// Lifecycle-move fallback — delegates to the shared implementation in
/// `ovp_domain::vault_layout` so every reader AND writer resolves the same
/// candidate. Kept as a re-export shim for existing callers.
pub fn lifecycle_moved_path(
    vault_root: &Path,
    layout: &VaultLayout,
    rel: &str,
    expected_sha256: Option<&str>,
) -> Option<PathBuf> {
    ovp_domain::vault_layout::lifecycle_moved_path(vault_root, layout, rel, expected_sha256)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        append_patch_record, FinalClass, HumanPatchRecord, ProvenanceClass, StoreOp,
        StrengthClass,
    };

    /// This reader feeds source-grounded chat, source summaries, the session
    /// glossary and the MCP `ovp://source/` resource. Every one of those puts
    /// the text in front of a model, so the reader's own note must be gone
    /// before it leaves here.
    #[test]
    fn read_source_doc_cuts_the_readers_annotation() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::create_dir_all(root.join("50-Inbox/01-Raw")).unwrap();
        std::fs::write(
            root.join("50-Inbox/01-Raw/n.md"),
            "---\ntitle: \"T\"\nannotation: |-\n  SENTINEL-my-own-verdict\ntags:\n  - \"x\"\n---\nThe author's own sentence.\n",
        )
        .unwrap();

        let (markdown, truncated, error) = read_source_doc(
            root,
            &VaultLayout,
            Some("50-Inbox/01-Raw/n.md"),
            None,
        );
        assert_eq!(error, None);
        assert!(!truncated);
        let md = markdown.expect("markdown");
        assert!(!md.contains("SENTINEL-my-own-verdict"), "{md}");
        assert!(md.contains("title: \"T\""), "{md}");
        assert!(md.contains("The author's own sentence."), "{md}");
    }

    #[test]
    fn test_load_active_records_overlays_patches() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let layout = VaultLayout;

        let store_dir = root.join(layout.crystal_store_dir());
        std::fs::create_dir_all(&store_dir).unwrap();

        let rec = DurableRecord {
            claim_key: "ck-1".into(),
            claim_id: "c01".into(),
            claim: "Unpatched baseline text".into(),
            theme: "original-theme".into(),
            theme_id: None,
            source_cases: vec!["case1".into()],
            citations: Vec::new(),
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "good".into(),
            final_class: FinalClass::Durable,
            run_id: "r1".into(),
            status: CrystalStatus::Active,
        };
        let event = StoreEvent {
            op: StoreOp::Write,
            record: rec,
            supersedes: None,
            reason: None,
        };
        std::fs::write(
            store_dir.join("ledger.jsonl"),
            format!("{}\n", serde_json::to_string(&event).unwrap()),
        )
        .unwrap();

        // 1. Initial load
        let initial = load_active_records_strict(root, &layout).unwrap();
        assert_eq!(initial.len(), 1);
        assert_eq!(initial[0].claim, "Unpatched baseline text");
        assert_eq!(initial[0].theme, "original-theme");

        // 2. Add patch
        let patches_file = root.join(layout.crystal_patches_ledger());
        let patch = HumanPatchRecord::new_apply(
            "c01",
            Some("ck-1".into()),
            "Unpatched baseline text",
            "Human patched assertion text",
            Some("patched-theme".into()),
            None,
            "operator:bob",
            "fixed nuance",
            None,
        );
        append_patch_record(&patches_file, &patch).unwrap();

        // 3. Load with patch overlay
        let patched = load_active_records_strict(root, &layout).unwrap();
        assert_eq!(patched.len(), 1);
        assert_eq!(patched[0].claim, "Human patched assertion text");
        assert_eq!(patched[0].theme, "patched-theme");

        // 4. Roll back patch
        let rollback = HumanPatchRecord::new_rollback(
            "c01",
            Some(patch.patch_id.clone()),
            Some("ck-1".into()),
            "Human patched assertion text",
            "operator:bob",
            "rollback test",
            None,
        );
        append_patch_record(&patches_file, &rollback).unwrap();

        // 5. Load after rollback
        let reverted = load_active_records_strict(root, &layout).unwrap();
        assert_eq!(reverted.len(), 1);
        assert_eq!(reverted[0].claim, "Unpatched baseline text");
        assert_eq!(reverted[0].theme, "original-theme");
    }

    #[test]
    fn test_corrupt_patch_ledger_strict_errors_server_degrades() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let layout = VaultLayout;

        let store_dir = root.join(layout.crystal_store_dir());
        std::fs::create_dir_all(&store_dir).unwrap();

        let rec = DurableRecord {
            claim_key: "ck-1".into(),
            claim_id: "c01".into(),
            claim: "Unpatched baseline text".into(),
            theme: "original-theme".into(),
            theme_id: None,
            source_cases: vec!["case1".into()],
            citations: Vec::new(),
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "good".into(),
            final_class: FinalClass::Durable,
            run_id: "r1".into(),
            status: CrystalStatus::Active,
        };
        let event = StoreEvent {
            op: StoreOp::Write,
            record: rec,
            supersedes: None,
            reason: None,
        };
        std::fs::write(
            store_dir.join("ledger.jsonl"),
            format!("{}\n", serde_json::to_string(&event).unwrap()),
        )
        .unwrap();

        // Truncated/malformed patch ledger line.
        let patches_file = root.join(layout.crystal_patches_ledger());
        std::fs::create_dir_all(patches_file.parent().unwrap()).unwrap();
        std::fs::write(&patches_file, "{\"patch_id\": \"hp-brok").unwrap();

        // Publisher path: corruption is an error, never a silent unpatched deploy.
        let err = load_active_records_strict(root, &layout).unwrap_err();
        assert!(err.contains("human patch ledger"), "{err}");

        // Live-server path: keep serving the unpatched records.
        let served = load_active_records(root, &layout);
        assert_eq!(served.len(), 1);
        assert_eq!(served[0].claim, "Unpatched baseline text");
    }
}
