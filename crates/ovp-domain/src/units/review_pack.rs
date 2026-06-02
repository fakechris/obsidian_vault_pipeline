//! Write the human-inspectable review pack for one extraction. Follows the
//! existing `ovp-review` pack convention (deterministic files + a REVIEW.md), but
//! is a plain function — NOT a pipeline `Sink` and NOT tied to `DomainBody`.

use std::fs;
use std::io;
use std::path::Path;

use serde_json::json;

use super::{MatchKind, SourceExtraction, Unit};

/// Write `.run/m14/<case>/` review-pack files for one extraction. Deterministic:
/// no timestamps, stable field order, so a replay produces a byte-identical pack.
pub fn write_unit_review_pack(
    out_dir: &Path,
    body_markdown: &str,
    ex: &SourceExtraction,
    raw_reply: Option<&str>,
) -> io::Result<()> {
    fs::create_dir_all(out_dir)?;

    fs::write(out_dir.join("input.md"), body_markdown)?;

    let source_json = json!({
        "source_id": ex.source_id,
        "source_fingerprint": ex.source_fingerprint,
        "title": ex.title,
        "source_url": ex.source_url,
        "schema_version": ex.schema_version,
    });
    write_json(out_dir.join("source.json"), &source_json)?;

    if let Some(raw) = raw_reply {
        fs::write(out_dir.join("model-reply.txt"), raw)?;
    }

    // Every emitted unit with its verdict (pre-bucketed view), plus the buckets.
    write_json(out_dir.join("units.all.json"), &ex.units)?;
    write_json(
        out_dir.join("units.accepted.json"),
        &ex.accepted().collect::<Vec<_>>(),
    )?;
    write_json(
        out_dir.join("units.rejected.json"),
        &ex.rejected().collect::<Vec<_>>(),
    )?;
    write_json(
        out_dir.join("units.needs-review.json"),
        &ex.needs_review().collect::<Vec<_>>(),
    )?;
    write_json(out_dir.join("validation-report.json"), &ex.report)?;

    fs::write(out_dir.join("REVIEW.md"), render_review_md(ex))?;
    Ok(())
}

fn write_json<T: serde::Serialize>(path: std::path::PathBuf, value: &T) -> io::Result<()> {
    let s = serde_json::to_string_pretty(value).map_err(io::Error::other)?;
    fs::write(path, format!("{s}\n"))
}

/// Render the human-facing REVIEW.md. Everything a reviewer needs to judge the
/// extraction without opening the JSON: each unit's quote, derived location,
/// attribution, modality, arguments, and any issues.
pub fn render_review_md(ex: &SourceExtraction) -> String {
    let r = &ex.report;
    let mut s = String::new();
    s.push_str(&format!("# Unit Review — {}\n\n", ex.title));
    s.push_str(&format!("- source: {}\n", ex.source_url));
    s.push_str(&format!("- fingerprint: `{}`\n", &ex.source_fingerprint[..16.min(ex.source_fingerprint.len())]));
    s.push_str(&format!("- schema: unit_extract/v{}\n\n", ex.schema_version));

    s.push_str("## Metrics\n\n");
    if let Some(err) = &r.parse_error {
        s.push_str(&format!("- **PARSE ERROR**: {err}\n"));
    }
    s.push_str(&format!(
        "- total: {}  ·  accepted: {}  ·  needs-review: {}  ·  rejected: {}\n",
        r.total, r.accepted, r.needs_review, r.rejected
    ));
    s.push_str(&format!("- quote_found_rate: {:.1}%\n", r.quote_found_rate * 100.0));
    s.push_str(&format!(
        "- accepted_without_quote: {} {}\n",
        r.accepted_without_quote,
        if r.accepted_without_quote == 0 { "(invariant holds)" } else { "(**INVARIANT VIOLATED**)" }
    ));
    s.push_str(&format!("- argument_locatable_rate: {:.1}%\n", r.argument_locatable_rate * 100.0));
    if !r.duplicate_groups.is_empty() {
        s.push_str(&format!("- **duplicate groups**: {}\n", r.duplicate_groups.len()));
        for g in &r.duplicate_groups {
            s.push_str(&format!("    - {}\n", g.join(", ")));
        }
    }
    s.push('\n');

    section(&mut s, "Accepted units", ex.accepted());
    section(&mut s, "Needs review", ex.needs_review());
    section(&mut s, "Rejected units", ex.rejected());
    s
}

