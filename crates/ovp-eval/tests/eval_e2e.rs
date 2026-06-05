//! End-to-end tests for the M8 comparator. Offline by default: the ovp side
//! runs through replay cassettes (no network, no API key) and the Nowledge side
//! is an injected `FakeNowledgeClient` (canned data) — so the comparator's
//! orchestration, partial-pack-on-failure, and normalization are all exercised
//! without touching the live service. One `#[ignore]`d test hits the real
//! Nowledge Mem at 127.0.0.1:14242 for manual/on-demand verification.

use std::path::{Path, PathBuf};
use std::time::Duration;

use ovp_app::AppWiring;
use ovp_core::RunId;
use ovp_domain::{ConceptRegistry, ARTICLE_PROMPT_ID};
use ovp_eval::nowledge::{
    CrystalInfo, IngestResponse, MemorySearchResult, NowledgeClient, NowledgeError, SearchMemory,
    SourceContentResponse, SourceDetail, SourceInfo, SourceMemory,
};
use ovp_eval::{CompareConfig, CompareRun};
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};

fn repo_root() -> PathBuf {
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap(); // <root>/crates/ovp-eval
    Path::new(&md).ancestors().nth(2).unwrap().to_path_buf()
}

fn article_input() -> PathBuf {
    repo_root().join("fixtures/article_clean/input.md")
}

/// Build the ovp-side wiring factory exactly like `review-run` does, with a
/// replay-only cassette client (offline). Binds whatever input path the
/// comparator resolves (local markdown or materialized artifact).
fn make_cassette_wiring() -> impl FnOnce(&Path) -> Result<AppWiring, String> {
    let root = repo_root();
    move |input: &Path| {
        let client: Box<dyn ModelClient> = Box::new(
            CachedModelClient::new(
                NeverCallsClient,
                root.join("crates/ovp-domain/tests/cassettes"),
                ARTICLE_PROMPT_ID,
                CacheMode::ReplayOnly,
            )
            .map_err(|e| e.to_string())?,
        );
        Ok(AppWiring::new(RunId::new("compare"))
            .with_date_stamp("2026-05-04")
            .with_area("ai")
            .with_input_path(input)
            .with_client("default_llm", client)
            .with_registry("default", ConceptRegistry::from_slugs(&[])))
    }
}

/// An injected fake. `fail_ingest` simulates the service being unreachable;
/// `fail_search` simulates the core ingest succeeding but `/memories/search`
/// erroring (a degraded-retrieval scenario).
#[derive(Default)]
struct FakeNowledgeClient {
    fail_ingest: bool,
    fail_search: bool,
    fail_crystals: bool,
    /// `/sources/{id}/content` returns an error (forces the summary fallback).
    fail_content: bool,
    /// Override the short source `summary` snippet.
    summary_override: Option<String>,
    /// The FULL parsed content returned by `/sources/{id}/content`.
    content_override: Option<String>,
}

impl FakeNowledgeClient {
    fn working() -> Self {
        Self::default()
    }
    fn unavailable() -> Self {
        Self { fail_ingest: true, ..Self::default() }
    }
    fn search_fails() -> Self {
        Self { fail_search: true, ..Self::default() }
    }
    fn crystals_fail() -> Self {
        Self { fail_crystals: true, ..Self::default() }
    }
    fn with_summary(summary: String) -> Self {
        Self { summary_override: Some(summary), ..Self::default() }
    }
    /// Full parsed content distinct from a deliberately short summary — so the
    /// materialize path can be checked to use the FULL content, not the snippet.
    fn with_content(full: String) -> Self {
        Self {
            content_override: Some(full),
            summary_override: Some("SHORT summary snippet (not the full article)".into()),
            ..Self::default()
        }
    }

    fn detail(&self) -> SourceDetail {
        let mut d = Self::canned_detail();
        if let Some(s) = &self.summary_override {
            d.source.summary = Some(s.clone());
        }
        d
    }

    /// The text `/sources/{id}/content` serves: the override, else the summary.
    fn full_content(&self) -> String {
        self.content_override
            .clone()
            .or_else(|| self.summary_override.clone())
            .unwrap_or_else(|| Self::canned_detail().source.summary.unwrap_or_default())
    }

