//! OVP Next M8 — the External E2E Comparator (`ovp-eval`).
//!
//! Given one input (a URL and/or a local markdown file) it runs BOTH systems
//! end-to-end and emits a deterministic, human-inspectable comparison pack:
//!   1. **ovp2** — via the M7 [`ovp_review::ReviewRun`] harness (the real
//!      pipeline: note + canonical + MOC + knowledge index + RAG), reusing it
//!      wholesale (no pipeline logic is reimplemented here);
//!   2. **Nowledge Mem** — an EXTERNAL reference system, reached only through the
//!      [`nowledge::NowledgeClient`] HTTP adapter (ingest → extract → read).
//!
//! Then it normalizes both into a shared [`normalize::NormalizedSubject`] and
//! compares them across five deterministic, explicitly-lexical dimensions
//! (concept overlap, claim diff, grounding, structure, retrieval).
//!
//! ## Boundaries (deliberate)
//! - Nowledge Mem is a *comparator*, NOT legacy OVP and NOT a dependency of the
//!   trunk. `ovp-eval` may depend on the trunk crates; nothing in the trunk
//!   depends on `ovp-eval`.
//! - The ONLY writes are the comparison pack + the normal writes the M7 review /
//!   `RunCycle` perform into the vault/canonical roots. No async, no shell-out.
//! - **Partial-pack on failure:** if either side fails, the pack is still written
//!   with that side marked unavailable and the loud reason recorded. The
//!   Nowledge adapter itself never degrades silently — every fault is an `Err`.
//!
//! ## Known limitations (v1, surfaced loudly in the pack)
//! - The ovp trunk does not fetch URLs; for `--url`-only input the ovp side is
//!   marked unavailable (pass a local `--input` markdown to run it).
//! - All cross-system metrics are LEXICAL (set overlap, token-overlap
//!   grounding); they flag things to inspect, they are not semantic verdicts.
//! - Nowledge concepts are atomic-memory titles and its retrieval is over the
//!   whole store, vs ovp's canonical nodes / freshly-built vault — different
//!   unit types and scopes, stated in every relevant section.

pub mod compare;
pub mod nowledge;
pub mod normalize;
mod pack;

pub use compare::Comparison;
pub use nowledge::{LiveNowledgeClient, NowledgeClient, NowledgeError};
pub use normalize::NormalizedSubject;

use std::path::{Path, PathBuf};
use std::time::Duration;

use ovp_app::AppWiring;
use ovp_core::ApplyMode;
use ovp_rag::{ContextBuilder, RagCorpus, Ranker, Retriever};
use ovp_review::{ReviewRun, ReviewRunConfig};

use compare::SideStatus;
use nowledge::{MemorySearchResult, SourceDetail};
use normalize::{
    audit_grounding, lexical_overlap_score, normalize_nowledge, normalize_ovp, NormRetrievalHit,
};

/// Everything one comparison run needs.
pub struct CompareConfig {
    pub case_id: String,
    pub out_dir: PathBuf,
    /// Remote URL to ingest on the Nowledge side (the service fetches it).
    pub url: Option<String>,
    /// Local markdown — drives the ovp side, and the Nowledge side when no
    /// `url` is given. Also the grounding reference text when present.
    pub markdown_input: Option<PathBuf>,
    // --- ovp (review-run) parameters ---
    pub manifest_path: PathBuf,
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub run_id: String,
    /// Fixed retrieval query set run through both systems.
    pub queries: Vec<String>,
    pub rag_limit: usize,
    // --- Nowledge parameters ---
    pub space_id: String,
    pub search_limit: usize,
    pub poll_interval: Duration,
    pub poll_max_attempts: u32,
    /// Token-overlap ratio above which a claim counts as grounded.
    pub grounding_threshold: f64,
    /// Strict same-input mode: after Nowledge ingests + parses the URL, write its
    /// parsed content to a shared `materialized-input.md` and feed THAT to the
    /// ovp side, so both systems analyze byte-identical text (URL becomes mere
    /// source metadata). No effect when only a local `--input` is given (already
    /// a shared artifact).
    pub materialize_from_nowledge: bool,
}

