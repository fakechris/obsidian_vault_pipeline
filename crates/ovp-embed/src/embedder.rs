//! The feature-gated fastembed wrapper (compiled only with `--features embed`).
//!
//! Model: [`crate::EMBED_MODEL_ID`] (paraphrase-multilingual-MiniLM-L12-v2,
//! 384d, Apache-2.0, ~450MB fp32 ONNX — the exact theme-spike winner,
//! reproduced at cosine parity 1.0000). fastembed mean-pools and
//! L2-normalizes; the token cap is pinned to [`crate::EMBED_MAX_TOKENS`]
//! because the validated geometry depends on it.
//!
//! Model files download once (checksummed by hf-hub) into the fastembed
//! cache: `$FASTEMBED_CACHE_DIR` if set, else `~/.cache/ovp/models`
//! (`%LOCALAPPDATA%\ovp\models` on Windows). Offline
//! with a cold cache, construction fails with a clear error — callers degrade
//! gracefully (skip themes, everything Unclassified) instead of blocking.

use std::path::PathBuf;

use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};

use crate::{EMBED_DIM, EMBED_MAX_TOKENS, EMBED_MODEL_ID, EMBED_TEXT_PREFIX};

/// Where model files live. `$FASTEMBED_CACHE_DIR` wins; otherwise the
/// platform cache root (`~/.cache/ovp/models`, `%LOCALAPPDATA%\ovp\models`);
/// otherwise a local `.fastembed_cache`.
pub fn model_cache_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("FASTEMBED_CACHE_DIR")
        && !dir.trim().is_empty()
    {
        return PathBuf::from(dir);
    }
    match cache_root() {
        Some(root) => root.join("ovp").join("models"),
        // Last resort: relative to the cwd. A ~450MB download landing in the
        // vault would be worse, but there is nowhere else to put it.
        None => PathBuf::from(".fastembed_cache"),
    }
}

/// The per-user cache root. Windows has no `HOME` and no `~/.cache`
/// convention — a 450MB model belongs in Local (never-roaming) AppData.
fn cache_root() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        // LOCALAPPDATA is always set for an interactive user; USERPROFILE
        // covers the stripped service-account case.
        for var in ["LOCALAPPDATA", "USERPROFILE"] {
            if let Ok(v) = std::env::var(var)
                && !v.trim().is_empty()
            {
                return Some(PathBuf::from(v));
            }
        }
        None
    }
    #[cfg(not(windows))]
    {
        let home = std::env::var("HOME")
            .ok()
            .filter(|h| !h.trim().is_empty())?;
        Some(PathBuf::from(home).join(".cache"))
    }
}

/// A loaded multilingual-e5-small session.
pub struct Embedder {
    inner: TextEmbedding,
}

impl Embedder {
    /// Load (downloading model files on first use). `show_progress` prints
    /// hf-hub download progress to stderr — enable in interactive commands.
    pub fn new(show_progress: bool) -> Result<Self, String> {
        let cache = model_cache_dir();
        let opts = InitOptions::new(EmbeddingModel::ParaphraseMLMiniLML12V2)
            .with_cache_dir(cache.clone())
            .with_max_length(EMBED_MAX_TOKENS)
            .with_show_download_progress(show_progress);
        let inner = TextEmbedding::try_new(opts).map_err(|e| {
            format!(
                "loading {EMBED_MODEL_ID} (cache: {}): {e}",
                cache.display()
            )
        })?;
        Ok(Self { inner })
    }

    /// Embed documents (the pinned [`EMBED_TEXT_PREFIX`] is added
    /// internally — empty for the paraphrase family). Returns one
    /// L2-normalized [`EMBED_DIM`]-vector per input, in order.
    pub fn embed(&mut self, texts: &[String]) -> Result<Vec<Vec<f32>>, String> {
        let prefixed: Vec<String> = texts
            .iter()
            .map(|t| format!("{EMBED_TEXT_PREFIX}{t}"))
            .collect();
        let out = self
            .inner
            .embed(&prefixed, Some(16))
            .map_err(|e| format!("embedding {} doc(s): {e}", texts.len()))?;
        for v in &out {
            if v.len() != EMBED_DIM {
                return Err(format!(
                    "model returned {}-dim vector, expected {EMBED_DIM}",
                    v.len()
                ));
            }
        }
        Ok(out)
    }
}
