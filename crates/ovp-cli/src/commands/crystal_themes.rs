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
//!       bilingual `theme_label/v2` call per community whose
//!       membership moved (ids + stable names carry over from the last run)
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
    LabelsProvenance, THEME_LABEL_PROMPT_ID, THEMES_SCHEMA, ThemeCommunity, ThemeParams,
    ThemesFile, UNCLASSIFIED_ID,
    input_hash, parse_theme_label, theme_label_request,
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
/// Member titles shown to the labeling model, sampled across the community.
const LABEL_SAMPLE_TITLES: usize = 12;
/// Claims shown to the labeling model (what a reader sees under the theme).
const LABEL_SAMPLE_CLAIMS: usize = 5;
/// A new community inherits a previous community's id when their member
/// sets overlap at least this much (Jaccard). Keeps theme URLs, theme pages
/// and synthesis cluster keys (`t{id:03}`) stable across weekly reclusters.
const INHERIT_ID_JACCARD: f64 = 0.3;
/// …and keeps the previous NAME (no model call) when membership barely
/// moved. Below this the community is renamed from its current members.
const KEEP_LABEL_JACCARD: f64 = 0.6;

pub struct CrystalThemesArgs {
    pub vault_root: PathBuf,
    pub client_kind: ClientKind,
    /// Cassette root for `theme_label/v2`. Default:
    /// `<vault-root>/.ovp/cassettes/crystal` (shared with crystal-synth).
    pub cache_dir: Option<PathBuf>,
    /// Recompute even when themes.json matches the current input set.
    pub refresh: bool,
    pub k: usize,
    pub cosine_threshold: f64,
    pub resolution: f64,
    pub seed: u64,
    /// Print the old → new table without writing themes.json.
    pub dry_run: bool,
}

/// One themable reader pack.
pub(crate) struct ThemeDoc {
    pub(crate) case_id: String,
    pub(crate) title: String,
    pub(crate) text: String,
    pub(crate) sha: String,
}

/// Strip reader.md presentation boilerplate before embedding — mirrors the
/// spike's `prep_docs.py` (which fed "title + first 1500 chars of evidence
/// text, boilerplate stripped"). Removed: the title heading (carried
/// separately), stats blockquotes (`> 12 cards · …`), `<details>/<summary>`
/// evidence scaffolding, trailing `` `[u-… · line N]` `` unit refs, heading
/// markers, and trailing `_definition_`-style card-kind tags. Deterministic,
/// line-based, no regex.
pub(crate) fn clean_reader_body(md: &str) -> String {
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
    if let Some(last) = trimmed.rsplit_once(char::is_whitespace).map(|(_, l)| l)
        && last.len() > 2
        && last.starts_with('_')
        && last.ends_with('_')
    {
        return trimmed[..trimmed.len() - last.len()].trim_end();
    }
    trimmed
}