fn section<'a>(s: &mut String, title: &str, units: impl Iterator<Item = &'a Unit>) {
    let units: Vec<&Unit> = units.collect();
    s.push_str(&format!("## {title} ({})\n\n", units.len()));
    if units.is_empty() {
        s.push_str("_none_\n\n");
        return;
    }
    for u in units {
        let kind = match &u.subtype {
            Some(st) => format!("{:?} / {st}", u.kind),
            None => format!("{:?}", u.kind),
        };
        s.push_str(&format!("### `{}` — {}\n\n", u.id, kind.to_lowercase()));
        s.push_str(&format!("> {}\n\n", u.text));
        s.push_str(&format!("- **quote**: \"{}\"\n", u.evidence.quote));
        match &u.evidence.location {
            Some(loc) => s.push_str(&format!(
                "- **location**: line {} ({})\n",
                loc.line,
                match loc.match_kind {
                    MatchKind::Exact => "exact",
                    MatchKind::Whitespace => "whitespace-normalized",
                    MatchKind::Relaxed => "relaxed — verify",
                }
            )),
            None => s.push_str("- **location**: NOT FOUND in source\n"),
        }
        s.push_str(&format!("- **attribution**: {:?}  ·  **modality**: {:?}\n", u.attribution, u.modality));
        if !u.arguments.is_empty() {
            let args: Vec<String> = u
                .arguments
                .iter()
                .map(|a| format!("{} ({}{})", a.surface, a.role, if a.locatable { ", ✓" } else { ", ✗ drift" }))
                .collect();
            s.push_str(&format!("- **arguments**: {}\n", args.join("; ")));
        }
        for issue in &u.issues {
            s.push_str(&format!("- ⚠ `{}`: {}\n", issue.code, issue.detail));
        }
        s.push('\n');
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;

    fn extraction() -> SourceExtraction {
        let body = "A chunk is a structurally neutral container.";
        let raw = serde_json::json!({
            "kind": "assertion", "text": "A chunk is neutral.",
            "evidence_quote": "A chunk is a structurally neutral container.",
            "attribution": "author", "modality": "asserted",
            "arguments": [{"surface":"chunk","role":"subject"}]
        });
        validate(&[raw], &SourceDoc::article("T", "https://e/x", None, None, vec![], body))
    }

    #[test]
    fn writes_all_pack_files() {
        let ex = extraction();
        let dir = tempfile::tempdir().unwrap();
        write_unit_review_pack(dir.path(), "body", &ex, Some("raw reply")).unwrap();
        for f in [
            "input.md",
            "source.json",
            "model-reply.txt",
            "units.all.json",
            "units.accepted.json",
            "units.rejected.json",
            "units.needs-review.json",
            "validation-report.json",
            "REVIEW.md",
        ] {
            assert!(dir.path().join(f).exists(), "missing {f}");
        }
    }

    #[test]
    fn review_md_shows_quote_and_location() {
        let md = render_review_md(&extraction());
        assert!(md.contains("Accepted units (1)"));
        assert!(md.contains("A chunk is a structurally neutral container."));
        assert!(md.contains("line 1 (exact)"));
        assert!(md.contains("invariant holds"));
    }

    #[test]
    fn pack_is_deterministic() {
        let ex = extraction();
        let d1 = tempfile::tempdir().unwrap();
        let d2 = tempfile::tempdir().unwrap();
        write_unit_review_pack(d1.path(), "body", &ex, None).unwrap();
        write_unit_review_pack(d2.path(), "body", &ex, None).unwrap();
        for f in ["units.all.json", "validation-report.json", "REVIEW.md"] {
            let a = std::fs::read_to_string(d1.path().join(f)).unwrap();
            let b = std::fs::read_to_string(d2.path().join(f)).unwrap();
            assert_eq!(a, b, "{f} not deterministic");
        }
    }
}
