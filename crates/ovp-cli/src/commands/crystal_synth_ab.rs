//! `crystal-synth --experiment` — the L3 A/B harness.
//!
//! Given a deterministic, seeded sample of UNCOVERED reader packs (packs not
//! cited by any ACTIVE durable claim in the real store), materialize the
//! slice into an isolated packs dir and run BOTH arms against the SAME packs:
//!
//!   arm A — `--cluster-mode batch` (current deterministic batching)
//!   arm B — `--cluster-mode llm`   (L3 coverage-first selection sweep)
//!
//! Each arm gets its own work dir, its own FRESH store (yields comparable),
//! and its own cassette dir — record once with `--client live`, replay
//! offline forever after. The real vault store is only READ (for the
//! uncovered set); nothing in the experiment ever writes to it.
//!
//! Output: a comparison table on stdout + `<work-dir>/comparison.json` with
//! durable yield per synth call, gate pass rate, mean distinct sources per
//! durable claim, refusal rate, and total LLM calls per arm.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use ovp_domain::crystal::CrystalStatus;
use ovp_domain::crystal::synth::collect_catalog;
use serde::Serialize;

use crate::CliError;
use crate::commands::client::ClientKind;
use crate::commands::crystal_synth::{
    ClusterMode, CrystalSynthArgs, RunStats, resolve_paths, run_stats,
};
use crate::commands::crystal_synth_llm::uncovered_seeds;
use crate::commands::crystal_write::read_ledger;

pub struct ExperimentArgs {
    pub reader_dir: Option<PathBuf>,
    pub vault_root: Option<PathBuf>,
    pub store: Option<PathBuf>,
    pub themes_file: Option<PathBuf>,
    pub embed_cache_dir: Option<PathBuf>,
    pub work_dir: PathBuf,
    pub client_kind: ClientKind,
    /// Sample size (uncovered packs) both arms run against.
    pub slice: usize,
    /// Sampling seed (deterministic slice — replays pick the same packs).
    pub seed: u64,
    /// Arm B `--max-seeds`; 0 = the slice size (every pack gets a chance).
    pub max_seeds: usize,
    pub neighborhood: usize,
    pub max_cases_per_cluster: usize,
    pub max_units_per_case: usize,
}

/// SplitMix64 — the same tiny deterministic generator ovp-embed's Louvain
/// pins; no rand dependency.
fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Seeded Fisher–Yates over the (sorted) input, truncated to `n`, re-sorted.
/// Deterministic: same items + seed + n → same slice.
pub(crate) fn seeded_sample(items: &[String], n: usize, seed: u64) -> Vec<String> {
    let mut v = items.to_vec();
    v.sort();
    let mut s = seed;
    for i in (1..v.len()).rev() {
        let j = (splitmix64(&mut s) % (i as u64 + 1)) as usize;
        v.swap(i, j);
    }
    v.truncate(n.min(v.len()));
    v.sort();
    v
}

/// One arm's row in the comparison table.
#[derive(Debug, Serialize)]
struct ArmReport {
    arm: &'static str,
    mode: &'static str,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    total_llm_calls: usize,
    select_calls: usize,
    synth_calls: usize,
    strength_calls: usize,
    synthesized: usize,
    grounded: usize,
    durable: usize,
    review: usize,
    /// durable claims per synth call (the headline yield metric).
    durable_yield_per_synth_call: f64,
    /// durable / synthesized.
    gate_pass_rate: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    mean_distinct_sources_per_durable: Option<f64>,
    /// refusals / select calls (arm B only).
    #[serde(skip_serializing_if = "Option::is_none")]
    refusal_rate: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    uncovered_before: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    uncovered_after: Option<usize>,
}

fn ratio(num: usize, den: usize) -> f64 {
    if den == 0 { 0.0 } else { num as f64 / den as f64 }
}

