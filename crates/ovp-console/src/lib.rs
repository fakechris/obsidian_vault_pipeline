//! M31 product console — ONE deterministic, self-contained, bilingual HTML
//! page over the read model ([`ovp_index::IndexModel`]), written to
//! `.ovp/console/index.html`. The daily entry point: attention feed first,
//! then runs, sources, reader packs, and Crystal claims, every item linking
//! back to its provenance artifact (reader pack / run report / store files).
//!
//! Reads PRODUCT STATE only (never `.run/`); rendering is pure over the model
//! (no clock, no environment), so a rebuild from the same state is
//! byte-identical. Visual language (dark theme, status pills, EN + 中文
//! labels) carries over from the M28 console so review vocabulary stays
//! consistent. Deliberately not graph-first and not a KnowledgeMem clone —
//! tables and cards over an auditable read model.

use std::path::Path;

use ovp_domain::VaultLayout;
use ovp_index::{ClaimStatus, IndexModel, SourceStatus};

mod ops;
mod render;

pub use ops::{render_audit_page, render_candidates_page, render_ops_page};
pub use render::render_console;

/// Render and write `.ovp/console/index.html`. Overwrite is CORRECT — the
/// console is derived, rebuildable state. Returns the vault-relative path.
pub fn write_console(vault_root: &Path, model: &IndexModel) -> Result<String, String> {
    let layout = VaultLayout::new();
    let dir = vault_root.join(layout.console_dir());
    std::fs::create_dir_all(&dir).map_err(|e| format!("creating {}: {e}", dir.display()))?;
    let target = dir.join("index.html");
    std::fs::write(&target, render_console(model))
        .map_err(|e| format!("writing {}: {e}", target.display()))?;
    Ok(format!("{}/index.html", layout.console_dir()))
}

/// Render and write ops dashboard pages alongside the main console.
/// Returns paths of the three generated files.
pub fn write_ops_pages(vault_root: &Path, model: &IndexModel) -> Result<Vec<String>, String> {
    let layout = VaultLayout::new();
    let dir = vault_root.join(layout.console_dir());
    std::fs::create_dir_all(&dir).map_err(|e| format!("creating {}: {e}", dir.display()))?;

    let pages = [
        ("ops.html", render_ops_page(model)),
        ("audit.html", render_audit_page(model)),
        ("candidates.html", render_candidates_page(model)),
    ];

    let mut paths = Vec::new();
    for (name, html) in &pages {
        let target = dir.join(name);
        std::fs::write(&target, html)
            .map_err(|e| format!("writing {}: {e}", target.display()))?;
        paths.push(format!("{}/{name}", layout.console_dir()));
    }
    Ok(paths)
}

/// Bilingual label for a source status (EN, 中文, css class).
pub(crate) fn source_status_label(s: SourceStatus) -> (&'static str, &'static str, &'static str) {
    match s {
        SourceStatus::Processed => ("processed", "已处理", "ok"),
        SourceStatus::Queued => ("queued", "待读", "info"),
        SourceStatus::Failed => ("failed", "失败", "bad"),
        SourceStatus::Blocked => ("blocked", "失败暂停", "bad"),
        SourceStatus::NeedsContent => ("needs content", "待补内容", "warn"),
        SourceStatus::Unparseable => ("unparseable", "无法解析", "warn"),
        SourceStatus::Duplicate => ("duplicate", "重复", "dim"),
    }
}

pub(crate) fn claim_status_label(s: ClaimStatus) -> (&'static str, &'static str, &'static str) {
    match s {
        ClaimStatus::Durable => ("durable", "持久化", "ok"),
        ClaimStatus::Caveated => ("caveated", "保留意见", "warn"),
        ClaimStatus::Superseded => ("superseded", "已被取代", "dim"),
        ClaimStatus::Retracted => ("retracted", "已撤回", "dim"),
    }
}

