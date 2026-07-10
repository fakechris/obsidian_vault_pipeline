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
//!     → EXISTING `crystal_synth/v1` + grounded filter + dedup + strength —
//!       completely unchanged; gates and the ledger cannot drift
//!     → seeds covered by claims routed durable this run drop out mid-sweep.
//!
//! Per-seed outcomes go to stdout AND `<work-dir>/l3-sweep.jsonl` (written
//! incrementally, so even a failed run leaves evidence). The sweep only
//! PROPOSES groupings — every claim still passes the untouched citation +
//! provenance + strength gates before the idempotent durable write.

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
use crate::commands::crystal_synth::{RepairLog, call_and_parse};
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
    pub strength_calls: usize,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    durable_routed: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    newly_covered: Option<Vec<String>>,
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
            durable_routed: None,
            newly_covered: None,
        }
    }
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
    let mut write_line = |line: &SeedReport| -> Result<(), CliError> {
        let s = serde_json::to_string(line).map_err(|e| CliError::Io(e.to_string()))?;
        println!("  l3[{}]: {}", line.seed, line.outcome);
        writeln!(report, "{s}").map_err(|e| CliError::Io(format!("l3-sweep.jsonl: {e}")))
    };

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

    for seed in &seeds {
        if stats.select_calls >= cfg.max_seeds {
            stats.budget_exhausted = true;
            break;
        }
        // Seeds covered by claims written earlier in THIS run drop out.
        if covered.contains(seed) {
            stats.covered_mid_run += 1;
            write_line(&SeedReport::new(seed, "covered"))?;
            continue;
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
        let (selection, log) =
            call_and_parse(client, &req, "cluster-select", parse_cluster_selection)?;
        if let Some(l) = log {
            repairs.push(l);
        }
        let (case_ids, rationale) = match selection {
            ClusterSelection::Refused { reason } => {
                stats.refused += 1;
                let mut line = SeedReport::new(seed, "refused");
                line.reason = Some(&reason);
                write_line(&line)?;
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
                write_line(&line)?;
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
            write_line(&line)?;
            continue;
        }
        if attempted_clusters.contains(&selected) {
            stats.guarded += 1;
            let mut line = SeedReport::new(seed, "guarded");
            line.selected = Some(&selected);
            line.reason = Some("cluster already attempted this run");
            write_line(&line)?;
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
        if kept.is_empty() {
            let mut line = SeedReport::new(seed, "selected");
            line.selected = Some(&selected);
            line.rationale = Some(&rationale);
            line.claims = Some(n_claims);
            line.grounded = Some(0);
            write_line(&line)?;
            stats.selected += 1;
            continue;
        }

        // Strength for this cluster's grounded claims (1:1 or fail loud —
        // same invariant the batch mode enforces globally).
        let cluster_candidate = CrystalCandidate { items: kept };
        let strength_req = strength_request(&cluster_candidate, catalog);
        stats.strength_calls += 1;
        let stage = format!("strength-{}", cluster.key);
        let (cluster_verdicts, log): (Vec<ClaimStrengthVerdict>, _) =
            call_and_parse(client, &strength_req, &stage, parse_strength_verdicts)?;
        if let Some(l) = log {
            repairs.push(l);
        }
        let ids: Vec<String> = cluster_candidate.items.iter().map(|c| c.id.clone()).collect();
        let coverage = strength_coverage(&ids, &cluster_verdicts);
        if !coverage.complete() {
            return Err(CliError::Gate(format!(
                "crystal-synth: strength verdicts incomplete for cluster {} — \
                 missing={:?} duplicate={:?} unknown={:?}",
                cluster.key, coverage.missing, coverage.duplicate, coverage.unknown
            )));
        }

        // Deterministic routing preview: seeds cited by claims that will be
        // written durable drop out of the remainder of the sweep.
        let lint = lint_candidate(&cluster_candidate, index);
        let scores = score_candidate(&lint);
        let mut durable_routed = 0usize;
        let mut newly_covered: Vec<String> = Vec::new();
        for item in &cluster_candidate.items {
            let class = scores
                .iter()
                .find(|s| s.claim_id == item.id)
                .map(|s| s.class);
            let verdict = cluster_verdicts.iter().find(|v| v.claim_id == item.id);
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
        newly_covered.dedup();

        grounded_all.extend(cluster_candidate.items);
        verdicts.extend(cluster_verdicts);
        stats.selected += 1;
        let mut line = SeedReport::new(seed, "selected");
        line.selected = Some(&selected);
        line.rationale = Some(&rationale);
        line.claims = Some(n_claims);
        line.grounded = Some(n_grounded);
        line.durable_routed = Some(durable_routed);
        line.newly_covered = Some(newly_covered);
        write_line(&line)?;
    }

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