/// The only failure that prevents producing ANY pack (e.g. the output dir can't
/// be created). Per-side failures are captured in the pack instead.
#[derive(Debug)]
pub enum CompareError {
    Io(String),
}

impl std::fmt::Display for CompareError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CompareError::Io(s) => write!(f, "{s}"),
        }
    }
}

impl std::error::Error for CompareError {}

/// The in-memory result of a comparison run.
pub struct CompareReport {
    pub case_id: String,
    pub out_dir: PathBuf,
    pub ovp_available: bool,
    pub nowledge_available: bool,
    pub comparison: Comparison,
}

/// The comparator entry point.
pub struct CompareRun;

impl CompareRun {
    /// Run one comparison. `make_wiring` builds the ovp side's [`AppWiring`]
    /// (the CLI owns client construction, exactly like `review-run`); `nowledge`
    /// is the HTTP adapter (live, or a fake in tests). Always writes a pack.
    pub fn execute<F>(
        config: CompareConfig,
        make_wiring: F,
        nowledge: &dyn NowledgeClient,
    ) -> Result<CompareReport, CompareError>
    where
        F: FnOnce(&Path) -> Result<AppWiring, String>,
    {
        ensure_dir(&config.out_dir)?;
        ensure_dir(&config.out_dir.join("ovp"))?;
        ensure_dir(&config.out_dir.join("nowledge"))?;
        ensure_dir(&config.out_dir.join("comparison"))?;

        let markdown_text =
            config.markdown_input.as_ref().and_then(|p| std::fs::read_to_string(p).ok());

        // --- Nowledge side FIRST (so its parse can be materialized for ovp) ---
        let core = run_nowledge_core(&config, nowledge);
        let mut nowledge_status = core.status.clone();
        let nowledge_detail = core.detail;
        let mut nowledge_subject =
            nowledge_detail.as_ref().map(|d| normalize_nowledge(d, core.global_crystals));

        // Nowledge comparable lane: lexical retrieval over THIS source's memories.
        if let (Some(subject), Some(detail)) = (nowledge_subject.as_mut(), nowledge_detail.as_ref()) {
            subject.retrieval = nowledge_scoped_retrieval(detail, &config.queries, config.rag_limit);
        }
        // Nowledge background lane: whole-store /memories/search (NOT comparable).
        let (nowledge_global, nowledge_global_raw, nowledge_global_status) =
            if nowledge_subject.is_some() {
                match nowledge_global_search(nowledge, &config) {
                    Ok((hits, raw)) => {
                        (hits, raw, "available (/memories/search, whole store)".to_string())
                    }
                    Err(e) => (Vec::new(), Vec::new(), format!("error: {e}")),
                }
            } else {
                (Vec::new(), Vec::new(), "unavailable (nowledge side failed)".to_string())
            };
        let nowledge_scoped_status = if nowledge_subject.is_some() {
            "available (lexical over this source's memories)".to_string()
        } else {
            "unavailable (nowledge side failed)".to_string()
        };

        // --- Resolve the ovp input + the same-input mode ---
        let (ovp_input, input_mode, materialized_text) =
            resolve_ovp_input(&config, &nowledge_detail, &core.parsed_content)?;

        // --- ovp side (real pipeline via M7) on the resolved input ---
        let (ovp_report, ovp_note_md, mut ovp_status) =
            run_ovp_side(&config, make_wiring, ovp_input.as_deref());
        let mut ovp_subject = ovp_report
            .as_ref()
            .filter(|r| r.cycle_succeeded())
            .map(|r| normalize_ovp(&r.canonical, ovp_note_md.as_deref()));
        let ovp_retrieval_status;
        if let Some(subject) = ovp_subject.as_mut() {
            match ovp_retrieval(&config) {
                Ok(hits) => {
                    subject.retrieval = hits;
                    ovp_retrieval_status = "available (ovp-rag, lexical over the built vault)".to_string();
                }
                Err(e) => ovp_retrieval_status = format!("error: {e}"),
            }
        } else {
            ovp_retrieval_status = "unavailable (ovp side failed)".to_string();
        }

        // --- grounding reference: the shared/materialized text if we have it,
        //     else the ovp markdown, else Nowledge's parsed summary. The chosen
        //     source is disclosed (it is the SINGLE reference both sides are
        //     scored against — load-bearing for honest grounding comparison). ---
        let (reference, grounding_reference) = if let Some(m) = materialized_text.clone() {
            (m, "materialized shared artifact (Nowledge's full parsed content; both sides analyzed this)".to_string())
        } else if let Some(md) = markdown_text.clone() {
            let desc = if config.url.is_some() {
                "the local --input markdown (split mode: NOT the URL Nowledge fetched)".to_string()
            } else {
                "the shared local --input markdown".to_string()
            };
            (md, desc)
        } else if let Some(full) = core.parsed_content.clone() {
            (full, "Nowledge's FULL parsed content (no local markdown available)".to_string())
        } else if let Some(sum) = nowledge_detail.as_ref().and_then(|d| d.source.summary.clone()) {
            (sum, "Nowledge's SHORT summary snippet (full content unavailable — grounding may be biased)".to_string())
        } else {
            (String::new(), "none (no input text available)".to_string())
        };
        if let Some(s) = ovp_subject.as_mut() {
            audit_grounding(s, &reference, config.grounding_threshold);
        }
        if let Some(s) = nowledge_subject.as_mut() {
            audit_grounding(s, &reference, config.grounding_threshold);
        }

        if ovp_subject.is_none() && ovp_status.available {
            ovp_status = SideStatus::failed("ovp produced no usable subject");
        }
        if nowledge_subject.is_none() && nowledge_status.available {
            nowledge_status = SideStatus::failed("nowledge produced no usable subject");
        }

        let comparison = compare::build(compare::BuildInputs {
            case_id: &config.case_id,
            input_mode: &input_mode,
            ovp: ovp_subject.as_ref(),
            nowledge: nowledge_subject.as_ref(),
            ovp_status: ovp_status.clone(),
            nowledge_status: nowledge_status.clone(),
            grounding_threshold: config.grounding_threshold,
            queries: &config.queries,
            nowledge_global: &nowledge_global,
            retrieval_ovp_status: ovp_retrieval_status,
            retrieval_scoped_status: nowledge_scoped_status,
            retrieval_global_status: nowledge_global_status,
            crystal_status: core.crystal_status.clone(),
            grounding_reference,
        });

        pack::write(pack::PackInputs {
            config: &config,
            comparison: &comparison,
            input_mode: &input_mode,
            ovp_subject: ovp_subject.as_ref(),
            nowledge_subject: nowledge_subject.as_ref(),
            nowledge_detail: nowledge_detail.as_ref(),
            nowledge_global_raw: &nowledge_global_raw,
            materialized_text: materialized_text.as_deref(),
            markdown_text: markdown_text.as_deref(),
            reference: &reference,
        })?;

        Ok(CompareReport {
            case_id: config.case_id,
            out_dir: config.out_dir,
            ovp_available: ovp_status.available,
            nowledge_available: nowledge_status.available,
            comparison,
        })
    }
}

