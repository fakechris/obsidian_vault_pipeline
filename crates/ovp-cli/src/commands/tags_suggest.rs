//! `tags-suggest` — deterministic tag-curation proposals over the embedding
//! layer. Two outputs, both projections, neither ever auto-applied:
//!
//! 1. **Merge candidates** (`.ovp/tags/proposals.md`): every canonical tag is
//!    embedded as "tag name + sample titles of sources carrying it"; pairs
//!    above a cosine threshold become alias proposals (canonical = the
//!    higher-count member). The operator reviews the report and pastes
//!    accepted lines into `.ovp/tags/aliases.toml` — the pipeline proposes,
//!    the operator disposes (same contract as crystal-review).
//!
//! 2. **Backfill** (`.ovp/tags/inferred.json`): sources with NO operator tags
//!    get tags voted by their k nearest tagged neighbors in pack-embedding
//!    space (similarity-weighted vote, support + share thresholds). Reuses
//!    the SAME pack vectors `crystal-themes` caches, so a themed vault runs
//!    this with zero new embeddings. The index attaches these as
//!    `tags_inferred`, never mixed into operator tags.
//!
//! No LLM anywhere. Degradation contract mirrors crystal-themes: missing
//! embed feature/model with a cold cache → explain and exit 0.

use std::collections::BTreeMap;
use std::path::PathBuf;

use ovp_domain::tags::{InferredTag, TAGS_INFERRED_SCHEMA, TagsInferredFile};
use ovp_domain::vault_layout::VaultLayout;
use ovp_embed::knn::cosine;
use ovp_embed::{EMBED_MODEL_ID, document_text};
use ovp_index::read_index;

use crate::CliError;
use crate::commands::crystal_themes::{ThemeDoc, collect_docs, resolve_vectors};

/// Sample titles embedded alongside a tag name (the short-tag disambiguation
/// trick: "rust" alone is ambiguous; "rust + 3 article titles" is not).
const TAG_SAMPLE_TITLES: usize = 3;
/// Merge pairs at or above this cosine are marked STRONG in the report.
const STRONG_MERGE: f64 = 0.90;
/// Cap on reported merge pairs; the count of anything beyond it is logged
/// (no silent truncation).
const MAX_MERGE_PROPOSALS: usize = 100;

pub struct TagsSuggestArgs {
    pub vault_root: PathBuf,
    /// Cosine floor for a merge proposal to enter the report.
    pub merge_threshold: f64,
    /// Neighbors consulted per untagged source.
    pub knn: usize,
    /// Minimum share of neighbor similarity weight a tag needs (0..1).
    pub vote_threshold: f64,
    /// Minimum number of neighbors carrying the tag.
    pub min_support: usize,
    /// Cap on inferred tags per source.
    pub max_tags: usize,
}

impl Default for TagsSuggestArgs {
    fn default() -> Self {
        Self {
            vault_root: PathBuf::new(),
            merge_threshold: 0.75,
            knn: 10,
            vote_threshold: 0.35,
            min_support: 2,
            max_tags: 5,
        }
    }
}

pub(crate) use ovp_domain::tags::toml_basic_string;

/// One tag's neighbor vote on one source (pure, unit-tested): neighbors are
/// `(similarity, tags)` pairs, already the k nearest. A tag wins when its
/// similarity-weighted share ≥ `vote_threshold` AND ≥ `min_support` distinct
/// neighbors carry it. Ties broken by tag name for determinism.
pub(crate) fn vote_tags(
    neighbors: &[(f64, &[String])],
    vote_threshold: f64,
    min_support: usize,
    max_tags: usize,
) -> Vec<InferredTag> {
    let total: f64 = neighbors.iter().map(|(s, _)| s.max(0.0)).sum();
    if total <= 0.0 {
        return Vec::new();
    }
    let mut weight: BTreeMap<&str, (f64, usize)> = BTreeMap::new();
    for (sim, tags) in neighbors {
        for tag in *tags {
            let e = weight.entry(tag.as_str()).or_insert((0.0, 0));
            e.0 += sim.max(0.0);
            e.1 += 1;
        }
    }
    let mut out: Vec<InferredTag> = weight
        .into_iter()
        .filter_map(|(tag, (w, support))| {
            let score = w / total;
            (score >= vote_threshold && support >= min_support).then(|| InferredTag {
                tag: tag.to_string(),
                score: (score * 1000.0).round() / 1000.0,
                support,
                method: "knn".into(),
            })
        })
        .collect();
    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.tag.cmp(&b.tag))
    });
    out.truncate(max_tags);
    out
}

