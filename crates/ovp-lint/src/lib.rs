//! OVP Next L5 — the health layer (`ovp-lint`).
//!
//! Read-only WIGS-style checks over the canonical store + vault + derived index.
//! It **reports** findings; it never fixes (a fix is a write, and writes go
//! through L3/L4, not here). Built on `ovp-query::KnowledgeView`. See
//! `docs/stage-read-health.md`.

use std::collections::BTreeSet;
use std::path::Path;

use ovp_domain::{extract_wikilinks, KnowledgeIndex, MocBuilder, VaultLayout};
use ovp_query::{KnowledgeView, QueryError};
use ovp_stores::{backlinks_from_files, walk_markdown};
use serde::Serialize;

/// Finding severity, ordered `Info < Warning < Error` so a threshold compares
/// naturally.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Info,
    Warning,
    Error,
}

impl Severity {
    pub fn as_str(self) -> &'static str {
        match self {
            Severity::Info => "info",
            Severity::Warning => "warning",
            Severity::Error => "error",
        }
    }
}

/// One health finding. `code` is a stable dotted identifier (e.g.
/// `evergreen.missing_note`); `location` names the offending concept/file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LintFinding {
    pub severity: Severity,
    pub code: String,
    pub detail: String,
    pub location: Option<String>,
}

impl LintFinding {
    fn new(severity: Severity, code: &str, detail: String, location: Option<String>) -> Self {
        Self { severity, code: code.to_string(), detail, location }
    }
}

/// All findings from one lint pass, in deterministic order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LintReport {
    pub findings: Vec<LintFinding>,
}

impl LintReport {
    /// The most severe finding, or `None` if clean.
    pub fn worst(&self) -> Option<Severity> {
        self.findings.iter().map(|f| f.severity).max()
    }

    /// Count of findings at a given severity.
    pub fn count(&self, severity: Severity) -> usize {
        self.findings.iter().filter(|f| f.severity == severity).count()
    }

    /// True iff no finding is at or above `threshold` (the gate for CI use).
    pub fn passed(&self, threshold: Severity) -> bool {
        !self.findings.iter().any(|f| f.severity >= threshold)
    }
}

/// The health checker. Read-only: every method reads the vault + canonical store
/// and returns findings; nothing is mutated.
pub struct Lint;

impl Lint {
    /// Run all checks. A load failure (corrupt canonical / unreadable store /
    /// malformed index) is surfaced as an `error` finding rather than aborting,
    /// so lint always returns a report.
    pub fn check(vault_root: &Path, canonical_root: &Path) -> LintReport {
        let mut findings: Vec<LintFinding> = Vec::new();

        let view = match KnowledgeView::load(vault_root, canonical_root) {
            Ok(v) => v,
            Err(e) => {
                findings.push(load_error_finding(&e));
                return LintReport { findings };
            }
        };

        // Walk-independent checks (filesystem stat / in-memory only).
        check_evergreen_notes(&view, vault_root, &mut findings);
        check_orphan_concepts(&view, &mut findings);
        check_moc_freshness(&view, vault_root, &mut findings);
        check_index_presence(&view, &mut findings);

        // Walk-dependent checks share ONE vault scan. A scan failure is a LOUD
        // `error` finding — never silently "no broken links / no backlinks"
        // (the health gate must not pass on an unreadable vault). Mirrors the
        // run-cycle's fail-loud backlink scan.
        match walk_markdown(vault_root) {
            Ok(files) => {
                check_index_staleness(&view, &files, &mut findings);
                check_broken_wikilinks(&view, &files, &mut findings);
            }
            Err(e) => findings.push(LintFinding::new(
                Severity::Error,
                "vault.scan_failed",
                format!("scanning vault for markdown failed: {e}"),
                Some(vault_root.display().to_string()),
            )),
        }

        // Deterministic order: by (code, location).
        findings.sort_by(|a, b| (a.code.as_str(), &a.location).cmp(&(b.code.as_str(), &b.location)));
        LintReport { findings }
    }
}

fn load_error_finding(e: &QueryError) -> LintFinding {
    let (code, detail) = match e {
        QueryError::CanonicalRead(m) => ("canonical.unreadable", m.clone()),
        QueryError::CanonicalParse(p) => ("canonical.unparseable", p.to_string()),
        QueryError::IndexRead(m) => ("index.unreadable", m.clone()),
        QueryError::IndexParse(m) => ("index.unparseable", m.clone()),
    };
    LintFinding::new(Severity::Error, code, detail, None)
}

/// Every canonical concept must have its evergreen note on disk.
fn check_evergreen_notes(view: &KnowledgeView, vault_root: &Path, out: &mut Vec<LintFinding>) {
    for c in view.concepts() {
        if !vault_root.join(&c.evergreen_path).exists() {
            out.push(LintFinding::new(
                Severity::Error,
                "evergreen.missing_note",
                format!("concept `{}` has no evergreen note at `{}`", c.slug, c.evergreen_path),
                Some(c.slug.clone()),
            ));
        }
    }
}