    fn canned_detail() -> SourceDetail {
        SourceDetail {
            source: SourceInfo {
                id: "src_fake".into(),
                source_url: "https://every.to/guides/ai-product-management-guide".into(),
                original_name: "input.md".into(),
                lifecycle_state: "extracted".into(),
                summary: Some("Agent-native product management: the conversation is the work.".into()),
                section_tree: Some(r#"[{"level":1,"title":"Agent-native PM","line":1}]"#.into()),
                memory_count: 2,
                error_message: None,
            },
            memories: vec![
                SourceMemory {
                    id: "m1".into(),
                    title: "Agent-native product management".into(),
                    content: "In agent-native product management the conversation IS the work."
                        .into(),
                    unit_type: "fact".into(),
                },
                SourceMemory {
                    id: "m2".into(),
                    title: "Compound engineering".into(),
                    content: "Compound engineering reuses agents across the product loop.".into(),
                    unit_type: "fact".into(),
                },
            ],
        }
    }
}

impl NowledgeClient for FakeNowledgeClient {
    fn ingest_url(&self, _url: &str, _space: &str) -> Result<IngestResponse, NowledgeError> {
        self.ingest_common()
    }
    fn ingest_file_path(&self, _path: &str, _space: &str) -> Result<IngestResponse, NowledgeError> {
        self.ingest_common()
    }
    fn trigger_extract(&self, _id: &str) -> Result<(), NowledgeError> {
        Ok(())
    }
    fn get_source(&self, _id: &str) -> Result<SourceDetail, NowledgeError> {
        Ok(self.detail())
    }
    fn get_source_content(
        &self,
        _id: &str,
        offset: usize,
        _limit: usize,
    ) -> Result<SourceContentResponse, NowledgeError> {
        if self.fail_content {
            return Err(NowledgeError::Http {
                status: 500,
                op: "get_source_content".into(),
                detail: "fake content endpoint down".into(),
            });
        }
        let full = self.full_content();
        let total = full.chars().count();
        // Single-page fake: everything at offset 0, nothing beyond.
        if offset > 0 {
            return Ok(SourceContentResponse {
                content: String::new(),
                offset,
                returned_length: 0,
                total_length: total,
                has_more: false,
            });
        }
        Ok(SourceContentResponse {
            content: full,
            offset: 0,
            returned_length: total,
            total_length: total,
            has_more: false,
        })
    }
    fn search_memories(&self, query: &str, _limit: usize) -> Result<Vec<MemorySearchResult>, NowledgeError> {
        if self.fail_search {
            return Err(NowledgeError::Http {
                status: 500,
                op: "search_memories".into(),
                detail: "fake search failure".into(),
            });
        }
        Ok(vec![MemorySearchResult {
            memory: Some(SearchMemory {
                title: Some(format!("hit for {query}")),
                content: "Agent-native product management conversation work.".into(),
                is_crystal: false,
            }),
            similarity_score: 0.8,
        }])
    }
    fn list_crystals(&self, _limit: usize) -> Result<Vec<CrystalInfo>, NowledgeError> {
        if self.fail_crystals {
            return Err(NowledgeError::Http {
                status: 503,
                op: "list_crystals".into(),
                detail: "fake crystal endpoint down".into(),
            });
        }
        Ok(vec![CrystalInfo { title: "Agent-native PM crystal".into() }])
    }
}

impl FakeNowledgeClient {
    fn ingest_common(&self) -> Result<IngestResponse, NowledgeError> {
        if self.fail_ingest {
            return Err(NowledgeError::Transport {
                op: "ingest".into(),
                detail: "connection refused (fake)".into(),
            });
        }
        Ok(IngestResponse {
            source_id: "src_fake".into(),
            original_name: "input.md".into(),
            lifecycle_state: "indexed".into(),
            is_duplicate: false,
        })
    }
}

fn base_config(out: &Path, vault: &Path, canon: &Path, markdown: Option<PathBuf>, url: Option<String>) -> CompareConfig {
    CompareConfig {
        case_id: "test-case".into(),
        out_dir: out.to_path_buf(),
        url,
        markdown_input: markdown,
        manifest_path: repo_root().join("manifests/article_evergreen.pipeline.toml"),
        vault_root: vault.to_path_buf(),
        canonical_root: canon.to_path_buf(),
        run_id: "compare".into(),
        queries: vec!["agent native product management".into()],
        rag_limit: 5,
        space_id: "default".into(),
        search_limit: 5,
        poll_interval: Duration::from_secs(0),
        poll_max_attempts: 3,
        grounding_threshold: 0.5,
        materialize_from_nowledge: false,
    }
}

fn pack_files_exist(out: &Path) {
    for f in [
        "REVIEW.md",
        "grounding-reference.txt",
        "comparison/summary.md",
        "comparison/concept-overlap.md",
        "comparison/claim-diff.md",
        "comparison/grounding-audit.md",
        "comparison/retrieval-comparison.md",
        "comparison/score.json",
    ] {
        assert!(out.join(f).exists(), "missing pack file: {f}");
    }
}

#[test]
fn both_sides_run_produces_full_comparison() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(),
        &FakeNowledgeClient::working(),
    )
    .expect("a pack must always be produced");

    assert!(report.ovp_available, "ovp cassette run should succeed");
    assert!(report.nowledge_available, "fake nowledge should succeed");
    pack_files_exist(out.path());

    // Both sides present → all cross-system dimensions computed.
    let c = &report.comparison;
    assert!(c.concept_overlap.is_some(), "concept overlap needs both sides");
    assert!(c.claim_diff.is_some());
    assert!(c.structure.is_some());
    assert!(c.grounding.is_some());
    assert_eq!(c.retrieval.rows.len(), 1, "one fixed query");
    // The ovp side minted concepts; the nowledge fake supplied two memory-titles.
    let co = c.concept_overlap.as_ref().unwrap();
    assert!(co.ovp_count > 0, "ovp should mint concepts");
    assert_eq!(co.nowledge_count, 2, "fake supplied two memory-titles");

    // ovp/review-pack was written by the reused M7 harness.
    assert!(out.path().join("ovp/review-pack/REVIEW.md").exists());
    assert!(out.path().join("ovp/normalized.json").exists());
    assert!(out.path().join("nowledge/normalized.json").exists());
    assert!(out.path().join("nowledge/source-detail.json").exists());
}

