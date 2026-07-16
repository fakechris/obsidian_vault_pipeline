//! `tags-bootstrap` — cold-start tagging for vaults WITHOUT a curated tag
//! vocabulary (the P1 md-only persona in `docs/stage-tags-product.md` §2).
//!
//! Two layers, both landing in `tags_inferred` (never frontmatter):
//!
//! 1. **Deterministic floor (0 tokens, always runs)**: the theme communities
//!    (`crystal-themes`, required input) already carve any corpus into ~17
//!    clusters with c-TF-IDF keywords. Community keywords seed the closed
//!    vocabulary; every target source inherits its community's top keywords
//!    as `method:"community"` inferred tags. Coarse but honest.
//! 2. **LLM classification (`--client live`, cassette-cached)**: batched
//!    sources are classified INTO the closed vocabulary (title + card
//!    titles). The model may propose a few new names per batch; proposals
//!    are normalized, alias-resolved, and admitted only if genuinely new —
//!    then persisted in `vocabulary.toml` with `origin:"llm"` so the
//!    operator can curate them. `method:"llm"` entries replace the floor.
//!
//! Targets = sources with NO operator tags and NO existing inferred entry
//! (kNN backfill from `tags-suggest` outranks bootstrap; `--refresh` redoes
//! bootstrap-method entries but never touches knn ones). Everything written
//! is a projection; delete the files to reset.

use std::collections::BTreeMap;
use std::path::PathBuf;

use ovp_domain::tags::{
    ClassifyInput, InferredTag, TAGS_INFERRED_SCHEMA, TagAliases, TagOrigin, TagVocabulary,
    TagsInferredFile, canonical_tags, parse_tag_classify, tag_classify_request,
};
use ovp_domain::vault_layout::VaultLayout;
use ovp_index::read_index;

use ovp_embed::document_text;
use ovp_embed::knn::cosine;

use crate::CliError;
use crate::commands::client::{ClientKind, build_client};
use crate::commands::crystal_synth::call_and_parse;
use crate::commands::crystal_themes::{ThemeDoc, resolve_vectors};

/// Fixed confidence bands (see `InferredTag.score` docs).
const LLM_SCORE: f64 = 0.8;
const FLOOR_SCORE: f64 = 0.4;
/// Community keywords inherited by a floor-tagged source.
const FLOOR_KEYWORDS: usize = 2;
/// Community keywords contributed to the vocabulary per community.
const VOCAB_KEYWORDS: usize = 3;

pub struct TagsBootstrapArgs {
    pub vault_root: PathBuf,
    pub client_kind: ClientKind,
    /// Cassette root for `tag_classify/v1`. Default:
    /// `<vault-root>/.ovp/cassettes/tags`.
    pub cache_dir: Option<PathBuf>,
    /// Sources per classification call.
    pub batch_size: usize,
    /// New-name proposals admitted per batch.
    pub max_new_per_batch: usize,
    /// Redo bootstrap-method entries (knn entries are never touched).
    pub refresh: bool,
}

/// Validate one batch's picks against the closed vocabulary (pure,
/// unit-tested): picked names are normalized + alias-resolved; anything not
/// in the vocabulary is DROPPED (rule 1 violations never enter the
/// projection); `new_tags` are admitted (normalized, alias-resolved,
/// deduped, capped) only if absent from the vocabulary. Returns
/// (per-source canonical tags, admitted new names in admission order).
pub(crate) fn validate_batch(
    picks: &BTreeMap<usize, Vec<String>>,
    new_tags: &[String],
    vocabulary: &TagVocabulary,
    aliases: &TagAliases,
    max_new: usize,
) -> (BTreeMap<usize, Vec<String>>, Vec<String>) {
    let mut admitted: Vec<String> = Vec::new();
    for raw in new_tags {
        if admitted.len() >= max_new {
            break;
        }
        let canon = match canonical_tags(&[raw.as_str()], aliases).pop() {
            Some(c) => c,
            None => continue,
        };
        if !vocabulary.contains(&canon) && !admitted.contains(&canon) {
            admitted.push(canon);
        }
    }
    let mut out = BTreeMap::new();
    for (id, tags) in picks {
        let mut kept: Vec<String> = tags
            .iter()
            .filter_map(|t| canonical_tags(&[t.as_str()], aliases).pop())
            .filter(|t| vocabulary.contains(t))
            .collect();
        kept.sort();
        kept.dedup();
        kept.truncate(5);
        out.insert(*id, kept);
    }
    (out, admitted)
}