/// One proposed merge, evidence attached. Serialized as-is into
/// `proposals.json` (the curation inbox's input).
#[derive(Debug, PartialEq, serde::Serialize)]
pub(crate) struct MergeProposal {
    /// Lower-count member — the proposed alias.
    pub(crate) alias: String,
    pub(crate) alias_count: usize,
    /// Higher-count member — the proposed canonical.
    pub(crate) canonical: String,
    pub(crate) canonical_count: usize,
    /// NAME-only embedding similarity — the score that decides candidacy.
    /// Two tags are merge candidates because their NAMES mean the same
    /// thing (variants, 中英 pairs), never because their articles do.
    pub(crate) cosine: f64,
    /// Content-context similarity (name + sample titles) — display-only
    /// evidence. High context + low name = related topics, not variants
    /// (the operator-confirmed false-positive mode this field exposes).
    pub(crate) context_cosine: f64,
}

/// Corroboration gate for a name-cosine candidate. The paraphrase embedding
/// model degenerates on short opaque tech tokens (ios/ida/ai/ui/git/go
/// cluster together at cosine >0.9), so name similarity alone is not enough:
/// a pair must ALSO show lexical kinship (shared hyphen-token, containment,
/// long common prefix), or be a cross-script pair (中↔英 — the case the
/// multilingual model is actually good at) with minimal context support.
/// Deliberately NO bare context fallback: high context similarity between
/// unrelated names is precisely the related-topics-not-variants failure
/// mode this gate exists to kill (git/go at ctx 0.54 would sneak back in).
///
/// `sim` is the NAME cosine. Cross-script (中↔英) pairs are the one case the
/// multilingual model is genuinely good at, BUT only for real translations,
/// which it places VERY close: on the real vault every true pair scores
/// ≥0.926 (训练→training 0.979, 预测市场→prediction-market 0.926) while the
/// `算命`-style garbage cluster of degenerate short-token vectors tops out at
/// 0.906 — a clean gap. So a cross-script pair needs both a high name cosine
/// AND minimal context support.
pub(crate) fn name_evidence(a: &str, b: &str, sim: f64, ctx: f64) -> bool {
    fn tokens(s: &str) -> Vec<&str> {
        s.split('-').filter(|t| t.len() >= 2).collect()
    }
    let shared_token = tokens(a).iter().any(|t| tokens(b).contains(t));
    let containment = a.contains(b) || b.contains(a);
    let common_prefix = a
        .chars()
        .zip(b.chars())
        .take_while(|(x, y)| x == y)
        .count()
        >= 5;
    let has_cjk = |s: &str| s.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c));
    let cross_script = has_cjk(a) != has_cjk(b);
    shared_token || containment || common_prefix || (cross_script && sim >= 0.92 && ctx >= 0.24)
}

/// Two tags on mostly the SAME sources are co-occurring (related topics on
/// one article), not spelling variants — true synonyms almost never co-occur
/// on one item, because nobody tags an article `agent` AND `agents`. Their
/// embed texts also share sample titles, which inflates cosine to ~1.0, so
/// without this suppression the report leads with same-article noise.
/// Overlap = |A∩B| / min(|A|,|B|); at or above this the pair is suppressed.
const CO_OCCURRENCE_OVERLAP: f64 = 0.5;

