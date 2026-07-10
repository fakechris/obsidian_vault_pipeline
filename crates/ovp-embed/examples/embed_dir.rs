//! Validation harness: embed every `*.md` in a directory with a candidate
//! production ONNX artifact and dump `{ids, vectors}` JSON, so the
//! theme-spike's Python sweep scripts can score each candidate against the
//! spike's structural gates.
//!
//! Usage:
//!   cargo run -p ovp-embed --features embed --release --example embed_dir -- \
//!     <model-key> <docs_dir> <out.json> [max_length_tokens]
//!
//! model-key: e5-small | e5-small-noprefix | mpnet | bge-m3 | minilm-l12

use std::path::PathBuf;

use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};

fn main() {
    let mut args = std::env::args().skip(1);
    let usage = "usage: embed_dir <e5-small|e5-small-noprefix|mpnet|bge-m3|minilm-l12> <docs_dir> <out.json> [max_length]";
    let model_key = args.next().expect(usage);
    let docs_dir = PathBuf::from(args.next().expect(usage));
    let out_path = PathBuf::from(args.next().expect(usage));
    let max_length: usize = args
        .next()
        .map(|s| s.parse().expect("max_length must be an integer"))
        .unwrap_or(512);

    let (model, model_id, prefix) = match model_key.as_str() {
        "e5-small" => (
            EmbeddingModel::MultilingualE5Small,
            "intfloat/multilingual-e5-small",
            "passage: ",
        ),
        "e5-small-noprefix" => (
            EmbeddingModel::MultilingualE5Small,
            "intfloat/multilingual-e5-small",
            "",
        ),
        "mpnet" => (
            EmbeddingModel::ParaphraseMLMpnetBaseV2,
            "Xenova/paraphrase-multilingual-mpnet-base-v2",
            "",
        ),
        "bge-m3" => (EmbeddingModel::BGEM3, "BAAI/bge-m3", ""),
        "minilm-l12" => (
            EmbeddingModel::ParaphraseMLMiniLML12V2,
            "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
            "",
        ),
        "minilm-l12-q" => (
            EmbeddingModel::ParaphraseMLMiniLML12V2Q,
            "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
            "",
        ),
        other => panic!("unknown model key `{other}` — {usage}"),
    };

    let mut ids: Vec<String> = std::fs::read_dir(&docs_dir)
        .expect("reading docs dir")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".md"))
        .collect();
    ids.sort();
    eprintln!(
        "embedding {} docs from {} with {model_id} (prefix {prefix:?}, max_length {max_length})",
        ids.len(),
        docs_dir.display()
    );

    let texts: Vec<String> = ids
        .iter()
        .map(|n| {
            let raw = std::fs::read_to_string(docs_dir.join(n)).expect("reading doc");
            format!("{prefix}{raw}")
        })
        .collect();

    let cache = ovp_embed::embedder::model_cache_dir();
    let mut embedder = TextEmbedding::try_new(
        InitOptions::new(model)
            .with_cache_dir(cache)
            .with_max_length(max_length)
            .with_show_download_progress(true),
    )
    .expect("loading model");
    let start = std::time::Instant::now();
    let mut vectors: Vec<Vec<f32>> = Vec::with_capacity(texts.len());
    for (i, chunk) in texts.chunks(64).enumerate() {
        vectors.extend(embedder.embed(chunk, Some(16)).expect("embedding batch"));
        eprintln!(
            "  batch {} done ({} docs, {:?})",
            i + 1,
            vectors.len(),
            start.elapsed()
        );
    }

    let out = serde_json::json!({
        "model": model_id,
        "prefix": prefix,
        "max_length": max_length,
        "ids": ids,
        "vectors": vectors,
    });
    std::fs::write(&out_path, serde_json::to_string(&out).unwrap()).expect("writing output");
    eprintln!("wrote {}", out_path.display());
}