/// Decide what markdown the ovp side eats and label the same-input mode. Writes
/// `materialized-input.md` when materializing from Nowledge's parse. Returns
/// `(ovp_input_path, input_mode, materialized_text)`.
fn resolve_ovp_input(
    config: &CompareConfig,
    nowledge_detail: &Option<SourceDetail>,
    parsed_content: &Option<String>,
) -> Result<(Option<PathBuf>, String, Option<String>), CompareError> {
    // Strict same-input: materialize Nowledge's FULL parsed content as the
    // shared artifact and feed it to ovp. URL becomes mere source metadata.
    if config.materialize_from_nowledge {
        let path = config.out_dir.join("materialized-input.md");
        // 1. Strict same-input: the FULL parsed content from /sources/{id}/content.
        if let Some(full) = parsed_content.as_ref().filter(|c| !c.trim().is_empty()) {
            write_file(&path, full)?;
            return Ok((
                Some(path),
                "materialized-from-nowledge (shared artifact: both sides analyze Nowledge's FULL parsed content)"
                    .to_string(),
                Some(full.clone()),
            ));
        }
        // 2. Fallback: the SHORT summary snippet — explicitly NOT strict same-input.
        if let Some(sum) =
            nowledge_detail.as_ref().and_then(|d| d.source.summary.clone()).filter(|s| !s.trim().is_empty())
        {
            write_file(&path, &sum)?;
            return Ok((
                Some(path),
                "materialize-summary-fallback (content endpoint unavailable; ovp got Nowledge's SHORT \
                 summary snippet, NOT the full article — NOT strict same-input)"
                    .to_string(),
                Some(sum),
            ));
        }
        // 3. Nothing materializable → fall back to the local --input if present.
        let mode = "materialize-failed (Nowledge produced no parsed content or summary; \
                    falling back to local --input if present)"
            .to_string();
        return Ok((config.markdown_input.clone(), mode, None));
    }

    let mode = match (&config.url, &config.markdown_input) {
        (None, Some(_)) => "shared-markdown (both sides ate the same local file)",
        (Some(_), Some(_)) => {
            "split (ovp = local --input markdown; nowledge = fetched --url — NOT byte-identical)"
        }
        (Some(_), None) => "url-only (ovp cannot fetch URLs; ovp side unavailable)",
        (None, None) => "no-input",
    };
    Ok((config.markdown_input.clone(), mode.to_string(), None))
}

