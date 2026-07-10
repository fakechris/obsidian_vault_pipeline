//! `crystal-themes` — build the semantic display-theme PROJECTION
//! (`.ovp/crystal/themes.json`) over all reader packs.
//!
//! Pipeline (spike-validated recipe, `.run/theme-spike-20260709/REPORT.md`):
//!
//!   reader packs (title + reader.md head, sorted by case_id)
//!     → multilingual embeddings (content-sha cache under
//!       `.ovp/cache/embeddings/`; model download on first cold run)
//!     → non-mutual kNN graph (k=10, cosine ≥ 0.5)
//!     → Louvain communities (resolution 1.5, pinned seed)
//!     → per-community c-TF-IDF keywords (the CJK-aware search tokenizer)
//!     → labels: keyword-derived offline; `--client live` adds one cached
//!       bilingual `theme_label/v1` call per community
//!     → `.ovp/crystal/themes.json` (rebuildable projection — never ledger
//!       state; claims are NEVER re-synthesized to re-theme).
//!
//! Degradation contract: no `embed` feature / no model / offline with a cold
//! cache → print why and exit 0 WITHOUT touching an existing themes.json.
//! Everything downstream treats missing themes as "Unclassified"; daily runs
//! are never blocked. A warmed embedding cache works even in builds without
//! the `embed` feature.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ovp_domain::crystal::synth::resolve_title;
use ovp_domain::crystal::themes::{
    THEMES_SCHEMA, ThemeCommunity, ThemeParams, ThemesFile, UNCLASSIFIED_ID, input_hash,
    parse_theme_label, theme_label_request,
};
use ovp_domain::vault_layout::VaultLayout;
use ovp_embed::cache as embed_cache;
use ovp_embed::knn::{cosine, knn_edges};
use ovp_embed::louvain::louvain_labels;
use ovp_embed::{EMBED_DIM, EMBED_HEAD_CHARS, EMBED_MODEL_ID, EMBED_TEXT_PREFIX, document_text};
use ovp_index::score::tokenize_for_search;

use crate::CliError;
use crate::commands::client::{ClientKind, build_client};
use crate::commands::crystal_synth::call_and_parse;

/// Keywords kept per community (auditable label layer).
const KEYWORDS_PER_THEME: usize = 10;
/// Tokens present in more than this fraction of ALL documents are stop-words
/// for keyword purposes (function words, residual scaffolding). 0.3 was tuned
/// on the real 1077-pack vault: 0.4 still admitted "but/not/can/when".
const MAX_KEYWORD_DOC_FREQ: f64 = 0.3;

/// Minimal function-word stoplist for the KEYWORD layer only (labels are
/// presentation; clustering never sees this). The df filter above catches
/// corpus-wide noise; this catches the mid-frequency function words a
/// truncated corpus lets through. English words + the CJK bigrams the search
/// tokenizer emits for common function pairs. Deliberately small — c-TF-IDF
/// does the real work.
const KEYWORD_STOPLIST: &[&str] = &[
    // English
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "for", "from", "has",
    "have", "how", "if", "in", "into", "is", "it", "its", "more", "no", "not", "of", "on", "or",
    "so", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to",
    "until", "via", "was", "we", "what", "when", "where", "which", "while", "who", "why", "will",
    "with", "you", "your",
    // common CJK function bigrams (as emitted by the sliding-bigram tokenizer)
    "一个", "不是", "我们", "可以", "没有", "这个", "什么", "就是", "但是", "因为", "所以",
    "如果", "他们", "你的", "我的", "自己", "这些", "那些", "使用", "需要", "通过",
];
/// Keywords joined into the offline label.
const LABEL_KEYWORDS: usize = 3;
/// Centroid-nearest titles shown to the labeling model.
const LABEL_SAMPLE_TITLES: usize = 3;

pub struct CrystalThemesArgs {
    pub vault_root: PathBuf,
    pub client_kind: ClientKind,
    /// Cassette root for `theme_label/v1`. Default:
    /// `<vault-root>/.ovp/cassettes/crystal` (shared with crystal-synth).
    pub cache_dir: Option<PathBuf>,
    /// Recompute even when themes.json matches the current input set.
    pub refresh: bool,
    pub k: usize,
    pub cosine_threshold: f64,
    pub resolution: f64,
    pub seed: u64,
}