fn arm_report(arm: &'static str, mode: &'static str, result: &Result<RunStats, CliError>) -> ArmReport {
    match result {
        Ok(s) => ArmReport {
            arm,
            mode,
            ok: true,
            error: None,
            total_llm_calls: s.select_calls + s.synth_calls + s.strength_calls,
            select_calls: s.select_calls,
            synth_calls: s.synth_calls,
            strength_calls: s.strength_calls,
            synthesized: s.synthesized,
            grounded: s.grounded,
            durable: s.durable_distinct_sources.len(),
            review: s.review,
            durable_yield_per_synth_call: ratio(s.durable_distinct_sources.len(), s.synth_calls),
            gate_pass_rate: ratio(s.durable_distinct_sources.len(), s.synthesized),
            mean_distinct_sources_per_durable: if s.durable_distinct_sources.is_empty() {
                None
            } else {
                Some(
                    s.durable_distinct_sources.iter().sum::<usize>() as f64
                        / s.durable_distinct_sources.len() as f64,
                )
            },
            refusal_rate: (s.select_calls > 0).then(|| ratio(s.refused, s.select_calls)),
            uncovered_before: s.uncovered_before,
            uncovered_after: s.uncovered_after,
        },
        Err(e) => ArmReport {
            arm,
            mode,
            ok: false,
            error: Some(format!("{e:?}")),
            total_llm_calls: 0,
            select_calls: 0,
            synth_calls: 0,
            strength_calls: 0,
            synthesized: 0,
            grounded: 0,
            durable: 0,
            review: 0,
            durable_yield_per_synth_call: 0.0,
            gate_pass_rate: 0.0,
            mean_distinct_sources_per_durable: None,
            refusal_rate: None,
            uncovered_before: None,
            uncovered_after: None,
        },
    }
}

/// Copy one reader pack's product files into the slice dir.
fn copy_pack(src_root: &Path, dst_root: &Path, case_id: &str) -> Result<(), CliError> {
    let src = src_root.join(case_id);
    let dst = dst_root.join(case_id);
    std::fs::create_dir_all(&dst)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", dst.display())))?;
    for name in ["units.accepted.json", "reader.md", "run-status.json"] {
        let from = src.join(name);
        if from.exists() {
            std::fs::copy(&from, dst.join(name))
                .map_err(|e| CliError::Io(format!("copying {}: {e}", from.display())))?;
        }
    }
    Ok(())
}

