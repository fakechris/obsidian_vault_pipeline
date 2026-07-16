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

/// One proposed merge, evidence attached.
#[derive(Debug, PartialEq)]
pub(crate) struct MergeProposal {
    /// Lower-count member — the proposed alias.
    pub(crate) alias: String,
    pub(crate) alias_count: usize,
    /// Higher-count member — the proposed canonical.
    pub(crate) canonical: String,
    pub(crate) canonical_count: usize,
    pub(crate) cosine: f64,
}

/// Two tags on mostly the SAME sources are co-occurring (related topics on
/// one article), not spelling variants — true synonyms almost never co-occur
/// on one item, because nobody tags an article `agent` AND `agents`. Their
/// embed texts also share sample titles, which inflates cosine to ~1.0, so
/// without this suppression the report leads with same-article noise.
/// Overlap = |A∩B| / min(|A|,|B|); at or above this the pair is suppressed.
const CO_OCCURRENCE_OVERLAP: f64 = 0.5;

/// Pairwise merge candidates over per-tag vectors (pure, unit-tested).
/// `tags` = (name, source indices carrying it). Canonical = higher count;
/// ties by name (lexicographically smaller wins). Returns (proposals,
/// suppressed co-occurrence pair count).
pub(crate) fn merge_proposals(
    tags: &[(String, Vec<usize>)],
    vectors: &[Vec<f32>],
    threshold: f64,
) -> (Vec<MergeProposal>, usize) {
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

pub fn run(args: TagsSuggestArgs) -> Result<(), CliError> {
    if !args.merge_threshold.is_finite() || !args.vote_threshold.is_finite() {
        return Err(CliError::Io(
            "tags-suggest: thresholds must be finite numbers".into(),
        ));
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
    let case_of = |pack_dir: &str| -> String {
        pack_dir
            .rsplit(['/', '\\'])
            .next()
            .unwrap_or(pack_dir)
            .to_string()
    };
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
    for (idx, sha) in &untagged {
        let mut sims: Vec<(f64, &[String])> = tagged
            .iter()
            .map(|(t_idx, _, tags)| (cosine(&vectors[*idx], &vectors[*t_idx]), *tags))
            .collect();
        sims.sort_by(|(sa, _), (sb, _)| sb.partial_cmp(sa).unwrap_or(std::cmp::Ordering::Equal));
        sims.truncate(args.knn);
        let voted = vote_tags(&sims, args.vote_threshold, args.min_support, args.max_tags);
        if !voted.is_empty() {
            entries.insert((*sha).to_string(), voted);
        }
    }
    let covered = entries.len();
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
    let inferred_path = inferred.save(&args.vault_root).map_err(CliError::Io)?;
    println!(
        "  backfill: {covered}/{} untagged source(s) received inferred tags → {inferred_path}",
        untagged.len()
    );

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
    let Some(tag_vectors) = resolve_vectors(&tag_docs, &embed_cache_dir)? else {
        return Ok(());
    };
    let counts: Vec<(String, Vec<usize>)> = vocab
        .iter()
        .map(|(tag, (srcs, _))| ((*tag).to_string(), srcs.clone()))
        .collect();
    let (mut proposals, suppressed) =
        merge_proposals(&counts, &tag_vectors, args.merge_threshold);
    let dropped = proposals.len().saturating_sub(MAX_MERGE_PROPOSALS);
    proposals.truncate(MAX_MERGE_PROPOSALS);

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
         | cosine | alias (count) | → canonical (count) |\n|---|---|---|\n",
        proposals.len(),
        if dropped > 0 {
            format!(", {dropped} more below the display cap")
        } else {
            String::new()
        }
    ));
    for p in &proposals {
        report.push_str(&format!(
            "| {:.3}{} | {} ({}) | {} ({}) |\n",
            p.cosine,
            if p.cosine >= STRONG_MERGE { " ★" } else { "" },
            p.alias,
            p.alias_count,
            p.canonical,
            p.canonical_count
        ));
    }
    report.push_str("\n### Paste-ready block (edit before use)\n\n```toml\n[aliases]\n");
    for p in &proposals {
        report.push_str(&format!("# cosine {:.3}\n\"{}\" = \"{}\"\n", p.cosine, p.alias, p.canonical));
    }
    report.push_str("```\n");
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
        "  merges: {} candidate pair(s) → {}",
        proposals.len(),
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
        let (got, suppressed) = merge_proposals(&t, &v, 0.75);
        assert_eq!(suppressed, 0);
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].alias, "agents");
        assert_eq!(got[0].canonical, "agent");
        assert!(got[0].cosine >= 0.98);
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
        let (got, suppressed) = merge_proposals(&t, &v, 0.75);
        assert!(got.is_empty());
        assert_eq!(suppressed, 1);
    }
}