/// Pairwise merge candidates over per-tag NAME vectors (pure, unit-tested).
/// `tags` = (name, source indices carrying it). Candidacy is decided by
/// name-embedding similarity alone — the operator-confirmed failure mode of
/// scoring on titles is that a few similar ARTICLES make two unrelated tag
/// names look synonymous. `context_vectors` (name + sample titles) only
/// annotates each proposal as evidence. Canonical = higher count; ties by
/// name (lexicographically smaller wins). Returns (proposals, suppressed
/// co-occurrence pair count).
pub(crate) fn merge_proposals(
    tags: &[(String, Vec<usize>)],
    vectors: &[Vec<f32>],
    context_vectors: &[Vec<f32>],
    threshold: f64,
) -> (Vec<MergeProposal>, usize) {
    assert_eq!(
        tags.len(),
        vectors.len(),
        "merge_proposals: one name vector per tag"
    );
    assert_eq!(
        tags.len(),
        context_vectors.len(),
        "merge_proposals: one context vector per tag"
    );
    let sets: Vec<std::collections::BTreeSet<usize>> = tags
        .iter()
        .map(|(_, srcs)| srcs.iter().copied().collect())
        .collect();
    let mut out = Vec::new();
    let mut suppressed = 0usize;
    for i in 0..tags.len() {
        for j in (i + 1)..tags.len() {
            let sim = cosine(&vectors[i], &vectors[j]);
            if sim < threshold {
                continue;
            }
            let inter = sets[i].intersection(&sets[j]).count();
            let min = sets[i].len().min(sets[j].len()).max(1);
            if inter as f64 / min as f64 >= CO_OCCURRENCE_OVERLAP {
                suppressed += 1;
                continue;
            }
            let ctx = cosine(&context_vectors[i], &context_vectors[j]);
            if !name_evidence(&tags[i].0, &tags[j].0, sim, ctx) {
                continue;
            }
            let (ni, nj) = (tags[i].1.len(), tags[j].1.len());
            let (a, c) = if ni < nj || (ni == nj && tags[i].0 > tags[j].0) {
                (i, j)
            } else {
                (j, i)
            };
            out.push(MergeProposal {
                alias: tags[a].0.clone(),
                alias_count: tags[a].1.len(),
                canonical: tags[c].0.clone(),
                canonical_count: tags[c].1.len(),
                cosine: (sim * 1000.0).round() / 1000.0,
                context_cosine: (ctx * 1000.0).round() / 1000.0,
            });
        }
    }
    out.sort_by(|a, b| {
        b.cosine
            .partial_cmp(&a.cosine)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.alias.cmp(&b.alias))
    });
    (out, suppressed)
}

/// Schmitz subsumption thresholds (T3, docs §4): a specific tag implies a
/// generic when almost every specific-source also carries the generic, but
/// not vice versa (a hierarchy signal, not synonymy).
const IMPLY_FORWARD: f64 = 0.7; // P(generic | specific) ≥
const IMPLY_REVERSE: f64 = 0.3; // P(specific | generic) ≤
const IMPLY_MIN_SPECIFIC: usize = 2; // ignore singleton specifics (noise)
/// A real `specific ⇒ generic` also has RELATED NAMES (agentic-engineering /
/// agent, creative-writing / writing, asr / voice). Pure co-occurrence with
/// no name relation is base-rate noise — in an all-AI corpus every rare tag
/// co-occurs with `ai`, but `business` is not a KIND of `ai`. Gate on the
/// same name embeddings the merge candidates use.
const IMPLY_NAME_COSINE: f64 = 0.35;

/// One proposed `specific ⇒ generic` implication, evidence attached.
#[derive(Debug, PartialEq, serde::Serialize)]
pub(crate) struct ImplicationProposal {
    pub(crate) specific: String,
    pub(crate) specific_count: usize,
    pub(crate) generic: String,
    pub(crate) generic_count: usize,
    /// P(generic | specific) — how reliably the specific rolls up.
    pub(crate) forward: f64,
    /// P(specific | generic) — low = the generic is genuinely broader.
    pub(crate) reverse: f64,
    /// Name-embedding cosine — the semantic-relatedness evidence; the list is
    /// sorted by it so the true is-a-kind-of pairs float above base-rate
    /// co-occurrence (`business ⇒ ai` sinks below `agentic-engineering ⇒ agent`).
    pub(crate) name_cosine: f64,
}

/// Count the intersection of two ASCENDING source-index lists (the vocab
/// pushes indices in source order, so they're pre-sorted).
fn sorted_intersection(a: &[usize], b: &[usize]) -> usize {
    let (mut i, mut j, mut n) = (0, 0, 0);
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => {
                n += 1;
                i += 1;
                j += 1;
            }
        }
    }
    n
}

