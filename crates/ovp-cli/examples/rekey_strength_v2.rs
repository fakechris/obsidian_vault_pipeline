//! ONE-OFF fixture migration: re-key the live-repro strength cassettes for
//! `crystal_strength/v2`.
//!
//! v2 hands the model SHORT positional handles (`c1`, `c2`, …) instead of the
//! long claim ids it kept transcribing wrong, so the request TEXT changed and
//! every recorded key moved. Same shape of migration as
//! `migrate_live_fixture.rs` did when the claim-id prefixes changed.
//!
//! The reply bodies are copied BYTE FOR BYTE. `Aliases::resolve` accepts a real
//! id quoted back verbatim, so a v1 reply is still a valid v2 reply — only its
//! key moved. Rewriting the model's recorded judgments to make a test pass
//! would be fabricating evidence; re-keying them is not.
//!
//! Run: `cargo run -p ovp-cli --example rekey_strength_v2`

use std::path::{Path, PathBuf};

use ovp_domain::crystal::CrystalCandidate;
use ovp_domain::crystal::synth::{
    CRYSTAL_STRENGTH_PROMPT_ID, cluster_batches, collect_catalog,
    crystal_synth_batch_request, filter_grounded, parse_synth_claims, strength_request,
    build_grounding_index,
};
use ovp_domain::crystal::themes::{ThemesFile, clusters_from_themes};
use ovp_llm::{ModelRequest, request_key};

const MAX_STRENGTH_CLAIMS_PER_CALL: usize = 20;

fn cassette_path(cassettes: &Path, req: &ModelRequest) -> PathBuf {
    let ns = req.cache_namespace.as_deref().expect("namespaced request");
    cassettes.join(ns).join(format!("{}.json", request_key(req)))
}

fn main() {
    let fixture =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/crystal-synth-live");
    let cassettes = fixture.join("cassettes");
    let catalog = collect_catalog(&fixture.join("reader")).expect("catalog");

    // Rebuild the candidate by walking the PIPELINE'S OWN path — themes ->
    // clusters -> batches -> synth request -> recorded reply. Guessing the
    // cluster key from a reply is not possible (it is what builds the claim
    // ids), and a wrong key would silently produce a candidate that never
    // existed.
    let themes: ThemesFile =
        serde_json::from_str(&std::fs::read_to_string(fixture.join("themes.json")).expect("themes"))
            .expect("parse themes");
    let clusters = clusters_from_themes(&catalog, &themes);
    let batches = cluster_batches(&clusters, 16);
    let mut items = Vec::new();
    for batch in &batches {
        let req = crystal_synth_batch_request(&catalog, batch, 22);
        let path = cassette_path(&cassettes, &req);
        let raw = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("synth cassette {} missing: {e}", path.display()));
        let reply: serde_json::Value = serde_json::from_str(&raw).expect("parse cassette");
        let text = reply["text"].as_str().unwrap_or_default();
        let claims = parse_synth_claims(text, &batch.claim_prefix()).expect("parse synth claims");
        items.extend(claims);
    }
    // The strength stage runs on the GROUNDED subset, not every claim.
    let index = build_grounding_index(&fixture.join("reader")).expect("grounding index");
    let grounded = filter_grounded(&CrystalCandidate { items }, &index);
    let items = grounded.0.items;
    println!("rebuilt {} grounded claim(s) from {} batch(es)", items.len(), batches.len());

    let old_dir = cassettes.join("crystal_strength/v1");
    let new_dir = cassettes.join(CRYSTAL_STRENGTH_PROMPT_ID);
    std::fs::create_dir_all(&new_dir).expect("mkdir v2");

    // The e2e pipeline chunks the GROUNDED candidate; the recorded v1 keys tell
    // us which chunking actually happened. Try the natural chunking and match
    // each new key against the pool of old replies by position.
    let mut old_files: Vec<PathBuf> = std::fs::read_dir(&old_dir)
        .expect("v1 strength cassettes")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    old_files.sort();
    println!("v1 strength cassettes: {}", old_files.len());

    let chunks: Vec<&[_]> = items.chunks(MAX_STRENGTH_CLAIMS_PER_CALL).collect();
    if chunks.len() != old_files.len() {
        eprintln!(
            "chunk count {} != recorded cassette count {} — the candidate this \
             rebuilt does not match the recorded run; refusing to guess",
            chunks.len(),
            old_files.len()
        );
        std::process::exit(1);
    }

    for (i, chunk) in chunks.iter().enumerate() {
        let req = strength_request(
            &CrystalCandidate {
                items: chunk.to_vec(),
            },
            &catalog,
        );
        let dest = cassette_path(&cassettes, &req);
        std::fs::copy(&old_files[i], &dest).expect("copy reply");
        println!(
            "  {} -> {}",
            old_files[i].file_name().unwrap().to_string_lossy(),
            dest.file_name().unwrap().to_string_lossy()
        );
    }
    println!("re-keyed {} strength cassette(s) into {}", chunks.len(), CRYSTAL_STRENGTH_PROMPT_ID);
    println!("v1 dir left in place; delete it once the e2e test is green.");
}