/// Cosine at or above which a proposed new name is a respelling of an
/// existing vocabulary entry (design §2: generate-free → embed-map).
const NEW_NAME_DEDUP_COSINE: f64 = 0.9;

/// Drop proposed names that embed within [`NEW_NAME_DEDUP_COSINE`] of any
/// existing vocabulary name. Embeddings are name-only texts through the
/// shared cache; unavailable embedder + cold cache → ALL proposals dropped
/// (with a printed reason) — the closed vocabulary never grows unverified.
fn embed_dedup_new_names(
    candidates: Vec<String>,
    vocabulary: &TagVocabulary,
    embed_cache_dir: &std::path::Path,
) -> Result<Vec<String>, CliError> {
    if candidates.is_empty() {
        return Ok(candidates);
    }
    let docs: Vec<ThemeDoc> = vocabulary
        .names()
        .map(str::to_string)
        .chain(candidates.iter().cloned())
        .map(|name| {
            let text = document_text(&name, "", 0);
            let sha = ovp_embed::cache::text_sha256(&text);
            ThemeDoc {
                case_id: format!("tagname:{name}"),
                title: name,
                text,
                sha,
            }
        })
        .collect();
    let Some(vectors) = resolve_vectors(&docs, embed_cache_dir)? else {
        println!(
            "  classify: {} new-name proposal(s) DISCARDED — embeddings unavailable, \
             semantic dedup against the vocabulary is mandatory before admission",
            candidates.len()
        );
        return Ok(Vec::new());
    };
    let n_vocab = vocabulary.len();
    let mut kept = Vec::new();
    for (i, name) in candidates.into_iter().enumerate() {
        let v = &vectors[n_vocab + i];
        let dup = vectors[..n_vocab]
            .iter()
            .any(|u| cosine(u, v) >= NEW_NAME_DEDUP_COSINE);
        if dup {
            println!("  classify: proposal {name:?} maps to an existing vocabulary tag — skipped");
        } else {
            kept.push(name);
        }
    }
    Ok(kept)
}