/// One themable reader pack.
struct ThemeDoc {
    case_id: String,
    title: String,
    text: String,
    sha: String,
}

/// Strip reader.md presentation boilerplate before embedding — mirrors the
/// spike's `prep_docs.py` (which fed "title + first 1500 chars of evidence
/// text, boilerplate stripped"). Removed: the title heading (carried
/// separately), stats blockquotes (`> 12 cards · …`), `<details>/<summary>`
/// evidence scaffolding, trailing `` `[u-… · line N]` `` unit refs, heading
/// markers, and trailing `_definition_`-style card-kind tags. Deterministic,
/// line-based, no regex.
fn clean_reader_body(md: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    for line in md.lines() {
        let s = line.trim();
        if s.is_empty()
            || s.starts_with("# ")
            || s.starts_with('>')
            || s.starts_with("<details")
            || s.starts_with("</details")
            || s.starts_with("<summary")
        {
            continue;
        }
        // Drop the trailing backticked unit reference on quote bullets.
        let s = match s.find("`[u-") {
            Some(i) => s[..i].trim_end(),
            None => s,
        };
        // "## 3. Card heading  _definition_" → keep the heading text.
        let s = s.trim_start_matches('#').trim_start();
        let s = strip_trailing_em_tag(s);
        if !s.is_empty() {
            out.push(s);
        }
    }
    out.join(" ")
}

/// Drop a trailing `_tag_` token (the card-kind italics: `_definition_`,
/// `_fact_`, …) if present.
fn strip_trailing_em_tag(s: &str) -> &str {
    let trimmed = s.trim_end();
    if let Some(last) = trimmed.rsplit_once(char::is_whitespace).map(|(_, l)| l) {
        if last.len() > 2 && last.starts_with('_') && last.ends_with('_') {
            return trimmed[..trimmed.len() - last.len()].trim_end();
        }
    }
    trimmed
}

/// Collect every reader-pack dir (any dir carrying reader.md /
/// run-status.json / units.accepted.json), sorted by case_id.
fn collect_docs(reader_root: &Path) -> Result<Vec<ThemeDoc>, CliError> {
    let entries = std::fs::read_dir(reader_root).map_err(|e| {
        CliError::Io(format!(
            "crystal-themes: reading reader root {}: {e}",
            reader_root.display()
        ))
    })?;
    let mut docs = Vec::new();
    for entry in entries.flatten() {
        let dir = entry.path();
        if !dir.is_dir() {
            continue;
        }
        let is_pack = dir.join("reader.md").exists()
            || dir.join("run-status.json").exists()
            || dir.join("units.accepted.json").exists();
        if !is_pack {
            continue;
        }
        let case_id = entry.file_name().to_string_lossy().into_owned();
        let title = resolve_title(&dir, &case_id);
        let body = std::fs::read_to_string(dir.join("reader.md")).unwrap_or_default();
        let text = document_text(&title, &clean_reader_body(&body), EMBED_HEAD_CHARS);
        let sha = embed_cache::text_sha256(&text);
        docs.push(ThemeDoc {
            case_id,
            title,
            text,
            sha,
        });
    }
    docs.sort_by(|a, b| a.case_id.cmp(&b.case_id));
    Ok(docs)
}

/// Resolve vectors for every doc: cache first, embedder for the misses.
/// `Ok(None)` = a graceful skip (reason already printed).
fn resolve_vectors(
    docs: &[ThemeDoc],
    cache_dir: &Path,
) -> Result<Option<Vec<Vec<f32>>>, CliError> {
    let mut vectors: Vec<Option<Vec<f32>>> = docs
        .iter()
        .map(|d| embed_cache::load(cache_dir, &d.sha, EMBED_MODEL_ID, EMBED_DIM))
        .collect();
    let missing: Vec<usize> = (0..docs.len()).filter(|&i| vectors[i].is_none()).collect();
    if !missing.is_empty() {
        let Some(embedded) = embed_missing(docs, &missing)? else {
            return Ok(None);
        };
        for (slot, vector) in missing.iter().zip(embedded) {
            embed_cache::store(cache_dir, &docs[*slot].sha, EMBED_MODEL_ID, &vector)
                .map_err(|e| CliError::Io(format!("crystal-themes: embedding cache: {e}")))?;
            vectors[*slot] = Some(vector);
        }
    }
    Ok(Some(vectors.into_iter().map(|v| v.unwrap()).collect()))
}

