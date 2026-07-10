# Semantic Theme System (L1 embeddings + L2 display themes)

Status: shipped on `feat/semantic-themes-l1-l2` (2026-07-10). Replaces the
pilot's hardcoded 8-bucket English keyword taxonomy (operator-mandated,
merge-blocking). Spike anchor: `.run/theme-spike-20260709/REPORT.md` in the
main repo (994-doc real-corpus sweep).

## The recipe (pinned)

| knob | value | where pinned |
|---|---|---|
| embed text | title + first 1500 chars of reader.md | `ovp_embed::EMBED_HEAD_CHARS` |
| model | `Xenova/paraphrase-multilingual-MiniLM-L12-v2` (384d fp32 ONNX via fastembed 5, mean-pool, L2-norm) | `ovp_embed::EMBED_MODEL_ID` |
| token cap | **128** (sentence-transformers parity; fastembed defaults to 512 — see below) | `ovp_embed::EMBED_MAX_TOKENS` |
| graph | non-mutual kNN, k=10, cosine ≥ 0.5, weight = cosine | `crystal-themes` flags (defaults) |
| communities | Louvain, resolution 1.5, seed 42, singletons → Unclassified | same |
| keywords | c-TF-IDF over the CJK-aware search tokenizer, top 10 | `ovp_embed::ctfidf` + `ovp_index::score::tokenize_for_search` |
| labels | offline = top-3 keyword join; `--client live` = one cached `theme_label/v1` call per community (bilingual) | `ovp-domain/prompts/theme_label.md` |

## Validation table (production ONNX artifacts, spike's own sweep harness)

Gates (pre-registered): coverage > 80% in clusters ≥ 5, noise < 20%, largest
< 25%, bilingual co-clustering of the spike's sampled pairs. Pinned recipe row
(k10 t0.5 r1.5 seed42) shown for each candidate; `@N` = token cap.

| candidate (fastembed 5.17.2) | cl≥5 | cov% | noise% | largest% | bilingual | zh-max%¹ | pairs² | download |
|---|---|---|---|---|---|---|---|---|
| spike reference (sentence-transformers MiniLM-L12) | 17 | 96.6 | 3.2 | 12.5 | 17/17 | 20.2 | 4/4 | n/a |
| multilingual-e5-small @512 `passage:` | 12 | 100 | 0 | 20.2 | 1/12 | 100.0 | 0/4 | 449MB |
| multilingual-e5-small @128 `passage:` | 12 | 100 | 0 | 20.2 | 4/12 | 92.9 | 1/4 | (same) |
| paraphrase-multilingual-mpnet-base-v2 @128 | 19 | 98.8 | 1.2 | 10.5 | 19/19 | 23.0 | 3/4 | 1.0GB |
| bge-m3 @128 | 16 | 100 | 0 | 14.9 | 15/16 | 18.0 | 3/4 | 2.1GB |
| **paraphrase-multilingual-MiniLM-L12-v2 fp32 @128 (PINNED)** | **17** | **96.6** | **3.2** | **12.5** | **17/17** | **20.2** | **4/4** | **464MB** |
| paraphrase-multilingual-MiniLM-L12-v2 Q @128 | 18 | 96.5 | 3.3 | 12.2 | 16/18 | 18.0 | 4/4 | 252MB |

¹ share of all zh/mixed docs concentrated in one cluster (lower = better mixing).
² the spike's sampled bilingual pairs (REPORT §4): Polymarket做市圣经 ↔ Order
Book Like a Quant; 反共识：预测市场退出策略 ↔ Quant Playbook for Polymarket;
Claude Code 源码解读 ↔ I read every line of Claude Code; Garry Tan 400x ↔ AI
Research Skills.

Artifacts: `.run/theme-spike-20260709/{embeddings-*-rs*.json, sweep-*-rs.json,
clusters-*RS128*.md}` (main repo).

### Findings that changed the plan

1. **multilingual-e5-small (the mandated default) FAILS the bilingual gate**:
   at every setting it concentrates 93–100% of zh/mixed docs into one 86–91%
   pure-Chinese cluster — the same language-axis defect that killed
   embeddinggemma in the spike. Its compressed cosine range (~0.8+) also makes
   thresholds 0.5–0.8 indistinguishable. mpnet and bge-m3 pass structure but
   split the Claude-Code sampled pair (3/4).
2. **fastembed DOES ship the literal spike winner** — the REPORT's "fastembed
   does not ship MiniLM-L12" note was outdated. `ParaphraseMLMiniLML12V2`
   (Xenova ONNX) at token cap 128 reproduces the spike's vectors at per-doc
   cosine parity **1.0000** and the winning row byte-for-byte. It is pinned as
   production. This is a deliberate deviation from the mandated
   e5→mpnet→bge-m3 fallback order: the order's premise was the outdated note,
   and the pre-registered gates picked the winner unambiguously.