/// Collect every PRODUCT reader-pack dir (any dir carrying reader.md /
/// run-status.json / units.accepted.json, minus failed attempts), sorted by
/// case_id. A missing reader root is an empty corpus (fresh vault), not an
/// error — the degradation contract says never block.
pub(crate) fn collect_docs(reader_root: &Path) -> Result<Vec<ThemeDoc>, CliError> {
    let entries = match std::fs::read_dir(reader_root) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => {
            return Err(CliError::Io(format!(
                "crystal-themes: reading reader root {}: {e}",
                reader_root.display()
            )));
        }
    };
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
        // The index's product-pack eligibility rule (shared predicate; see
        // `ovp_index::failed_reader_attempt` / `build_packs`): failed reader
        // attempts leave dirs with run-status.json carrying a parse_error or
        // zero cards — audit artifacts, not product. They must not shape
        // embeddings/kNN/communities either.
        if ovp_index::failed_reader_attempt(&dir) {
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
/// `Ok(None)` = a graceful skip (reason already printed). `pub(crate)`:
/// `tags-suggest` shares the cache/embedder plumbing (messages still say
/// crystal-themes — same model, same cache, same degradation contract).
pub(crate) fn resolve_vectors(
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
    // Embedding is CPU-bound and the first cold run also downloads ~450MB —
    // stream a flushed per-batch line so it never looks stalled. stderr is
    // line-buffered and survives a kill under launchd.
    let total = texts.len();
    for chunk in texts.chunks(64) {
        let batch = embedder
            .embed(chunk)
            .map_err(|e| CliError::Io(format!("crystal-themes: {e}")))?;
        out.extend(batch);
        sayln_err!("  [{}/{total}] embedding …", out.len());
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

/// Is an existing themes.json current for THIS invocation? The input hash
/// alone is not enough (codex P1): clustering params can change without the
/// pack set changing, and a keyword-labeled file must not satisfy the
/// documented `--client live` relabel run. Provenance is one-way — `Llm`
/// labels satisfy a keyword (replay) request, so an offline rerun never
/// downgrades paid bilingual labels, but never the reverse. Any mismatch
/// takes the full recompute path: embeddings are content-cached and the
/// clustering is deterministic, so recompute costs seconds (correctness
/// over cleverness).
fn up_to_date(
    existing: &ThemesFile,
    generated_from: &str,
    params: &ThemeParams,
    requested_labels: LabelsProvenance,
) -> bool {
    existing.generated_from == generated_from
        && existing.model == EMBED_MODEL_ID
        && existing.params == *params
        && (requested_labels == LabelsProvenance::Keyword
            || (existing.labels_provenance == LabelsProvenance::Llm
                && existing.label_prompt.as_deref() == Some(THEME_LABEL_PROMPT_ID)))
}

/// `LABEL_SAMPLE_TITLES` member titles spread across the community: members
/// ranked by closeness to the centroid, then picked at even rank intervals,
/// so the model sees the typical core AND the edges. Only the core (three
/// centroid-nearest titles) once named a 129-pack open-source-tools
/// community after the two stock-data repos that happened to sit there.
fn spread_titles(docs: &[ThemeDoc], vectors: &[Vec<f32>], members: &[usize]) -> Vec<String> {
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
    let mut ranked: Vec<(f64, usize)> = members
        .iter()
        .map(|&m| (cosine(&centroid, &vectors[m]), m))
        .collect();
    ranked.sort_by(|(sa, ma), (sb, mb)| {
        sb.partial_cmp(sa)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(ma.cmp(mb))
    });
    spread_pick(ranked.len(), LABEL_SAMPLE_TITLES)
        .into_iter()
        .map(|r| docs[ranked[r].1].title.clone())
        .collect()
}

/// `n` evenly spaced ranks out of `len` (first and last included), or all
/// of them when `len <= n`.
fn spread_pick(len: usize, n: usize) -> Vec<usize> {
    if len <= n {
        return (0..len).collect();
    }
    if n <= 1 {
        return vec![0; n.min(1)];
    }
    (0..n).map(|i| i * (len - 1) / (n - 1)).collect()
}

/// The previous name a community keeps instead of a model call: only for a
/// live run, when the previous file's names came from the CURRENT prompt
/// (a better prompt must reach every theme once), and membership barely
/// moved (`KEEP_LABEL_JACCARD`).
fn reused_name(
    prev: Option<&ThemesFile>,
    requested: LabelsProvenance,
    carry: &Carry,
) -> Option<(String, String)> {
    let p = prev?;
    if requested != LabelsProvenance::Llm
        || p.labels_provenance != LabelsProvenance::Llm
        || p.label_prompt.as_deref() != Some(THEME_LABEL_PROMPT_ID)
    {
        return None;
    }
    let (old, j) = carry.prev?;
    if j < KEEP_LABEL_JACCARD {
        return None;
    }
    let c = p.communities.iter().find(|c| c.id == old)?;
    Some((c.label.clone(), c.label_zh.clone()))
}

/// The lowest id a fresh community may take: the previous file's persisted
/// high-water mark, or past every id it lists — whichever is higher. A
/// legacy file (no `next_id`) thus still protects its highest id when that
/// community retires in the very run that first writes the mark.
fn id_floor(prev: &ThemesFile) -> i64 {
    let past_listed = prev
        .communities
        .iter()
        .map(|c| c.id)
        .chain(prev.packs.values().copied())
        .max()
        .map_or(0, |m| m.max(-1) + 1);
    prev.next_id.unwrap_or(0).max(past_listed)
}

/// How one freshly clustered community relates to the previous themes.json.
#[derive(Debug, Clone, PartialEq)]
struct Carry {
    /// The id it is published under: inherited, or fresh past every old id.
    id: i64,
    /// The previous community it continues, with their member Jaccard.
    prev: Option<(i64, f64)>,
}

/// Map each new community (by member case ids) to a previous one: greedy
/// one-to-one by descending Jaccard, ties broken by new index then old id
/// (deterministic). Pairs below `INHERIT_ID_JACCARD` do not inherit; those
/// communities get fresh ids from `floor` (the previous file's high-water
/// mark) or past every previous id, whichever is higher, so a retired id is
/// never reused for different content.
fn carry_ids(
    new: &[std::collections::BTreeSet<&str>],
    prev: &BTreeMap<i64, std::collections::BTreeSet<&str>>,
    floor: i64,
) -> Vec<Carry> {
    let mut pairs: Vec<(f64, usize, i64)> = Vec::new();
    for (n, ns) in new.iter().enumerate() {
        for (&o, os) in prev {
            let inter = ns.intersection(os).count();
            if inter == 0 {
                continue;
            }
            let union = ns.len() + os.len() - inter;
            pairs.push((inter as f64 / union as f64, n, o));
        }
    }
    pairs.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.1.cmp(&b.1))
            .then(a.2.cmp(&b.2))
    });
    let mut out: Vec<Option<Carry>> = vec![None; new.len()];
    let mut taken: std::collections::BTreeSet<i64> = std::collections::BTreeSet::new();
    for (j, n, o) in pairs {
        if j < INHERIT_ID_JACCARD || out[n].is_some() || taken.contains(&o) {
            continue;
        }
        taken.insert(o);
        out[n] = Some(Carry { id: o, prev: Some((o, j)) });
    }
    let mut next = prev.keys().copied().max().map_or(0, |m| m.max(-1) + 1).max(floor);
    out.into_iter()
        .map(|c| {
            c.unwrap_or_else(|| {
                let id = next;
                next += 1;
                Carry { id, prev: None }
            })
        })
        .collect()
}