#[test]
fn nowledge_unavailable_yields_partial_pack_loudly() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(),
        &FakeNowledgeClient::unavailable(),
    )
    .expect("a partial pack must still be produced");

    assert!(report.ovp_available, "ovp side still ran");
    assert!(!report.nowledge_available, "nowledge ingest failed");
    pack_files_exist(out.path());

    let c = &report.comparison;
    assert!(c.concept_overlap.is_none(), "no cross-system overlap without nowledge");
    assert!(!c.nowledge.available);
    assert!(
        c.findings.iter().any(|f| f.contains("nowledge side unavailable") && f.contains("connection refused")),
        "the loud failure must be recorded: {:?}",
        c.findings
    );
    // Grounding + retrieval dimensions are always present, with the nowledge
    // side reported unavailable and zeroed (not silently omitted).
    let g = c.grounding.as_ref().expect("grounding is always present");
    assert_eq!(g.nowledge_grounded, 0);
    assert_eq!(g.nowledge_ungrounded, 0);
    assert!(c.retrieval.ovp_status.contains("available"), "ovp ran: {}", c.retrieval.ovp_status);
    assert!(
        c.retrieval.nowledge_scoped_status.contains("unavailable"),
        "nowledge scoped retrieval should be loud-unavailable: {}",
        c.retrieval.nowledge_scoped_status
    );
    // ovp side is still fully captured; nowledge subject + raw detail are absent,
    // and the always-written global search dump is an empty array.
    assert!(out.path().join("ovp/normalized.json").exists());
    assert!(!out.path().join("nowledge/normalized.json").exists());
    assert!(!out.path().join("nowledge/source-detail.json").exists());
    assert_eq!(
        std::fs::read_to_string(out.path().join("nowledge/mem-search-global.json")).unwrap().trim(),
        "[]"
    );
}

