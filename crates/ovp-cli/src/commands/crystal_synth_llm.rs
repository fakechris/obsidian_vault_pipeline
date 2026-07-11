//! `crystal-synth --cluster-mode llm` — the L3 coverage-first sweep.
//!
//!   uncovered packs (not cited by any ACTIVE durable claim), case_id order
//!     → seed's kNN neighborhood from cached embeddings (cross-community)
//!     → `cluster_select/v1`: pick 3..cap case ids for ONE claim-worthy
//!       cross-source cluster, or REFUSE (a first-class answer)
//!     → mechanical selection validation (offered set, ≥3, ≤cap) — a
//!       violation fails THAT seed loudly (recorded) and the sweep continues
//!     → superset guard: an active claim whose source_cases ⊇ the chosen set
//!       skips the synth call; ditto a cluster already attempted this run
//!     → EXISTING `crystal_synth/v1` + grounded filter + dedup — unchanged;
//!       gates and the ledger cannot drift
//!     → grounded claims pool up and strength runs in micro-batched WAVES:
//!       whenever the pool reaches batch mode's chunk size (or the sweep
//!       ends) full chunks flush through `crystal_strength/v1`, so a
//!       many-cluster sweep no longer pays one strength call per cluster
//!     → seeds covered by claims routed durable in a flushed wave drop out
//!       mid-sweep. Coverage therefore updates in waves, not per cluster:
//!       the lag is bounded by one chunk of pending claims, and a seed that
//!       would previously have dropped out immediately may still spend a
//!       select call — the honest cost of the batching, measured by the A/B.
//!
//! Per-seed outcomes go to stdout AND `<work-dir>/l3-sweep.jsonl` (written
//! incrementally, so even a failed run leaves evidence); each strength wave
//! appends its own `{"outcome":"wave",...}` line. The sweep only PROPOSES
//! groupings — every claim still passes the untouched citation + provenance +
//! strength gates before the idempotent durable write.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write as _;
use std::path::Path;

use ovp_domain::crystal::select::{
    CaseDigest, ClusterSelection, cluster_select_request, digest_from_reader_md,
    parse_cluster_selection, validate_selection,
};
use ovp_domain::crystal::synth::{
    Cluster, UnitsCatalog, citation_signature, crystal_synth_request, parse_synth_claims,
    parse_strength_verdicts, strength_request,
};
use ovp_domain::crystal::themes::ThemesFile;
use ovp_domain::crystal::{
    ClaimStrengthVerdict, CrystalCandidate, CrystalClaim, CrystalStatus, FinalClass,
    GroundingIndex, final_routing, lint_candidate, score_candidate, strength_coverage,
};
use ovp_embed::cache as embed_cache;
use ovp_embed::knn::cosine;
use ovp_embed::{EMBED_DIM, EMBED_HEAD_CHARS, EMBED_MODEL_ID, document_text};
use ovp_llm::ModelClient;
use serde::Serialize;

use crate::CliError;
use crate::commands::crystal_synth::{MAX_STRENGTH_CLAIMS_PER_CALL, RepairLog, call_and_parse};
use crate::commands::crystal_themes::clean_reader_body;
use crate::commands::crystal_write::read_ledger;

/// Fallback synthesis-context theme when the seed maps to no community.
const FALLBACK_THEME: &str = "cross-source";

/// Sweep knobs (all operator-visible flags).
pub(crate) struct SweepConfig {
    /// Per-run LLM budget cap: at most this many `cluster_select/v1` calls.
    pub max_seeds: usize,
    /// kNN neighborhood size offered alongside the seed.
    pub neighborhood: usize,
    /// Cluster size cap (shared with batch mode's per-request cap).
    pub max_cases_per_cluster: usize,
    pub max_units_per_case: usize,
}

/// Sweep counters, serialized into the run report.
#[derive(Debug, Default, Clone, Serialize)]
pub(crate) struct SweepStats {
    pub uncovered_before: usize,
    pub uncovered_after: usize,
    pub selected: usize,
    pub refused: usize,
    pub guarded: usize,
    pub failed: usize,
    pub covered_mid_run: usize,
    /// True when `max_seeds` ran out with uncovered seeds still unattempted.
    pub budget_exhausted: bool,
    pub select_calls: usize,
    pub synth_calls: usize,
    /// Chunked `crystal_strength/v1` calls (≤ `MAX_STRENGTH_CLAIMS_PER_CALL`
    /// claims each — batch mode's constant), NOT clusters.
    pub strength_calls: usize,
    /// Flush events (one wave may span several chunked calls).
    pub strength_waves: usize,
}

/// Everything the shared crystal-synth tail (durable write + summary) needs.
pub(crate) struct SweepOutcome {
    /// Every claim the synth stage emitted (pre-gate) — `candidate.json`.
    pub raw_claims: Vec<CrystalClaim>,
    /// Grounded + deduped claims, accumulated across clusters.
    pub grounded: CrystalCandidate,
    pub verdicts: Vec<ClaimStrengthVerdict>,
    pub deduped: Vec<ovp_domain::crystal::synth::DedupedClaim>,
    pub dropped_ungrounded: Vec<String>,
    pub stats: SweepStats,
    pub repairs: Vec<RepairLog>,
}

/// One line of `l3-sweep.jsonl`.
#[derive(Debug, Serialize)]
struct SeedReport<'a> {
    seed: &'a str,
    outcome: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    selected: Option<&'a [String]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rationale: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    guarded_by: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    claims: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    grounded: Option<usize>,
    /// Strength-pool size after this cluster's grounded claims joined it —
    /// they are verdict-pending until the next wave flush.
    #[serde(skip_serializing_if = "Option::is_none")]
    pending: Option<usize>,
}

impl<'a> SeedReport<'a> {
    fn new(seed: &'a str, outcome: &'static str) -> Self {
        SeedReport {
            seed,
            outcome,
            selected: None,
            rationale: None,
            reason: None,
            error: None,
            guarded_by: None,
            claims: None,
            grounded: None,
            pending: None,
        }
    }
}

/// One line of `l3-sweep.jsonl` for a strength wave (no `seed` field —
/// consumers keep per-seed lines by filtering on `seed`). `durable_routed` /
/// `newly_covered` live here now: routing is only known once a wave flushes.
#[derive(Debug, Serialize)]
struct WaveReport {
    /// Always `"wave"`, so line consumers keyed on `outcome` stay simple.
    outcome: &'static str,
    wave: usize,
    claims: usize,
    /// Chunked `crystal_strength/v1` calls this wave spent.
    strength_calls: usize,
    durable_routed: usize,
    newly_covered: Vec<String>,
}

fn write_jsonl<T: Serialize>(report: &mut std::fs::File, line: &T) -> Result<(), CliError> {
    let s = serde_json::to_string(line).map_err(|e| CliError::Io(e.to_string()))?;
    writeln!(report, "{s}").map_err(|e| CliError::Io(format!("l3-sweep.jsonl: {e}")))
}

fn write_seed_line(report: &mut std::fs::File, line: &SeedReport) -> Result<(), CliError> {
    println!("  l3[{}]: {}", line.seed, line.outcome);
    write_jsonl(report, line)
}

/// Resolve one embedding vector per catalog case from the content-addressed
/// cache (the SAME text derivation `crystal-themes` uses, so a themes run
/// warms this sweep). Missing vectors are embedded when the build carries the
/// `embed` feature; otherwise the run fails with a clear remedy — llm cluster
/// mode is meaningless without neighborhoods, so there is NO graceful skip
/// here (unlike `crystal-themes`' degradation contract).
pub(crate) fn resolve_catalog_vectors(
    reader_dir: &Path,
    catalog: &UnitsCatalog,
    embed_cache_dir: &Path,
) -> Result<BTreeMap<String, Vec<f32>>, CliError> {
    let mut texts: BTreeMap<String, String> = BTreeMap::new();
    for (case_id, case) in &catalog.cases {
        let body = std::fs::read_to_string(reader_dir.join(case_id).join("reader.md"))
            .unwrap_or_default();
        texts.insert(
            case_id.clone(),
            document_text(&case.title, &clean_reader_body(&body), EMBED_HEAD_CHARS),
        );
    }
    let mut vectors: BTreeMap<String, Vec<f32>> = BTreeMap::new();
    let mut missing: Vec<&String> = Vec::new();
    for (case_id, text) in &texts {
        let sha = embed_cache::text_sha256(text);
        match embed_cache::load(embed_cache_dir, &sha, EMBED_MODEL_ID, EMBED_DIM) {
            Some(v) => {
                vectors.insert(case_id.clone(), v);
            }
            None => missing.push(case_id),
        }
    }
    if !missing.is_empty() {
        embed_and_cache(&texts, &missing, embed_cache_dir, &mut vectors)?;
    }
    Ok(vectors)
}

