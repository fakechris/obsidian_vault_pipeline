//! Write the human-inspectable M14b review pack. Deterministic (no timestamps,
//! stable order) so a replay is byte-identical. Plain functions — NOT a pipeline
//! Sink, NOT tied to DomainBody.

use std::fs;
use std::io;
use std::path::Path;

use crate::units::Unit;

use super::{ReferentCandidate, ReferentExtraction, ReferentKind};

/// Write `.run/m14b/<case>/` pack files: the 6 review views plus the audit chain
/// (input.units.json, model-reply.txt, report.json).
pub fn write_referent_review_pack(
    out_dir: &Path,
    units: &[Unit],
    ex: &ReferentExtraction,
    raw_reply: Option<&str>,
) -> io::Result<()> {
    fs::create_dir_all(out_dir)?;

    write_json(out_dir.join("input.units.json"), &units)?;
    if let Some(raw) = raw_reply {
        fs::write(out_dir.join("model-reply.txt"), raw)?;
    }
    write_json(out_dir.join("referents.all.json"), &ex.referents)?;
    write_json(out_dir.join("referents.rejected.json"), &ex.rejected)?;
    write_json(out_dir.join("report.json"), &ex.report)?;

    fs::write(out_dir.join("REVIEW.md"), render_review_md(ex))?;
    fs::write(out_dir.join("referents.by-kind.md"), render_by_kind(ex))?;
    fs::write(out_dir.join("referents.by-unit.md"), render_by_unit(units, ex))?;
    fs::write(out_dir.join("rejected-or-noise.md"), render_rejected_or_noise(ex))?;
    fs::write(out_dir.join("unresolved-ambiguous.md"), render_ambiguous(ex))?;
    Ok(())
}

fn write_json<T: serde::Serialize>(path: std::path::PathBuf, value: &T) -> io::Result<()> {
    let s = serde_json::to_string_pretty(value).map_err(io::Error::other)?;
    fs::write(path, format!("{s}\n"))
}

pub fn render_review_md(ex: &ReferentExtraction) -> String {
    let r = &ex.report;
    let mut s = String::new();
    s.push_str(&format!("# Referent Review — {}\n\n", ex.case_id));
    s.push_str(&format!("- schema: referent_extract/v{}\n\n", ex.schema_version));
    s.push_str("## Metrics\n\n");
    if let Some(err) = &r.parse_error {
        s.push_str(&format!("- **PARSE ERROR**: {err}\n"));
    }
    s.push_str(&format!("- total: {}  ·  live: {}  ·  rejected: {}\n", r.total_candidates, r.live, r.rejected));
    s.push_str(&format!(
        "- referents_ungrounded: {} {}\n",
        r.referents_ungrounded,
        if r.referents_ungrounded == 0 { "(invariant holds)" } else { "(**INVARIANT VIOLATED**)" }
    ));
    let k = &r.kind_counts;
    s.push_str(&format!(
        "- kinds — entity: {}  concept: {}  ambiguous: {}  local_phrase: {}  noise: {}\n",
        k.entity, k.concept, k.ambiguous, k.local_phrase, k.noise
    ));
    s.push_str(&format!(
        "- concept_rate: {:.0}% {} · ambiguous_rate: {:.0}% {} · grouped: {} · dedup-collapsed: {}\n\n",
        r.concept_rate * 100.0,
        if r.concept_rate > 0.30 { "(⚠ over-mint risk)" } else { "" },
        r.ambiguous_rate * 100.0,
        if r.ambiguous_rate > 0.30 { "(⚠ rubric too timid)" } else { "" },
        r.grouped_candidates,
        r.duplicates_collapsed,
    ));
    s.push_str("See `referents.by-kind.md`, `referents.by-unit.md`, `rejected-or-noise.md`, `unresolved-ambiguous.md`.\n");
    s
}

fn render_by_kind(ex: &ReferentExtraction) -> String {
    let mut s = format!("# Referents by kind — {}\n\n", ex.case_id);
    // by-kind shows entity/concept/local_phrase here; ambiguous + noise have their
    // own files, but include them too for a complete single view.
    for (title, kind) in [
        ("Entities", ReferentKind::Entity),
        ("Concepts", ReferentKind::Concept),
        ("Local phrases", ReferentKind::LocalPhrase),
        ("Ambiguous", ReferentKind::Ambiguous),
        ("Noise", ReferentKind::Noise),
    ] {
        let items: Vec<&ReferentCandidate> = ex.by_kind(kind).collect();
        s.push_str(&format!("## {title} ({})\n\n", items.len()));
        if items.is_empty() {
            s.push_str("_none_\n\n");
        }
        // Note: by_kind needs the units for quotes; render compactly without them.
        for c in items {
            s.push_str(&format!("### {} — conf {:.2}\n\n", c.surface_names.first().cloned().unwrap_or_default(), c.confidence));
            if c.surface_names.len() > 1 {
                s.push_str(&format!("- surfaces: {}\n", c.surface_names.join(" · ")));
            }
            if let Some(b) = &c.boundary {
                s.push_str(&format!("- includes: {}\n", b.includes));
                if let Some(x) = &b.excludes {
                    s.push_str(&format!("- excludes: {x}\n"));
                }
            }
            s.push_str(&format!("- support: {}\n", c.support_unit_ids.join(", ")));
            if !c.rationale.is_empty() {
                s.push_str(&format!("- rationale: {}\n", c.rationale));
            }
            s.push('\n');
        }
    }
    s
}

