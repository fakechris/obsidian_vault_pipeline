//! ONE-OFF fixture migration for the M32 live-repro test after the keyword
//! taxonomy retirement (kept for auditability of how the fixture moved; not
//! part of any product flow).
//!
//! Rebuilds `tests/fixtures/crystal-synth-live/` for semantic-theme grouping:
//! 1. Writes a `themes.json` whose communities are EXACTLY the retired
//!    keyword clusters of the live run (same membership, labels = the old
//!    bucket descriptions), so every synthesis request stays byte-identical
//!    and the 5 recorded synth cassettes replay unchanged.
//! 2. Re-keys the 2 strength cassettes: claim-id prefixes change
//!    (`agents-N` → `t00X-N`), so the request keys move and the replies'
//!    claim_id fields are rewritten by positional mapping. The verdict
//!    payloads (the model's actual judgments) are untouched.

use std::path::{Path, PathBuf};

use ovp_domain::crystal::CrystalCandidate;
use ovp_domain::crystal::synth::{
    Cluster, build_grounding_index, cluster_batches, collect_catalog,
    crystal_synth_batch_request, dedup_exact_citation_sets, filter_grounded, parse_synth_claims,
    strength_request, write_packs,
};
use ovp_domain::crystal::themes::{
    THEMES_SCHEMA, ThemeCommunity, ThemeParams, ThemesFile, clusters_from_themes,
};
use ovp_llm::{ModelReply, ModelRequest, request_key};

const MAX_STRENGTH_CLAIMS_PER_CALL: usize = 20;
const CAP_CASES: usize = 16;
const CAP_UNITS: usize = 22;

/// The RETIRED pilot keyword taxonomy, copied verbatim for the migration.
fn old_bucket_for(title: &str) -> (&'static str, &'static str) {
    let t = title.to_lowercase();
    const BUCKETS: &[(&str, &str, &[&str])] = &[
        ("agents", "Agents & agentic systems", &["agent", "agentic", "autonomy", "tool use", "tool-use"]),
        ("memory", "Memory & context", &["memory", "context", "retrieval", "rag", "recall", "embedding"]),
        ("coding", "Coding & software", &["code", "coding", "software", "programming", "compiler", "refactor"]),
        ("models", "Models & training", &["model", "llm", "training", "fine-tune", "fine tuning", "transformer", "weights"]),
        ("prompting", "Prompting & evaluation", &["prompt", "prompting", "eval", "benchmark", "evaluation"]),
        ("product", "Product & design", &["product", "design", "ux", "user", "workflow", "interface"]),
        ("infra", "Infrastructure & systems", &["infra", "infrastructure", "system", "database", "server", "distributed", "cache"]),
    ];
    for (key, theme, needles) in BUCKETS {
        if needles.iter().any(|n| t.contains(n)) {
            return (key, theme);
        }
    }
    ("misc", "Miscellaneous")
}

fn old_clusters(catalog: &ovp_domain::crystal::synth::UnitsCatalog) -> Vec<Cluster> {
    let order = ["agents", "memory", "coding", "models", "prompting", "product", "infra", "misc"];
    let mut by_key: std::collections::BTreeMap<&str, (&str, Vec<String>)> = Default::default();
    for (case_id, case) in &catalog.cases {
        let (key, theme) = old_bucket_for(&case.title);
        by_key.entry(key).or_insert_with(|| (theme, Vec::new())).1.push(case_id.clone());
    }
    let mut clusters = Vec::new();
    for key in order {
        if let Some((theme, mut cases)) = by_key.remove(key) {
            cases.sort();
            clusters.push(Cluster { key: key.to_string(), theme: theme.to_string(), cases });
        }
    }
    clusters
}

fn cassette_path(cassettes: &Path, req: &ModelRequest) -> PathBuf {
    let ns = req.cache_namespace.as_deref().expect("namespaced request");
    cassettes.join(ns).join(format!("{}.json", request_key(req)))
}

fn read_reply(cassettes: &Path, req: &ModelRequest) -> ModelReply {
    let path = cassette_path(cassettes, req);
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!("reading cassette {}: {e}", path.display())
    }))
    .expect("cassette parses")
}

