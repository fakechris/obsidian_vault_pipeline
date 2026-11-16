//! Attach the claim-propagation projection (INV-1882) to active claim rows.
//!
//! Joins each cited case to its source row (via the pack's `source_sha256`)
//! and reads the source note once per build for two things: its frontmatter
//! `published` day, and its body (to see whether a later source links or
//! credits the origin). The rule itself lives in
//! `ovp_domain::crystal::propagation`.
//!
//! Dates are deliberately narrower than `SourceRow::content_date`, which
//! falls back to filename days and, for Pinboard captures, holds the
//! BOOKMARK day. Ordering sources by when they were saved would invent a
//! chronology, so only a non-Pinboard frontmatter `published` counts; any
//! other source is undated and takes no part in origin selection.

use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use ovp_domain::crystal::propagation::{compute_propagation, SourceFacts};
use ovp_domain::units::read_source_from_path;
use ovp_domain::vault_layout::{lifecycle_moved_path, VaultLayout};
use ovp_domain::SourceKind;

use crate::model::{AdoptionTimelineEntry, ClaimRow, ClaimStatus, PackRow, SourceRow};

/// What one source note contributes, read once per build.
struct NoteFacts {
    published: Option<String>,
    /// A paper's byline (`source_authors`), which the article `author`
    /// field does not carry.
    authors: Option<String>,
    body: String,
}

fn note<'a>(
    notes: &'a mut HashMap<String, Option<NoteFacts>>,
    vault_root: &Path,
    s: &SourceRow,
) -> Option<&'a NoteFacts> {
    let rel = s.rel_path.as_deref()?;
    notes
        .entry(rel.to_string())
        .or_insert_with(|| {
            // Same lifecycle-move fallback as attach_tags: a processed
            // source's recorded raw-inbox path may have moved to 03-Processed.
            let recorded = vault_root.join(rel);
            let path = if recorded.is_file() {
                recorded
            } else {
                lifecycle_moved_path(vault_root, &VaultLayout::new(), rel, Some(&s.sha256))?
            };
            let doc = read_source_from_path(&path).ok()?;
            // A paper's own publication metadata beats the clipping's
            // top-level `published` (which may be the clip day) — but only
            // when it is a real day; otherwise fall through to `published`.
            let (paper_day, paper_authors) = match &doc.source_kind {
                SourceKind::Paper(meta) => (meta.published.as_deref(), meta.authors.join(", ")),
                SourceKind::Article => (None, String::new()),
            };
            let published = [paper_day, doc.published.as_deref()]
                .into_iter()
                .flatten()
                .filter_map(|p| p.get(..10))
                .find(|d| crate::model::is_iso_day(d))
                .map(str::to_string);
            Some(NoteFacts {
                published,
                authors: (!paper_authors.is_empty()).then_some(paper_authors),
                body: doc.body_markdown,
            })
        })
        .as_ref()
}