/// Embed the docs at `missing` indices. `Ok(None)` = graceful skip.
#[cfg(feature = "embed")]
fn embed_missing(docs: &[ThemeDoc], missing: &[usize]) -> Result<Option<Vec<Vec<f32>>>, CliError> {
    eprintln!(
        "crystal-themes: embedding {} of {} pack(s) with {EMBED_MODEL_ID} \
         (first run downloads ~450MB of model files to {})",
        missing.len(),
        docs.len(),
        ovp_embed::embedder::model_cache_dir().display()
    );
    let mut embedder = match ovp_embed::embedder::Embedder::new(true) {
        Ok(e) => e,
        Err(e) => {
            println!(
                "crystal-themes: semantic themes SKIPPED — the embedding model is \
                 unavailable ({e}). Existing themes.json (if any) is untouched; \
                 unthemed claims stay \"Unclassified\". Retry when online."
            );
            return Ok(None);
        }
    };
    let texts: Vec<String> = missing.iter().map(|&i| docs[i].text.clone()).collect();
    let mut out = Vec::with_capacity(texts.len());
    for chunk in texts.chunks(64) {
        let batch = embedder
            .embed(chunk)
            .map_err(|e| CliError::Io(format!("crystal-themes: {e}")))?;
        out.extend(batch);
    }
    Ok(Some(out))
}

#[cfg(not(feature = "embed"))]
fn embed_missing(docs: &[ThemeDoc], missing: &[usize]) -> Result<Option<Vec<Vec<f32>>>, CliError> {
    println!(
        "crystal-themes: semantic themes SKIPPED — this build lacks the `embed` \
         feature and {} of {} pack(s) have no cached embedding under \
         .ovp/cache/embeddings. Install a prebuilt ovp2 (which bundles the \
         embedder) or rebuild with `--features embed`.",
        missing.len(),
        docs.len()
    );
    Ok(None)
}

/// Offline label: the top c-TF-IDF keywords joined (deterministic).
fn keyword_label(keywords: &[String]) -> String {
    let head: Vec<&str> = keywords
        .iter()
        .take(LABEL_KEYWORDS)
        .map(String::as_str)
        .collect();
    if head.is_empty() {
        "Unlabeled".to_string()
    } else {
        head.join(" · ")
    }
}

/// The `LABEL_SAMPLE_TITLES` member titles nearest the community centroid.
fn centroid_titles(docs: &[ThemeDoc], vectors: &[Vec<f32>], members: &[usize]) -> Vec<String> {
    let dim = vectors.first().map(|v| v.len()).unwrap_or(0);
    let mut centroid = vec![0.0f32; dim];
    for &m in members {
        for (c, v) in centroid.iter_mut().zip(&vectors[m]) {
            *c += v;
        }
    }
    let norm = centroid.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        for c in centroid.iter_mut() {
            *c /= norm;
        }
    }
    let mut scored: Vec<(f64, usize)> = members
        .iter()
        .map(|&m| (cosine(&centroid, &vectors[m]), m))
        .collect();
    scored.sort_by(|(sa, ma), (sb, mb)| {
        sb.partial_cmp(sa)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(ma.cmp(mb))
    });
    scored
        .into_iter()
        .take(LABEL_SAMPLE_TITLES)
        .map(|(_, m)| docs[m].title.clone())
        .collect()
}