/// Up to `LABEL_SAMPLE_CLAIMS` active claims per published community id,
/// in the wording the portal shows (human text patches applied — the same
/// reader the portal uses): each claim goes where the portal shows it — the
/// community holding most of its cited packs, ties to the LOWER id
/// (`ThemesFile::majority_community`) — most-cited first, then by
/// claim_key. No ledger means no claims; labeling still works from titles.
fn claims_by_community(
    vault_root: &Path,
    layout: &VaultLayout,
    community_of: &BTreeMap<&str, i64>,
) -> BTreeMap<i64, Vec<String>> {
    let mut out: BTreeMap<i64, Vec<(usize, String, String)>> = BTreeMap::new();
    for rec in ovp_api_projection::readers::load_active_records(vault_root, layout) {
        let mut votes: BTreeMap<i64, usize> = BTreeMap::new();
        for case in &rec.source_cases {
            if let Some(&c) = community_of.get(case.as_str()) {
                *votes.entry(c).or_insert(0) += 1;
            }
        }
        let Some((&c, _)) = votes.iter().max_by(|a, b| a.1.cmp(b.1).then(b.0.cmp(a.0))) else {
            continue;
        };
        out.entry(c)
            .or_default()
            .push((rec.source_cases.len(), rec.claim_key.clone(), rec.claim.clone()));
    }
    out.into_iter()
        .map(|(id, mut v)| {
            v.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
            (id, v.into_iter().take(LABEL_SAMPLE_CLAIMS).map(|(_, _, c)| c).collect())
        })
        .collect()
}