fn main() {
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/crystal-synth-live");
    let reader = fixture.join("reader");
    let cassettes = fixture.join("cassettes");

    let catalog = collect_catalog(&reader).expect("catalog");
    let old = old_clusters(&catalog);
    println!("old clusters: {:?}", old.iter().map(|c| (&c.key, c.cases.len())).collect::<Vec<_>>());

    // 1. themes.json fixture — communities are exactly the old clusters.
    let themes = ThemesFile {
        schema: THEMES_SCHEMA.to_string(),
        model: "fixture-migration/keyword-era".to_string(),
        params: ThemeParams {
            k: 10,
            cosine_threshold: 0.5,
            resolution: 1.5,
            seed: 42,
            text_prefix: String::new(),
            head_chars: 1500,
        },
        generated_from: "keyword-era-live-repro".to_string(),
        packs: old
            .iter()
            .enumerate()
            .flat_map(|(i, c)| c.cases.iter().map(move |case| (case.clone(), i as i64)))
            .collect(),
        communities: old
            .iter()
            .enumerate()
            .map(|(i, c)| ThemeCommunity {
                id: i as i64,
                label: c.theme.clone(),
                label_zh: c.theme.clone(),
                keywords: vec![c.key.clone()],
                size: c.cases.len(),
            })
            .collect(),
    };
    std::fs::write(
        fixture.join("themes.json"),
        format!("{}\n", serde_json::to_string_pretty(&themes).unwrap()),
    )
    .unwrap();

    // 2. Sanity: the new grouping is identical, so synth requests are too.
    let new = clusters_from_themes(&catalog, &themes);
    assert_eq!(old.len(), new.len());
    let old_batches = cluster_batches(&old, CAP_CASES);
    let new_batches = cluster_batches(&new, CAP_CASES);
    assert_eq!(old_batches.len(), new_batches.len());
    let mut old_claims = Vec::new();
    let mut new_claims = Vec::new();
    for (ob, nb) in old_batches.iter().zip(&new_batches) {
        assert_eq!(ob.cases, nb.cases, "membership preserved");
        assert_eq!(ob.theme, nb.theme, "theme string preserved");
        let old_req = crystal_synth_batch_request(&catalog, ob, CAP_UNITS);
        let new_req = crystal_synth_batch_request(&catalog, nb, CAP_UNITS);
        assert_eq!(
            request_key(&old_req),
            request_key(&new_req),
            "synth cassettes must replay unchanged"
        );
        let reply = read_reply(&cassettes, &old_req);
        old_claims.extend(parse_synth_claims(&reply.text, &ob.claim_prefix()).expect("parse old"));
        new_claims.extend(parse_synth_claims(&reply.text, &nb.claim_prefix()).expect("parse new"));
    }
    println!("claims: {} (both eras)", old_claims.len());
    assert_eq!(old_claims.len(), new_claims.len());

    // 3. Grounded filter + dedup — the exact run() pipeline — for both eras.
    let tmp = tempfile::tempdir().unwrap();
    let packs = tmp.path().join("packs");
    write_packs(&packs, &reader, &catalog).unwrap();
    let index = build_grounding_index(&packs).unwrap();
    let ground = |claims: Vec<ovp_domain::crystal::CrystalClaim>| {
        let (g, _) = filter_grounded(&CrystalCandidate { items: claims }, &index);
        let (g, _) = dedup_exact_citation_sets(&g);
        g
    };
    let old_grounded = ground(old_claims);
    let new_grounded = ground(new_claims);
    assert_eq!(old_grounded.items.len(), new_grounded.items.len());
    println!("grounded: {}", old_grounded.items.len());

    // 4. Re-key the strength cassettes with positionally-rewritten claim ids.
    for (old_chunk, new_chunk) in old_grounded
        .items
        .chunks(MAX_STRENGTH_CLAIMS_PER_CALL)
        .zip(new_grounded.items.chunks(MAX_STRENGTH_CLAIMS_PER_CALL))
    {
        let old_req = strength_request(
            &CrystalCandidate { items: old_chunk.to_vec() },
            &catalog,
        );
        let new_req = strength_request(
            &CrystalCandidate { items: new_chunk.to_vec() },
            &catalog,
        );
        let mut reply = read_reply(&cassettes, &old_req);
        // Longest-first so `agents-10` rewrites before `agents-1`.
        let mut pairs: Vec<(&str, &str)> = old_chunk
            .iter()
            .zip(new_chunk)
            .map(|(o, n)| (o.id.as_str(), n.id.as_str()))
            .collect();
        pairs.sort_by_key(|(o, _)| std::cmp::Reverse(o.len()));
        for (o, n) in &pairs {
            reply.text = reply.text.replace(&format!("\"{o}\""), &format!("\"{n}\""));
        }
        let old_path = cassette_path(&cassettes, &old_req);
        let new_path = cassette_path(&cassettes, &new_req);
        std::fs::write(&new_path, serde_json::to_string_pretty(&reply).unwrap()).unwrap();
        if old_path != new_path {
            std::fs::remove_file(&old_path).unwrap();
        }
        println!(
            "strength cassette: {} -> {}",
            old_path.file_name().unwrap().to_string_lossy(),
            new_path.file_name().unwrap().to_string_lossy()
        );
    }
    println!("fixture migrated.");
}