pub fn run_experiment(args: ExperimentArgs) -> Result<(), CliError> {
    if args.slice == 0 {
        return Err(CliError::Gate(
            "crystal-synth: --experiment-slice must be greater than 0".into(),
        ));
    }
    // Arm B enforces these in run_stats, but by then arm A has already spent
    // its (possibly live) budget — refuse impossible llm bounds before any arm.
    if args.max_cases_per_cluster < 3 {
        return Err(CliError::Gate(
            "crystal-synth: --experiment needs --max-cases-per-cluster >= 3 \
             (arm B's cluster_select/v1 selects 3..=cap case ids)"
                .into(),
        ));
    }
    if args.neighborhood < 2 {
        return Err(CliError::Gate(
            "crystal-synth: --experiment needs --neighborhood >= 2 \
             (arm B offers seed + neighborhood and a valid cluster needs at least 3)"
                .into(),
        ));
    }
    let embed_cache_dir = args
        .embed_cache_dir
        .clone()
        .or_else(|| {
            args.vault_root
                .as_ref()
                .map(|v| v.join(".ovp/cache/embeddings"))
        })
        .ok_or_else(|| {
            CliError::Io(
                "crystal-synth: --experiment needs an embedding cache for arm B — pass \
                 --vault-root or --embed-cache-dir"
                    .into(),
            )
        })?;
    // Resolve the REAL reader dir + store (read-only) through the same
    // precedence crystal-synth uses.
    let base = CrystalSynthArgs {
        reader_dir: args.reader_dir.clone(),
        vault_root: args.vault_root.clone(),
        work_dir: args.work_dir.clone(),
        store: args.store.clone(),
        themes_file: args.themes_file.clone(),
        client_kind: args.client_kind,
        cache_dir: None,
        max_cases_per_cluster: args.max_cases_per_cluster,
        max_units_per_case: args.max_units_per_case,
        run_id: None,
        title: None,
        scope: None,
        not_claiming: None,
        refresh: false,
        date: None,
        strict: false,
        strict_cluster_cap: false,
        cluster_mode: ClusterMode::Batch,
        max_seeds: args.max_seeds,
        neighborhood: args.neighborhood,
        embed_cache_dir: Some(embed_cache_dir.clone()),
    };
    let paths = resolve_paths(&base);
    std::fs::create_dir_all(&args.work_dir).map_err(|e| {
        CliError::Io(format!(
            "creating work dir {}: {e}",
            args.work_dir.display()
        ))
    })?;

    // The themes projection is optional but shared by both arms when present.
    let themes_file: Option<PathBuf> = args
        .themes_file
        .clone()
        .or_else(|| {
            args.vault_root
                .as_ref()
                .map(|v| v.join(".ovp/crystal/themes.json"))
        })
        .filter(|p| p.exists());

    // ---- Deterministic uncovered slice (real store is only READ). ----
    let catalog =
        collect_catalog(&paths.reader_dir).map_err(|e| CliError::Io(format!("crystal-synth: {e}")))?;
    let events = read_ledger(&paths.store.join("ledger.jsonl"))?;
    let mut covered: BTreeSet<String> = BTreeSet::new();
    for r in ovp_domain::crystal::fold_ledger(&events) {
        if r.status == CrystalStatus::Active {
            covered.extend(r.source_cases);
        }
    }
    let uncovered = uncovered_seeds(&catalog, &covered);
    if uncovered.is_empty() {
        println!("crystal-synth: experiment: no uncovered packs — nothing to compare.");
        return Ok(());
    }
    let slice = seeded_sample(&uncovered, args.slice, args.seed);
    println!(
        "crystal-synth: experiment: {} uncovered pack(s) → slice of {} (seed {})",
        uncovered.len(),
        slice.len(),
        args.seed
    );
    let slice_dir = args.work_dir.join("slice-packs");
    if slice_dir.exists() {
        std::fs::remove_dir_all(&slice_dir)
            .map_err(|e| CliError::Io(format!("clearing {}: {e}", slice_dir.display())))?;
    }
    for case_id in &slice {
        copy_pack(&paths.reader_dir, &slice_dir, case_id)?;
    }
    write_json(&args.work_dir.join("slice.json"), &slice)?;

    // ---- Arms: same packs, fresh stores, separate cassette dirs. ----
    // The documented live-then-replay workflow reuses the work dir, so each
    // arm's store must be recreated per run: a leftover ledger would mark the
    // slice packs covered (arm B skips selection entirely) and contaminate
    // every yield metric. The cassette dirs are deliberately PRESERVED —
    // record once with `--client live`, replay offline forever after.
    for name in ["arm-a", "arm-b"] {
        let store = args.work_dir.join(name).join("store");
        if store.exists() {
            std::fs::remove_dir_all(&store)
                .map_err(|e| CliError::Io(format!("clearing {}: {e}", store.display())))?;
        }
    }
    let arm_args = |mode: ClusterMode, name: &str| CrystalSynthArgs {
        reader_dir: Some(slice_dir.clone()),
        vault_root: None,
        work_dir: args.work_dir.join(name),
        store: Some(args.work_dir.join(name).join("store")),
        themes_file: themes_file.clone(),
        client_kind: args.client_kind,
        cache_dir: Some(args.work_dir.join(format!("cassettes-{name}"))),
        max_cases_per_cluster: args.max_cases_per_cluster,
        max_units_per_case: args.max_units_per_case,
        run_id: None,
        title: Some(format!("L3 experiment {name}")),
        scope: None,
        not_claiming: None,
        refresh: false,
        date: None,
        strict: false,
        strict_cluster_cap: false,
        cluster_mode: mode,
        max_seeds: if args.max_seeds == 0 {
            slice.len()
        } else {
            args.max_seeds
        },
        neighborhood: args.neighborhood,
        embed_cache_dir: Some(embed_cache_dir.clone()),
    };

    println!("\n=== arm A (batch) ===");
    let a = run_stats(arm_args(ClusterMode::Batch, "arm-a"));
    println!("\n=== arm B (llm) ===");
    let b = run_stats(arm_args(ClusterMode::Llm, "arm-b"));

    let reports = vec![
        arm_report("A", "batch", &a),
        arm_report("B", "llm", &b),
    ];
    write_json(&args.work_dir.join("comparison.json"), &reports)?;

    println!("\n=== L3 A/B comparison (slice={}, seed={}) ===", slice.len(), args.seed);
    println!(
        "{:<4} {:<6} {:>9} {:>7} {:>6} {:>8} {:>8} {:>8} {:>12} {:>10} {:>10} {:>9}",
        "arm", "mode", "llm-calls", "select", "synth", "strength", "synth'd", "durable",
        "yield/synth", "gate-pass", "mean-srcs", "refusal"
    );
    for r in &reports {
        if !r.ok {
            println!(
                "{:<4} {:<6} FAILED: {}",
                r.arm,
                r.mode,
                r.error.as_deref().unwrap_or("?")
            );
            continue;
        }
        println!(
            "{:<4} {:<6} {:>9} {:>7} {:>6} {:>8} {:>8} {:>8} {:>12.2} {:>10.2} {:>10} {:>9}",
            r.arm,
            r.mode,
            r.total_llm_calls,
            r.select_calls,
            r.synth_calls,
            r.strength_calls,
            r.synthesized,
            r.durable,
            r.durable_yield_per_synth_call,
            r.gate_pass_rate,
            r.mean_distinct_sources_per_durable
                .map(|m| format!("{m:.2}"))
                .unwrap_or_else(|| "—".into()),
            r.refusal_rate
                .map(|m| format!("{m:.2}"))
                .unwrap_or_else(|| "—".into()),
        );
    }
    println!(
        "\n  comparison: {}",
        args.work_dir.join("comparison.json").display()
    );
    // A failed arm is a failed experiment: the diagnostics above (table +
    // comparison.json with ok=false and the error) are written first, then the
    // failure propagates so callers/CI never mistake a broken run for data.
    let mut failures: Vec<String> = Vec::new();
    if let Err(e) = &a {
        eprintln!("crystal-synth: experiment: arm A failed: {e:?}");
        failures.push(format!("arm A (batch) failed: {e:?}"));
    }
    if let Err(e) = &b {
        eprintln!("crystal-synth: experiment: arm B failed: {e:?}");
        failures.push(format!("arm B (llm) failed: {e:?}"));
    }
    if !failures.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-synth: experiment: {} (see comparison.json for the partial run)",
            failures.join("; ")
        )));
    }
    Ok(())
}