/// Implication candidates over `tags` = (name, ascending source indices),
/// gated by co-occurrence subsumption AND name-embedding relatedness
/// (`name_vectors[i]` aligns with `tags[i]`). Pure, unit-tested. Proposes
/// `specific ⇒ generic` when the specific is strictly rarer, forward ≥
/// [`IMPLY_FORWARD`], reverse ≤ [`IMPLY_REVERSE`], names cosine ≥
/// [`IMPLY_NAME_COSINE`].
pub(crate) fn implication_proposals(
    tags: &[(String, Vec<usize>)],
    name_vectors: &[Vec<f32>],
) -> Vec<ImplicationProposal> {
    let mut out = Vec::new();
    for i in 0..tags.len() {
        let si = tags[i].1.len();
        if si < IMPLY_MIN_SPECIFIC {
            continue;
        }
        for (j, (jname, jsrcs)) in tags.iter().enumerate() {
            let sj = jsrcs.len();
            // specific (i) must be strictly rarer than the generic (j).
            if *jname == tags[i].0 || si >= sj {
                continue;
            }
            let both = sorted_intersection(&tags[i].1, jsrcs);
            if both == 0 {
                continue;
            }
            let forward = both as f64 / si as f64;
            let reverse = both as f64 / sj as f64;
            let name_cos = cosine(&name_vectors[i], &name_vectors[j]);
            if forward >= IMPLY_FORWARD && reverse <= IMPLY_REVERSE && name_cos >= IMPLY_NAME_COSINE
            {
                out.push(ImplicationProposal {
                    specific: tags[i].0.clone(),
                    specific_count: si,
                    generic: jname.clone(),
                    generic_count: sj,
                    forward: (forward * 1000.0).round() / 1000.0,
                    reverse: (reverse * 1000.0).round() / 1000.0,
                    name_cosine: (name_cos * 1000.0).round() / 1000.0,
                });
            }
        }
    }
    // Strongest name relation first (base-rate co-occurrence sinks), then
    // rarest specific, then name for determinism.
    out.sort_by(|a, b| {
        b.name_cosine
            .partial_cmp(&a.name_cosine)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.specific_count.cmp(&b.specific_count))
            .then_with(|| a.specific.cmp(&b.specific))
            .then_with(|| a.generic.cmp(&b.generic))
    });
    out
}