// --- ovp side -------------------------------------------------------------

type ReviewReport = ovp_review::ReviewReport;

fn run_ovp_side<F>(
    config: &CompareConfig,
    make_wiring: F,
    ovp_input: Option<&Path>,
) -> (Option<ReviewReport>, Option<String>, SideStatus)
where
    F: FnOnce(&Path) -> Result<AppWiring, String>,
{
    let Some(input) = ovp_input else {
        return (
            None,
            None,
            SideStatus::failed(
                "ovp side needs a local markdown input; the trunk does not fetch URLs \
                 (pass --input, or --materialize-from-nowledge for a URL run)",
            ),
        );
    };

    let review_config = ReviewRunConfig {
        input_path: input.to_path_buf(),
        manifest_path: config.manifest_path.clone(),
        vault_root: config.vault_root.clone(),
        canonical_root: config.canonical_root.clone(),
        out_dir: config.out_dir.join("ovp").join("review-pack"),
        run_id: config.run_id.clone(),
        // Seed the M7 RAG preview with the first fixed query (the multi-query
        // retrieval comparison is run separately, below, via ovp-rag directly).
        rag_query: config.queries.first().cloned(),
        rag_limit: config.rag_limit,
        expected_dir: None,
        mode: ApplyMode::Apply,
    };

    // The wiring factory binds the SAME resolved input the review config uses,
    // so a materialized artifact flows to both.
    match ReviewRun::execute(review_config, || make_wiring(input)) {
        Ok(report) => {
            let note_md = report
                .primary_note
                .as_ref()
                .and_then(|rel| std::fs::read_to_string(config.vault_root.join(rel)).ok());
            let status = if report.cycle_succeeded() {
                SideStatus::ok()
            } else {
                SideStatus::failed(
                    report.failure_reason().unwrap_or_else(|| "ovp cycle did not succeed".into()),
                )
            };
            (Some(report), note_md, status)
        }
        Err(e) => (None, None, SideStatus::failed(format!("review-run could not produce a pack: {e}"))),
    }
}