fn write_json<T: serde::Serialize>(path: &Path, v: &T) -> Result<(), CliError> {
    let s = serde_json::to_string_pretty(v)
        .map_err(|e| CliError::Io(format!("serializing {}: {e}", path.display())))?;
    std::fs::write(path, format!("{s}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", path.display())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    use ovp_domain::crystal::CrystalCandidate;
    use ovp_domain::crystal::select::{CaseDigest, cluster_select_request, digest_from_reader_md};
    use ovp_domain::crystal::synth::{
        Cluster, UnitsCatalog, cluster_batches, crystal_synth_batch_request,
        crystal_synth_request, parse_synth_claims, strength_request,
    };
    use ovp_domain::crystal::themes::clusters_date_ordered;
    use ovp_domain::source_doc::SourceDoc;
    use ovp_domain::units::{Unit, validate};
    use ovp_embed::cache as embed_cache;
    use ovp_embed::{EMBED_DIM, EMBED_HEAD_CHARS, EMBED_MODEL_ID, document_text};
    use ovp_llm::request_key;

    use crate::commands::crystal_synth_llm::resolve_catalog_vectors;
    use crate::commands::crystal_themes::clean_reader_body;

    fn write_pack(dir: &Path, case_id: &str, title: &str, body: &str) {
        let case_dir = dir.join(case_id);
        std::fs::create_dir_all(&case_dir).unwrap();
        let raw = vec![serde_json::json!({
            "kind": "assertion", "text": "t0", "evidence_ref": "p001",
            "evidence_quote": body, "attribution": "author", "modality": "asserted", "arguments": []
        })];
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
            format!("# {title}\n\n## 1. Key point  _fact_\n\n{body}\n"),
        )
        .unwrap();
    }

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

    /// Full replay experiment: three uncovered packs, arm A (one date-ordered
    /// batch) vs arm B (select→synth→wave strength + guard + refusal), each
    /// from its own cassette dir; both arms yield one durable claim.
    #[test]
    fn experiment_runs_both_arms_and_writes_comparison() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        let embed = tmp.path().join("embed-cache");
        let store = tmp.path().join("real-store"); // empty → all uncovered
        let work = tmp.path().join("work");
        let cases = [
            ("2026-06-01_a", "Alpha memory", "Alpha says memory is a scarce budget."),
            ("2026-06-02_b", "Beta context", "Beta says context is a scarce budget."),
            ("2026-06-03_c", "Gamma bounds", "Gamma says retrieval must stay bounded."),
        ];
        let vecs: [[f32; 3]; 3] = [[1.0, 0.05, 0.0], [1.0, 0.0, 0.05], [0.9, 0.3, 0.0]];
        for ((case_id, title, body), v) in cases.iter().zip(vecs) {
            write_pack(&reader, case_id, title, body);
            seed_vector(&embed, &reader, case_id, title, v);
        }
        let catalog: UnitsCatalog =
            ovp_domain::crystal::synth::collect_catalog(&reader).unwrap();
        let ua = catalog.cases["2026-06-01_a"].units[0].unit_id.clone();
        let ub = catalog.cases["2026-06-02_b"].units[0].unit_id.clone();

        // ---- Arm A cassettes (batch: one date-ordered batch of 3). ----
        let cache_a = work.join("cassettes-arm-a");
        let clusters = clusters_date_ordered(&catalog, 16);
        assert_eq!(clusters.len(), 1);
        let batches = cluster_batches(&clusters, 16);
        let synth_a = crystal_synth_batch_request(&catalog, &batches[0], 22);
        let synth_a_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Alpha and Beta treat capacity as a scarce budget.","theme":"batch","citations":[
                {{"case_id":"2026-06-01_a","unit_id":"{ua}","quote":"memory is a scarce budget"}},
                {{"case_id":"2026-06-02_b","unit_id":"{ub}","quote":"context is a scarce budget"}}
            ]}}]}}"#
        );
        write_cassette(&cache_a, &synth_a, &synth_a_reply);
        let grounded_a = CrystalCandidate {
            items: parse_synth_claims(&synth_a_reply, &batches[0].claim_prefix()).unwrap(),
        };
        write_cassette(
            &cache_a,
            &strength_request(&grounded_a, &catalog),
            &format!(
                r#"[{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"ok"}}]"#,
                grounded_a.items[0].id
            ),
        );

        // ---- Arm B cassettes (llm sweep). ----
        let cache_b = work.join("cassettes-arm-b");
        let vectors = resolve_catalog_vectors(&reader, &catalog, &embed).unwrap();
        let digests: BTreeMap<String, CaseDigest> = catalog
            .cases
            .iter()
            .map(|(id, case)| {
                let md = std::fs::read_to_string(reader.join(id).join("reader.md")).unwrap();
                (id.clone(), digest_from_reader_md(id, &case.title, &md))
            })
            .collect();
        let neighborhood = |seed: &str| -> Vec<CaseDigest> {
            let mut scored: Vec<(f64, &String)> = vectors
                .iter()
                .filter(|(id, _)| id.as_str() != seed)
                .map(|(id, v)| (ovp_embed::knn::cosine(&vectors[seed], v), id))
                .collect();
            scored.sort_by(|(sa, ia), (sb, ib)| {
                sb.partial_cmp(sa)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then(ia.cmp(ib))
            });
            scored
                .into_iter()
                .take(12)
                .map(|(_, id)| digests[id].clone())
                .collect()
        };
        let sel_a = cluster_select_request(&digests["2026-06-01_a"], &neighborhood("2026-06-01_a"), 16);
        write_cassette(
            &cache_b,
            &sel_a,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"budget framing"}"#,
        );
        let cluster = Cluster {
            key: "l3-2026-06-01_a".into(),
            theme: "cross-source".into(),
            cases: cases.iter().map(|(id, _, _)| (*id).to_string()).collect(),
        };
        let synth_b = crystal_synth_request(&catalog, &cluster, 16, 22);
        let synth_b_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Capacity is treated as a scarce budget across sources.","theme":"cross-source","citations":[
                {{"case_id":"2026-06-01_a","unit_id":"{ua}","quote":"memory is a scarce budget"}},
                {{"case_id":"2026-06-02_b","unit_id":"{ub}","quote":"context is a scarce budget"}}
            ]}}]}}"#
        );
        write_cassette(&cache_b, &synth_b, &synth_b_reply);
        let grounded_b = CrystalCandidate {
            items: parse_synth_claims(&synth_b_reply, &cluster.key).unwrap(),
        };
        write_cassette(
            &cache_b,
            &strength_request(&grounded_b, &catalog),
            &format!(
                r#"[{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"ok"}}]"#,
                grounded_b.items[0].id
            ),
        );
        // Seed b: NOT covered before the end-of-sweep strength wave flushes
        // (coverage updates in waves now) — it re-selects the same trio and
        // the attempted-cluster guard blocks the duplicate synth spend.
        let sel_b = cluster_select_request(&digests["2026-06-02_b"], &neighborhood("2026-06-02_b"), 16);
        write_cassette(
            &cache_b,
            &sel_b,
            r#"{"selected_case_ids":["2026-06-01_a","2026-06-02_b","2026-06-03_c"],"rationale":"same trio"}"#,
        );
        // Seed c refuses.
        let sel_c = cluster_select_request(&digests["2026-06-03_c"], &neighborhood("2026-06-03_c"), 16);
        write_cassette(&cache_b, &sel_c, r#"{"refuse":true,"reason":"no cluster"}"#);

        let mk_args = || ExperimentArgs {
            reader_dir: Some(reader.clone()),
            vault_root: None,
            store: Some(store.clone()),
            themes_file: None,
            embed_cache_dir: Some(embed.clone()),
            work_dir: work.clone(),
            client_kind: ClientKind::Replay,
            slice: 3,
            seed: 42,
            max_seeds: 0,
            neighborhood: 12,
            max_cases_per_cluster: 16,
            max_units_per_case: 22,
        };
        run_experiment(mk_args()).expect("first experiment run");

        // The REAL store was only read — never created/written.
        assert!(!store.join("ledger.jsonl").exists());
        let comparison: Vec<serde_json::Value> = serde_json::from_str(
            &std::fs::read_to_string(work.join("comparison.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(comparison.len(), 2);
        let a = &comparison[0];
        let b = &comparison[1];
        assert_eq!((a["arm"].as_str(), a["mode"].as_str()), (Some("A"), Some("batch")));
        assert_eq!(a["ok"], true, "{a}");
        assert_eq!(a["synth_calls"], 1);
        assert_eq!(a["durable"], 1);
        assert_eq!(a["select_calls"], 0);
        assert_eq!((b["arm"].as_str(), b["mode"].as_str()), (Some("B"), Some("llm")));
        assert_eq!(b["ok"], true, "{b}");
        assert_eq!(
            b["select_calls"], 3,
            "a, b, c — b guarded, not covered, before the strength wave"
        );
        assert_eq!(b["strength_calls"], 1);
        assert_eq!(b["durable"], 1);
        assert_eq!(b["refusal_rate"], serde_json::json!(1.0 / 3.0));
        assert_eq!(b["mean_distinct_sources_per_durable"], 2.0);
        // Both arms wrote their own fresh stores.
        assert!(work.join("arm-a/store/ledger.jsonl").exists());
        assert!(work.join("arm-b/store/ledger.jsonl").exists());
        assert!(work.join("slice.json").exists());

        // Replay purity: the documented live-then-replay workflow reuses the
        // SAME work dir. The arm stores from the first run must not leak into
        // the second (a leftover ledger marks the slice covered → arm B skips
        // selection and every metric collapses). Same cassettes → the second
        // run's metrics equal the first's, byte for byte.
        run_experiment(mk_args()).expect("second experiment run (same work dir)");
        let comparison2: Vec<serde_json::Value> = serde_json::from_str(
            &std::fs::read_to_string(work.join("comparison.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(
            comparison2, comparison,
            "replaying in the same work dir must reproduce the first run's metrics"
        );
    }

    /// Arm A replays to success but arm B has no cassettes: the experiment
    /// must still write diagnostics (comparison.json with ok=false + error)
    /// and then PROPAGATE the failure — a broken run is never reported as Ok.
    #[test]
    fn failed_arm_propagates_error_after_writing_comparison() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        let embed = tmp.path().join("embed-cache");
        let store = tmp.path().join("real-store"); // empty → all uncovered
        let work = tmp.path().join("work");
        let cases = [
            ("2026-06-01_a", "Alpha memory", "Alpha says memory is a scarce budget."),
            ("2026-06-02_b", "Beta context", "Beta says context is a scarce budget."),
        ];
        let vecs: [[f32; 3]; 2] = [[1.0, 0.05, 0.0], [1.0, 0.0, 0.05]];
        for ((case_id, title, body), v) in cases.iter().zip(vecs) {
            write_pack(&reader, case_id, title, body);
            seed_vector(&embed, &reader, case_id, title, v);
        }
        let catalog: UnitsCatalog =
            ovp_domain::crystal::synth::collect_catalog(&reader).unwrap();
        let ua = catalog.cases["2026-06-01_a"].units[0].unit_id.clone();
        let ub = catalog.cases["2026-06-02_b"].units[0].unit_id.clone();

        // ---- Arm A cassettes ONLY (arm B's select call will replay-miss). ----
        let cache_a = work.join("cassettes-arm-a");
        let clusters = clusters_date_ordered(&catalog, 16);
        let batches = cluster_batches(&clusters, 16);
        let synth_a = crystal_synth_batch_request(&catalog, &batches[0], 22);
        let synth_a_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Alpha and Beta treat capacity as a scarce budget.","theme":"batch","citations":[
                {{"case_id":"2026-06-01_a","unit_id":"{ua}","quote":"memory is a scarce budget"}},
                {{"case_id":"2026-06-02_b","unit_id":"{ub}","quote":"context is a scarce budget"}}
            ]}}]}}"#
        );
        write_cassette(&cache_a, &synth_a, &synth_a_reply);
        let grounded_a = CrystalCandidate {
            items: parse_synth_claims(&synth_a_reply, &batches[0].claim_prefix()).unwrap(),
        };
        write_cassette(
            &cache_a,
            &strength_request(&grounded_a, &catalog),
            &format!(
                r#"[{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"ok"}}]"#,
                grounded_a.items[0].id
            ),
        );

        let err = run_experiment(ExperimentArgs {
            reader_dir: Some(reader.clone()),
            vault_root: None,
            store: Some(store.clone()),
            themes_file: None,
            embed_cache_dir: Some(embed.clone()),
            work_dir: work.clone(),
            client_kind: ClientKind::Replay,
            slice: 2,
            seed: 42,
            max_seeds: 0,
            neighborhood: 12,
            max_cases_per_cluster: 16,
            max_units_per_case: 22,
        })
        .expect_err("a failed arm must fail the experiment");
        assert!(matches!(err, CliError::Gate(_)), "{err:?}");
        let msg = format!("{err:?}");
        assert!(msg.contains("arm B"), "failure names the failed arm: {msg}");

        // Diagnostics were written BEFORE the failure propagated.
        let comparison: Vec<serde_json::Value> = serde_json::from_str(
            &std::fs::read_to_string(work.join("comparison.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(comparison[0]["ok"], true, "{}", comparison[0]);
        assert_eq!(comparison[1]["ok"], false, "{}", comparison[1]);
        assert!(
            !comparison[1]["error"].as_str().unwrap_or_default().is_empty(),
            "arm B's error is recorded in comparison.json"
        );
    }

    /// llm-arm bounds that make every cluster_select/v1 call unsatisfiable
    /// are refused BEFORE any arm runs (arm A must never spend live budget
    /// on an experiment whose arm B is doomed by construction).
    #[test]
    fn experiment_rejects_impossible_llm_bounds_before_any_arm() {
        let tmp = tempfile::tempdir().unwrap();
        let mk = |cap: usize, neigh: usize| ExperimentArgs {
            reader_dir: Some(tmp.path().join("reader")),
            vault_root: None,
            store: Some(tmp.path().join("real-store")),
            themes_file: None,
            embed_cache_dir: Some(tmp.path().join("embed-cache")),
            work_dir: tmp.path().join("work"),
            client_kind: ClientKind::Replay,
            slice: 2,
            seed: 42,
            max_seeds: 0,
            neighborhood: neigh,
            max_cases_per_cluster: cap,
            max_units_per_case: 22,
        };
        let err = run_experiment(mk(2, 12)).expect_err("cap < 3 must be refused");
        assert!(matches!(err, CliError::Gate(_)), "{err:?}");
        assert!(format!("{err:?}").contains("max-cases-per-cluster"));
        let err = run_experiment(mk(16, 1)).expect_err("neighborhood < 2 must be refused");
        assert!(matches!(err, CliError::Gate(_)), "{err:?}");
        assert!(format!("{err:?}").contains("neighborhood"));
        assert!(
            !tmp.path().join("work").exists(),
            "refused before creating the work dir or running any arm"
        );
    }

    #[test]
    fn seeded_sample_is_deterministic_and_sorted() {
        let items: Vec<String> = (0..20).map(|i| format!("case-{i:02}")).collect();
        let a = seeded_sample(&items, 5, 42);
        let b = seeded_sample(&items, 5, 42);
        assert_eq!(a, b, "same seed → same slice");
        assert_eq!(a.len(), 5);
        let mut sorted = a.clone();
        sorted.sort();
        assert_eq!(a, sorted, "slice is emitted sorted");
        let c = seeded_sample(&items, 5, 7);
        assert_ne!(a, c, "different seed → different slice (overwhelmingly)");
        // n larger than the population → the whole (sorted) population.
        let all = seeded_sample(&items, 100, 42);
        assert_eq!(all, items);
    }
}
