//! `PublicView` — the redaction choke-point.
//!
//! Reduces a full `IndexModel` to a public-safe clone BEFORE any body builder
//! runs, so nothing private can leak into a published site. Single place to
//! audit: durable claims only, processed sources only, no internal paths,
//! failure reasons, run internals, or operational backlog.

use ovp_index::{ClaimStatus, IndexModel, OpsState, SourceStatus};

/// Last path segment of a `pack_dir` — the case_id, which claim↔source linking
/// keys on. Publishing this instead of the full path drops the vault layout.
fn case_id(pack_dir: &str) -> &str {
    pack_dir.rsplit(['/', '\\']).next().unwrap_or(pack_dir)
}

/// A public-safe projection of the read model. Hold onto `.model()` and feed it
/// to the same body builders the live server uses.
pub struct PublicView {
    model: IndexModel,
}

impl PublicView {
    /// Filter + scrub a full model into its public-safe form.
    pub fn from_model(model: &IndexModel) -> Self {
        let mut m = model.clone();

        // Sources: only the happy-path Processed rows appear publicly; strip
        // internal vault paths, failure diagnostics, and run internals. Reduce
        // `pack_dir` to its case_id basename so the vault folder layout
        // (`40-Resources/Reader/…`) doesn't leak while claim↔source linking
        // (which keys on the last path segment) still works.
        m.sources.retain(|s| s.status == SourceStatus::Processed);
        for s in m.sources.iter_mut() {
            s.rel_path = None;
            s.last_reason = None;
            s.last_run_id = None;
            s.fail_count = 0;
            s.pack_dir = s.pack_dir.as_deref().map(case_id).map(str::to_string);
            // Tags are the operator's personal taxonomy — private by default.
            // Publishing them is a deliberate future decision, not a side
            // effect of the tag facet landing on the live portal.
            s.tags.clear();
        }
        let public_shas: std::collections::HashSet<&str> =
            m.sources.iter().map(|s| s.sha256.as_str()).collect();

        // Packs: keep only those joined to a retained (processed) source — drop
        // orphan packs. Strip the folder path (→ case_id) and the card titles
        // (synthesis internals that also feed search).
        m.packs.retain(|p| {
            p.source_sha256
                .as_deref()
                .is_some_and(|sha| public_shas.contains(sha))
        });
        for p in m.packs.iter_mut() {
            p.pack_dir = case_id(&p.pack_dir).to_string();
            p.card_titles.clear();
        }
        // The public case_id universe: the retained packs' basenames. A claim
        // citing anything outside this is citing an orphan/private pack.
        let public_cases: std::collections::HashSet<String> =
            m.packs.iter().map(|p| p.pack_dir.clone()).collect();

        // Claims: durable only. Caveated/superseded/retracted never ship, and
        // the review lane (`review.json`) is simply never read. Drop the run id
        // (a pipeline internal), and scrub citations to PUBLIC cases only — a
        // raw orphan case id carries a date/title/hash. A claim left with no
        // public source can't show provenance, so it's dropped entirely.
        m.claims.retain(|c| c.status == ClaimStatus::Durable);
        for c in m.claims.iter_mut() {
            c.run_id = None;
            c.sources.retain(|s| public_cases.contains(s));
        }
        m.claims.retain(|c| !c.sources.is_empty());

        // Runs + ops are pipeline internals (report paths, blocked backlog,
        // liveness heartbeat) — drop them entirely.
        m.runs.clear();
        m.ops = OpsState::default();

        // Recompute totals so no backlog/failure counts leak; the public view
        // only knows about what it actually ships.
        m.totals.sources = m.sources.len();
        m.totals.processed = m.sources.len();
        m.totals.queued = 0;
        m.totals.failed = 0;
        m.totals.blocked = 0;
        m.totals.needs_content = 0;
        m.totals.unparseable = 0;
        m.totals.duplicates = 0;
        m.totals.packs = m.packs.len();
        m.totals.claims_durable = m.claims.len();
        m.totals.claims_caveated = 0;
        m.totals.runs = 0;

        Self { model: m }
    }

    /// The filtered, public-safe model.
    pub fn model(&self) -> &IndexModel {
        &self.model
    }