/// A canonical concept that nothing references (zero backlinks). Only meaningful
/// when an index exists; informational (not every concept must be referenced).
fn check_orphan_concepts(view: &KnowledgeView, out: &mut Vec<LintFinding>) {
    if view.index().is_none() {
        return;
    }
    for c in view.concepts() {
        if view.backlinks(&c.slug).is_empty() {
            out.push(LintFinding::new(
                Severity::Info,
                "canonical.orphan",
                format!("concept `{}` has no backlinks (nothing references it)", c.slug),
                Some(c.slug.clone()),
            ));
        }
    }
}

/// Warn when no knowledge index has been built yet. (An *unreadable* or
/// *unparseable* index is loud at load — it fails `KnowledgeView::load` and is
/// reported as `index.unreadable` / `index.unparseable` — so reaching here with
/// `index() == None` means genuinely absent.)
fn check_index_presence(view: &KnowledgeView, out: &mut Vec<LintFinding>) {
    if view.index().is_none() {
        out.push(LintFinding::new(
            Severity::Warning,
            "index.absent",
            "no knowledge index has been built yet (run `run-cycle`)".into(),
            Some(VaultLayout::new().knowledge_index().as_str().to_string()),
        ));
    }
}

/// When an index is present, it must equal one freshly built from canonical +
/// the (already-walked) vault backlinks. Structural comparison (re-serializing
/// both is byte-identical for a `run-cycle`-produced index). Drifted → warn.
fn check_index_staleness(
    view: &KnowledgeView,
    files: &[(String, String)],
    out: &mut Vec<LintFinding>,
) {
    let Some(persisted) = view.index() else { return };
    let moc_rel = MocBuilder::new().moc_path().as_str().to_string();
    let backlinks = backlinks_from_files(files, &moc_rel, extract_wikilinks);
    let fresh = KnowledgeIndex::build(view.concepts(), &backlinks);
    if *persisted != fresh {
        out.push(LintFinding::new(
            Severity::Warning,
            "index.stale",
            "persisted knowledge index differs from a fresh rebuild (run `run-cycle`)".into(),
            Some(VaultLayout::new().knowledge_index().as_str().to_string()),
        ));
    }
}

/// The persisted MOC must match one freshly rendered from the canonical store.
fn check_moc_freshness(view: &KnowledgeView, vault_root: &Path, out: &mut Vec<LintFinding>) {
    let builder = MocBuilder::new();
    let moc_path = builder.moc_path();
    let current = std::fs::read_to_string(vault_root.join(moc_path.as_str())).ok();
    let fresh = builder.render(view.concepts());
    match current {
        None => out.push(LintFinding::new(
            Severity::Warning,
            "moc.absent",
            "no MOC index has been built yet (run `run-cycle`)".into(),
            Some(moc_path.as_str().to_string()),
        )),
        Some(cur) if cur != fresh => out.push(LintFinding::new(
            Severity::Warning,
            "moc.stale",
            "persisted MOC differs from a fresh render (run `run-cycle`)".into(),
            Some(moc_path.as_str().to_string()),
        )),
        Some(_) => {}
    }
}

/// A `[[target]]` that resolves to neither a canonical concept nor an existing
/// vault note is broken. The derived MOC (which links every concept) is excluded
/// as a *source* — its links are mechanical, not authored references. Operates
/// on the already-walked `files` (the single vault scan).
fn check_broken_wikilinks(
    view: &KnowledgeView,
    files: &[(String, String)],
    out: &mut Vec<LintFinding>,
) {
    // Resolvable set: canonical slugs ∪ every note's file stem.
    let mut resolvable: BTreeSet<String> = view.concepts().iter().map(|c| c.slug.clone()).collect();
    for (path, _) in files {
        if let Some(stem) = Path::new(path).file_stem().and_then(|s| s.to_str()) {
            resolvable.insert(stem.to_string());
        }
    }
    let moc_rel = MocBuilder::new().moc_path().as_str().to_string();
    for (path, content) in files {
        if *path == moc_rel {
            continue;
        }
        let mut reported: BTreeSet<String> = BTreeSet::new();
        for target in extract_wikilinks(content) {
            if !resolvable.contains(&target) && reported.insert(target.clone()) {
                out.push(LintFinding::new(
                    Severity::Warning,
                    "wikilink.broken",
                    format!("`[[{target}]]` resolves to no concept or note"),
                    Some(path.clone()),
                ));
            }
        }
    }
}

/// The knowledge-index artifact path, for callers that want to display it.
pub fn knowledge_index_path() -> String {
    VaultLayout::new().knowledge_index().as_str().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn severity_orders_and_threshold() {
        assert!(Severity::Error > Severity::Warning);
        assert!(Severity::Warning > Severity::Info);
        let report = LintReport {
            findings: vec![
                LintFinding::new(Severity::Warning, "x.y", "w".into(), None),
                LintFinding::new(Severity::Info, "a.b", "i".into(), None),
            ],
        };
        assert!(report.passed(Severity::Error), "no errors → passes error gate");
        assert!(!report.passed(Severity::Warning), "a warning fails the warning gate");
        assert_eq!(report.worst(), Some(Severity::Warning));
        assert_eq!(report.count(Severity::Warning), 1);
    }
}