/// Returns how many claims received a propagation origin.
pub fn attach_propagation(
    vault_root: &Path,
    claims: &mut [ClaimRow],
    sources: &[SourceRow],
    packs: &[PackRow],
) -> usize {
    let by_sha: HashMap<&str, &SourceRow> =
        sources.iter().map(|s| (s.sha256.as_str(), s)).collect();
    let case_source: BTreeMap<&str, &SourceRow> = packs
        .iter()
        .filter_map(|p| {
            let case = p.pack_dir.rsplit('/').next().unwrap_or(p.pack_dir.as_str());
            Some((case, *by_sha.get(p.source_sha256.as_deref()?)?))
        })
        .collect();
    let mut notes: HashMap<String, Option<NoteFacts>> = HashMap::new();

    let mut stamped = 0;
    for claim in claims
        .iter_mut()
        .filter(|c| matches!(c.status, ClaimStatus::Durable | ClaimStatus::Caveated))
    {
        if claim.sources.len() < 2 {
            continue;
        }
        let mut bodies: HashMap<String, String> = HashMap::new();
        let facts: Vec<SourceFacts> = claim
            .sources
            .iter()
            .filter_map(|case| {
                let s = case_source.get(case.as_str())?;
                let facts = note(&mut notes, vault_root, s);
                if let Some(f) = facts {
                    bodies.insert(case.clone(), f.body.clone());
                }
                let pinboard = s.origin.as_deref() == Some("pinboard");
                Some(SourceFacts {
                    case_id: case.clone(),
                    identity: s.sha256.clone(),
                    published: facts.and_then(|f| f.published.clone()).filter(|_| !pinboard),
                    url: s.url.clone(),
                    author: s.author.clone().or_else(|| facts.and_then(|f| f.authors.clone())),
                })
            })
            .collect();
        let Some(p) = compute_propagation(&facts, |case| bodies.remove(case)) else {
            continue;
        };
        claim.origin_source = Some(p.origin_case_id);
        claim.adoption_count = Some(p.adoptions.len());
        claim.credit_rate = p.credit_rate;
        claim.adoption_timeline = p
            .adoptions
            .into_iter()
            .map(|a| AdoptionTimelineEntry {
                source_case_id: a.case_id,
                first_seen_date: Some(a.published),
                lag_days: Some(a.lag_days),
                citation_type: a.citation_type,
            })
            .collect();
        stamped += 1;
    }
    stamped
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SourceStatus;
    use ovp_domain::citation_support::CitationType;

    fn source(sha: &str, date: &str, url: &str, rel: &str) -> SourceRow {
        let mut s = SourceRow::blank(sha, SourceStatus::Processed);
        s.content_date = Some(date.into());
        s.url = Some(url.into());
        s.rel_path = Some(rel.into());
        s
    }

    fn pack(case: &str, sha: &str) -> PackRow {
        PackRow {
            pack_dir: format!("40-Resources/Reader/{case}"),
            title: case.into(),
            date: None,
            units: 1,
            cards: 1,
            json_repaired: false,
            card_titles: vec![],
            source_sha256: Some(sha.into()),
        }
    }

    fn claim(status: ClaimStatus, cases: &[&str]) -> ClaimRow {
        ClaimRow {
            claim_id: "c".into(),
            claim_key: Some("ck".into()),
            claim: "claim".into(),
            theme: None,
            theme_id: None,
            status,
            sources: cases.iter().map(|c| (*c).to_string()).collect(),
            strength: None,
            run_id: None,
            run_date: None,
            lane: None,
            patched_by: None,
            xy: None,
            origin_source: None,
            adoption_timeline: Vec::new(),
            adoption_count: None,
            credit_rate: None,
        }
    }

    fn write_note(dir: &Path, rel: &str, published: &str, body: &str) {
        std::fs::write(
            dir.join(rel),
            format!("---\ntitle: \"T\"\nsource: \"https://x.dev/{rel}\"\npublished: {published}\n---\n{body}\n"),
        )
        .unwrap();
    }

    #[test]
    fn stamps_active_claims_from_frontmatter_published_and_body_text() {
        let dir = tempfile::tempdir().unwrap();
        write_note(dir.path(), "a.md", "2026-01-01", "the original");
        write_note(dir.path(), "b.md", "2026-01-04T09:00:00Z", "as https://a.dev/post shows");
        // content_date is deliberately misleading: only frontmatter counts.
        let sources = vec![
            source("sha-a", "2026-09-09", "https://a.dev/post", "a.md"),
            source("sha-b", "2025-01-01", "https://b.dev/x", "b.md"),
        ];
        let packs = vec![pack("case-b", "sha-b"), pack("case-a", "sha-a")];
        let mut claims = vec![
            claim(ClaimStatus::Durable, &["case-b", "case-a"]),
            claim(ClaimStatus::Superseded, &["case-b", "case-a"]),
            claim(ClaimStatus::Durable, &["case-a"]),
        ];
        let n = attach_propagation(dir.path(), &mut claims, &sources, &packs);
        assert_eq!(n, 1);
        let c = &claims[0];
        assert_eq!(c.origin_source.as_deref(), Some("case-a"));
        assert_eq!(c.adoption_count, Some(1));
        assert_eq!(c.credit_rate, Some(1.0));
        assert_eq!(c.adoption_timeline[0].first_seen_date.as_deref(), Some("2026-01-04"));
        assert_eq!(c.adoption_timeline[0].lag_days, Some(3));
        assert_eq!(c.adoption_timeline[0].citation_type, CitationType::ExactLink);
        assert!(claims[1].origin_source.is_none(), "superseded rows are not stamped");
        assert!(claims[2].origin_source.is_none(), "single-source claims have no propagation");
    }

    #[test]
    fn a_papers_own_publication_date_beats_the_clip_day() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("paper.md"),
            "---\ntitle: \"P\"\nsource: \"https://arxiv.org/abs/2601.1\"\npublished: 2026-05-01\n\
             source_type: arxiv-paper\narxiv_id: \"2601.1\"\nsource_published_at: 2026-01-01\n---\nBody.\n",
        )
        .unwrap();
        write_note(dir.path(), "b.md", "2026-02-01", "y");
        let sources = vec![
            source("sha-p", "2026-05-01", "https://arxiv.org/abs/2601.1", "paper.md"),
            source("sha-b", "2026-02-01", "https://b.dev/x", "b.md"),
        ];
        let packs = vec![pack("case-p", "sha-p"), pack("case-b", "sha-b")];
        let mut claims = vec![claim(ClaimStatus::Durable, &["case-b", "case-p"])];
        assert_eq!(attach_propagation(dir.path(), &mut claims, &sources, &packs), 1);
        assert_eq!(claims[0].origin_source.as_deref(), Some("case-p"));
        assert_eq!(claims[0].adoption_timeline[0].lag_days, Some(31));
    }

    #[test]
    fn reads_notes_moved_from_raw_inbox_and_falls_back_from_a_bad_paper_date() {
        let dir = tempfile::tempdir().unwrap();
        // a: recorded in 01-Raw, actually archived under 03-Processed.
        let moved = dir.path().join("50-Inbox/03-Processed/2026-01");
        std::fs::create_dir_all(&moved).unwrap();
        write_note(&moved, "a-0123abcd.md", "2026-01-01", "x");
        // b: a paper whose own date is junk — the top-level published counts.
        std::fs::write(
            dir.path().join("b.md"),
            "---\ntitle: \"P\"\nsource: \"https://arxiv.org/abs/1\"\npublished: 2026-01-04\n\
             source_type: arxiv-paper\narxiv_id: \"1\"\nsource_published_at: unknown\n---\nBody.\n",
        )
        .unwrap();
        let sources = vec![
            source("sha-a", "2026-01-01", "https://a.dev/post", "50-Inbox/01-Raw/2026-01/a-0123abcd.md"),
            source("sha-b", "2026-01-04", "https://arxiv.org/abs/1", "b.md"),
        ];
        let packs = vec![pack("case-a", "sha-a"), pack("case-b", "sha-b")];
        let mut claims = vec![claim(ClaimStatus::Durable, &["case-b", "case-a"])];
        assert_eq!(attach_propagation(dir.path(), &mut claims, &sources, &packs), 1);
        assert_eq!(claims[0].origin_source.as_deref(), Some("case-a"));
        assert_eq!(claims[0].adoption_timeline[0].lag_days, Some(3));
    }

    #[test]
    fn pinboard_bookmark_days_are_not_publication_dates() {
        let dir = tempfile::tempdir().unwrap();
        write_note(dir.path(), "a.md", "2026-01-01", "x");
        write_note(dir.path(), "b.md", "2026-01-04", "y");
        let mut a = source("sha-a", "2026-01-01", "https://a.dev/post", "a.md");
        a.origin = Some("pinboard".into());
        let sources = vec![a, source("sha-b", "2026-01-04", "https://b.dev/x", "b.md")];
        let packs = vec![pack("case-a", "sha-a"), pack("case-b", "sha-b")];
        let mut claims = vec![claim(ClaimStatus::Durable, &["case-a", "case-b"])];
        assert_eq!(attach_propagation(dir.path(), &mut claims, &sources, &packs), 0);
        assert!(claims[0].origin_source.is_none());
    }
}