#[test]
fn global_search_error_is_loud_but_scoped_lane_and_core_survive() {
    // Ingest + extraction succeed, but the GLOBAL /memories/search errors. The
    // Nowledge side stays available (source + memories were read), the COMPARABLE
    // scoped lane (lexical over this source's memories, no network) still works,
    // and only the background global lane is loud-degraded.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(),
        &FakeNowledgeClient::search_fails(),
    )
    .expect("a pack must be produced");

    assert!(report.nowledge_available, "core ingest succeeded → side available");
    let c = &report.comparison;
    assert!(c.concept_overlap.is_some(), "both sides present → overlap computed");
    assert!(
        c.retrieval.nowledge_global_status.contains("error"),
        "global retrieval error must be loud: {}",
        c.retrieval.nowledge_global_status
    );
    assert!(
        c.retrieval.nowledge_scoped_status.contains("available"),
        "the scoped lane is client-side and must survive a global-search failure: {}",
        c.retrieval.nowledge_scoped_status
    );
    // Global lane has 0 hits (search failed); scoped lane matched this source's
    // memories for the query.
    assert!(c.retrieval.rows.iter().all(|r| r.nowledge_global_hits == 0));
    assert!(
        c.retrieval.rows.iter().any(|r| r.nowledge_scoped_hits > 0),
        "scoped lane should match the source's memories"
    );
}

#[test]
fn global_hits_never_produce_an_ovp_deficiency_finding() {
    // The honesty fix end-to-end: a query that the ovp vault has but this
    // source's Nowledge memories do NOT match in the scoped lane, while the
    // Nowledge GLOBAL search returns hits. There must be no "retrieval gap"
    // finding blaming ovp from the global lane.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);
    // A query unrelated to the fake's two memories (so scoped Nowledge = 0),
    // but the fake's global search always returns a hit.
    config.queries = vec!["quantum chromodynamics lattice gauge".into()];

    let report = CompareRun::execute(config, make_cassette_wiring(), &FakeNowledgeClient::working())
        .expect("a pack must be produced");
    let row = &report.comparison.retrieval.rows[0];
    assert_eq!(row.nowledge_scoped_hits, 0, "scoped lane: query unrelated to this source");
    assert!(row.nowledge_global_hits > 0, "global lane returned a (whole-store) hit");
    assert!(
        !report.comparison.findings.iter().any(|f| f.contains("retrieval gap")),
        "a whole-store global hit must NOT become an ovp-deficiency finding: {:?}",
        report.comparison.findings
    );
}

#[test]
fn multi_query_retrieval_produces_one_row_per_query() {
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);
    config.queries = vec![
        "agent native product management".into(),
        "compound engineering".into(),
        "strategy document".into(),
    ];

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(),
        &FakeNowledgeClient::working(),
    )
    .expect("a pack must be produced");

    assert_eq!(report.comparison.retrieval.rows.len(), 3, "one row per query");
    let queries: Vec<&str> = report.comparison.retrieval.rows.iter().map(|r| r.query.as_str()).collect();
    assert!(queries.contains(&"compound engineering"));
}

#[test]
fn ovp_unavailable_for_url_only_yields_partial_pack() {
    // URL-only input: the ovp trunk cannot fetch URLs, so its side is loudly
    // unavailable, but the Nowledge side (which fetches) still runs.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(
        out.path(),
        vault.path(),
        canon.path(),
        None,
        Some("https://every.to/guides/ai-product-management-guide".into()),
    );

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(), // never called (no markdown)
        &FakeNowledgeClient::working(),
    )
    .expect("a partial pack must still be produced");

    assert!(!report.ovp_available, "ovp can't fetch URLs");
    assert!(report.nowledge_available, "nowledge fetched the URL");
    pack_files_exist(out.path());

    let c = &report.comparison;
    assert!(c.concept_overlap.is_none(), "no cross-system overlap without ovp");
    assert!(
        c.findings.iter().any(|f| f.contains("ovp side unavailable") && f.contains("does not fetch URLs")),
        "the trunk URL limitation must be loud: {:?}",
        c.findings
    );
    assert!(c.input_mode.starts_with("url-only"), "mode: {}", c.input_mode);
    assert!(
        c.findings.iter().any(|f| f.contains("did NOT necessarily see byte-identical")),
        "url-only is not a same-input run — caveat must be present: {:?}",
        c.findings
    );
    // Input recorded as a URL (with the exact URL), and the nowledge side captured.
    let url_txt = std::fs::read_to_string(out.path().join("input-url.txt")).unwrap();
    assert!(
        url_txt.contains("https://every.to/guides/ai-product-management-guide"),
        "input-url.txt should record the exact URL: {url_txt:?}"
    );
    assert!(out.path().join("nowledge/normalized.json").exists());
}