pub fn run(args: TagsSuggestArgs) -> Result<(), CliError> {
    if !(-1.0..=1.0).contains(&args.merge_threshold) {
        return Err(CliError::Io(format!(
            "tags-suggest: --merge-threshold is a cosine, must be in [-1, 1] (got {})",
            args.merge_threshold
        )));
    }
    if !(0.0..=1.0).contains(&args.vote_threshold) {
        return Err(CliError::Io(format!(
            "tags-suggest: --vote-threshold is a share, must be in [0, 1] (got {})",
            args.vote_threshold
        )));
    }
    let layout = VaultLayout::new();
    let model = read_index(&args.vault_root).map_err(|e| {
        CliError::Io(format!(
            "tags-suggest: {e} — run `ovp2 index` first (the tag vocabulary and \
             source↔pack joins come from the read model)"
        ))
    })?;
    let reader_root = args.vault_root.join(layout.reader_root());
    let embed_cache_dir = args.vault_root.join(".ovp/cache/embeddings");

    // ---- Join sources ↔ pack docs (case_id = pack_dir basename) ----
    let docs = collect_docs(&reader_root)?;
    let case_of = |pack_dir: &str| ovp_domain::vault_layout::pack_case_id(pack_dir).to_string();
    let mut doc_idx: BTreeMap<String, usize> = BTreeMap::new();
    for (i, d) in docs.iter().enumerate() {
        doc_idx.insert(d.case_id.clone(), i);
    }
    // (doc index, sha256, tags) for every source joined to a pack.
    let mut tagged: Vec<(usize, &str, &[String])> = Vec::new();
    let mut untagged: Vec<(usize, &str)> = Vec::new();
    for s in &model.sources {
        let Some(idx) = s
            .pack_dir
            .as_deref()
            .and_then(|p| doc_idx.get(&case_of(p)))
        else {
            continue;
        };
        if s.tags.is_empty() {
            untagged.push((*idx, s.sha256.as_str()));
        } else {
            tagged.push((*idx, s.sha256.as_str(), s.tags.as_slice()));
        }
    }
    if tagged.is_empty() {
        println!(
            "tags-suggest: no tagged sources in the index — nothing to learn from. \
             Tag some sources (pinboard or frontmatter), rebuild the index, retry."
        );
        return Ok(());
    }
    println!(
        "tags-suggest: {} tagged / {} untagged source(s) joined to packs",
        tagged.len(),
        untagged.len()
    );

    // ---- Pack vectors (warm from crystal-themes; embeds only the misses) ----
    let Some(vectors) = resolve_vectors(&docs, &embed_cache_dir)? else {
        return Ok(()); // graceful skip, reason already printed
    };

    // ---- Backfill: kNN vote for every untagged source ----
    let mut entries: BTreeMap<String, Vec<InferredTag>> = BTreeMap::new();
    let mut sims: Vec<(f64, &[String])> = Vec::with_capacity(tagged.len());
    for (idx, sha) in &untagged {
        sims.clear();
        sims.extend(
            tagged
                .iter()
                .map(|(t_idx, _, tags)| (cosine(&vectors[*idx], &vectors[*t_idx]), *tags)),
        );
        sims.sort_by(|(sa, _), (sb, _)| sb.partial_cmp(sa).unwrap_or(std::cmp::Ordering::Equal));
        sims.truncate(args.knn);
        let voted = vote_tags(&sims, args.vote_threshold, args.min_support, args.max_tags);
        if !voted.is_empty() {
            entries.insert((*sha).to_string(), voted);
        }
    }
    let covered = entries.len();
    // Merge, don't clobber: bootstrap-method entries (community/llm) survive
    // for sources where kNN produced no vote — otherwise a P1 vault's first
    // `tags-suggest` run (once a few operator tags exist) would erase every
    // bootstrap tag that fails the vote thresholds. A fresh kNN vote for a
    // sha replaces its old entry of any method.
    if let Some(existing) = TagsInferredFile::load(&args.vault_root).map_err(CliError::Io)? {
        for (sha, tags) in existing.entries {
            let bootstrap = tags
                .iter()
                .any(|t| !t.method.is_empty() && t.method != "knn");
            if bootstrap {
                entries.entry(sha).or_insert(tags);
            }
        }
    }
    let inferred = TagsInferredFile {
        schema: TAGS_INFERRED_SCHEMA.into(),
        model: EMBED_MODEL_ID.into(),
        params: BTreeMap::from([
            ("knn".into(), args.knn as f64),
            ("vote_threshold".into(), args.vote_threshold),
            ("min_support".into(), args.min_support as f64),
            ("max_tags".into(), args.max_tags as f64),
        ]),
        entries,
    };
    // NOT saved yet — both projections persist together at the end, so a
    // graceful embedding skip below can never leave inferred.json from this
    // run beside a proposals.md from an older one.

    // ---- Merge candidates over the tag vocabulary ----
    // Vocabulary + carrying-source indices + up-to-3 sample titles per tag.
    let mut vocab: BTreeMap<&str, (Vec<usize>, Vec<&str>)> = BTreeMap::new();
    for (src_idx, s) in model.sources.iter().enumerate() {
        for t in &s.tags {
            let e = vocab.entry(t.as_str()).or_default();
            e.0.push(src_idx);
            if e.1.len() < TAG_SAMPLE_TITLES
                && let Some(title) = s.title.as_deref()
            {
                e.1.push(title);
            }
        }
    }
    // NAME-only vectors decide candidacy (same `tagname:` texts the
    // bootstrap dedup embeds — one shared cache population); the
    // name+titles context vectors are display-only evidence.
    let name_docs: Vec<ThemeDoc> = vocab
        .keys()
        .map(|tag| {
            let text = document_text(tag, "", 0);
            let sha = ovp_embed::cache::text_sha256(&text);
            ThemeDoc {
                case_id: format!("tagname:{tag}"),
                title: (*tag).to_string(),
                text,
                sha,
            }
        })
        .collect();
    let tag_docs: Vec<ThemeDoc> = vocab
        .iter()
        .map(|(tag, (_, titles))| {
            let text = document_text(tag, &titles.join(" | "), 1500);
            let sha = ovp_embed::cache::text_sha256(&text);
            ThemeDoc {
                case_id: format!("tag:{tag}"),
                title: (*tag).to_string(),
                text,
                sha,
            }
        })
        .collect();
    let (Some(name_vectors), Some(tag_vectors)) = (
        resolve_vectors(&name_docs, &embed_cache_dir)?,
        resolve_vectors(&tag_docs, &embed_cache_dir)?,
    ) else {
        println!(
            "tags-suggest: nothing written — tag-vocabulary embeddings unavailable \
             (both projections persist together or not at all)."
        );
        return Ok(());
    };
    let inferred_path = inferred.save(&args.vault_root).map_err(CliError::Io)?;
    println!(
        "  backfill: {covered}/{} untagged source(s) received inferred tags → {inferred_path}",
        untagged.len()
    );
    let counts: Vec<(String, Vec<usize>)> = vocab
        .iter()
        .map(|(tag, (srcs, _))| ((*tag).to_string(), srcs.clone()))
        .collect();
    let (mut proposals, suppressed) =
        merge_proposals(&counts, &name_vectors, &tag_vectors, args.merge_threshold);
    // Rejected-in-the-UI pairs never resurface (decisions.toml `ignore`).
    let decisions = ovp_domain::tags::TagDecisions::load(&args.vault_root).map_err(CliError::Io)?;
    proposals.retain(|p| !decisions.is_ignored(&p.alias, &p.canonical));
    let dropped = proposals.len().saturating_sub(MAX_MERGE_PROPOSALS);
    proposals.truncate(MAX_MERGE_PROPOSALS);

    // Machine-readable twin for the curation inbox, with per-side sample
    // titles — a high-cosine pair can be related-topics rather than
    // variants, and names+cosine alone don't let the operator tell.
    let titles_of = |tag: &str| -> Vec<&str> {
        vocab.get(tag).map(|(_, t)| t.clone()).unwrap_or_default()
    };
    let proposals_with_titles: Vec<serde_json::Value> = proposals
        .iter()
        .map(|p| {
            serde_json::json!({
                "alias": p.alias,
                "alias_count": p.alias_count,
                "alias_titles": titles_of(&p.alias),
                "canonical": p.canonical,
                "canonical_count": p.canonical_count,
                "canonical_titles": titles_of(&p.canonical),
                "cosine": p.cosine,
                "context_cosine": p.context_cosine,
            })
        })
        .collect();
    // Implication candidates (specific ⇒ generic) over the same source-index
    // sets. Drop pairs already implied (operator + UI table) or UI-rejected.
    let aliases = ovp_domain::tags::TagAliases::load(&args.vault_root).map_err(CliError::Io)?;
    let mut implications = implication_proposals(&counts, &name_vectors);
    implications.retain(|p| {
        !decisions.is_ignored(&p.specific, &p.generic)
            && !aliases.implied_generics(&p.specific).contains(&p.generic)
    });
    implications.truncate(MAX_MERGE_PROPOSALS);
    let implications_json: Vec<serde_json::Value> = implications
        .iter()
        .map(|p| {
            serde_json::json!({
                "specific": p.specific,
                "specific_count": p.specific_count,
                "generic": p.generic,
                "generic_count": p.generic_count,
                "forward": p.forward,
                "reverse": p.reverse,
                "name_cosine": p.name_cosine,
            })
        })
        .collect();
    let proposals_json = serde_json::json!({
        "schema": "ovp.tags-proposals/v1",
        "date": model.date,
        "merge_threshold": args.merge_threshold,
        "suppressed_co_occurrence": suppressed,
        "proposals": proposals_with_titles,
        "implications": implications_json,
    });
    let json_path = args.vault_root.join(layout.tags_proposals_json_file());
    // Atomic write: the live portal polls this file — a truncated read (or a
    // silently-blank serialize failure) would make the inbox look empty.
    let json_body = serde_json::to_string_pretty(&proposals_json)
        .map_err(|e| CliError::Io(format!("serializing proposals: {e}")))?;
    ovp_domain::tags::write_atomic(&json_path, &(json_body + "\n")).map_err(CliError::Io)?;

    // ---- Human-review report ----
    let mut report = String::new();
    report.push_str(&format!(
        "# Tag curation proposals — {}\n\nGenerated by `ovp2 tags-suggest` \
         (model {EMBED_MODEL_ID}, merge cosine ≥ {}, kNN {} / vote ≥ {} / support ≥ {}).\n\
         NOTHING here is applied automatically: review, then paste accepted lines into\n\
         `.ovp/tags/aliases.toml` and rebuild with `ovp2 index`.\n\n",
        model.date, args.merge_threshold, args.knn, args.vote_threshold, args.min_support
    ));
    report.push_str(&format!(
        "## Merge candidates ({}{}; {suppressed} co-occurrence pair(s) suppressed — \
         tags sharing most sources are related topics, not variants)\n\n\
         | name cos | context cos | alias (count) | → canonical (count) |\n|---|---|---|---|\n",
        proposals.len(),
        if dropped > 0 {
            format!(", {dropped} more below the display cap")
        } else {
            String::new()
        }
    ));
    for p in &proposals {
        report.push_str(&format!(
            "| {:.3}{} | {:.3} | {} ({}) | {} ({}) |\n",
            p.cosine,
            if p.cosine >= STRONG_MERGE { " ★" } else { "" },
            p.context_cosine,
            p.alias,
            p.alias_count,
            p.canonical,
            p.canonical_count
        ));
    }
    report.push_str("\n### Paste-ready block (edit before use)\n\n```toml\n[aliases]\n");
    for p in &proposals {
        report.push_str(&format!(
            "# cosine {:.3}\n{} = {}\n",
            p.cosine,
            toml_basic_string(&p.alias),
            toml_basic_string(&p.canonical)
        ));
    }
    report.push_str("```\n");
    report.push_str(&format!(
        "\n## Implication candidates ({}) — `specific ⇒ generic` (specific rolls \
         up under generic; accept in the /tags inbox or paste below)\n\n\
         | name cos | forward | reverse | specific (count) | ⇒ generic (count) |\n\
         |---|---|---|---|---|\n",
        implications.len()
    ));
    for p in &implications {
        report.push_str(&format!(
            "| {:.2} | {:.2} | {:.2} | {} ({}) | {} ({}) |\n",
            p.name_cosine, p.forward, p.reverse, p.specific, p.specific_count, p.generic, p.generic_count
        ));
    }
    if !implications.is_empty() {
        report.push_str("\n```toml\n[implications]\n");
        // Group generics per specific for the paste-ready block.
        let mut by_specific: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
        for p in &implications {
            by_specific.entry(&p.specific).or_default().push(&p.generic);
        }
        for (s, generics) in by_specific {
            let list: Vec<String> = generics.iter().map(|g| toml_basic_string(g)).collect();
            report.push_str(&format!("{} = [{}]\n", toml_basic_string(s), list.join(", ")));
        }
        report.push_str("```\n");
    }
    report.push_str(&format!(
        "\n## Backfill\n\n{covered}/{} untagged sources received inferred tags \
         (`.ovp/tags/inferred.json`); they surface as `tags_inferred`, never as \
         operator tags. Regenerate anytime; delete the file to turn backfill off.\n",
        untagged.len()
    ));
    let report_path = args.vault_root.join(layout.tags_proposals_file());
    if let Some(parent) = report_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| CliError::Io(format!("creating {}: {e}", parent.display())))?;
    }
    std::fs::write(&report_path, report)
        .map_err(|e| CliError::Io(format!("writing {}: {e}", report_path.display())))?;
    println!(
        "  merges: {} candidate pair(s), {} implication(s) → {}",
        proposals.len(),
        implications.len(),
        report_path.display()
    );
    println!("tags-suggest: done. Rebuild the index (`ovp2 index`) to surface inferred tags.");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tags(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn vote_requires_share_and_support() {
        let a = tags(&["agent", "rust"]);
        let b = tags(&["agent"]);
        let c = tags(&["python"]);
        let neighbors: Vec<(f64, &[String])> = vec![(0.9, &a), (0.8, &b), (0.5, &c)];
        let got = vote_tags(&neighbors, 0.35, 2, 5);
        // agent: share (0.9+0.8)/2.2 ≈ 0.77, support 2 → wins.
        // rust: support 1 → out. python: share 0.5/2.2 ≈ 0.23 → out.
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].tag, "agent");
        assert_eq!(got[0].support, 2);
    }

    #[test]
    fn vote_is_empty_on_zero_weight_and_caps_output() {
        let a = tags(&["x", "y", "z"]);
        assert!(vote_tags(&[(0.0, &a)], 0.1, 1, 5).is_empty());
        let got = vote_tags(&[(1.0, &a), (1.0, &a)], 0.1, 1, 2);
        assert_eq!(got.len(), 2);
    }

    #[test]
    fn merge_canonical_is_higher_count_and_sorted_by_cosine() {
        let t = vec![
            ("agent".to_string(), (0..150).collect::<Vec<usize>>()),
            ("agents".to_string(), vec![200, 201]),
            ("python".to_string(), (300..314).collect()),
        ];
        // agent ≈ agents (disjoint sources); python orthogonal.
        let v = vec![vec![1.0, 0.0], vec![0.99, 0.14], vec![0.0, 1.0]];
        let (got, suppressed) = merge_proposals(&t, &v, &v, 0.75);
        assert_eq!(suppressed, 0);
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].alias, "agents");
        assert_eq!(got[0].canonical, "agent");
        assert!(got[0].cosine >= 0.98);
    }

    #[test]
    fn toml_basic_string_escapes_quotes_and_backslashes() {
        assert_eq!(toml_basic_string("agent"), "\"agent\"");
        assert_eq!(toml_basic_string("foo\"bar"), "\"foo\\\"bar\"");
        assert_eq!(toml_basic_string("a\\b"), "\"a\\\\b\"");
        // Round-trips through the real parser.
        let line = format!("[aliases]\n{} = \"ok\"\n", toml_basic_string("foo\"bar"));
        assert!(ovp_domain::tags::TagAliases::parse(&line).is_ok());
    }

    #[test]
    fn name_evidence_kills_short_token_degeneracy_and_keeps_real_pairs() {
        // Lexical kinship passes regardless of name/context similarity.
        assert!(name_evidence("agents", "agent", 0.99, 0.0)); // containment
        assert!(name_evidence("self-improvement", "self-improving", 0.98, 0.0)); // prefix
        assert!(name_evidence("agent-sdk", "agent", 0.93, 0.0)); // shared token
        assert!(name_evidence("github项目", "github", 0.94, 0.0)); // containment
        // Real cross-script translations: high name cosine + context support.
        assert!(name_evidence("训练", "training", 0.979, 0.253));
        assert!(name_evidence("预测市场", "prediction-market", 0.926, 0.589));
        // 算命-class garbage: cross-script but name cosine below the 0.92
        // translation floor → rejected even with passing context.
        assert!(!name_evidence("sem", "算命", 0.906, 0.285));
        assert!(!name_evidence("ios", "算命", 0.841, 0.327));
        assert!(!name_evidence("fastapi", "算命", 0.946, 0.16)); // high name, low ctx
        // Same-script opaque tokens with no lexical kinship never pass —
        // not even with high context (related topics ≠ variants).
        assert!(!name_evidence("ios", "ai", 0.94, 0.242));
        assert!(!name_evidence("git", "go", 0.93, 0.544));
        assert!(!name_evidence("tiptap", "nodejs", 0.94, 0.9));
    }

    #[test]
    fn implication_proposals_fire_on_subsumption_only() {
        // autogen (3 srcs) ⊂ agent (10 srcs): every autogen source is also
        // agent (forward 1.0), but agent is much broader (reverse 0.3).
        // rust (4 srcs) overlaps agent partially — NOT a subsumption.
        let tags = vec![
            ("agent".to_string(), (0..10).collect::<Vec<usize>>()),
            ("autogen".to_string(), vec![0, 1, 2]),
            ("rust".to_string(), vec![0, 1, 20, 21]),
        ];
        // Name vectors aligned with `tags`: agent≈autogen (related), rust ⟂.
        let v = vec![vec![1.0, 0.0], vec![0.9, 0.44], vec![0.0, 1.0]];
        let got = implication_proposals(&tags, &v);
        assert_eq!(got.len(), 1, "{got:?}");
        assert_eq!(got[0].specific, "autogen");
        assert_eq!(got[0].generic, "agent");
        assert_eq!(got[0].forward, 1.0);
        assert!(got[0].reverse <= 0.3);
        // Same subsumption but UNRELATED names (business ⟂ agent) → rejected.
        let unrelated = vec![vec![1.0, 0.0], vec![0.0, 1.0], vec![0.0, 1.0]];
        assert!(implication_proposals(&tags, &unrelated).is_empty());
        // A singleton specific is ignored (noise floor).
        let single = vec![
            ("agent".to_string(), (0..10).collect::<Vec<usize>>()),
            ("x".to_string(), vec![5]),
        ];
        assert!(implication_proposals(&single, &[vec![1.0], vec![1.0]]).is_empty());
    }

    #[test]
    fn merge_suppresses_co_occurring_pairs() {
        // Two singleton tags on the SAME source: near-1.0 cosine (shared
        // sample titles) but co-occurrence, not synonymy — suppressed.
        let t = vec![
            ("思考".to_string(), vec![7]),
            ("内容创作".to_string(), vec![7]),
        ];
        let v = vec![vec![1.0, 0.0], vec![0.999, 0.04]];
        let (got, suppressed) = merge_proposals(&t, &v, &v, 0.75);
        assert!(got.is_empty());
        assert_eq!(suppressed, 1);
    }
}
