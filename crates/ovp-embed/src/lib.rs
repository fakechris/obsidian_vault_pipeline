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

/// Pinned production embedding model — the EXACT model the theme spike
/// validated as winner, served as fp32 ONNX by fastembed. Validation
/// 2026-07-10 (`.run/theme-spike-20260709/sweep-minilmrs128-rs.json`):
/// per-doc cosine parity vs the spike's sentence-transformers vectors =
/// 1.0000, and the pinned recipe reproduces the winning row byte-for-byte
/// (17 clusters ≥5, 96.6% coverage, 3.2% noise, 12.5% largest, 17/17
/// bilingual, zh max-share 20.2%, 4/4 sampled bilingual pairs co-clustered).
/// The originally mandated candidates FAILED the bilingual gates on the same
/// harness: multilingual-e5-small concentrates 93–100% of zh/mixed docs into
/// one near-pure Chinese cluster (0–1/4 pairs); mpnet and bge-m3 pass
/// structure but split a sampled pair (3/4). Recorded in `themes.json` and in
/// every cache entry; a cache entry embedded by a different model is a miss.
pub const EMBED_MODEL_ID: &str = "Xenova/paraphrase-multilingual-MiniLM-L12-v2";
/// Embedding dimension of [`EMBED_MODEL_ID`].
pub const EMBED_DIM: usize = 384;
/// Token cap at inference — part of the validated recipe. sentence-transformers
/// caps this model at 128; fastembed defaults to 512, and at 512 the same
/// model drifts back toward language-segregated clusters (zh max-share 46%).
pub const EMBED_MAX_TOKENS: usize = 128;
/// Instruction prefix added at inference time. Empty for the paraphrase
/// family (kept as a pinned recipe knob — E5-family models would need
/// `"passage: "` here, and the prefix is deliberately NOT part of the cache
/// key).
pub const EMBED_TEXT_PREFIX: &str = "";
/// Head window fed to the embedder: title + first ~1500 chars of the body
/// (the tokenizer then truncates at [`EMBED_MAX_TOKENS`] — same shape the
/// spike corpus used).
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
