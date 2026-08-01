//! Optional dense-embedding lane for hybrid claim retrieval.
//!
//! Behind the `embed` feature (ONNX / fastembed). When disabled, callers get
//! `None` and hybrid retrieve marks the embed lane Unavailable.
//!
//! Vectors are content-addressed under
//! `<vault>/.ovp/cache/embeddings/<sha>.json` (shared with theme clustering).

#![cfg(feature = "embed")]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ovp_embed::cache::{self, text_sha256};
use ovp_embed::{document_text, EMBED_DIM, EMBED_HEAD_CHARS, EMBED_MODEL_ID};

use crate::retrieve::EmbedLane;

/// Build an embed lane for claim texts. Returns `None` when the embedder
/// cannot start (missing model cache) — soft-fail, lexical+token lanes remain.
pub fn build_claim_embed_lane(
    vault_root: &Path,
    query: &str,
    claim_docs: &BTreeMap<String, String>,
) -> Option<EmbedLane> {
    let cache_dir = vault_root.join(".ovp/cache/embeddings");
    let mut embedder = ovp_embed::embedder::Embedder::new(false).ok()?;
    let q_text = document_text("", query, EMBED_HEAD_CHARS);
    let q_vec = embed_one(&mut embedder, &cache_dir, &q_text)?;
    let mut docs = BTreeMap::new();
    for (id, text) in claim_docs {
        let t = document_text("", text, EMBED_HEAD_CHARS);
        if let Some(v) = embed_one(&mut embedder, &cache_dir, &t) {
            docs.insert(id.clone(), v);
        }
    }
    if docs.is_empty() {
        return None;
    }
    Some(EmbedLane {
        query: q_vec,
        docs,
        min_cosine: 0.35,
    })
}

fn embed_one(
    embedder: &mut ovp_embed::embedder::Embedder,
    cache_dir: &Path,
    text: &str,
) -> Option<Vec<f32>> {
    let sha = text_sha256(text);
    if let Some(v) = cache::load(cache_dir, &sha, EMBED_MODEL_ID, EMBED_DIM) {
        return Some(v);
    }
    let vectors = embedder.embed(&[text.to_string()]).ok()?;
    let v = vectors.into_iter().next()?;
    let _ = cache::store(cache_dir, &sha, EMBED_MODEL_ID, &v);
    Some(v)
}

/// Resolve cache dir helper (tests / diagnostics).
pub fn default_embed_cache(vault_root: &Path) -> PathBuf {
    vault_root.join(".ovp/cache/embeddings")
}