/// Run the fixed query set through ovp-rag over the freshly-built vault.
fn ovp_retrieval(config: &CompareConfig) -> Result<Vec<NormRetrievalHit>, String> {
    let corpus = RagCorpus::load(&config.vault_root, &config.canonical_root)
        .map_err(|e| format!("rag corpus load: {e}"))?;
    let mut hits = Vec::new();
    for query in &config.queries {
        let scored = Retriever::new().score(&corpus, query);
        let ranked = Ranker::with_limit(config.rag_limit).rank(scored);
        let ctx = ContextBuilder { max_concepts: config.rag_limit, ..ContextBuilder::default() }
            .build(&corpus, &ranked, query);
        for (rank, c) in ctx.selected.iter().enumerate() {
            hits.push(NormRetrievalHit {
                query: query.clone(),
                rank,
                title: c.title.clone(),
                snippet: c.snippet.clone().unwrap_or_default(),
                grounded: false,
            });
        }
    }
    Ok(hits)
}

// --- Nowledge side --------------------------------------------------------

struct NowledgeCore {
    detail: Option<SourceDetail>,
    /// The source's FULL parsed content (paged from `/sources/{id}/content`).
    /// `None` if that endpoint failed — the comparator then falls back to the
    /// short `summary`, but never silently passes the snippet off as "full".
    parsed_content: Option<String>,
    /// Whole-store crystal count: `None` if the endpoint failed (never 0-coerced).
    global_crystals: Option<usize>,
    /// Human status for the crystal context query.
    crystal_status: String,
    status: SideStatus,
}

impl NowledgeCore {
    fn failed(status: SideStatus) -> Self {
        Self {
            detail: None,
            parsed_content: None,
            global_crystals: None,
            crystal_status: "not queried (nowledge side failed)".to_string(),
            status,
        }
    }
}

/// The service caps `/sources/{id}/content` `limit` at 50000.
const CONTENT_PAGE: usize = 50_000;
/// Safety bound on paging (≈ 10 MB of content); prevents a runaway loop.
const MAX_CONTENT_PAGES: usize = 200;

/// Page `/sources/{id}/content` until `has_more` is false, concatenating the
/// FULL parsed markdown. This is the real text Nowledge extracted (the
/// `summary` field is only a short snippet). Loud on adapter error.
fn fetch_full_content(
    nowledge: &dyn NowledgeClient,
    source_id: &str,
) -> Result<String, NowledgeError> {
    let mut out = String::new();
    let mut offset = 0usize;
    for _ in 0..MAX_CONTENT_PAGES {
        let page = nowledge.get_source_content(source_id, offset, CONTENT_PAGE)?;
        out.push_str(&page.content);
        if !page.has_more {
            break;
        }
        // Advance by the server's accounting; fall back to what we read. Stop on
        // no progress (defensive — a well-behaved server won't hit this).
        let advance =
            if page.returned_length > 0 { page.returned_length } else { page.content.chars().count() };
        if advance == 0 {
            break;
        }
        offset += advance;
        if page.total_length > 0 && offset >= page.total_length {
            break;
        }
    }
    Ok(out)
}