#[cfg(feature = "embed")]
fn embed_and_cache(
    texts: &BTreeMap<String, String>,
    missing: &[&String],
    embed_cache_dir: &Path,
    vectors: &mut BTreeMap<String, Vec<f32>>,
) -> Result<(), CliError> {
    eprintln!(
        "crystal-synth: embedding {} pack(s) with {EMBED_MODEL_ID} for llm cluster mode",
        missing.len()
    );
    let mut embedder = ovp_embed::embedder::Embedder::new(true).map_err(|e| {
        CliError::Io(format!(
            "crystal-synth: llm cluster mode needs the embedding model but it is \
             unavailable ({e}). Warm the cache first: `ovp2 crystal-themes --vault-root ...` \
             (embed-enabled build, online once)."
        ))
    })?;
    let batch_texts: Vec<String> = missing.iter().map(|id| texts[id.as_str()].clone()).collect();
    let mut out = Vec::with_capacity(batch_texts.len());
    for chunk in batch_texts.chunks(64) {
        let batch = embedder
            .embed(chunk)
            .map_err(|e| CliError::Io(format!("crystal-synth: embedding: {e}")))?;
        out.extend(batch);
    }
    for (id, vector) in missing.iter().zip(out) {
        let sha = embed_cache::text_sha256(&texts[id.as_str()]);
        embed_cache::store(embed_cache_dir, &sha, EMBED_MODEL_ID, &vector)
            .map_err(|e| CliError::Io(format!("crystal-synth: embedding cache: {e}")))?;
        vectors.insert((*id).clone(), vector);
    }
    Ok(())
}

#[cfg(not(feature = "embed"))]
fn embed_and_cache(
    _texts: &BTreeMap<String, String>,
    missing: &[&String],
    embed_cache_dir: &Path,
    _vectors: &mut BTreeMap<String, Vec<f32>>,
) -> Result<(), CliError> {
    Err(CliError::Io(format!(
        "crystal-synth: llm cluster mode needs cached embeddings for every reader \
         pack, but {} pack(s) have none under {} and this build lacks the `embed` \
         feature. Warm the cache first (`ovp2 crystal-themes --vault-root ...` with \
         an embed-enabled build) or rebuild with `--features embed`.",
        missing.len(),
        embed_cache_dir.display()
    )))
}

/// The coverage-first seed order: catalog cases not cited by any ACTIVE
/// durable claim, ascending case_id (BTreeMap order — deterministic).
pub(crate) fn uncovered_seeds(catalog: &UnitsCatalog, covered: &BTreeSet<String>) -> Vec<String> {
    catalog
        .cases
        .keys()
        .filter(|id| !covered.contains(*id))
        .cloned()
        .collect()
}

/// Top-`k` neighbors of `seed` by cosine (desc; ties by ascending case_id).
/// Cross-community by construction — the neighborhood never consults themes.
fn neighborhood_of(
    seed: &str,
    vectors: &BTreeMap<String, Vec<f32>>,
    k: usize,
) -> Vec<String> {
    let Some(sv) = vectors.get(seed) else {
        return Vec::new();
    };
    let mut scored: Vec<(f64, &String)> = vectors
        .iter()
        .filter(|(id, _)| id.as_str() != seed)
        .map(|(id, v)| (cosine(sv, v), id))
        .collect();
    scored.sort_by(|(sa, ia), (sb, ib)| {
        sb.partial_cmp(sa)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(ia.cmp(ib))
    });
    scored.into_iter().take(k).map(|(_, id)| id.clone()).collect()
}

/// Synthesis-context theme for an L3 cluster: the SEED's community keywords
/// when the themes projection maps it (deterministic — never the display
/// label), else a fixed fallback. Presentation relabels cannot move cassette
/// keys, same invariant as batch mode.
fn cluster_theme(seed: &str, themes: Option<&ThemesFile>) -> String {
    if let Some(t) = themes
        && let Some(&cid) = t.packs.get(seed)
        && let Some(c) = t.communities.iter().find(|c| c.id == cid)
    {
        return c.synth_theme();
    }
    FALLBACK_THEME.to_string()
}

/// Flush the pending strength pool in ≤`MAX_STRENGTH_CLAIMS_PER_CALL` chunks
/// (batch mode's constant — one shared knob, never redeclared).
///
/// `drain_all=false` (mid-sweep trigger) flushes FULL chunks only, so the
/// pool — and with it the mid-run coverage lag — always stays under one
/// chunk, and the total call count stays at `ceil(total_claims / chunk)`.
/// `drain_all=true` (end of sweep, or the select budget is spent) flushes
/// the remainder too. Each chunk is ONE `crystal_strength/v1` call; a
/// failure fails the RUN, exactly like batch mode (`call_and_parse` already
/// invalidates the cassette on content failures). After the verdicts land,
/// the wave's claims are routed with the SAME `final_routing` the write path
/// uses and every case cited by a durable-routed claim joins `covered`, so
/// later seeds drop out — coverage now moves in waves, not per cluster.
#[allow(clippy::too_many_arguments)]
fn flush_strength_wave(
    client: &mut dyn ModelClient,
    catalog: &UnitsCatalog,
    index: &GroundingIndex,
    pending: &mut Vec<CrystalClaim>,
    drain_all: bool,
    covered: &mut BTreeSet<String>,
    grounded_all: &mut Vec<CrystalClaim>,
    verdicts_all: &mut Vec<ClaimStrengthVerdict>,
    stats: &mut SweepStats,
    repairs: &mut Vec<RepairLog>,
    report: &mut std::fs::File,
) -> Result<(), CliError> {
    let full_chunks = pending.len() - pending.len() % MAX_STRENGTH_CLAIMS_PER_CALL;
    let n_take = if drain_all { pending.len() } else { full_chunks };
    if n_take == 0 {
        return Ok(());
    }
    stats.strength_waves += 1;
    let wave = stats.strength_waves;
    let wave_claims: Vec<CrystalClaim> = pending.drain(..n_take).collect();

    // Chunked verdicts — 1:1 coverage per wave or the run fails loud, the
    // same invariant batch mode enforces globally.
    let mut wave_verdicts: Vec<ClaimStrengthVerdict> = Vec::new();
    let mut calls = 0usize;
    for chunk in wave_claims.chunks(MAX_STRENGTH_CLAIMS_PER_CALL) {
        let req = strength_request(
            &CrystalCandidate {
                items: chunk.to_vec(),
            },
            catalog,
        );
        calls += 1;
        stats.strength_calls += 1;
        let stage = format!("strength-w{wave:03}-{calls:03}");
        let (chunk_verdicts, log): (Vec<ClaimStrengthVerdict>, _) =
            call_and_parse(client, &req, &stage, parse_strength_verdicts)?;
        if let Some(l) = log {
            repairs.push(l);
        }
        wave_verdicts.extend(chunk_verdicts);
    }
    let ids: Vec<String> = wave_claims.iter().map(|c| c.id.clone()).collect();
    let coverage = strength_coverage(&ids, &wave_verdicts);
    if !coverage.complete() {
        return Err(CliError::Gate(format!(
            "crystal-synth: strength verdicts incomplete for wave {wave} — \
             missing={:?} duplicate={:?} unknown={:?}",
            coverage.missing, coverage.duplicate, coverage.unknown
        )));
    }

    // Deterministic routing preview (the SAME gate functions the durable
    // write uses): cases cited by durable-routed claims join the covered set
    // so later seeds drop out of the sweep.
    let wave_candidate = CrystalCandidate { items: wave_claims };
    let lint = lint_candidate(&wave_candidate, index);
    let scores = score_candidate(&lint);
    let mut durable_routed = 0usize;
    let mut newly_covered: Vec<String> = Vec::new();
    for item in &wave_candidate.items {
        let class = scores
            .iter()
            .find(|s| s.claim_id == item.id)
            .map(|s| s.class);
        let verdict = wave_verdicts.iter().find(|v| v.claim_id == item.id);
        let routed = match class {
            Some(c) => final_routing(c, verdict),
            None => FinalClass::Reject,
        };
        if routed == FinalClass::Durable {
            durable_routed += 1;
            for c in &item.citations {
                if covered.insert(c.case_id.clone()) {
                    newly_covered.push(c.case_id.clone());
                }
            }
        }
    }
    newly_covered.sort();

    grounded_all.extend(wave_candidate.items);
    verdicts_all.extend(wave_verdicts);
    println!(
        "  l3[wave {wave:03}]: {} claim(s) → {calls} strength call(s), {durable_routed} durable-routed",
        ids.len()
    );
    write_jsonl(
        report,
        &WaveReport {
            outcome: "wave",
            wave,
            claims: ids.len(),
            strength_calls: calls,
            durable_routed,
            newly_covered,
        },
    )
}

