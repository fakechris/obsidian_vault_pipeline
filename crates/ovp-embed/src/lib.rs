//! Semantic theme infrastructure for OVP: multilingual document embeddings
//! (feature-gated behind `embed` — fastembed + ONNX Runtime are heavy), a
//! vault-local embedding cache, and the pure-Rust clustering stack (non-mutual
//! kNN graph + Louvain communities + c-TF-IDF keywords).
//!
//! The recipe implemented here was validated on the real 994-doc corpus
//! (`.run/theme-spike-20260709/REPORT.md`): multilingual sentence embeddings +
//! Louvain on a non-mutual kNN graph (k=10, cosine ≥ 0.5, resolution 1.5,
//! pinned seed) → ~17 bilingual clusters, >95% coverage, <5% noise.
//!
//! Layering: this crate is a leaf — it knows nothing about vault layout,
//! themes.json, prompts, or the crystal ledger. Those live in `ovp-domain`
//! (`crystal::themes`) and the CLI. Everything except `embedder` compiles
//! without the `embed` feature, so the clustering math and the cache reader
//! are always available (a vault with fully warmed caches can re-theme with a
//! binary built without ONNX).

pub mod cache;
pub mod ctfidf;
#[cfg(feature = "embed")]
pub mod embedder;
pub mod knn;
pub mod louvain;

/// Pinned production embedding model. Recorded in `themes.json` and in every
/// cache entry; a cache entry embedded by a different model is a miss.
pub const EMBED_MODEL_ID: &str = "intfloat/multilingual-e5-small";
/// Embedding dimension of [`EMBED_MODEL_ID`].
pub const EMBED_DIM: usize = 384;
/// E5-family models are trained with an instruction prefix; fastembed does NOT
/// add it, so we pin it here as part of the recipe (symmetric across all docs —
/// clustering only needs consistency, but the prefix is what the model saw in
/// training and measurably improves its geometry).
pub const EMBED_TEXT_PREFIX: &str = "passage: ";
/// Head window fed to the embedder: title + first ~1500 chars of the body.
pub const EMBED_HEAD_CHARS: usize = 1500;

/// Build the canonical embed text for a document: `title\n` + the first
/// `head_chars` characters of `body` (char-boundary safe). The instruction
/// prefix is NOT included here — the embedder adds it at inference time so
/// cache keys stay a pure function of the document content.
pub fn document_text(title: &str, body: &str, head_chars: usize) -> String {
    let head: String = body.chars().take(head_chars).collect();
    let mut s = String::with_capacity(title.len() + 1 + head.len());
    s.push_str(title.trim());
    s.push('\n');
    s.push_str(head.trim());
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn document_text_truncates_on_char_boundary() {
        // Multibyte chars must not be split.
        let body = "中文内容".repeat(1000);
        let t = document_text("标题", &body, 5);
        assert_eq!(t, "标题\n中文内容中");
    }

    #[test]
    fn document_text_trims() {
        assert_eq!(document_text(" T ", "  body  ", 100), "T\nbody");
    }
}