/// Returns the number of sources whose inferred entries this run wrote —
/// `daily` uses it to decide whether the projection needs a second rebuild.
pub fn run(args: TagsBootstrapArgs) -> Result<usize, CliError> {
    let layout = VaultLayout::new();
    let model = read_index(&args.vault_root).map_err(|e| {
        CliError::Io(format!("tags-bootstrap: {e} — run `ovp2 index` first"))
    })?;
    let embed_cache_dir = args.vault_root.join(".ovp/cache/embeddings");
    let themes_path = args
        .vault_root
        .join(layout.crystal_store_dir())
        .join("themes.json");
    let themes = match ovp_domain::crystal::themes::ThemesFile::load(&themes_path)
        .map_err(CliError::Io)?
    {
        Some(t) => t,
        None => {
            println!(
                "tags-bootstrap: no themes.json — run `ovp2 crystal-themes` first \
                 (the theme communities are the deterministic vocabulary seed)."
            );
            return Ok(0);
        }
    };
    let aliases = TagAliases::load(&args.vault_root).map_err(CliError::Io)?;

    // ---- Vocabulary: user tags ∪ community keywords ∪ persisted llm ----
    let mut vocabulary = TagVocabulary::default();
    for s in &model.sources {
        for t in &s.tags {
            vocabulary.insert(t.clone(), TagOrigin::User);
        }
    }
    for c in &themes.communities {
        for kw in c.keywords.iter().take(VOCAB_KEYWORDS) {
            for canon in canonical_tags(&[kw.as_str()], &aliases) {
                vocabulary.insert(canon, TagOrigin::Community);
            }
        }
    }
    for (name, origin) in TagVocabulary::load(&args.vault_root)
        .map_err(CliError::Io)?
        .iter()
    {
        if origin == TagOrigin::Llm {
            vocabulary.insert(name.to_string(), TagOrigin::Llm);
        }
    }

    // ---- Targets: untagged, not already covered (unless --refresh) ----
    let mut inferred = TagsInferredFile::load(&args.vault_root)
        .map_err(CliError::Io)?
        .unwrap_or_else(|| TagsInferredFile {
            schema: TAGS_INFERRED_SCHEMA.into(),
            model: String::new(),
            params: BTreeMap::new(),
            entries: BTreeMap::new(),
        });
    fn case_of(pack_dir: &str) -> &str {
        pack_dir.rsplit(['/', '\\']).next().unwrap_or(pack_dir)
    }
    let card_titles: BTreeMap<&str, &[String]> = model
        .packs
        .iter()
        .map(|p| (case_of(&p.pack_dir), p.card_titles.as_slice()))
        .collect();
    struct Target<'a> {
        sha: &'a str,
        title: String,
        cards: Vec<String>,
        community: Option<i64>,
    }
    let mut targets: Vec<Target> = Vec::new();
    for s in &model.sources {
        if !s.tags.is_empty() {
            continue;
        }
        let covered = inferred.entries.get(&s.sha256).is_some_and(|e| {
            e.iter().any(|t| t.method == "knn" || t.method.is_empty())
                || (!args.refresh && !e.is_empty())
        });
        if covered {
            continue;
        }
        let Some(case) = s.pack_dir.as_deref().map(case_of) else {
            continue;
        };
        targets.push(Target {
            sha: &s.sha256,
            title: s.title.clone().unwrap_or_else(|| case.to_string()),
            cards: card_titles
                .get(case)
                .map(|c| c.to_vec())
                .unwrap_or_default(),
            community: themes.packs.get(case).copied().filter(|&id| {
                id != ovp_domain::crystal::themes::UNCLASSIFIED_ID
            }),
        });
    }
    println!(
        "tags-bootstrap: vocabulary {} tag(s), {} target source(s)",
        vocabulary.len(),
        targets.len()
    );
    if targets.is_empty() {
        vocabulary.save(&args.vault_root).map_err(CliError::Io)?;
        println!("tags-bootstrap: nothing to do (vocabulary refreshed).");
        return Ok(0);
    }

    // ---- Layer 1: deterministic community-keyword floor ----
    let community_keywords: BTreeMap<i64, Vec<String>> = themes
        .communities
        .iter()
        .map(|c| {
            let kws: Vec<String> = c
                .keywords
                .iter()
                .flat_map(|kw| canonical_tags(&[kw.as_str()], &aliases))
                .take(FLOOR_KEYWORDS)
                .collect();
            (c.id, kws)
        })
        .collect();
    let mut floored = 0usize;
    for t in &targets {
        let Some(kws) = t.community.and_then(|id| community_keywords.get(&id)) else {
            continue;
        };
        if kws.is_empty() {
            continue;
        }
        let size = themes
            .communities
            .iter()
            .find(|c| Some(c.id) == t.community)
            .map(|c| c.size)
            .unwrap_or(0);
        inferred.entries.insert(
            t.sha.to_string(),
            kws.iter()
                .map(|k| InferredTag {
                    tag: k.clone(),
                    score: FLOOR_SCORE,
                    support: size,
                    method: "community".into(),
                })
                .collect(),
        );
        floored += 1;
    }
    println!("  floor: {floored}/{} target(s) inherit community keywords", targets.len());

    // ---- Layer 2: LLM classification into the closed vocabulary ----
    let mut classified = 0usize;
    let mut admitted_total = 0usize;
    if matches!(args.client_kind, ClientKind::Live) {
        let cassette_dir = args
            .cache_dir
            .clone()
            .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/tags"));
        let mut client = build_client(ClientKind::Live, &cassette_dir)?;
        for chunk in targets.chunks(args.batch_size.max(1)) {
            let inputs: Vec<ClassifyInput> = chunk
                .iter()
                .enumerate()
                .map(|(i, t)| ClassifyInput {
                    id: i,
                    title: t.title.clone(),
                    card_titles: t.cards.iter().take(6).cloned().collect(),
                })
                .collect();
            // Scoped so the name borrows end before `vocabulary.insert` below
            // (the request owns its text; it keeps no references).
            let req = {
                let vocab_names: Vec<&str> = vocabulary.names().collect();
                tag_classify_request(&vocab_names, &inputs, args.max_new_per_batch)
            };
            let batch_len = inputs.len();
            let (reply, _repair) = call_and_parse(client.as_mut(), &req, "tag-classify", |t| {
                parse_tag_classify(t, batch_len)
            })?;
            let (picks, admitted) = validate_batch(
                &reply.picks,
                &reply.new_tags,
                &vocabulary,
                &aliases,
                args.max_new_per_batch,
            );
            // Semantic dedup: a proposal whose embedding sits within
            // NEW_NAME_DEDUP_COSINE of an existing vocabulary name is a
            // respelling, not a new tag (`agentic-systems` vs `ai-agents`).
            // No embedder + cold cache → proposals are DISCARDED (conservative:
            // the closed vocabulary must not grow unverified).
            let admitted =
                embed_dedup_new_names(admitted, &vocabulary, &embed_cache_dir)?;
            for name in admitted {
                vocabulary.insert(name, TagOrigin::Llm);
                admitted_total += 1;
            }
            for (i, t) in chunk.iter().enumerate() {
                let Some(tags) = picks.get(&i).filter(|v| !v.is_empty()) else {
                    continue; // floor entry (if any) stays
                };
                inferred.entries.insert(
                    t.sha.to_string(),
                    tags.iter()
                        .map(|tag| InferredTag {
                            tag: tag.clone(),
                            score: LLM_SCORE,
                            support: 0,
                            method: "llm".into(),
                        })
                        .collect(),
                );
                classified += 1;
            }
            sayln!("  [{classified}/{}] classified …", targets.len());
        }
        println!(
            "  classify: {classified} source(s) tagged from the vocabulary, \
             {admitted_total} new name(s) admitted"
        );
    } else {
        println!(
            "  classify: skipped (replay client) — the community floor is the \
             0-token result; add `--client live` for vocabulary classification."
        );
    }

    // ---- Persist (vocabulary first: inferred references its names) ----
    let vocab_path = vocabulary.save(&args.vault_root).map_err(CliError::Io)?;
    if inferred.model.is_empty() {
        inferred.model = "tags-bootstrap".into();
    }
    inferred
        .params
        .insert("bootstrap_batch_size".into(), args.batch_size as f64);
    let inferred_path = inferred.save(&args.vault_root).map_err(CliError::Io)?;
    println!("  vocabulary: {} tag(s) → {vocab_path}", vocabulary.len());
    println!("  inferred: → {inferred_path}");
    println!("tags-bootstrap: done. Rebuild the index (`ovp2 index`) to surface inferred tags.");
    Ok(floored.max(classified))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vocab(names: &[&str]) -> TagVocabulary {
        let mut v = TagVocabulary::default();
        for n in names {
            v.insert((*n).to_string(), TagOrigin::User);
        }
        v
    }

    #[test]
    fn validate_drops_out_of_vocab_picks_and_resolves_aliases() {
        let aliases = TagAliases::parse("[aliases]\n\"ai-agents\" = \"agent\"\n").unwrap();
        let v = vocab(&["agent", "memory"]);
        let picks = BTreeMap::from([(0usize, vec![
            "AI Agents".to_string(),   // alias → agent (in vocab)
            "Memory".to_string(),      // normalize → memory (in vocab)
            "hallucinated".to_string(), // not in vocab → dropped
        ])]);
        let (kept, admitted) = validate_batch(&picks, &[], &v, &aliases, 2);
        assert_eq!(kept[&0], vec!["agent", "memory"]);
        assert!(admitted.is_empty());
    }

    #[test]
    fn validate_caps_and_dedups_new_names() {
        let aliases = TagAliases::parse("").unwrap();
        let v = vocab(&["agent"]);
        let new = vec![
            "Agent".to_string(),      // already in vocab → not admitted
            "KV Cache".to_string(),   // → kv-cache admitted
            "kv_cache".to_string(),   // duplicate after normalize → skipped
            "third".to_string(),      // over the cap of 1 remaining? cap=2 → admitted
            "fourth".to_string(),     // over cap → dropped
        ];
        let (_, admitted) = validate_batch(&BTreeMap::new(), &new, &v, &aliases, 2);
        assert_eq!(admitted, vec!["kv-cache", "third"]);
    }
}