fn render_by_unit(units: &[Unit], ex: &ReferentExtraction) -> String {
    let mut s = format!("# Referents by supporting unit — {}\n\n", ex.case_id);
    for u in units {
        let refs: Vec<&ReferentCandidate> =
            ex.referents.iter().filter(|c| c.support_unit_ids.iter().any(|id| id == &u.id)).collect();
        if refs.is_empty() {
            continue;
        }
        s.push_str(&format!("## `{}` — \"{}\"\n\n", u.id, u.text));
        for c in refs {
            s.push_str(&format!(
                "- **{}** ({}) — {}\n",
                c.surface_names.first().cloned().unwrap_or_default(),
                c.kind.as_str(),
                c.rationale
            ));
        }
        s.push('\n');
    }
    s
}

fn render_rejected_or_noise(ex: &ReferentExtraction) -> String {
    let mut s = format!("# Rejected + noise — {}\n\n", ex.case_id);
    s.push_str(&format!("## Rejected by the validator ({})\n\n", ex.rejected.len()));
    if ex.rejected.is_empty() {
        s.push_str("_none_\n\n");
    }
    for c in &ex.rejected {
        s.push_str(&format!(
            "- **{}** — reject_reason: `{}`  (support: {})\n",
            c.surface_names.first().cloned().unwrap_or_else(|| "<no surface>".into()),
            c.reject_reason.as_deref().unwrap_or("?"),
            c.support_unit_ids.join(", ")
        ));
    }
    let noise: Vec<&ReferentCandidate> = ex.by_kind(ReferentKind::Noise).collect();
    s.push_str(&format!("\n## Live but kind=noise ({})\n\n", noise.len()));
    if noise.is_empty() {
        s.push_str("_none_\n");
    }
    for c in noise {
        s.push_str(&format!(
            "- **{}** — {}\n",
            c.surface_names.first().cloned().unwrap_or_default(),
            c.rationale
        ));
    }
    s
}

fn render_ambiguous(ex: &ReferentExtraction) -> String {
    let mut s = format!("# Unresolved / ambiguous — {}\n\n", ex.case_id);
    let items: Vec<&ReferentCandidate> = ex.by_kind(ReferentKind::Ambiguous).collect();
    s.push_str(&format!("{} candidate(s) kept as ambiguous (NOT forced into concept/entity):\n\n", items.len()));
    if items.is_empty() {
        s.push_str("_none_\n");
    }
    for c in items {
        s.push_str(&format!(
            "- **{}** — support {} — {}\n",
            c.surface_names.join(" · "),
            c.support_unit_ids.join(", "),
            c.rationale
        ));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::referents::validate_referents;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;

    fn extraction() -> (Vec<Unit>, ReferentExtraction) {
        let raw = vec![serde_json::json!({
            "kind":"assertion","text":"IdeaBlocks replace prose chunks.",
            "evidence_ref":"p001.s001","evidence_quote":"IdeaBlocks replace prose chunks.",
            "attribution":"author","modality":"asserted","arguments":[{"surface":"IdeaBlocks","role":"subject"}]
        })];
        let units = validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![], "IdeaBlocks replace prose chunks.")).units
;
        let rraw = vec![serde_json::json!({
            "kind":"entity","surface_names":["IdeaBlocks"],"support_unit_ids":[units[0].id],"rationale":"named construct"
        })];
        let ex = validate_referents(&rraw, &units, "t");
        (units, ex)
    }

    #[test]
    fn writes_all_six_files_plus_audit() {
        let (units, ex) = extraction();
        let dir = tempfile::tempdir().unwrap();
        write_referent_review_pack(dir.path(), &units, &ex, Some("raw")).unwrap();
        for f in [
            "REVIEW.md", "referents.all.json", "referents.by-unit.md", "referents.by-kind.md",
            "rejected-or-noise.md", "unresolved-ambiguous.md", "input.units.json",
            "model-reply.txt", "report.json",
        ] {
            assert!(dir.path().join(f).exists(), "missing {f}");
        }
    }

    #[test]
    fn pack_is_deterministic() {
        let (units, ex) = extraction();
        let d1 = tempfile::tempdir().unwrap();
        let d2 = tempfile::tempdir().unwrap();
        write_referent_review_pack(d1.path(), &units, &ex, None).unwrap();
        write_referent_review_pack(d2.path(), &units, &ex, None).unwrap();
        for f in ["referents.all.json", "report.json", "REVIEW.md", "referents.by-kind.md"] {
            assert_eq!(
                std::fs::read_to_string(d1.path().join(f)).unwrap(),
                std::fs::read_to_string(d2.path().join(f)).unwrap(),
                "{f} not deterministic"
            );
        }
    }

    #[test]
    fn review_md_flags_invariant() {
        let (_u, ex) = extraction();
        let md = render_review_md(&ex);
        assert!(md.contains("referents_ungrounded: 0 (invariant holds)"));
    }
}