/// Ingest → extract → poll → read. Any fault here fails the Nowledge side loud
/// (the adapter returns `Err`); the comparator records it and writes a partial
/// pack. The global crystal count is best-effort context — its endpoint failing
/// is recorded loudly (`None` + status), never silently shown as 0.
fn run_nowledge_core(config: &CompareConfig, nowledge: &dyn NowledgeClient) -> NowledgeCore {
    let ingest = if let Some(url) = &config.url {
        nowledge.ingest_url(url, &config.space_id)
    } else if let Some(md) = &config.markdown_input {
        // The Nowledge file-path ingest needs an absolute path. Distinguish the
        // common "file missing" case from a genuine path-resolution failure so
        // the loud error is actionable.
        match md.canonicalize() {
            Ok(abs) => match abs.to_str() {
                Some(s) => nowledge.ingest_file_path(s, &config.space_id),
                None => Err(NowledgeError::MissingState {
                    op: "resolve-input".into(),
                    detail: format!("input path is not valid UTF-8: {}", md.display()),
                }),
            },
            Err(e) => Err(NowledgeError::MissingState {
                op: "resolve-input".into(),
                detail: format!("input markdown not readable ({}): {e}", md.display()),
            }),
        }
    } else {
        return NowledgeCore::failed(SideStatus::failed(
            "no --url or --input provided for Nowledge ingest",
        ));
    };

    let ingest = match ingest {
        Ok(r) => r,
        Err(e) => return NowledgeCore::failed(SideStatus::failed(e.to_string())),
    };

    if let Err(e) = nowledge.trigger_extract(&ingest.source_id) {
        return NowledgeCore::failed(SideStatus::failed(e.to_string()));
    }

    // Poll for a terminal lifecycle state.
    let detail = match poll_until_extracted(nowledge, &ingest.source_id, config) {
        Ok(d) => d,
        Err(e) => return NowledgeCore::failed(SideStatus::failed(e.to_string())),
    };

    // Fetch the FULL parsed content (paged). Best-effort: a failure leaves
    // `None` so materialization/grounding fall back to the short summary with a
    // loud label, rather than passing the snippet off as the full article.
    let parsed_content =
        fetch_full_content(nowledge, &detail.source.id).ok().filter(|c| !c.trim().is_empty());

    // Best-effort global crystal count (whole-store CONTEXT only — crystals are
    // cross-source, NOT scoped to this input). A failure is recorded loudly as
    // `None` + a status string; it never coerces to 0 and never fails the side.
    let (global_crystals, crystal_status) =
        match nowledge.list_crystals(config.search_limit.max(50)) {
            Ok(c) => (Some(c.len()), format!("available (global store: {} crystals)", c.len())),
            Err(e) => (None, format!("UNAVAILABLE — crystal endpoint error: {e}")),
        };

    NowledgeCore {
        detail: Some(detail),
        parsed_content,
        global_crystals,
        crystal_status,
        status: SideStatus::ok(),
    }
}

/// Poll `get_source` until lifecycle is `extracted` (success) or a failure state
/// (`error`/`failed`), or the attempt budget runs out. Fails loud on timeout.
fn poll_until_extracted(
    nowledge: &dyn NowledgeClient,
    source_id: &str,
    config: &CompareConfig,
) -> Result<SourceDetail, NowledgeError> {
    let mut last_state = String::new();
    for attempt in 0..config.poll_max_attempts {
        let detail = nowledge.get_source(source_id)?;
        last_state = detail.source.lifecycle_state.clone();
        match last_state.as_str() {
            "extracted" => return Ok(detail),
            "error" | "failed" => {
                return Err(NowledgeError::MissingState {
                    op: "extract".into(),
                    detail: format!(
                        "source entered terminal failure state `{last_state}`: {}",
                        detail.source.error_message.clone().unwrap_or_default()
                    ),
                })
            }
            _ => {
                if attempt + 1 < config.poll_max_attempts {
                    std::thread::sleep(config.poll_interval);
                }
            }
        }
    }
    Err(NowledgeError::Timeout {
        op: format!(
            "extract poll ({} attempts; last state `{}`)",
            config.poll_max_attempts, last_state
        ),
    })
}

/// The COMPARABLE lane: lexically retrieve over THIS source's extracted
/// memories (title + content), so it lines up with ovp-rag (also lexical, also
/// over this input) — NOT with Nowledge's whole-store semantic search. No
/// network: scored client-side from the already-fetched source detail.
fn nowledge_scoped_retrieval(
    detail: &SourceDetail,
    queries: &[String],
    limit: usize,
) -> Vec<NormRetrievalHit> {
    let mut hits = Vec::new();
    for query in queries {
        // Score each memory by lexical overlap; keep > 0, rank desc, take `limit`.
        let mut scored: Vec<(usize, &nowledge::SourceMemory)> = detail
            .memories
            .iter()
            .map(|m| {
                let text = format!("{} {}", m.title, m.content);
                (lexical_overlap_score(query, &text), m)
            })
            .filter(|(s, _)| *s > 0)
            .collect();
        scored.sort_by_key(|hit| std::cmp::Reverse(hit.0));
        for (rank, (_, m)) in scored.into_iter().take(limit).enumerate() {
            hits.push(NormRetrievalHit {
                query: query.clone(),
                rank,
                title: m.title.clone(),
                snippet: m.content.chars().take(280).collect::<String>(),
                grounded: false,
            });
        }
    }
    hits
}