/// Run the sweep. `index` is the grounding index over the canonical packs dir
/// (already written by the caller); `store` is read for ACTIVE durable claims
/// (coverage + superset guard) and never written here.
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_sweep(
    catalog: &UnitsCatalog,
    reader_dir: &Path,
    index: &GroundingIndex,
    themes: Option<&ThemesFile>,
    store: &Path,
    work_dir: &Path,
    embed_cache_dir: &Path,
    client: &mut dyn ModelClient,
    cfg: &SweepConfig,
) -> Result<SweepOutcome, CliError> {
    // ---- Coverage state from the ledger (read-only). ----
    let events = read_ledger(&store.join("ledger.jsonl"))?;
    let active: Vec<_> = ovp_domain::crystal::fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect();
    let mut covered: BTreeSet<String> = BTreeSet::new();
    let mut active_source_sets: Vec<(String, BTreeSet<String>)> = Vec::new();
    for r in &active {
        covered.extend(r.source_cases.iter().cloned());
        active_source_sets.push((
            r.claim_key.clone(),
            r.source_cases.iter().cloned().collect(),
        ));
    }
    let seeds = uncovered_seeds(catalog, &covered);
    let uncovered_before = seeds.len();

    // ---- Neighborhood inputs: vectors + digests (both deterministic). ----
    let vectors = resolve_catalog_vectors(reader_dir, catalog, embed_cache_dir)?;
    let digests: BTreeMap<String, CaseDigest> = catalog
        .cases
        .iter()
        .map(|(case_id, case)| {
            let body = std::fs::read_to_string(reader_dir.join(case_id).join("reader.md"))
                .unwrap_or_default();
            (
                case_id.clone(),
                digest_from_reader_md(case_id, &case.title, &body),
            )
        })
        .collect();

    // ---- Sweep. ----
    let report_path = work_dir.join("l3-sweep.jsonl");
    let mut report = std::fs::File::create(&report_path)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", report_path.display())))?;

    let mut stats = SweepStats {
        uncovered_before,
        ..SweepStats::default()
    };
    let mut attempted_clusters: BTreeSet<Vec<String>> = BTreeSet::new();
    let mut seen_signatures: BTreeSet<String> = BTreeSet::new();
    let mut raw_claims: Vec<CrystalClaim> = Vec::new();
    let mut grounded_all: Vec<CrystalClaim> = Vec::new();
    let mut verdicts: Vec<ClaimStrengthVerdict> = Vec::new();
    let mut deduped: Vec<ovp_domain::crystal::synth::DedupedClaim> = Vec::new();
    let mut dropped_ungrounded: Vec<String> = Vec::new();
    let mut repairs: Vec<RepairLog> = Vec::new();
    // Grounded claims awaiting a strength wave. Always smaller than one
    // chunk right after a flush check — the bounded coverage lag.
    let mut pending: Vec<CrystalClaim> = Vec::new();

    for seed in &seeds {
        // Seeds covered by claims routed durable earlier in THIS run drop
        // out. This check runs BEFORE the budget check: a final permitted
        // selection that covers every remaining seed is a complete sweep, not
        // an exhausted budget (each covered seed counts as covered_mid_run).
        if covered.contains(seed) {
            stats.covered_mid_run += 1;
            write_seed_line(&mut report, &SeedReport::new(seed, "covered"))?;
            continue;
        }
        if stats.select_calls >= cfg.max_seeds {
            // The select budget is spent, but the pending pool may still
            // cover this seed: flush it first (those strength calls are owed
            // no matter what) and only then declare exhaustion — a fully
            // covered sweep stays a COMPLETE sweep under waves.
            if !pending.is_empty() {
                flush_strength_wave(
                    client,
                    catalog,
                    index,
                    &mut pending,
                    true,
                    &mut covered,
                    &mut grounded_all,
                    &mut verdicts,
                    &mut stats,
                    &mut repairs,
                    &mut report,
                )?;
                if covered.contains(seed) {
                    stats.covered_mid_run += 1;
                    write_seed_line(&mut report, &SeedReport::new(seed, "covered"))?;
                    continue;
                }
            }
            stats.budget_exhausted = true;
            break;
        }
        let neighbor_ids = neighborhood_of(seed, &vectors, cfg.neighborhood);
        let neighbor_digests: Vec<CaseDigest> = neighbor_ids
            .iter()
            .filter_map(|id| digests.get(id).cloned())
            .collect();
        let offered: BTreeSet<String> = std::iter::once(seed.clone())
            .chain(neighbor_ids.iter().cloned())
            .collect();

        // ---- cluster_select/v1 (budgeted). ----
        let req = cluster_select_request(
            &digests[seed],
            &neighbor_digests,
            cfg.max_cases_per_cluster,
        );
        stats.select_calls += 1;
        // Parse-level failures (no JSON envelope, unrecoverable reply) are a
        // property of ONE model exchange, not the sweep: fail this seed,
        // forget the bad cassette so a rerun re-asks, keep sweeping — the
        // same contract as mechanical validation below. (A live A/B run was
        // aborted whole-arm by a single envelope-less reply; never again.)
        // EXCEPTION: a replay cache MISS means the recording itself is
        // incomplete — swallowing it per-seed would let a replay produce
        // different metrics than the live run, so that one stays fatal.
        // (String match: ModelClient errors are unstructured today.)
        let (selection, log) =
            match call_and_parse(client, &req, "cluster-select", parse_cluster_selection) {
                Ok(v) => v,
                Err(e) if e.to_string().contains("cache miss") => return Err(e),
                Err(e) => {
                    stats.failed += 1;
                    client.invalidate(&req);
                    let msg = e.to_string();
                    let mut line = SeedReport::new(seed, "failed");
                    line.error = Some(&msg);
                    write_seed_line(&mut report, &line)?;
                    continue;
                }
            };
        if let Some(l) = log {
            repairs.push(l);
        }
        let (case_ids, rationale) = match selection {
            ClusterSelection::Refused { reason } => {
                stats.refused += 1;
                let mut line = SeedReport::new(seed, "refused");
                line.reason = Some(&reason);
                write_seed_line(&mut report, &line)?;
                continue;
            }
            ClusterSelection::Selected {
                case_ids,
                rationale,
            } => (case_ids, rationale),
        };

        // ---- Mechanical validation: fail THIS seed loudly, keep sweeping. ----
        let selected = match validate_selection(&offered, &case_ids, cfg.max_cases_per_cluster) {
            Ok(ids) => ids,
            Err(e) => {
                stats.failed += 1;
                // Under a recording cache, forget the bad exchange so a rerun
                // re-asks the model instead of replaying the violation forever.
                client.invalidate(&req);
                let mut line = SeedReport::new(seed, "failed");
                line.error = Some(&e);
                write_seed_line(&mut report, &line)?;
                continue;
            }
        };

        // ---- Superset guard: never spend a synth call re-deriving a subset
        // of an existing active claim's source set, or repeating a cluster
        // already attempted this run. ----
        if let Some((key, _)) = active_source_sets
            .iter()
            .find(|(_, sources)| selected.iter().all(|id| sources.contains(id)))
        {
            stats.guarded += 1;
            let mut line = SeedReport::new(seed, "guarded");
            line.selected = Some(&selected);
            line.guarded_by = Some(key);
            write_seed_line(&mut report, &line)?;
            continue;
        }
        if attempted_clusters.contains(&selected) {
            stats.guarded += 1;
            let mut line = SeedReport::new(seed, "guarded");
            line.selected = Some(&selected);
            line.reason = Some("cluster already attempted this run");
            write_seed_line(&mut report, &line)?;
            continue;
        }
        attempted_clusters.insert(selected.clone());

        // ---- EXISTING crystal_synth/v1 + gates, unchanged. ----
        let cluster = Cluster {
            key: format!("l3-{seed}"),
            theme: cluster_theme(seed, themes),
            cases: selected.clone(),
        };
        let synth_req = crystal_synth_request(
            catalog,
            &cluster,
            cfg.max_cases_per_cluster,
            cfg.max_units_per_case,
        );
        stats.synth_calls += 1;
        let (claims, log): (Vec<CrystalClaim>, _) =
            call_and_parse(client, &synth_req, "synth", |t| {
                parse_synth_claims(t, &cluster.key)
            })?;
        if let Some(l) = log {
            repairs.push(l);
        }
        let n_claims = claims.len();
        raw_claims.extend(claims.clone());

        // Grounded filter (same linter) + incremental exact-citation dedup.
        let (cluster_grounded, cluster_dropped) = ovp_domain::crystal::synth::filter_grounded(
            &CrystalCandidate { items: claims },
            index,
        );
        dropped_ungrounded.extend(cluster_dropped);
        let mut kept: Vec<CrystalClaim> = Vec::new();
        for claim in cluster_grounded.items {
            let sig = citation_signature(&claim.citations);
            if seen_signatures.contains(&sig) {
                let prior_id = grounded_all
                    .iter()
                    .chain(kept.iter())
                    .find(|g| citation_signature(&g.citations) == sig)
                    .map(|g| g.id.clone())
                    .unwrap_or_default();
                deduped.push(ovp_domain::crystal::synth::DedupedClaim {
                    kept_claim_id: prior_id,
                    dropped_claim_id: claim.id.clone(),
                    reason: "exact_citation_set".to_string(),
                });
                continue;
            }
            seen_signatures.insert(sig);
            kept.push(claim);
        }
        let n_grounded = kept.len();
        stats.selected += 1;
        let mut line = SeedReport::new(seed, "selected");
        line.selected = Some(&selected);
        line.rationale = Some(&rationale);
        line.claims = Some(n_claims);
        line.grounded = Some(n_grounded);
        if kept.is_empty() {
            write_seed_line(&mut report, &line)?;
            continue;
        }

        // Micro-batched strength: this cluster's grounded claims join the
        // pending pool; full chunks flush as soon as the pool reaches batch
        // mode's chunk size, and coverage updates with the wave (never per
        // cluster — the durable routing is unknowable before its wave).
        pending.extend(kept);
        line.pending = Some(pending.len());
        write_seed_line(&mut report, &line)?;
        flush_strength_wave(
            client,
            catalog,
            index,
            &mut pending,
            false,
            &mut covered,
            &mut grounded_all,
            &mut verdicts,
            &mut stats,
            &mut repairs,
            &mut report,
        )?;
    }

    // End of sweep: flush the remainder (< one chunk by construction).
    flush_strength_wave(
        client,
        catalog,
        index,
        &mut pending,
        true,
        &mut covered,
        &mut grounded_all,
        &mut verdicts,
        &mut stats,
        &mut repairs,
        &mut report,
    )?;

    stats.uncovered_after = uncovered_seeds(catalog, &covered).len();
    Ok(SweepOutcome {
        raw_claims,
        grounded: CrystalCandidate {
            items: grounded_all,
        },
        verdicts,
        deduped,
        dropped_ungrounded,
        stats,
        repairs,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::synth::CatalogCase;

    fn catalog_of(ids: &[&str]) -> UnitsCatalog {
        let mut cat = UnitsCatalog::default();
        for id in ids {
            cat.cases.insert(
                (*id).to_string(),
                CatalogCase {
                    title: format!("Title {id}"),
                    units: vec![],
                },
            );
        }
        cat
    }

    #[test]
    fn sweep_order_is_deterministic_case_id_ascending() {
        let cat = catalog_of(&["c-b", "a-z", "b-m", "d-q"]);
        let covered: BTreeSet<String> = ["b-m".to_string()].into_iter().collect();
        let seeds = uncovered_seeds(&cat, &covered);
        assert_eq!(seeds, vec!["a-z", "c-b", "d-q"], "sorted, covered dropped");
        // Same inputs → same order, every time.
        assert_eq!(seeds, uncovered_seeds(&cat, &covered));
    }

    #[test]
    fn neighborhood_ranks_by_cosine_with_case_id_ties() {
        let mut vectors: BTreeMap<String, Vec<f32>> = BTreeMap::new();
        vectors.insert("seed".into(), vec![1.0, 0.0]);
        vectors.insert("near".into(), vec![0.99, 0.14]);
        vectors.insert("far".into(), vec![0.0, 1.0]);
        // Exact tie with `near-2` → ascending case_id breaks it.
        vectors.insert("near-2".into(), vec![0.99, 0.14]);
        let n = neighborhood_of("seed", &vectors, 2);
        assert_eq!(n, vec!["near", "near-2"]);
        let all = neighborhood_of("seed", &vectors, 10);
        assert_eq!(all, vec!["near", "near-2", "far"], "k caps, order stable");
    }

    // ---- e2e fixtures: the full `--cluster-mode llm` run over replay
    // cassettes (no themes file → theme "cross-source"). ----

    use crate::commands::client::ClientKind;
    use crate::commands::crystal_synth::{ClusterMode, CrystalSynthArgs, run_stats};
    use crate::commands::crystal_themes::clean_reader_body;
    use ovp_domain::crystal::synth::collect_catalog;
    use ovp_domain::source_doc::SourceDoc;
    use ovp_domain::units::{Unit, validate};
    use ovp_llm::request_key;
    use std::path::Path;

    /// Reader pack with real reader.md content (title + one card + body).
    fn write_pack(dir: &Path, case_id: &str, title: &str, body: &str, quotes: &[&str]) {
        let case_dir = dir.join(case_id);
        std::fs::create_dir_all(&case_dir).unwrap();
        let raw: Vec<_> = quotes
            .iter()
            .enumerate()
            .map(|(i, q)| {
                serde_json::json!({
                    "kind": "assertion", "text": format!("t{i}"), "evidence_ref": "p001",
                    "evidence_quote": q, "attribution": "author", "modality": "asserted", "arguments": []
                })
            })
            .collect();
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![], body),
        );
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        std::fs::write(
            case_dir.join("units.accepted.json"),
            serde_json::to_string_pretty(&units).unwrap(),
        )
        .unwrap();
        std::fs::write(
            case_dir.join("reader.md"),
            format!("# {title}\n\n## 1. Key point of {title}  _fact_\n\n{body}\n"),
        )
        .unwrap();
    }

    /// Seed the embedding cache with a normalized EMBED_DIM vector for a
    /// pack, keyed by the EXACT text derivation the sweep uses.
    fn seed_vector(embed_dir: &Path, reader_dir: &Path, case_id: &str, title: &str, v: [f32; 3]) {
        let md = std::fs::read_to_string(reader_dir.join(case_id).join("reader.md")).unwrap();
        let text = document_text(title, &clean_reader_body(&md), EMBED_HEAD_CHARS);
        let sha = embed_cache::text_sha256(&text);
        let norm = (v.iter().map(|x| x * x).sum::<f32>()).sqrt();
        let mut full = vec![0.0f32; EMBED_DIM];
        for (slot, x) in full.iter_mut().zip(v) {
            *slot = x / norm;
        }
        embed_cache::store(embed_dir, &sha, EMBED_MODEL_ID, &full).unwrap();
    }

    fn write_cassette(cache_dir: &Path, req: &ovp_llm::ModelRequest, reply_text: &str) {
        let ns = req.cache_namespace.as_deref().unwrap();
        let dir = cache_dir.join(ns);
        std::fs::create_dir_all(&dir).unwrap();
        let reply = ovp_llm::ModelReply {
            model: "canned".into(),
            text: reply_text.to_string(),
            stop_reason: ovp_llm::StopReason::EndTurn,
            usage: ovp_llm::Usage {
                input_tokens: 1,
                output_tokens: 1,
            },
        };
        std::fs::write(
            dir.join(format!("{}.json", request_key(req))),
            serde_json::to_string_pretty(&reply).unwrap(),
        )
        .unwrap();
    }

    fn llm_args(reader: &Path, work: &Path, embed: &Path) -> CrystalSynthArgs {
        CrystalSynthArgs {
            reader_dir: Some(reader.to_path_buf()),
            vault_root: None,
            work_dir: work.to_path_buf(),
            store: Some(work.join("store")),
            themes_file: None,
            client_kind: ClientKind::Replay,
            cache_dir: Some(work.join("cassettes")),
            max_cases_per_cluster: 16,
            max_units_per_case: 22,
            run_id: None,
            title: Some("L3 Test".into()),
            scope: None,
            not_claiming: None,
            refresh: false,
            date: None,
            strict: false,
            strict_cluster_cap: false,
            cluster_mode: ClusterMode::Llm,
            max_seeds: 25,
            neighborhood: 12,
            embed_cache_dir: Some(embed.to_path_buf()),
        }
    }

    /// The four-pack corpus every e2e test uses: a/b/c near one another, d
    /// off-axis. Returns (reader_dir, embed_dir) under `root`.
    const CASES: [(&str, &str, &str); 4] = [
        (
            "2026-06-01_a",
            "Alpha memory systems",
            "Alpha says agent memory is a scarce budget.",
        ),
        (
            "2026-06-02_b",
            "Beta context budgets",
            "Beta says the context window is a scarce budget.",
        ),
        (
            "2026-06-03_c",
            "Gamma retrieval bounds",
            "Gamma says retrieval must stay bounded.",
        ),
        (
            "2026-06-04_d",
            "Delta gardening notes",
            "Delta says tomatoes need regular watering.",
        ),
    ];

    fn seed_corpus(root: &Path) -> (std::path::PathBuf, std::path::PathBuf) {
        let reader = root.join("reader");
        let embed = root.join("embed-cache");
        let vecs: [[f32; 3]; 4] = [
            [1.0, 0.05, 0.0],
            [1.0, 0.0, 0.05],
            [0.9, 0.3, 0.0],
            [0.0, 0.0, 1.0],
        ];
        for ((case_id, title, body), v) in CASES.iter().zip(vecs) {
            write_pack(&reader, case_id, title, body, &[body]);
            seed_vector(&embed, &reader, case_id, title, v);
        }
        (reader, embed)
    }

    /// Build the exact select request the sweep will issue for `seed`.
    fn select_request_for(
        reader: &Path,
        embed: &Path,
        catalog: &UnitsCatalog,
        seed: &str,
    ) -> ovp_llm::ModelRequest {
        let vectors = resolve_catalog_vectors(reader, catalog, embed).unwrap();
        let digests: BTreeMap<String, CaseDigest> = catalog
            .cases
            .iter()
            .map(|(id, case)| {
                let md =
                    std::fs::read_to_string(reader.join(id).join("reader.md")).unwrap_or_default();
                (id.clone(), digest_from_reader_md(id, &case.title, &md))
            })
            .collect();
        let neighbor_ids = neighborhood_of(seed, &vectors, 12);
        let neighbor_digests: Vec<CaseDigest> =
            neighbor_ids.iter().map(|id| digests[id].clone()).collect();
        cluster_select_request(&digests[seed], &neighbor_digests, 16)
    }

    #[test]
    fn garbage_select_reply_fails_only_that_seed() {
        // Regression: the first live A/B run was aborted whole-arm by one
        // envelope-less (prose) reply. Parse garbage must fail ONE seed and
        // keep sweeping; only replay cache MISSES stay fatal.
        let tmp = tempfile::tempdir().unwrap();
        let (reader, embed) = seed_corpus(tmp.path());
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();

        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(&cache, &sel_a, "I could not find a coherent cluster, sorry!");
        for c in ["2026-06-02_b", "2026-06-03_c", "2026-06-04_d"] {
            let r = select_request_for(&reader, &embed, &catalog, c);
            write_cassette(&cache, &r, r#"{"refuse":true,"reason":"scattered"}"#);
        }

        let stats = run_stats(llm_args(&reader, &work, &embed)).expect("sweep must survive");
        assert_eq!(stats.failed_seeds, 1, "only seed a fails");
        assert_eq!(stats.refused, 3, "b, c, d all reach the model and refuse");
        assert_eq!(stats.select_calls, 4);
        assert_eq!(stats.durable_appended, 0);

        let jsonl = std::fs::read_to_string(work.join("l3-sweep.jsonl")).unwrap();
        let first: serde_json::Value =
            serde_json::from_str(jsonl.lines().next().unwrap()).unwrap();
        assert_eq!(first["outcome"], "failed");
        assert!(first["error"].as_str().unwrap().contains("JSON"));
    }

    #[test]
    fn llm_e2e_selects_refuses_fails_and_is_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        let (reader, embed) = seed_corpus(tmp.path());
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();

        // Seed a: selection [a, b, c] → synth → one claim citing a+b →
        // pools up for the end-of-sweep strength wave → durable → covers a
        // and b (AFTER the wave, not per cluster).
        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(
            &cache,
            &sel_a,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"scarce-budget framing across sources"}"#,
        );
        let cluster = Cluster {
            key: "l3-2026-06-01_a".into(),
            theme: "cross-source".into(),
            cases: vec![
                "2026-06-01_a".into(),
                "2026-06-02_b".into(),
                "2026-06-03_c".into(),
            ],
        };
        let synth_req = crystal_synth_request(&catalog, &cluster, 16, 22);
        let ua = catalog.cases["2026-06-01_a"].units[0].unit_id.clone();
        let ub = catalog.cases["2026-06-02_b"].units[0].unit_id.clone();
        let synth_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Alpha and Beta both treat capacity as a scarce budget.","theme":"cross-source","citations":[
                {{"case_id":"2026-06-01_a","unit_id":"{ua}","quote":"memory is a scarce budget"}},
                {{"case_id":"2026-06-02_b","unit_id":"{ub}","quote":"context window is a scarce budget"}}
            ]}}]}}"#
        );
        write_cassette(&cache, &synth_req, &synth_reply);
        let grounded = CrystalCandidate {
            items: parse_synth_claims(&synth_reply, &cluster.key).unwrap(),
        };
        let strength_req = strength_request(&grounded, &catalog);
        write_cassette(
            &cache,
            &strength_req,
            &format!(
                r#"[{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"both quotes state a scarce budget"}}]"#,
                grounded.items[0].id
            ),
        );
        // Seed b: seed a's claim is still verdict-pending (the wave hasn't
        // flushed), so b is NOT covered yet — the honest cost of batching.
        // It re-selects the same trio and the attempted-cluster guard blocks
        // the duplicate synth spend.
        let sel_b = select_request_for(&reader, &embed, &catalog, "2026-06-02_b");
        write_cassette(
            &cache,
            &sel_b,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"same scarce-budget trio"}"#,
        );
        // Seed c: refusal.
        let sel_c = select_request_for(&reader, &embed, &catalog, "2026-06-03_c");
        write_cassette(
            &cache,
            &sel_c,
            r#"{"refuse":true,"reason":"neighborhood is topically scattered"}"#,
        );
        // Seed d: a selection VIOLATION (id outside the offered set).
        let sel_d = select_request_for(&reader, &embed, &catalog, "2026-06-04_d");
        write_cassette(
            &cache,
            &sel_d,
            r#"{"selected_case_ids":["2026-06-04_d","bogus-case","another-bogus"],"rationale":"?"}"#,
        );

        let stats = run_stats(llm_args(&reader, &work, &embed)).expect("first llm run");
        assert_eq!(stats.mode, "llm");
        assert_eq!(
            stats.select_calls, 4,
            "a, b, c, d — b is no longer covered before the wave flushes"
        );
        assert_eq!(stats.synth_calls, 1);
        assert_eq!(
            stats.strength_calls, 1,
            "one end-of-sweep wave (pool < one chunk)"
        );
        assert_eq!(stats.refused, 1);
        assert_eq!(stats.guarded, 1, "b re-selected the attempted trio");
        assert_eq!(stats.failed_seeds, 1);
        assert_eq!(stats.durable_appended, 1);
        assert_eq!(stats.durable_distinct_sources, vec![2]);
        assert_eq!(stats.uncovered_before, Some(4));
        assert_eq!(
            stats.uncovered_after,
            Some(2),
            "a+b covered by the new durable claim; c, d remain"
        );

        // Per-seed outcomes: one line each, in sweep order; the strength wave
        // appends its own line (no `seed` field) at the end-of-sweep flush.
        let jsonl = std::fs::read_to_string(work.join("l3-sweep.jsonl")).unwrap();
        let lines: Vec<serde_json::Value> = jsonl
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        let outcomes: Vec<(&str, &str)> = lines
            .iter()
            .filter(|v| v.get("seed").is_some())
            .map(|v| {
                (
                    v["seed"].as_str().unwrap(),
                    v["outcome"].as_str().unwrap(),
                )
            })
            .collect();
        assert_eq!(
            outcomes,
            vec![
                ("2026-06-01_a", "selected"),
                ("2026-06-02_b", "guarded"),
                ("2026-06-03_c", "refused"),
                ("2026-06-04_d", "failed"),
            ]
        );
        let failed = lines.iter().find(|v| v["outcome"] == "failed").unwrap();
        assert!(failed["error"].as_str().unwrap().contains("bogus-case"));
        let selected = lines.iter().find(|v| v["outcome"] == "selected").unwrap();
        assert_eq!(selected["pending"], 1, "claim pooled, wave still ahead");
        let wave = lines.last().unwrap();
        assert_eq!(wave["outcome"], "wave", "end-of-sweep flush is last: {wave}");
        assert_eq!(wave["claims"], 1);
        assert_eq!(wave["strength_calls"], 1);
        assert_eq!(wave["durable_routed"], 1);
        assert_eq!(
            wave["newly_covered"],
            serde_json::json!(["2026-06-01_a", "2026-06-02_b"])
        );
        assert!(work.join("l3-sweep-stats.json").exists());
        assert!(work.join("candidate.json").exists());

        let ledger = std::fs::read_to_string(work.join("store/ledger.jsonl")).unwrap();
        assert_eq!(ledger.lines().filter(|l| !l.trim().is_empty()).count(), 1);

        // Second run: a+b now covered BY THE LEDGER → only c and d sweep;
        // both replay to refusal/violation → 0 new ledger lines (idempotent).
        let stats2 = run_stats(llm_args(&reader, &work, &embed)).expect("second llm run");
        assert_eq!(stats2.uncovered_before, Some(2));
        assert_eq!(stats2.select_calls, 2);
        assert_eq!(stats2.synth_calls, 0, "no synth spend on a rerun");
        assert_eq!(stats2.strength_calls, 0, "empty pool → no wave, no calls");
        assert_eq!(stats2.durable_appended, 0, "re-run adds nothing");
        let ledger2 = std::fs::read_to_string(work.join("store/ledger.jsonl")).unwrap();
        assert_eq!(ledger2.lines().filter(|l| !l.trim().is_empty()).count(), 1);
    }

    /// Two selected clusters whose grounded claims total UNDER one chunk
    /// share ONE strength call at the end-of-sweep flush (previously: one
    /// call per cluster). The wave still covers every cited case afterwards.
    #[test]
    fn end_of_sweep_wave_pools_two_clusters_into_one_strength_call() {
        let tmp = tempfile::tempdir().unwrap();
        let (reader, embed) = seed_corpus(tmp.path());
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();

        // Seed a: cluster [a, b, c] → one claim citing a+b.
        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(
            &cache,
            &sel_a,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"scarce-budget framing"}"#,
        );
        let cluster_a = Cluster {
            key: "l3-2026-06-01_a".into(),
            theme: "cross-source".into(),
            cases: vec![
                "2026-06-01_a".into(),
                "2026-06-02_b".into(),
                "2026-06-03_c".into(),
            ],
        };
        let ua = catalog.cases["2026-06-01_a"].units[0].unit_id.clone();
        let ub = catalog.cases["2026-06-02_b"].units[0].unit_id.clone();
        let synth_a_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Alpha and Beta both treat capacity as a scarce budget.","theme":"cross-source","citations":[
                {{"case_id":"2026-06-01_a","unit_id":"{ua}","quote":"memory is a scarce budget"}},
                {{"case_id":"2026-06-02_b","unit_id":"{ub}","quote":"context window is a scarce budget"}}
            ]}}]}}"#
        );
        write_cassette(
            &cache,
            &crystal_synth_request(&catalog, &cluster_a, 16, 22),
            &synth_a_reply,
        );

        // Seed b (NOT covered — seed a's claim is still verdict-pending):
        // a DIFFERENT cluster [b, c, d] → one claim citing c+d.
        let sel_b = select_request_for(&reader, &embed, &catalog, "2026-06-02_b");
        write_cassette(
            &cache,
            &sel_b,
            r#"{"selected_case_ids":["2026-06-02_b","2026-06-03_c","2026-06-04_d"],"rationale":"boundedness framing"}"#,
        );
        let cluster_b = Cluster {
            key: "l3-2026-06-02_b".into(),
            theme: "cross-source".into(),
            cases: vec![
                "2026-06-02_b".into(),
                "2026-06-03_c".into(),
                "2026-06-04_d".into(),
            ],
        };
        let uc = catalog.cases["2026-06-03_c"].units[0].unit_id.clone();
        let ud = catalog.cases["2026-06-04_d"].units[0].unit_id.clone();
        let synth_b_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Gamma and Delta both describe bounded routines.","theme":"cross-source","citations":[
                {{"case_id":"2026-06-03_c","unit_id":"{uc}","quote":"retrieval must stay bounded"}},
                {{"case_id":"2026-06-04_d","unit_id":"{ud}","quote":"tomatoes need regular watering"}}
            ]}}]}}"#
        );
        write_cassette(
            &cache,
            &crystal_synth_request(&catalog, &cluster_b, 16, 22),
            &synth_b_reply,
        );

        // Seeds c, d: still uncovered mid-sweep (both claims verdict-pending)
        // — under the old per-cluster flow they would have dropped out
        // covered. Both refuse.
        for seed in ["2026-06-03_c", "2026-06-04_d"] {
            let r = select_request_for(&reader, &embed, &catalog, seed);
            write_cassette(&cache, &r, r#"{"refuse":true,"reason":"scattered"}"#);
        }

        // ONE strength cassette for BOTH clusters' claims, in pool order.
        let mut combined = parse_synth_claims(&synth_a_reply, &cluster_a.key).unwrap();
        combined.extend(parse_synth_claims(&synth_b_reply, &cluster_b.key).unwrap());
        let pooled = CrystalCandidate { items: combined };
        let verdicts_reply = format!(
            "[{}]",
            pooled
                .items
                .iter()
                .map(|c| format!(
                    r#"{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"both quotes support it"}}"#,
                    c.id
                ))
                .collect::<Vec<_>>()
                .join(",")
        );
        write_cassette(&cache, &strength_request(&pooled, &catalog), &verdicts_reply);

        let stats = run_stats(llm_args(&reader, &work, &embed)).expect("pooled run");
        assert_eq!(stats.select_calls, 4);
        assert_eq!(stats.synth_calls, 2);
        assert_eq!(
            stats.strength_calls, 1,
            "two clusters' claims share one end-of-sweep strength call"
        );
        assert_eq!(stats.refused, 2, "c and d refused BEFORE the wave covered them");
        assert_eq!(stats.durable_appended, 2);
        assert_eq!(
            stats.uncovered_after,
            Some(0),
            "the wave's durable claims cover all four packs"
        );

        let jsonl = std::fs::read_to_string(work.join("l3-sweep.jsonl")).unwrap();
        let lines: Vec<serde_json::Value> = jsonl
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        let wave = lines.last().unwrap();
        assert_eq!(wave["outcome"], "wave", "{wave}");
        assert_eq!(wave["claims"], 2);
        assert_eq!(wave["strength_calls"], 1);
        assert_eq!(
            wave["newly_covered"],
            serde_json::json!([
                "2026-06-01_a",
                "2026-06-02_b",
                "2026-06-03_c",
                "2026-06-04_d"
            ])
        );
    }

    /// A pool that exceeds one chunk flushes MID-SWEEP: the wave's coverage
    /// update lands before later seeds, so a later seed drops out `covered`
    /// (the whole point of updating coverage in waves), while the sub-chunk
    /// remainder waits for the end-of-sweep flush.
    #[test]
    fn mid_sweep_wave_flushes_and_covers_later_seeds() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        let embed = tmp.path().join("embed-cache");
        // Packs a and b carry MAX_STRENGTH_CLAIMS_PER_CALL + 1 units each, so
        // ONE cluster can ground a chunk-overflowing claim set.
        let n = MAX_STRENGTH_CLAIMS_PER_CALL + 1;
        let a_quotes: Vec<String> = (0..n)
            .map(|i| format!("Alpha fact number {i:02} states a scarce budget."))
            .collect();
        let b_quotes: Vec<String> = (0..n)
            .map(|i| format!("Beta fact number {i:02} states a scarce budget."))
            .collect();
        write_pack(
            &reader,
            "2026-06-01_a",
            "Alpha memory systems",
            &a_quotes.join(" "),
            &a_quotes.iter().map(String::as_str).collect::<Vec<_>>(),
        );
        write_pack(
            &reader,
            "2026-06-02_b",
            "Beta context budgets",
            &b_quotes.join(" "),
            &b_quotes.iter().map(String::as_str).collect::<Vec<_>>(),
        );
        write_pack(
            &reader,
            "2026-06-03_c",
            "Gamma retrieval bounds",
            "Gamma says retrieval must stay bounded.",
            &["Gamma says retrieval must stay bounded."],
        );
        write_pack(
            &reader,
            "2026-06-04_d",
            "Delta gardening notes",
            "Delta says tomatoes need regular watering.",
            &["Delta says tomatoes need regular watering."],
        );
        let vecs: [[f32; 3]; 4] = [
            [1.0, 0.05, 0.0],
            [1.0, 0.0, 0.05],
            [0.9, 0.3, 0.0],
            [0.0, 0.0, 1.0],
        ];
        let titles = [
            ("2026-06-01_a", "Alpha memory systems"),
            ("2026-06-02_b", "Beta context budgets"),
            ("2026-06-03_c", "Gamma retrieval bounds"),
            ("2026-06-04_d", "Delta gardening notes"),
        ];
        for ((case_id, title), v) in titles.iter().zip(vecs) {
            seed_vector(&embed, &reader, case_id, title, v);
        }
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();
        assert_eq!(catalog.cases["2026-06-01_a"].units.len(), n, "fixture sanity");

        // Seed a: cluster [a, b, c] → n cross-source claims (a+b each).
        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(
            &cache,
            &sel_a,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"scarce-budget framing"}"#,
        );
        let cluster = Cluster {
            key: "l3-2026-06-01_a".into(),
            theme: "cross-source".into(),
            cases: vec![
                "2026-06-01_a".into(),
                "2026-06-02_b".into(),
                "2026-06-03_c".into(),
            ],
        };
        let claims_json: Vec<String> = (0..n)
            .map(|i| {
                let ua = &catalog.cases["2026-06-01_a"].units[i];
                let ub = &catalog.cases["2026-06-02_b"].units[i];
                format!(
                    r#"{{"id":"{}","claim":"Cross-source scarce-budget fact {i:02}.","theme":"cross-source","citations":[
                        {{"case_id":"2026-06-01_a","unit_id":"{}","quote":"{}"}},
                        {{"case_id":"2026-06-02_b","unit_id":"{}","quote":"{}"}}
                    ]}}"#,
                    i + 1,
                    ua.unit_id,
                    ua.quote,
                    ub.unit_id,
                    ub.quote
                )
            })
            .collect();
        let synth_reply = format!(r#"{{"claims":[{}]}}"#, claims_json.join(","));
        write_cassette(
            &cache,
            &crystal_synth_request(&catalog, &cluster, 16, 22),
            &synth_reply,
        );

        // Strength cassettes for BOTH waves: the mid-sweep full chunk and
        // the end-of-sweep remainder.
        let all = parse_synth_claims(&synth_reply, &cluster.key).unwrap();
        assert_eq!(all.len(), n);
        for chunk in all.chunks(MAX_STRENGTH_CLAIMS_PER_CALL) {
            let cand = CrystalCandidate {
                items: chunk.to_vec(),
            };
            let reply = format!(
                "[{}]",
                cand.items
                    .iter()
                    .map(|c| format!(
                        r#"{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"both quotes state a scarce budget"}}"#,
                        c.id
                    ))
                    .collect::<Vec<_>>()
                    .join(",")
            );
            write_cassette(&cache, &strength_request(&cand, &catalog), &reply);
        }

        // Seed b must drop out COVERED (the mid-sweep wave already routed
        // a+b-citing claims durable) — no select cassette for b on purpose:
        // reaching the model for b would fail the test with a replay miss.
        // Seeds c and d stay uncovered → refusals.
        for seed in ["2026-06-03_c", "2026-06-04_d"] {
            let r = select_request_for(&reader, &embed, &catalog, seed);
            write_cassette(&cache, &r, r#"{"refuse":true,"reason":"scattered"}"#);
        }

        let stats = run_stats(llm_args(&reader, &work, &embed)).expect("wave run");
        assert_eq!(stats.select_calls, 3, "a, c, d — b covered by the mid-sweep wave");
        assert_eq!(stats.synth_calls, 1);
        assert_eq!(
            stats.strength_calls, 2,
            "n = chunk+1 pending → one full chunk mid-sweep + remainder at end"
        );
        assert_eq!(stats.durable_appended, n);
        assert_eq!(stats.uncovered_after, Some(2), "c and d remain");

        let sweep: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(work.join("l3-sweep-stats.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(sweep["covered_mid_run"], 1, "{sweep}");
        assert_eq!(sweep["strength_waves"], 2, "{sweep}");

        // Line order proves the coverage wave landed BEFORE seed b:
        // a selected → wave 1 (full chunk) → b covered → c, d refused →
        // wave 2 (remainder).
        let jsonl = std::fs::read_to_string(work.join("l3-sweep.jsonl")).unwrap();
        let lines: Vec<serde_json::Value> = jsonl
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines[0]["seed"], "2026-06-01_a");
        assert_eq!(lines[0]["outcome"], "selected");
        assert_eq!(lines[0]["pending"], n, "whole pool pending at selection time");
        assert_eq!(lines[1]["outcome"], "wave", "{}", lines[1]);
        assert_eq!(lines[1]["claims"], MAX_STRENGTH_CLAIMS_PER_CALL);
        assert_eq!(
            lines[1]["newly_covered"],
            serde_json::json!(["2026-06-01_a", "2026-06-02_b"])
        );
        assert_eq!(lines[2]["seed"], "2026-06-02_b");
        assert_eq!(lines[2]["outcome"], "covered", "covered BY the wave: {}", lines[2]);
        assert_eq!(lines[3]["outcome"], "refused");
        assert_eq!(lines[4]["outcome"], "refused");
        let wave2 = lines.last().unwrap();
        assert_eq!(wave2["outcome"], "wave", "{wave2}");
        assert_eq!(wave2["claims"], 1, "sub-chunk remainder at end of sweep");
        assert_eq!(
            wave2["newly_covered"],
            serde_json::json!([]),
            "a and b were already covered by wave 1"
        );
    }

    #[test]
    fn superset_guard_blocks_before_the_synth_spend() {
        let tmp = tempfile::tempdir().unwrap();
        let (reader, embed) = seed_corpus(tmp.path());
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();

        // Pre-existing ACTIVE durable claim citing b, c, d → only a uncovered.
        let store = work.join("store");
        std::fs::create_dir_all(&store).unwrap();
        let record = ovp_domain::crystal::DurableRecord {
            claim_key: "ck-preexisting-000".into(),
            claim_id: "old-1".into(),
            claim: "b, c and d already synthesized".into(),
            theme: "t".into(),
            source_cases: vec![
                "2026-06-02_b".into(),
                "2026-06-03_c".into(),
                "2026-06-04_d".into(),
            ],
            citations: vec![],
            provenance_score: 0.9,
            provenance_class: ovp_domain::crystal::ProvenanceClass::Durable,
            strength: ovp_domain::crystal::StrengthClass::Supported,
            strength_rationale: "r".into(),
            final_class: FinalClass::Durable,
            run_id: "run-old".into(),
            status: CrystalStatus::Active,
        };
        let event = ovp_domain::crystal::StoreEvent {
            op: ovp_domain::crystal::StoreOp::Write,
            record,
            supersedes: None,
            reason: None,
        };
        std::fs::write(
            store.join("ledger.jsonl"),
            format!("{}\n", serde_json::to_string(&event).unwrap()),
        )
        .unwrap();

        // Seed a's selection EXCLUDES the seed and re-picks the covered trio —
        // exactly the case the guard exists for. No synth cassette exists, so
        // reaching synthesis would fail the test loudly.
        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(
            &cache,
            &sel_a,
            r#"{"selected_case_ids":["2026-06-02_b","2026-06-03_c","2026-06-04_d"],"rationale":"the trio clusters"}"#,
        );

        let stats = run_stats(llm_args(&reader, &work, &embed)).expect("guarded run succeeds");
        assert_eq!(stats.guarded, 1);
        assert_eq!(stats.synth_calls, 0, "guard fires BEFORE the synth spend");
        assert_eq!(stats.durable_appended, 0);
        let jsonl = std::fs::read_to_string(work.join("l3-sweep.jsonl")).unwrap();
        let line: serde_json::Value = serde_json::from_str(jsonl.lines().next().unwrap()).unwrap();
        assert_eq!(line["outcome"], "guarded");
        assert_eq!(line["guarded_by"], "ck-preexisting-000");
        let ledger = std::fs::read_to_string(store.join("ledger.jsonl")).unwrap();
        assert_eq!(
            ledger.lines().filter(|l| !l.trim().is_empty()).count(),
            1,
            "ledger untouched"
        );
    }

    #[test]
    fn max_seeds_budget_caps_select_calls() {
        let tmp = tempfile::tempdir().unwrap();
        let (reader, embed) = seed_corpus(tmp.path());
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();
        // Only the FIRST seed gets a cassette; with --max-seeds 1 the sweep
        // must stop before ever asking about the others.
        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(&cache, &sel_a, r#"{"refuse":true,"reason":"nothing here"}"#);
        let mut args = llm_args(&reader, &work, &embed);
        args.max_seeds = 1;
        let stats = run_stats(args).expect("budgeted run");
        assert_eq!(stats.select_calls, 1);
        assert_eq!(stats.refused, 1);
        let sweep_stats: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(work.join("l3-sweep-stats.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(sweep_stats["budget_exhausted"], true);
        assert_eq!(sweep_stats["uncovered_after"], 4, "nothing got covered");
    }

    #[cfg(not(feature = "embed"))]
    #[test]
    fn llm_mode_without_embeddings_fails_with_clear_remedy() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        write_pack(
            &reader,
            "2026-06-01_a",
            "Alpha",
            "Alpha says memory is scarce.",
            &["Alpha says memory is scarce."],
        );
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        // Empty embedding cache + no embed feature → loud, actionable error
        // (llm mode is meaningless without neighborhoods — no graceful skip).
        let err = run_stats(llm_args(&reader, &work, &tmp.path().join("empty-cache")))
            .expect_err("must fail loud");
        let msg = format!("{err:?}");
        assert!(msg.contains("crystal-themes"), "remedy named: {msg}");
        assert!(msg.contains("embed"), "{msg}");
    }

    /// Bounds that make every cluster_select/v1 call unsatisfiable (the
    /// prompt demands 3..=cap ids from seed + neighborhood) must be refused
    /// at validation time — before any IO or model spend.
    #[test]
    fn llm_mode_rejects_impossible_selection_bounds() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        let work = tmp.path().join("work");

        let mut args = llm_args(&reader, &work, Path::new("unused"));
        args.max_cases_per_cluster = 2;
        let err = run_stats(args).expect_err("cap < 3 must fail validation");
        assert!(matches!(err, CliError::Gate(_)), "{err:?}");
        assert!(format!("{err:?}").contains("at least 3"), "{err:?}");

        let mut args = llm_args(&reader, &work, Path::new("unused"));
        args.neighborhood = 1;
        let err = run_stats(args).expect_err("neighborhood < 2 must fail validation");
        assert!(matches!(err, CliError::Gate(_)), "{err:?}");
        assert!(format!("{err:?}").contains("at least 2"), "{err:?}");

        assert!(!work.exists(), "refused before any work-dir IO");
    }

    /// When the final permitted selection covers every remaining seed, the
    /// sweep is COMPLETE — under waves that means the budget check must flush
    /// the pending pool (and re-check coverage) BEFORE declaring exhaustion,
    /// so the run never misreports budget_exhausted=true (or undercounts
    /// covered_mid_run) with uncovered_after == 0.
    #[test]
    fn last_selection_covering_all_seeds_is_not_budget_exhausted() {
        let tmp = tempfile::tempdir().unwrap();
        let (reader, embed) = seed_corpus(tmp.path());
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let cache = work.join("cassettes");
        let catalog = collect_catalog(&reader).unwrap();

        // Pre-existing ACTIVE claim covers d → uncovered seeds are a, b, c.
        let store = work.join("store");
        std::fs::create_dir_all(&store).unwrap();
        let record = ovp_domain::crystal::DurableRecord {
            claim_key: "ck-covers-d-000".into(),
            claim_id: "old-1".into(),
            claim: "d already synthesized".into(),
            theme: "t".into(),
            source_cases: vec!["2026-06-04_d".into()],
            citations: vec![],
            provenance_score: 0.9,
            provenance_class: ovp_domain::crystal::ProvenanceClass::Durable,
            strength: ovp_domain::crystal::StrengthClass::Supported,
            strength_rationale: "r".into(),
            final_class: FinalClass::Durable,
            run_id: "run-old".into(),
            status: CrystalStatus::Active,
        };
        let event = ovp_domain::crystal::StoreEvent {
            op: ovp_domain::crystal::StoreOp::Write,
            record,
            supersedes: None,
            reason: None,
        };
        std::fs::write(
            store.join("ledger.jsonl"),
            format!("{}\n", serde_json::to_string(&event).unwrap()),
        )
        .unwrap();

        // Seed a (the ONLY permitted select call): the selection yields one
        // durable claim citing a, b AND c — everything still uncovered.
        let sel_a = select_request_for(&reader, &embed, &catalog, "2026-06-01_a");
        write_cassette(
            &cache,
            &sel_a,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"scarce-budget framing across sources"}"#,
        );
        let cluster = Cluster {
            key: "l3-2026-06-01_a".into(),
            theme: "cross-source".into(),
            cases: vec![
                "2026-06-01_a".into(),
                "2026-06-02_b".into(),
                "2026-06-03_c".into(),
            ],
        };
        let synth_req = crystal_synth_request(&catalog, &cluster, 16, 22);
        let ua = catalog.cases["2026-06-01_a"].units[0].unit_id.clone();
        let ub = catalog.cases["2026-06-02_b"].units[0].unit_id.clone();
        let uc = catalog.cases["2026-06-03_c"].units[0].unit_id.clone();
        let synth_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"All three sources treat capacity as a scarce, bounded budget.","theme":"cross-source","citations":[
                {{"case_id":"2026-06-01_a","unit_id":"{ua}","quote":"memory is a scarce budget"}},
                {{"case_id":"2026-06-02_b","unit_id":"{ub}","quote":"context window is a scarce budget"}},
                {{"case_id":"2026-06-03_c","unit_id":"{uc}","quote":"retrieval must stay bounded"}}
            ]}}]}}"#
        );
        write_cassette(&cache, &synth_req, &synth_reply);
        let grounded = CrystalCandidate {
            items: parse_synth_claims(&synth_reply, &cluster.key).unwrap(),
        };
        write_cassette(
            &cache,
            &strength_request(&grounded, &catalog),
            &format!(
                r#"[{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"all three quotes state a bounded budget"}}]"#,
                grounded.items[0].id
            ),
        );

        let mut args = llm_args(&reader, &work, &embed);
        args.max_seeds = 1; // exactly the one permitted call
        let stats = run_stats(args).expect("budgeted run that covers everything");
        assert_eq!(stats.select_calls, 1);
        assert_eq!(stats.durable_appended, 1);
        assert_eq!(stats.uncovered_after, Some(0), "the last call covered b and c");

        let sweep: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(work.join("l3-sweep-stats.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(
            sweep["budget_exhausted"], false,
            "a fully covered sweep is complete, not budget-exhausted: {sweep}"
        );
        assert_eq!(sweep["covered_mid_run"], 2, "b and c dropped out covered: {sweep}");
        assert_eq!(sweep["uncovered_after"], 0, "{sweep}");
        assert_eq!(sweep["selected"], 1, "{sweep}");
    }

    #[test]
    fn llm_mode_needs_vault_root_or_embed_cache_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        let work = tmp.path().join("work");
        let mut args = llm_args(&reader, &work, Path::new("unused"));
        args.embed_cache_dir = None; // and vault_root is None
        let err = run_stats(args).expect_err("must fail before any IO");
        assert!(format!("{err:?}").contains("--embed-cache-dir"));
    }

    #[test]
    fn cluster_theme_uses_seed_community_keywords_never_labels() {
        use ovp_domain::crystal::themes::{
            LabelsProvenance, THEMES_SCHEMA, ThemeCommunity, ThemeParams,
        };
        let themes = ThemesFile {
            schema: THEMES_SCHEMA.into(),
            model: "m".into(),
            params: ThemeParams {
                k: 10,
                cosine_threshold: 0.5,
                resolution: 1.5,
                seed: 42,
                text_prefix: String::new(),
                head_chars: 1500,
            },
            generated_from: "gf".into(),
            packs: BTreeMap::from([("case-a".to_string(), 0)]),
            communities: vec![ThemeCommunity {
                id: 0,
                label: "Pretty Display Label".into(),
                label_zh: "展示名".into(),
                keywords: vec!["memory".into(), "context".into()],
                size: 1,
            }],
            labels_provenance: LabelsProvenance::Llm,
        };
        assert_eq!(cluster_theme("case-a", Some(&themes)), "memory · context");
        assert_eq!(cluster_theme("unknown", Some(&themes)), FALLBACK_THEME);
        assert_eq!(cluster_theme("case-a", None), FALLBACK_THEME);
    }
}