pub fn run(args: CrystalThemesArgs) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let reader_root = args.vault_root.join(layout.reader_root());
    let store = args.vault_root.join(layout.crystal_store_dir());
    let themes_path = store.join("themes.json");
    let embed_cache_dir = args.vault_root.join(".ovp/cache/embeddings");
    let cassette_dir = args
        .cache_dir
        .clone()
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/crystal"));

    let docs = collect_docs(&reader_root)?;
    if docs.is_empty() {
        println!(
            "crystal-themes: no reader packs under {} — nothing to theme.",
            reader_root.display()
        );
        return Ok(());
    }

    let inputs: Vec<(String, String)> = docs
        .iter()
        .map(|d| (d.case_id.clone(), d.sha.clone()))
        .collect();
    let generated_from = input_hash(&inputs);

    if !args.refresh {
        if let Ok(Some(existing)) = ThemesFile::load(&themes_path) {
            if existing.generated_from == generated_from && existing.model == EMBED_MODEL_ID {
                println!(
                    "crystal-themes: up to date ({} pack(s), {} communit(ies)) — use --refresh to recompute",
                    existing.packs.len(),
                    existing.communities.len()
                );
                print_table(&existing);
                return Ok(());
            }
        }
    }

    // ---- Embeddings (cached, incremental) ----
    let Some(vectors) = resolve_vectors(&docs, &embed_cache_dir)? else {
        return Ok(()); // graceful skip, reason already printed
    };

    // ---- Communities ----
    let edges = knn_edges(&vectors, args.k, args.cosine_threshold);
    let labels = louvain_labels(docs.len(), &edges, args.resolution, args.seed);
    let n_communities = labels.iter().copied().max().map_or(0, |m| (m + 1).max(0)) as usize;
    let mut members: Vec<Vec<usize>> = vec![Vec::new(); n_communities];
    for (i, &l) in labels.iter().enumerate() {
        if l >= 0 {
            members[l as usize].push(i);
        }
    }

    // ---- c-TF-IDF keywords (CJK-aware search tokenizer) ----
    // Corpus-level stop filter first: tokens present in most documents
    // (function words, residual pack scaffolding) carry no theme signal, and
    // c-TF-IDF's global penalty alone cannot beat their sheer term frequency.
    // Deterministic: document frequency over the same tokenizer.
    let doc_tokens: Vec<Vec<String>> = docs
        .iter()
        .map(|d| tokenize_for_search(&d.text))
        .collect();
    let mut df: BTreeMap<&str, usize> = BTreeMap::new();
    for tokens in &doc_tokens {
        let uniq: std::collections::BTreeSet<&str> =
            tokens.iter().map(String::as_str).collect();
        for t in uniq {
            *df.entry(t).or_insert(0) += 1;
        }
    }
    let df_cap = (docs.len() as f64 * MAX_KEYWORD_DOC_FREQ).ceil() as usize;
    let keep = |t: &str| -> bool {
        t.chars().count() > 1
            && !t.bytes().all(|b| b.is_ascii_digit())
            && !KEYWORD_STOPLIST.contains(&t)
            && df.get(t).copied().unwrap_or(0) <= df_cap
    };
    let token_clusters: Vec<Vec<String>> = members
        .iter()
        .map(|ms| {
            ms.iter()
                .flat_map(|&m| doc_tokens[m].iter())
                .filter(|t| keep(t))
                .cloned()
                .collect()
        })
        .collect();
    let keywords = ovp_embed::ctfidf::keywords(&token_clusters, KEYWORDS_PER_THEME);

    // ---- Labels ----
    let mut communities = Vec::with_capacity(n_communities);
    let mut live_client = match args.client_kind {
        ClientKind::Live => Some(build_client(ClientKind::Live, &cassette_dir)?),
        ClientKind::Replay => None,
    };
    for (id, ms) in members.iter().enumerate() {
        let kw = keywords[id].clone();
        let fallback = keyword_label(&kw);
        let (label, label_zh) = match live_client.as_mut() {
            Some(client) => {
                let titles = centroid_titles(&docs, &vectors, ms);
                let req = theme_label_request(&kw, &titles);
                let (parsed, _log) =
                    call_and_parse(client.as_mut(), &req, "theme-label", parse_theme_label)?;
                parsed
            }
            None => (fallback.clone(), fallback.clone()),
        };
        communities.push(ThemeCommunity {
            id: id as i64,
            label,
            label_zh,
            keywords: kw,
            size: ms.len(),
        });
    }

    // ---- Write the projection ----
    let mut packs: BTreeMap<String, i64> = BTreeMap::new();
    for (doc, &label) in docs.iter().zip(labels.iter()) {
        packs.insert(
            doc.case_id.clone(),
            if label >= 0 { label } else { UNCLASSIFIED_ID },
        );
    }
    let file = ThemesFile {
        schema: THEMES_SCHEMA.to_string(),
        model: EMBED_MODEL_ID.to_string(),
        params: ThemeParams {
            k: args.k,
            cosine_threshold: args.cosine_threshold,
            resolution: args.resolution,
            seed: args.seed,
            text_prefix: EMBED_TEXT_PREFIX.to_string(),
            head_chars: EMBED_HEAD_CHARS,
        },
        generated_from,
        packs,
        communities,
    };
    std::fs::create_dir_all(&store)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", store.display())))?;
    let body = serde_json::to_string_pretty(&file)
        .map_err(|e| CliError::Io(format!("serializing themes.json: {e}")))?;
    std::fs::write(&themes_path, format!("{body}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", themes_path.display())))?;

    let noise = file.packs.values().filter(|&&v| v == UNCLASSIFIED_ID).count();
    println!(
        "crystal-themes: {} pack(s) → {} communit(ies), {} unclassified → {}",
        docs.len(),
        file.communities.len(),
        noise,
        themes_path.display()
    );
    print_table(&file);
    println!("  next: `ovp2 index` re-projects claim themes; crystal-synth batches by community.");
    Ok(())
}

