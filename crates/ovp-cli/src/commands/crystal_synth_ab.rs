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
    if let Err(e) = &a {
        eprintln!("crystal-synth: experiment: arm A failed: {e:?}");
    }
    if let Err(e) = &b {
        eprintln!("crystal-synth: experiment: arm B failed: {e:?}");
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