    /// Take ownership of the filtered model (for callers that need to further
    /// adjust it, e.g. recompute claim themes from the surviving public cases).
    pub fn into_model(self) -> IndexModel {
        self.model
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_index::{ClaimRow, PackRow, SourceRow, Totals};

    fn pack(case: &str, sha: &str) -> PackRow {
        PackRow {
            pack_dir: format!("40-Resources/Reader/{case}"),
            title: "t".into(),
            date: None,
            units: 1,
            cards: 1,
            json_repaired: false,
            card_titles: vec!["secret card title".into()],
            source_sha256: Some(sha.into()),
        }
    }

    fn src(sha: &str, status: SourceStatus, reason: Option<&str>) -> SourceRow {
        SourceRow {
            sha256: sha.into(),
            status,
            title: Some("t".into()),
            url: Some("https://example.com".into()),
            rel_path: Some("50-Inbox/01-Raw/2026-07/secret.md".into()),
            date: Some("2026-07-01".into()),
            last_run_id: Some("r9".into()),
            pack_dir: Some("40-Resources/Reader/case".into()),
            fail_count: 3,
            last_reason: reason.map(String::from),
            tags: vec!["agent".into()],
        }
    }

    fn claim(id: &str, status: ClaimStatus) -> ClaimRow {
        ClaimRow {
            claim_id: id.into(),
            claim: "c".into(),
            theme: Some("Th".into()),
            status,
            sources: vec!["case".into()],
            strength: Some("supported".into()),
            run_id: Some("r9".into()),
            lane: None,
        }
    }

    fn model() -> IndexModel {
        IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-07-01".into(),
            built_at: Some("2026-07-01T00:00:00Z".into()),
            run_id: Some("r9".into()),
            totals: Totals::default(),
            sources: vec![
                src("aaa", SourceStatus::Processed, None),
                src("bbb", SourceStatus::Blocked, Some("3 strikes: llm 500")),
                src("ccc", SourceStatus::NeedsContent, None),
            ],
            // "case" joins to processed "aaa" (public); "orphan" joins to blocked
            // "bbb" (dropped) so a claim citing it is scrubbed.
            packs: vec![pack("case", "aaa"), pack("orphan", "bbb")],
            claims: vec![
                claim("d1", ClaimStatus::Durable),
                claim("c1", ClaimStatus::Caveated),
                claim("s1", ClaimStatus::Superseded),
            ],
            runs: vec![],
            ops: OpsState::default(),
        }
    }

    #[test]
    fn drops_non_public_rows_and_scrubs_internal_fields() {
        let pv = PublicView::from_model(&model());
        let m = pv.model();
        // Only the Processed source survives.
        assert_eq!(m.sources.len(), 1);
        assert_eq!(m.sources[0].sha256, "aaa");
        // Internal path + failure diagnostics scrubbed.
        assert!(m.sources[0].rel_path.is_none());
        assert!(m.sources[0].last_reason.is_none());
        assert!(m.sources[0].last_run_id.is_none());
        assert_eq!(m.sources[0].fail_count, 0);
        // Personal taxonomy never ships publicly.
        assert!(m.sources[0].tags.is_empty());
        // Only the durable claim survives, citing only the public case.
        assert_eq!(m.claims.len(), 1);
        assert_eq!(m.claims[0].claim_id, "d1");
        assert_eq!(m.claims[0].sources, vec!["case".to_string()]);
        assert!(m.claims[0].run_id.is_none());
        // Packs: orphan (blocked-source) pack dropped; path reduced to case_id;
        // card_titles scrubbed.
        assert_eq!(m.packs.len(), 1);
        assert_eq!(m.packs[0].pack_dir, "case");
        assert!(m.packs[0].card_titles.is_empty());
        assert_eq!(m.sources[0].pack_dir.as_deref(), Some("case"));
        // Totals recomputed — no backlog/failure leakage.
        assert_eq!(m.totals.sources, 1);
        assert_eq!(m.totals.blocked, 0);
        assert_eq!(m.totals.needs_content, 0);
        assert_eq!(m.totals.claims_caveated, 0);
        assert_eq!(m.totals.claims_durable, 1);
    }

    #[test]
    fn settings_public_body_hides_vault_and_ask() {
        let m = model();
        let body = crate::bodies::settings_public_body(Some(&m));
        assert!(body.get("vault_root").is_none());
        assert!(body.get("llm_configured").is_none());
        assert!(body.get("ask_limits").is_none());
        assert!(body.get("last_run").is_none());
        assert_eq!(body["schema_version"], "ovp.index/v2");
    }
}