pub fn run(args: CrystalThemesArgs) -> Result<(), CliError> {
    // Non-finite params would cluster meaninglessly AND serialize to JSON
    // null, making the written themes.json unloadable on the next index or
    // synth run (codex review P2) — fail loud before touching anything.
    if !args.cosine_threshold.is_finite() || !args.resolution.is_finite() {
        return Err(CliError::Io(format!(
            "crystal-themes: --cosine-threshold and --resolution must be finite numbers \
             (got {} / {})",
            args.cosine_threshold, args.resolution
        )));
    }
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
    let requested_params = ThemeParams {
        k: args.k,
        cosine_threshold: args.cosine_threshold,
        resolution: args.resolution,
        seed: args.seed,
        text_prefix: EMBED_TEXT_PREFIX.to_string(),
        head_chars: EMBED_HEAD_CHARS,
    };
    let requested_labels = match args.client_kind {
        ClientKind::Live => LabelsProvenance::Llm,
        ClientKind::Replay => LabelsProvenance::Keyword,
    };

    if !args.refresh
        && let Ok(Some(existing)) = ThemesFile::load(&themes_path)
        && up_to_date(&existing, &generated_from, &requested_params, requested_labels)
    {
        println!(
            "crystal-themes: up to date ({} pack(s), {} communit(ies)) — use --refresh to recompute",
            existing.packs.len(),
            existing.communities.len()
        );
        print_table(&existing);
        return Ok(());
    }

    // ---- Embeddings (cached, incremental) ----
    let Some(vectors) = resolve_vectors(&docs, &embed_cache_dir)? else {
        return Ok(()); // graceful skip, reason already printed
    };

    // ---- Communities ----
    // Louvain is deterministic and print-free (leaf crate); the CLI brackets
    // the call with flushed coarse lines so the phase boundary is visible.
    let edges = knn_edges(&vectors, args.k, args.cosine_threshold);
    sayln!(
        "  clustering {} node(s) over {} kNN edge(s) …",
        docs.len(),
        edges.len()
    );
    let labels = louvain_labels(docs.len(), &edges, args.resolution, args.seed);
    let n_communities = labels.iter().copied().max().map_or(0, |m| (m + 1).max(0)) as usize;
    sayln!("  … {n_communities} community(ies)");
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

    // ---- Identity: carry ids (and stable names) over from the last run ----
    // A corrupt previous file is no reason to fail; it just carries nothing.
    let prev = ThemesFile::load(&themes_path).ok().flatten();
    let new_sets: Vec<std::collections::BTreeSet<&str>> = members
        .iter()
        .map(|ms| ms.iter().map(|&m| docs[m].case_id.as_str()).collect())
        .collect();
    let mut prev_sets: BTreeMap<i64, std::collections::BTreeSet<&str>> = BTreeMap::new();
    if let Some(p) = &prev {
        for c in &p.communities {
            prev_sets.entry(c.id).or_default();
        }
        for (case, &c) in &p.packs {
            if c >= 0 {
                prev_sets.entry(c).or_default().insert(case.as_str());
            }
        }
    }
    let floor = prev.as_ref().map_or(0, id_floor);
    let carry = carry_ids(&new_sets, &prev_sets, floor);
    let docs_ref = &docs;
    let carry_ref = &carry;
    let community_of: BTreeMap<&str, i64> = members
        .iter()
        .enumerate()
        .flat_map(|(c, ms)| {
            ms.iter().map(move |&m| (docs_ref[m].case_id.as_str(), carry_ref[c].id))
        })
        .collect();

    // ---- Labels ----
    let mut communities = Vec::with_capacity(n_communities);
    let mut live_client = match args.client_kind {
        ClientKind::Live => Some(build_client(ClientKind::Live, &cassette_dir, None)?),
        ClientKind::Replay => None,
    };
    let claims = match live_client {
        Some(_) => claims_by_community(&args.vault_root, &layout, &community_of),
        None => BTreeMap::new(),
    };
    let mut renamed = 0usize;
    for (idx, ms) in members.iter().enumerate() {
        let kw = keywords[idx].clone();
        let fallback = keyword_label(&kw);
        let kept = reused_name(prev.as_ref(), requested_labels, &carry[idx]);
        let (label, label_zh) = match (kept, live_client.as_mut()) {
            (Some(names), _) => names,
            (None, Some(client)) => {
                let titles = spread_titles(&docs, &vectors, ms);
                let no_claims = Vec::new();
                let sample = claims.get(&carry[idx].id).unwrap_or(&no_claims);
                let req = theme_label_request(&kw, &titles, sample);
                let (parsed, _log, _raw) =
                    call_and_parse(client.as_mut(), &req, "theme-label", parse_theme_label)?;
                renamed += 1;
                parsed
            }
            (None, None) => (fallback.clone(), fallback.clone()),
        };
        communities.push(ThemeCommunity {
            id: carry[idx].id,
            label,
            label_zh,
            keywords: kw,
            size: ms.len(),
        });
    }
    communities.sort_by_key(|c| c.id);

    // ---- Write the projection ----
    let mut packs: BTreeMap<String, i64> = BTreeMap::new();
    for (doc, &label) in docs.iter().zip(labels.iter()) {
        packs.insert(
            doc.case_id.clone(),
            if label >= 0 { carry[label as usize].id } else { UNCLASSIFIED_ID },
        );
    }
    let file = ThemesFile {
        schema: THEMES_SCHEMA.to_string(),
        model: EMBED_MODEL_ID.to_string(),
        params: requested_params,
        generated_from,
        packs,
        communities,
        labels_provenance: requested_labels,
        label_prompt: (requested_labels == LabelsProvenance::Llm)
            .then(|| THEME_LABEL_PROMPT_ID.to_string()),
        next_id: Some(carry.iter().map(|c| c.id + 1).max().unwrap_or(0).max(floor)),
    };
    if args.client_kind == ClientKind::Live {
        println!("  {renamed} theme(s) named by the model, the rest kept their name");
    }
    print_changes(prev.as_ref(), &file);
    if args.dry_run {
        println!("crystal-themes: --dry-run, {} NOT written", themes_path.display());
        return Ok(());
    }
    std::fs::create_dir_all(&store)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", store.display())))?;
    let body = serde_json::to_string_pretty(&file)
        .map_err(|e| CliError::Io(format!("serializing themes.json: {e}")))?;
    // Publish atomically (temp + rename): crystal-synth reads themes.json to
    // group its batches, so a torn read of a direct write could feed it a
    // truncated projection and fail the run (codex P2).
    let tmp = themes_path.with_extension("json.tmp");
    std::fs::write(&tmp, format!("{body}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", tmp.display())))?;
    std::fs::rename(&tmp, &themes_path).map_err(|e| {
        CliError::Io(format!(
            "publishing {}: {e}",
            themes_path.display()
        ))
    })?;

    let noise = file.packs.values().filter(|&&v| v == UNCLASSIFIED_ID).count();
    println!(
        "crystal-themes: {} pack(s) → {} communit(ies), {} unclassified → {}",
        docs.len(),
        file.communities.len(),
        noise,
        themes_path.display()
    );
    println!("  next: `ovp2 index` re-projects claim themes; crystal-synth batches by community.");
    Ok(())
}