#[test]
fn crystal_endpoint_failure_is_none_not_zero() {
    // P1 fix: a failing crystal endpoint must NOT show as "0 crystals" — it is
    // recorded as None + a loud status, and the per-input crystal comparison is
    // declared unavailable (Nowledge has no source-scoped crystal API).
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);

    let report = CompareRun::execute(config, make_cassette_wiring(), &FakeNowledgeClient::crystals_fail())
        .expect("a pack must be produced");

    assert!(report.nowledge_available, "crystal failure must NOT fail the whole side");
    let st = report.comparison.structure.as_ref().expect("both sides present");
    assert_eq!(st.nowledge_global_crystals, None, "failure is None, never 0");
    assert!(st.crystal_status.contains("UNAVAILABLE"), "crystal status loud: {}", st.crystal_status);
    assert!(
        st.current_input_crystal_comparison.contains("UNAVAILABLE"),
        "per-input crystal comparison must be declared unavailable"
    );
    assert!(
        report.comparison.findings.iter().any(|f| f.contains("per-input crystal comparison UNAVAILABLE")),
        "findings must state per-input crystal comparison is unavailable: {:?}",
        report.comparison.findings
    );
}

#[test]
fn materialize_uses_full_content_not_summary() {
    // P1 fix: --materialize-from-nowledge must materialize the FULL parsed
    // content (/sources/{id}/content), NOT the short summary snippet. The fake's
    // full content == the real article fixture while its summary is a short
    // snippet, so we can prove the materialized artifact is the full article
    // (and the ovp cassette pipeline runs on identical text).
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let article = std::fs::read_to_string(article_input()).unwrap();
    assert!(article.len() > 1000, "fixture should be a full article");
    let mut config = base_config(
        out.path(),
        vault.path(),
        canon.path(),
        None,
        Some("https://every.to/guides/ai-product-management-guide".into()),
    );
    config.materialize_from_nowledge = true;

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(),
        &FakeNowledgeClient::with_content(article.clone()),
    )
    .expect("a pack must be produced");

    assert!(
        report.comparison.input_mode.contains("FULL parsed content"),
        "mode must declare full content: {}",
        report.comparison.input_mode
    );
    // The shared artifact == Nowledge's FULL parsed content (the whole article),
    // NOT the short summary snippet.
    let materialized = std::fs::read_to_string(out.path().join("materialized-input.md")).unwrap();
    assert_eq!(materialized, article, "must materialize the FULL content, not the summary");
    assert!(!materialized.contains("SHORT summary snippet"), "summary must not be the artifact");
    // Both sides ran on the same (full) bytes → a real cross-system comparison.
    assert!(report.ovp_available, "ovp ran on the materialized artifact: {:?}", report.comparison.ovp.detail);
    assert!(report.nowledge_available);
    assert!(report.comparison.concept_overlap.is_some());
}

#[test]
fn materialize_summary_fallback_when_content_endpoint_fails() {
    // If /sources/{id}/content fails, materialize falls back to the SHORT summary
    // but labels the mode "materialize-summary-fallback" (NOT strict same-input),
    // so a reader is never told the snippet is the full article.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut config = base_config(
        out.path(),
        vault.path(),
        canon.path(),
        None,
        Some("https://every.to/guides/ai-product-management-guide".into()),
    );
    config.materialize_from_nowledge = true;

    let fake = FakeNowledgeClient {
        fail_content: true,
        summary_override: Some("a short summary snippet only".into()),
        ..Default::default()
    };
    let report = CompareRun::execute(config, make_cassette_wiring(), &fake).expect("a pack must be produced");

    assert!(
        report.comparison.input_mode.contains("materialize-summary-fallback"),
        "content failure must label the fallback, not claim strict same-input: {}",
        report.comparison.input_mode
    );
    let materialized = std::fs::read_to_string(out.path().join("materialized-input.md")).unwrap();
    assert_eq!(materialized, "a short summary snippet only");
}