fn print_table(file: &ThemesFile) {
    for c in &file.communities {
        println!(
            "  t{:03}  n={:<4} {}  [{}]",
            c.id,
            c.size,
            c.label,
            c.keywords
                .iter()
                .take(5)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Write a minimal reader pack with a reader.md.
    fn write_pack(reader_root: &Path, case_id: &str, title: &str, body: &str) {
        let dir = reader_root.join(case_id);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("reader.md"), format!("# {title}\n\n{body}\n")).unwrap();
    }

    /// Pre-seed the embedding cache so the command runs without the embedder
    /// (this is also the documented path for `embed`-less builds).
    fn seed_vector(vault: &Path, case_id: &str, title: &str, body: &str, v: [f32; 3]) {
        // Mirror collect_docs' text derivation exactly.
        let _ = case_id;
        let text = document_text(
            title,
            &clean_reader_body(&format!("# {title}\n\n{body}\n")),
            EMBED_HEAD_CHARS,
        );
        let sha = embed_cache::text_sha256(&text);
        let norm = (v.iter().map(|x| x * x).sum::<f32>()).sqrt();
        // EMBED_DIM-length L2-normalized vectors are required by the loader — pad.
        let mut full = vec![0.0f32; EMBED_DIM];
        for (slot, x) in full.iter_mut().zip(v) {
            *slot = x / norm;
        }
        embed_cache::store(&vault.join(".ovp/cache/embeddings"), &sha, EMBED_MODEL_ID, &full)
            .unwrap();
    }

    fn args(vault: &Path) -> CrystalThemesArgs {
        CrystalThemesArgs {
            vault_root: vault.to_path_buf(),
            client_kind: ClientKind::Replay,
            cache_dir: None,
            refresh: false,
            k: 2,
            cosine_threshold: 0.5,
            resolution: 1.0,
            seed: 42,
        }
    }

    #[test]
    fn two_blobs_cluster_and_write_deterministic_themes_json() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        let reader = vault.join(VaultLayout::new().reader_root());
        // Two "memory" docs, two "market" docs, one orthogonal noise doc.
        let cases = [
            ("2026-06-01_mem-a", "Agent memory systems", "memory memory context agent"),
            ("2026-06-02_mem-b", "Working memory budget", "memory context budget agent"),
            ("2026-06-03_quant-a", "Polymarket order book", "market quant order polymarket"),
            ("2026-06-04_quant-b", "Prediction market math", "market quant math polymarket"),
            ("2026-06-05_lone", "Gardening notes", "tomato soil watering"),
        ];
        let vecs: [[f32; 3]; 5] = [
            [1.0, 0.05, 0.0],
            [1.0, 0.0, 0.05],
            [0.05, 1.0, 0.0],
            [0.0, 1.0, 0.05],
            [0.0, 0.0, 1.0],
        ];
        for ((case_id, title, body), v) in cases.iter().zip(vecs) {
            write_pack(&reader, case_id, title, body);
            seed_vector(vault, case_id, title, body, v);
        }

        run(args(vault)).expect("themes run");
        let themes_path = vault.join(".ovp/crystal/themes.json");
        let first = std::fs::read_to_string(&themes_path).unwrap();
        let file = ThemesFile::load(&themes_path).unwrap().unwrap();
        assert_eq!(file.communities.len(), 2, "{file:?}");
        assert_eq!(file.packs.len(), 5);
        assert_eq!(file.packs["2026-06-01_mem-a"], file.packs["2026-06-02_mem-b"]);
        assert_eq!(
            file.packs["2026-06-03_quant-a"],
            file.packs["2026-06-04_quant-b"]
        );
        assert_ne!(file.packs["2026-06-01_mem-a"], file.packs["2026-06-03_quant-a"]);
        assert_eq!(file.packs["2026-06-05_lone"], UNCLASSIFIED_ID, "singleton → noise");
        // Keyword labels are deterministic and CJK-tokenizer-derived.
        assert!(!file.communities[0].label.is_empty());
        assert_eq!(file.model, EMBED_MODEL_ID);

        // Re-run without --refresh: up-to-date fast path, file unchanged.
        run(args(vault)).expect("second run");
        assert_eq!(std::fs::read_to_string(&themes_path).unwrap(), first);

        // --refresh recomputes to identical bytes (determinism end-to-end).
        let mut a = args(vault);
        a.refresh = true;
        run(a).expect("refresh run");
        assert_eq!(std::fs::read_to_string(&themes_path).unwrap(), first);
    }

    #[test]
    fn missing_embeddings_without_embedder_skip_gracefully() {
        // Cache NOT seeded → in `embed`-less test builds this must exit 0
        // without writing themes.json. (With the embed feature this test
        // would try the real model; keep it to the pure path.)
        if cfg!(feature = "embed") {
            return;
        }
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        let reader = vault.join(VaultLayout::new().reader_root());
        write_pack(&reader, "2026-06-01_solo", "Solo pack", "body");
        run(args(vault)).expect("graceful skip");
        assert!(!vault.join(".ovp/crystal/themes.json").exists());
    }

    #[test]
    fn clean_reader_body_strips_pack_boilerplate() {
        let md = "# Claude Code 源码解读\n\n\
            > 12 cards · 26 grounded units · critic trims 0 / adds 4\n\n\
            ## 1. Agent Loop 是心脏，采用 async generator。  _definition_\n\n\
            **分层解耦将 Agent Loop、状态管理分离。**\n\n\
            <details><summary>Evidence — 1 quote(s)</summary>\n\n\
            - “Agent Loop 是 Claude Code 的心脏” `[u-002-227a2a68 · line 209]`\n\n\
            </details>\n";
        let cleaned = clean_reader_body(md);
        assert!(!cleaned.contains("cards ·"), "{cleaned}");
        assert!(!cleaned.contains("details"), "{cleaned}");
        assert!(!cleaned.contains("u-002"), "{cleaned}");
        assert!(!cleaned.contains("_definition_"), "{cleaned}");
        assert!(!cleaned.contains('#'), "{cleaned}");
        assert!(cleaned.contains("Agent Loop 是心脏"), "{cleaned}");
        assert!(cleaned.contains("“Agent Loop 是 Claude Code 的心脏”"), "{cleaned}");
        assert!(cleaned.contains("分层解耦"), "{cleaned}");
    }

    #[test]
    fn empty_reader_root_is_a_noop() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        std::fs::create_dir_all(vault.join(VaultLayout::new().reader_root())).unwrap();
        run(args(vault)).expect("noop");
        assert!(!vault.join(".ovp/crystal/themes.json").exists());
    }
}