/// One line per theme, biggest first: `kept` (same id, same name),
/// `renamed` (same id, new name — the old one follows), `new` (fresh id);
/// then every previous id that no longer exists as `retired`.
fn print_changes(prev: Option<&ThemesFile>, file: &ThemesFile) {
    for line in change_lines(prev, file) {
        println!("  {line}");
    }
}

fn change_lines(prev: Option<&ThemesFile>, file: &ThemesFile) -> Vec<String> {
    let old_of = |id: i64| prev.and_then(|p| p.communities.iter().find(|c| c.id == id));
    let mut rows: Vec<&ThemeCommunity> = file.communities.iter().collect();
    rows.sort_by(|a, b| b.size.cmp(&a.size).then(a.id.cmp(&b.id)));
    let mut out = Vec::new();
    for c in rows {
        let line = match old_of(c.id) {
            Some(o) if o.label == c.label => format!(
                "t{:03} n={:<4} kept     {}",
                c.id, c.size, c.label
            ),
            Some(o) => format!(
                "t{:03} n={:<4} renamed  {}  (was: {}, n={})",
                c.id, c.size, c.label, o.label, o.size
            ),
            None => format!("t{:03} n={:<4} new      {}", c.id, c.size, c.label),
        };
        out.push(line);
    }
    if let Some(p) = prev {
        for o in &p.communities {
            if !file.communities.iter().any(|c| c.id == o.id) {
                out.push(format!("t{:03} n={:<4} retired  {}", o.id, o.size, o.label));
            }
        }
    }
    out
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
            dry_run: false,
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

    /// Minimal 2-pack vault with pre-seeded (near-identical) vectors — one
    /// community, no network, works in `embed`-less builds.
    fn seed_two_pack_vault(vault: &Path) {
        let reader = vault.join(VaultLayout::new().reader_root());
        let cases: [(&str, &str, &str, [f32; 3]); 2] = [
            ("2026-06-01_mem-a", "Agent memory systems", "memory memory context agent", [1.0, 0.05, 0.0]),
            ("2026-06-02_mem-b", "Working memory budget", "memory context budget agent", [1.0, 0.0, 0.05]),
        ];
        for (case_id, title, body, v) in cases {
            write_pack(&reader, case_id, title, body);
            seed_vector(vault, case_id, title, body, v);
        }
    }

    fn test_params() -> ThemeParams {
        ThemeParams {
            k: 2,
            cosine_threshold: 0.5,
            resolution: 1.0,
            seed: 42,
            text_prefix: EMBED_TEXT_PREFIX.to_string(),
            head_chars: EMBED_HEAD_CHARS,
        }
    }

    fn themes_file(labels: LabelsProvenance) -> ThemesFile {
        ThemesFile {
            schema: THEMES_SCHEMA.to_string(),
            model: EMBED_MODEL_ID.to_string(),
            params: test_params(),
            generated_from: "gf".to_string(),
            packs: BTreeMap::new(),
            communities: Vec::new(),
            labels_provenance: labels,
            label_prompt: None,
            next_id: None,
        }
    }

    #[test]
    fn up_to_date_checks_params_and_label_provenance() {
        let keyword = themes_file(LabelsProvenance::Keyword);
        let llm = ThemesFile {
            label_prompt: Some(THEME_LABEL_PROMPT_ID.into()),
            ..themes_file(LabelsProvenance::Llm)
        };
        let p = test_params();
        // LLM names from an OLDER prompt owe a relabel to a live run, but
        // still satisfy a replay run (never downgrade paid names).
        let old_prompt = ThemesFile {
            label_prompt: Some("theme_label/v1".into()),
            ..themes_file(LabelsProvenance::Llm)
        };
        assert!(!up_to_date(&old_prompt, "gf", &p, LabelsProvenance::Llm));
        assert!(!up_to_date(&themes_file(LabelsProvenance::Llm), "gf", &p, LabelsProvenance::Llm));
        assert!(up_to_date(&old_prompt, "gf", &p, LabelsProvenance::Keyword));
        // Identical rerun: fast path in both provenance directions that keep
        // or improve labels.
        assert!(up_to_date(&keyword, "gf", &p, LabelsProvenance::Keyword));
        assert!(up_to_date(&llm, "gf", &p, LabelsProvenance::Llm));
        // A replay run never downgrades paid LLM labels.
        assert!(up_to_date(&llm, "gf", &p, LabelsProvenance::Keyword));
        // The documented replay-then-live relabel: keyword file must NOT
        // satisfy a live request.
        assert!(!up_to_date(&keyword, "gf", &p, LabelsProvenance::Llm));
        // Input-set change → recompute.
        assert!(!up_to_date(&keyword, "other", &p, LabelsProvenance::Keyword));
        // Any clustering-param change → recompute, even with the same packs.
        for mutated in [
            ThemeParams { k: 3, ..test_params() },
            ThemeParams { cosine_threshold: 0.6, ..test_params() },
            ThemeParams { resolution: 1.5, ..test_params() },
            ThemeParams { seed: 7, ..test_params() },
        ] {
            assert!(
                !up_to_date(&keyword, "gf", &mutated, LabelsProvenance::Keyword),
                "{mutated:?} must force a recompute"
            );
        }
    }

    #[test]
    fn param_change_recomputes_without_refresh() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        seed_two_pack_vault(vault);
        run(args(vault)).expect("first run");
        let themes_path = vault.join(".ovp/crystal/themes.json");
        let first = ThemesFile::load(&themes_path).unwrap().unwrap();
        assert_eq!(first.params.resolution, 1.0);
        assert_eq!(first.labels_provenance, LabelsProvenance::Keyword);

        // Same packs, different resolution, NO --refresh: must recompute.
        let mut a = args(vault);
        a.resolution = 2.0;
        run(a).expect("param-change run");
        let second = ThemesFile::load(&themes_path).unwrap().unwrap();
        assert_eq!(
            second.params.resolution, 2.0,
            "reconfigured params must not be swallowed by the freshness fast path"
        );
    }

    #[test]
    fn live_request_over_llm_labeled_file_takes_the_fast_path() {
        // An up-to-date, already-LLM-labeled file satisfies a `--client live`
        // request WITHOUT building a client (run() returns before
        // build_client), so this passes even in anthropic-less test builds.
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        seed_two_pack_vault(vault);
        run(args(vault)).expect("replay run");
        let themes_path = vault.join(".ovp/crystal/themes.json");
        let body = std::fs::read_to_string(&themes_path)
            .unwrap()
            .replace(
                "\"labels_provenance\": \"keyword\"",
                &format!(
                    "\"labels_provenance\": \"llm\",\n  \"label_prompt\": \"{THEME_LABEL_PROMPT_ID}\""
                ),
            );
        std::fs::write(&themes_path, &body).unwrap();

        let mut a = args(vault);
        a.client_kind = ClientKind::Live;
        run(a).expect("live rerun over llm-labeled file is a no-op fast path");
        assert_eq!(std::fs::read_to_string(&themes_path).unwrap(), body, "file untouched");
    }

    #[cfg(not(feature = "anthropic"))]
    #[test]
    fn live_request_over_keyword_file_skips_the_fast_path() {
        // A replay-generated (keyword-labeled) file must NOT satisfy a
        // `--client live` relabel run. In an anthropic-less build the only
        // observable difference is that run() proceeds far enough to try to
        // build the live client — and fails loud instead of reporting
        // "up to date" with keyword labels.
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        seed_two_pack_vault(vault);
        run(args(vault)).expect("replay run");

        let mut a = args(vault);
        a.client_kind = ClientKind::Live;
        let err = run(a).expect_err("live relabel must not take the keyword fast path");
        assert!(format!("{err:?}").contains("--client live"), "got {err:?}");
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

    #[test]
    fn missing_reader_root_is_a_graceful_noop() {
        // Fresh vault: 40-Resources/Reader does not exist yet. The documented
        // degradation contract is "print why and exit 0", not an IO error.
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        run(args(vault)).expect("missing reader root must be a no-op, not an error");
        assert!(!vault.join(".ovp/crystal/themes.json").exists());
    }

    #[test]
    fn failed_reader_attempts_are_excluded_from_theme_inputs() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        seed_two_pack_vault(vault);
        let reader = vault.join(VaultLayout::new().reader_root());
        // A failed attempt dir: run-status.json with zero cards (the index's
        // exclusion rule). No cached vector is seeded for it — if it leaked
        // into the inputs, this run could not resolve vectors at all.
        let failed = reader.join("2026-06-03_failed");
        std::fs::create_dir_all(&failed).unwrap();
        std::fs::write(
            failed.join("run-status.json"),
            r#"{"source": "Broken source", "cards": 0, "parse_error": "model reply unusable"}"#,
        )
        .unwrap();
        std::fs::write(failed.join("reader.md"), "# Broken source\n\nstub\n").unwrap();

        run(args(vault)).expect("themes run");
        let file = ThemesFile::load(&vault.join(".ovp/crystal/themes.json"))
            .unwrap()
            .expect("themes written from the two product packs");
        assert_eq!(file.packs.len(), 2, "{:?}", file.packs);
        assert!(
            !file.packs.contains_key("2026-06-03_failed"),
            "failed attempt must not shape communities: {:?}",
            file.packs
        );
    }

    fn set<'a>(xs: &[&'a str]) -> std::collections::BTreeSet<&'a str> {
        xs.iter().copied().collect()
    }

    #[test]
    fn spread_pick_covers_core_and_edges() {
        assert_eq!(spread_pick(5, 12), vec![0, 1, 2, 3, 4]);
        let picks = spread_pick(129, 12);
        assert_eq!(picks.len(), 12);
        assert_eq!((picks[0], picks[11]), (0, 128));
        assert!(picks.windows(2).all(|w| w[0] < w[1]));
    }

    #[test]
    fn carry_ids_inherits_by_overlap_and_never_reuses_a_retired_id() {
        let prev = BTreeMap::from([
            (0, set(&["a", "b", "c"])),
            (1, set(&["d", "e"])),
            (5, set(&["x", "y"])), // retired: nothing overlaps it any more
        ]);
        let new = vec![
            set(&["d", "e", "f"]), // continues t001
            set(&["a", "b"]),      // continues t000 (split: bigger share)
            set(&["c", "q", "r", "s"]), // the other half of t000: too little overlap
            set(&["z"]),
        ];
        let carry = carry_ids(&new, &prev, 0);
        assert_eq!(carry[0].id, 1);
        assert_eq!(carry[1].id, 0);
        assert!((carry[1].prev.unwrap().1 - 2.0 / 3.0).abs() < 1e-9);
        // Fresh ids start past EVERY previous id, including retired t005.
        assert_eq!((carry[2].id, carry[2].prev), (6, None));
        assert_eq!((carry[3].id, carry[3].prev), (7, None));
        // First run: no previous file → 0..n in clustering order.
        let first = carry_ids(&new, &BTreeMap::new(), 0);
        assert_eq!(first.iter().map(|c| c.id).collect::<Vec<_>>(), vec![0, 1, 2, 3]);
        // The highest id (t005) retired in an earlier run and is gone from
        // the file; the persisted high-water mark still keeps it unused.
        let survivors = BTreeMap::from([(0, set(&["a", "b", "c"]))]);
        let next = carry_ids(&[set(&["a", "b", "c"]), set(&["z"])], &survivors, 6);
        assert_eq!((next[0].id, next[1].id), (0, 6));
    }

    #[test]
    fn a_legacy_file_keeps_its_highest_id_retired() {
        let mut legacy = themes_file(LabelsProvenance::Llm);
        let c = |id| ThemeCommunity {
            id,
            label: String::new(),
            label_zh: String::new(),
            keywords: vec![],
            size: 1,
        };
        legacy.communities = vec![c(0), c(1)];
        assert_eq!(id_floor(&legacy), 2, "no next_id: past every listed id");
        legacy.next_id = Some(7);
        assert_eq!(id_floor(&legacy), 7, "the persisted mark wins when higher");
        // t001 retires in this run and nothing new appears: the written mark
        // must still be 2, so a later run cannot reuse id 1.
        legacy.next_id = None;
        let floor = id_floor(&legacy);
        let carry = carry_ids(&[set(&["a"])], &BTreeMap::from([(0, set(&["a"])), (1, set(&["b"]))]), floor);
        let written = carry.iter().map(|c| c.id + 1).max().unwrap_or(0).max(floor);
        assert_eq!(written, 2);
    }

    #[test]
    fn a_stable_theme_keeps_its_name_only_under_the_current_prompt() {
        let mut prev = themes_file(LabelsProvenance::Llm);
        prev.label_prompt = Some(THEME_LABEL_PROMPT_ID.into());
        prev.communities = vec![ThemeCommunity {
            id: 3,
            label: "Agent memory".into(),
            label_zh: "智能体记忆".into(),
            keywords: vec![],
            size: 10,
        }];
        let stable = Carry { id: 3, prev: Some((3, 0.9)) };
        let moved = Carry { id: 3, prev: Some((3, 0.4)) };
        let llm = LabelsProvenance::Llm;
        assert_eq!(
            reused_name(Some(&prev), llm, &stable),
            Some(("Agent memory".into(), "智能体记忆".into()))
        );
        assert_eq!(reused_name(Some(&prev), llm, &moved), None, "membership moved → rename");
        assert_eq!(reused_name(Some(&prev), LabelsProvenance::Keyword, &stable), None);
        let mut old_prompt = prev.clone();
        old_prompt.label_prompt = Some("theme_label/v1".into());
        assert_eq!(reused_name(Some(&old_prompt), llm, &stable), None, "old prompt → rename once");
        assert_eq!(reused_name(None, llm, &stable), None);
    }

    #[test]
    fn change_lines_report_kept_renamed_new_and_retired() {
        let c = |id: i64, label: &str, size: usize| ThemeCommunity {
            id,
            label: label.into(),
            label_zh: label.into(),
            keywords: vec![],
            size,
        };
        let mut prev = themes_file(LabelsProvenance::Llm);
        prev.communities = vec![c(0, "Memory", 10), c(5, "AI coding assistant stock data toolkits", 129), c(9, "Gone", 3)];
        let mut next = themes_file(LabelsProvenance::Llm);
        next.communities = vec![c(0, "Memory", 12), c(5, "Open-source AI developer tools", 140), c(10, "Fresh", 4)];
        let lines = change_lines(Some(&prev), &next);
        assert!(lines[0].starts_with("t005 n=140") && lines[0].contains("renamed"), "{lines:?}");
        assert!(lines[0].contains("(was: AI coding assistant stock data toolkits, n=129)"));
        assert!(lines[1].contains("kept     Memory"));
        assert!(lines[2].contains("new      Fresh"));
        assert!(lines[3].starts_with("t009") && lines[3].contains("retired  Gone"));
    }

    #[test]
    fn rerun_with_a_new_pack_keeps_theme_ids() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        let reader = vault.join(VaultLayout::new().reader_root());
        let cases = [
            ("2026-06-01_mem-a", "Agent memory systems", "memory memory context agent", [1.0, 0.05, 0.0]),
            ("2026-06-02_mem-b", "Working memory budget", "memory context budget agent", [1.0, 0.0, 0.05]),
            ("2026-06-03_quant-a", "Polymarket order book", "market quant order polymarket", [0.05, 1.0, 0.0]),
            ("2026-06-04_quant-b", "Prediction market math", "market quant math polymarket", [0.0, 1.0, 0.05]),
        ];
        for (case_id, title, body, v) in cases {
            write_pack(&reader, case_id, title, body);
            seed_vector(vault, case_id, title, body, v);
        }
        run(args(vault)).unwrap();
        let path = vault.join(".ovp/crystal/themes.json");
        let first = ThemesFile::load(&path).unwrap().unwrap();
        let quant_id = first.packs["2026-06-03_quant-a"];
        assert_eq!(first.next_id, Some(2), "high-water mark persisted");

        // A third market doc arrives; dry-run writes nothing.
        let (id, title, body) = ("2026-06-05_quant-c", "Market making", "market quant polymarket spread");
        write_pack(&reader, id, title, body);
        seed_vector(vault, id, title, body, [0.02, 1.0, 0.02]);
        let mut dry = args(vault);
        dry.dry_run = true;
        run(dry).unwrap();
        assert_eq!(ThemesFile::load(&path).unwrap().unwrap(), first, "dry run must not write");

        run(args(vault)).unwrap();
        let second = ThemesFile::load(&path).unwrap().unwrap();
        assert_eq!(second.packs["2026-06-03_quant-a"], quant_id, "theme id survives the recluster");
        assert_eq!(second.packs["2026-06-05_quant-c"], quant_id);
        assert_eq!(second.packs["2026-06-01_mem-a"], first.packs["2026-06-01_mem-a"]);
        assert_eq!(second.label_prompt, None, "replay labels are keyword labels");
    }

    #[test]
    fn label_claims_use_the_human_patched_wording() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        let store = vault.join(".ovp/crystal");
        std::fs::create_dir_all(&store).unwrap();
        let rec = |id: &str, text: &str, cases: &[&str]| {
            serde_json::json!({"op": "write", "record": {
                "claim_key": format!("k-{id}"), "claim_id": id, "claim": text, "theme": "t",
                "source_cases": cases, "citations": [], "provenance_score": 1.0,
                "provenance_class": "durable", "strength": "supported",
                "strength_rationale": "ok", "final_class": "durable",
                "run_id": "r", "status": "active"}})
            .to_string()
        };
        std::fs::write(
            store.join("ledger.jsonl"),
            format!(
                "{}\n{}\n",
                rec("c01", "Original wording.", &["case-a", "case-b"]),
                // tie between t003 and t001 → the lower id, as the portal shows it
                rec("c02", "Tied claim.", &["case-c", "case-a"]),
            ),
        )
        .unwrap();
        let patch = ovp_domain::crystal::HumanPatchRecord::new_apply(
            "c01",
            None,
            "Original wording.",
            "Corrected wording.",
            None,
            None,
            "operator:test",
            "fix",
            Some("2026-06-09T10:00:00Z".into()),
        );
        ovp_domain::crystal::append_patch_record(&store.join("patches.jsonl"), &patch).unwrap();
        let community_of = BTreeMap::from([("case-a", 3), ("case-b", 3), ("case-c", 1)]);
        let got = claims_by_community(vault, &VaultLayout::new(), &community_of);
        assert_eq!(got[&3], vec!["Corrected wording.".to_string()]);
        assert_eq!(got[&1], vec!["Tied claim.".to_string()]);
    }
}