#[test]
fn materialize_fallback_when_nowledge_has_no_content() {
    // --materialize-from-nowledge requested, but Nowledge produced no parsed
    // content (empty summary). The mode is labeled "materialize-failed" and ovp
    // falls back to the local --input markdown.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut config = base_config(
        out.path(),
        vault.path(),
        canon.path(),
        Some(article_input()),
        Some("https://every.to/guides/ai-product-management-guide".into()),
    );
    config.materialize_from_nowledge = true;

    let report = CompareRun::execute(
        config,
        make_cassette_wiring(),
        &FakeNowledgeClient::with_summary(String::new()), // empty parsed content
    )
    .expect("a pack must be produced");

    assert!(
        report.comparison.input_mode.contains("materialize-failed"),
        "expected materialize-failed fallback, got: {}",
        report.comparison.input_mode
    );
    // No materialized artifact was written; ovp ran on the local markdown.
    assert!(!out.path().join("materialized-input.md").exists());
    assert!(report.ovp_available, "ovp falls back to the local --input markdown");
}

#[test]
fn split_input_mode_is_labeled_and_caveated() {
    // Both --url and --input given → "split" mode: ovp eats the local markdown,
    // Nowledge fetches the URL. The asymmetry + grounding bias must be loud.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(
        out.path(),
        vault.path(),
        canon.path(),
        Some(article_input()),
        Some("https://every.to/guides/ai-product-management-guide".into()),
    );

    let report = CompareRun::execute(config, make_cassette_wiring(), &FakeNowledgeClient::working())
        .expect("a pack must be produced");

    assert!(report.comparison.input_mode.starts_with("split"), "mode: {}", report.comparison.input_mode);
    let f = &report.comparison.findings;
    assert!(f.iter().any(|x| x.contains("did NOT necessarily see byte-identical")), "{f:?}");
    assert!(
        f.iter().any(|x| x.contains("grounding for BOTH sides is measured against the local --input")),
        "split grounding caveat must be present: {f:?}"
    );
    let g = report.comparison.grounding.as_ref().unwrap();
    assert!(g.reference_source.contains("local --input markdown"), "ref: {}", g.reference_source);
}

#[test]
fn score_json_preserves_exact_counts_through_serialization() {
    // Fix #3 end-to-end: the written score.json carries EXACT only-counts, not
    // the capped display-list lengths.
    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let config = base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);

    let report = CompareRun::execute(config, make_cassette_wiring(), &FakeNowledgeClient::working())
        .expect("a pack must be produced");

    let raw = std::fs::read_to_string(out.path().join("comparison/score.json")).unwrap();
    let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
    let co = &v["concept_overlap"];
    let json_only = co["nowledge_only_count"].as_u64().unwrap() as usize;
    let struct_only = report.comparison.concept_overlap.as_ref().unwrap().nowledge_only_count;
    assert_eq!(json_only, struct_only, "score.json must carry the exact count");
    // The exact count is independent of the capped display list length.
    let shown = co["nowledge_only"].as_array().unwrap().len();
    assert!(json_only >= shown, "exact count >= shown list");
}

#[test]
#[ignore = "live: requires Nowledge Mem running at 127.0.0.1:14242; run with `cargo test -- --ignored`"]
fn live_compare_against_nowledge() {
    use ovp_eval::LiveNowledgeClient;

    let out = tempfile::tempdir().unwrap();
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut config =
        base_config(out.path(), vault.path(), canon.path(), Some(article_input()), None);
    config.poll_interval = Duration::from_secs(3);
    config.poll_max_attempts = 100;

    let nowledge =
        LiveNowledgeClient::new("http://127.0.0.1:14242", Duration::from_secs(30)).unwrap();

    let report = CompareRun::execute(config, make_cassette_wiring(), &nowledge)
        .expect("a pack must be produced");

    pack_files_exist(out.path());
    assert!(report.ovp_available, "ovp cassette run should succeed");
    assert!(report.nowledge_available, "live nowledge should extract: see pack for detail");
    let co = report.comparison.concept_overlap.expect("both sides → overlap");
    assert!(co.nowledge_count > 0, "live extraction should yield memories");
}