3. **Token cap is part of the recipe.** fastembed defaults to 512-token
   truncation; sentence-transformers caps this model at 128. At 512 the same
   model drifts back toward language segregation (zh-max 46%, pairs 3/4,
   parity vs the validated vectors 0.45 on long docs). `EMBED_MAX_TOKENS=128`
   is pinned; do not "add more context" without re-running these gates.
4. The quantized variant (`…-onnx-Q`, 252MB) also passes everything (parity
   1.0000, 4/4 pairs) — kept as a future download-size optimization, not
   default (fp32 is the exactly-validated artifact; Q trades 17/17 bilingual
   for 16/18).

## Architecture

- **L1 (embeddings)**: `crates/ovp-embed` — leaf crate. Pure Rust kNN +
  deterministic Louvain (~150 LoC, SplitMix64 pinned seed, no rand dep) +
  c-TF-IDF; the fastembed/ort embedder compiles only under the `embed`
  feature (rustls-only; no openssl, no tokio). Embedding cache:
  `.ovp/cache/embeddings/<text-sha256>.json` (records the model id — a
  different model is a miss). Model files: `~/.cache/ovp/models`
  (`FASTEMBED_CACHE_DIR` override), hf-hub checksummed download on first run.
- **L2 (display themes)**: `ovp2 crystal-themes` writes
  `.ovp/crystal/themes.json` (`ovp.themes/v1`: model, params, generated_from
  input-hash, packs → community id incl. `-1` noise, communities with
  id/label/label_zh/keywords/size, labels_provenance keyword|llm — additive,
  older files default to keyword). It is a **rebuildable projection** —
  never baked into the crystal ledger; claims are NEVER re-synthesized to
  re-theme. Deterministic given inputs; `--refresh` recomputes; the
  freshness short-circuit needs matching `generated_from` AND matching
  clustering params AND label provenance compatible with the requested
  client (`--client live` over a keyword-labeled file relabels; a replay
  run never downgrades llm labels).
- **Projection consumers**:
  - `ovp-index::build_claims`: `ClaimRow.theme` = majority community label
    among the claim's cited packs (tie → lexicographically first; nothing
    mapped → `Unclassified`); without themes.json the ledger theme passes
    through. Corrupt themes.json fails the index build loud.
  - `ovp-server::load_active_records`: same overlay for /api/themes, graph
    scopes and claim pages (corrupt file degrades to passthrough with a
    warning — the server keeps serving). `url_decode` now decodes
    percent-escapes into bytes before UTF-8 validation, so Chinese labels in
    query params round-trip.
  - `crystal-synth`: batches = themes.json communities when present
    (`--themes-file` or `<vault>/.ovp/crystal/themes.json`), else
    deterministic date-ordered cap-size batches with an explicit stderr note.
    Stage 3a full-coverage sub-batching unchanged. The M32 live-repro fixture
    was migrated losslessly (`crates/ovp-cli/examples/migrate_live_fixture.rs`).
  - `crystal-review-session`: `new_sources_in_theme` defer triggers count
    packs by community label (title-containment fallback without themes.json).
  - `daily`: prints a one-line hint when packs are missing from themes.json;
    it never auto-runs crystal-themes (first-run model download surprise).
- **Degradation contract**: no `embed` feature / no model / offline cold
  cache → `crystal-themes` prints why and exits 0 without touching an
  existing themes.json; everything downstream shows `Unclassified`; daily is
  never blocked. A warmed embedding cache works even in embed-less builds.

## Operations

```
ovp2 crystal-themes --vault-root ~/Documents/ovp-vault            # keyword labels
ovp2 crystal-themes --vault-root ~/Documents/ovp-vault --client live  # + bilingual LLM names (cached)
ovp2 index --vault-root ~/Documents/ovp-vault --date <today>      # re-project claim themes
```

First themes run: ~460MB model download (one-time), then ~1–2 min embedding
for ~1000 packs (Apple Silicon CPU); re-runs are incremental via the content
cache. Params (`--knn-k/--cosine-threshold/--resolution/--seed`) are
config — expect to raise k/resolution around the next corpus doubling
(REPORT risk #4).

## Known limits / follow-ups

- Louvain cross-seed ARI ≈ 0.6 — boundary docs move between adjacent themes
  when the corpus changes; the pinned seed makes any given input exactly
  reproducible, but adding packs can reshuffle a theme's edge membership.
- `theme_label/v1` live naming is registered as an evolution candidate
  (`evolution/candidates/theme_label-v1.json`) and validated via
  `ovp2 evolve validate`; the deterministic keyword layer is the auditable
  fallback under every label.
- **L3 follow-up (out of scope here)**: KMEM-style LLM-shaped synthesis
  clusters — using claim lineage / relations rather than doc embeddings to
  group synthesis inputs. The L2 projection deliberately keeps display
  themes decoupled so L3 can replace the grouping without touching the
  ledger or the portal.
