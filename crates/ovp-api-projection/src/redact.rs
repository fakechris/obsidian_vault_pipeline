//! `PublicView` — the redaction choke-point.
//!
//! Reduces a full `IndexModel` to a public-safe clone BEFORE any body builder
//! runs, so nothing private can leak into a published site. Single place to
//! audit: durable claims only, processed sources only, no internal paths,
//! failure reasons, run internals, or operational backlog.

use ovp_index::{ClaimStatus, IndexModel, OpsState, SourceStatus};

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
        // internal vault paths, failure diagnostics, and run internals.
        m.sources.retain(|s| s.status == SourceStatus::Processed);
        for s in m.sources.iter_mut() {
            s.rel_path = None;
            s.last_reason = None;
            s.last_run_id = None;
            s.fail_count = 0;
        }

        // Claims: durable only. Caveated/superseded/retracted never ship, and
        // the review lane (`review.json`) is simply never read.
        m.claims.retain(|c| c.status == ClaimStatus::Durable);

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
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_index::{ClaimRow, SourceRow, Totals};

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
            packs: vec![],
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
        // Only the durable claim survives.
        assert_eq!(m.claims.len(), 1);
        assert_eq!(m.claims[0].claim_id, "d1");
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