/// The BACKGROUND lane: Nowledge `/memories/search` over the WHOLE store. Useful
/// context, but its hits can come from unrelated prior sources, so it is NEVER
/// used to draw ovp-deficiency conclusions. Returns normalized hits + raw
/// results (for the pack). Loud on adapter error.
fn nowledge_global_search(
    nowledge: &dyn NowledgeClient,
    config: &CompareConfig,
) -> Result<(Vec<NormRetrievalHit>, Vec<MemorySearchResult>), NowledgeError> {
    let mut hits = Vec::new();
    let mut raw = Vec::new();
    for query in &config.queries {
        let results = nowledge.search_memories(query, config.search_limit)?;
        for (rank, r) in results.iter().enumerate() {
            let (title, snippet) = match &r.memory {
                Some(m) => (
                    m.title.clone().unwrap_or_default(),
                    m.content.chars().take(280).collect::<String>(),
                ),
                None => (String::new(), String::new()),
            };
            hits.push(NormRetrievalHit { query: query.clone(), rank, title, snippet, grounded: false });
        }
        raw.extend(results);
    }
    Ok((hits, raw))
}

// --- shared ---------------------------------------------------------------

pub(crate) fn ensure_dir(dir: &Path) -> Result<(), CompareError> {
    std::fs::create_dir_all(dir)
        .map_err(|e| CompareError::Io(format!("creating `{}`: {e}", dir.display())))
}

pub(crate) fn write_file(path: &Path, contents: &str) -> Result<(), CompareError> {
    if let Some(parent) = path.parent() {
        ensure_dir(parent)?;
    }
    std::fs::write(path, contents)
        .map_err(|e| CompareError::Io(format!("writing `{}`: {e}", path.display())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use nowledge::{SourceDetail, SourceInfo, SourceMemory};

    fn detail(memories: &[(&str, &str)]) -> SourceDetail {
        SourceDetail {
            source: SourceInfo {
                id: "s".into(),
                source_url: String::new(),
                original_name: "i.md".into(),
                lifecycle_state: "extracted".into(),
                summary: None,
                section_tree: None,
                memory_count: memories.len() as u32,
                error_message: None,
            },
            memories: memories
                .iter()
                .map(|(t, c)| SourceMemory {
                    id: "m".into(),
                    title: (*t).into(),
                    content: (*c).into(),
                    unit_type: "fact".into(),
                })
                .collect(),
        }
    }

    #[test]
    fn scoped_retrieval_ranks_by_overlap_and_drops_non_matches() {
        let d = detail(&[
            ("agent native product management", "the conversation is the work"),
            ("compound engineering", "reuse agents across the loop"),
            ("unrelated", "lorem ipsum dolor sit amet"),
        ]);
        let hits = nowledge_scoped_retrieval(&d, &["agent native product management".to_string()], 5);
        // Only the matching memory is returned; the unrelated one is dropped.
        assert_eq!(hits.len(), 1, "got {hits:?}");
        assert_eq!(hits[0].title, "agent native product management");
        assert_eq!(hits[0].rank, 0);
    }

    #[test]
    fn scoped_retrieval_empty_on_no_match_and_respects_limit() {
        let d = detail(&[
            ("rag retrieval augmented generation", "vector search over chunks"),
            ("rag pipeline", "retrieval over a corpus"),
        ]);
        // No overlap → empty.
        assert!(nowledge_scoped_retrieval(&d, &["quantum chromodynamics".to_string()], 5).is_empty());
        // Both match "rag"/"retrieval"; limit caps the result.
        let hits = nowledge_scoped_retrieval(&d, &["rag retrieval".to_string()], 1);
        assert_eq!(hits.len(), 1, "limit honored: {hits:?}");
    }
}
