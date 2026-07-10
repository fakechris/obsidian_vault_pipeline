//! Vault-local embedding cache: one JSON file per document under
//! `.ovp/cache/embeddings/<content-sha256>.json`. Content-addressed, so
//! re-runs are incremental and the cache is a rebuildable projection (safe to
//! delete). Entries record the model id; a different model is a cache MISS,
//! never a silent reuse.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// One cached document embedding.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CachedEmbedding {
    pub model: String,
    pub dim: usize,
    pub vector: Vec<f32>,
}

/// Lowercase hex sha256 of the canonical embed text — the cache key.
pub fn text_sha256(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    let digest = hasher.finalize();
    let mut out = String::with_capacity(64);
    for b in digest {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

fn entry_path(dir: &Path, sha: &str) -> PathBuf {
    dir.join(format!("{sha}.json"))
}

/// Load a cached vector for `sha` iff it was produced by `model` and has the
/// expected dimension. Any parse/shape/model mismatch is a miss (None).
pub fn load(dir: &Path, sha: &str, model: &str, dim: usize) -> Option<Vec<f32>> {
    let raw = std::fs::read_to_string(entry_path(dir, sha)).ok()?;
    let entry: CachedEmbedding = serde_json::from_str(&raw).ok()?;
    (entry.model == model && entry.dim == dim && entry.vector.len() == dim)
        .then_some(entry.vector)
}

/// Persist a vector under its content sha. Creates the directory as needed.
pub fn store(dir: &Path, sha: &str, model: &str, vector: &[f32]) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("creating {}: {e}", dir.display()))?;
    let entry = CachedEmbedding {
        model: model.to_string(),
        dim: vector.len(),
        vector: vector.to_vec(),
    };
    let body = serde_json::to_string(&entry).map_err(|e| format!("serializing {sha}: {e}"))?;
    let path = entry_path(dir, sha);
    std::fs::write(&path, body).map_err(|e| format!("writing {}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_and_model_mismatch_is_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let sha = text_sha256("hello");
        assert_eq!(sha.len(), 64);
        let vec = vec![0.1f32, 0.2, 0.3];
        store(tmp.path(), &sha, "model-a", &vec).unwrap();
        assert_eq!(load(tmp.path(), &sha, "model-a", 3), Some(vec));
        assert_eq!(load(tmp.path(), &sha, "model-b", 3), None, "other model");
        assert_eq!(load(tmp.path(), &sha, "model-a", 4), None, "other dim");
        assert_eq!(load(tmp.path(), &text_sha256("other"), "model-a", 3), None);
    }

    #[test]
    fn text_sha_is_stable() {
        assert_eq!(
            text_sha256("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
