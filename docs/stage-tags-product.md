# Tags as a Product Surface — design anchor

Status: direction approved by operator 2026-07-16. T0 shipped (PR #340 facet +
plumbing, PR #341 tags-suggest). This doc designs T1–T3 and the two parallel
tracks (theme deepening, Tier-0 URL entities). Nothing below is implemented
unless marked shipped.

## 0. Why this doc exists

T0 was built against the operator's own vault: pinboard bookmarks with a
curated 116-tag vocabulary seeding everything. **A general user has none of
that.** They have a markdown folder — maybe sparse, sloppy frontmatter tags,
maybe zero. KMEM-class products are friendly to that user because tags simply
appear (LLM labels everything, reuse-prompted). Our answer must match that UX
floor without inheriting its failure mode (KMEM live vocabulary: 30.7%
singleton rate; unconstrained LLM tagging is the best-documented failure in
this space — Karakeep/Readwise/Raindrop all converged on closed vocabularies).

### Personas

| Persona | Corpus | Tags today | T0 behavior |
|---|---|---|---|
| P0 curator (operator) | pinboard + clipper | curated vocabulary | full stack works |
| P1 md-only | markdown folder | **zero tags** | nothing — no vocabulary, no kNN signal |
| P2 casual | markdown + some frontmatter | sparse, sloppy | partial — alias pipe works, backfill weak |

The product gap is P1/P2. T1 closes it.

## 1. Principles (carried from the research round + M34 process rules)

1. **Humans/code own the vocabulary; the LLM only classifies into it.**
   Vocabulary *invention* is allowed only as capped proposals that are
   embedding-deduped against existing names before entering.
2. **The pipeline never rewrites note frontmatter.** One deliberate exception:
   an explicit per-source USER action in the UI (accept an inferred tag / add
   a tag) writes that note's frontmatter — that is the user editing their
   vault through the product, identical in kind to an Obsidian edit.
3. **Everything derived is a projection**: `aliases.toml` (operator judgments),
   `inferred.json`, `vocabulary.toml`, proposals — all rebuildable, none
   ledger-grade.
4. **Provenance always visible**: user `#tag` vs inferred `~#tag`, everywhere.
5. **Decidability rule**: normalization/dedup/counting = code; classification
   into a closed vocabulary = LLM permitted (not decidable ground truth);
   free-form generation = never.

## 2. T1 — cold-start bootstrap (`tags-bootstrap`)

Goal: a P1 vault gets useful tags with zero manual work, KMEM-equivalent UX.

**Vocabulary seed without pinboard (deterministic floor, 0 tokens):**
the theme communities already exist for any vault (`crystal-themes`:
embeddings → Louvain → c-TF-IDF). Their per-community keywords ARE a
candidate vocabulary: ~17 communities × top keywords ≈ 60–120 candidate tags,
bilingual, corpus-derived, no LLM. Floor behavior with no LLM configured:
every source inherits its community's top keyword(s) as `tags_inferred` —
coarse but honest, and strictly better than nothing.

**LLM classification pass (optional, `--client live`, cassette-cached):**
batched (~20 sources/call, same batching discipline as crystal-synth):

- input: source title + card titles + the candidate vocabulary (names only);
- the model picks 1–5 tags per source **from the vocabulary**, and may propose
  at most 2 new tag names per batch;
- proposed new names are normalized, then embedding-matched against the
  existing vocabulary — cosine ≥ 0.9 → silently mapped to the existing tag
  (the Karakeep lesson: put reuse in the MATCHING layer, not the prompt);
  genuinely new survivors enter `vocabulary.toml` marked `origin = "llm"`;
- assignments land in `tags_inferred` — never frontmatter.

**`vocabulary.toml`** (new, projection): the closed list the classifier is
allowed to use = user tags observed in the index ∪ accepted community
keywords ∪ surviving LLM proposals. P0's pinboard vocabulary is just the
degenerate case where the first term dominates. Users curate it in the UI
(T2) or ignore it.

**Self-healing (already shipped, T1 inherits it):** hand-tagging a source
retires its inferred tags on the next index build; inferred names re-run
through the alias/drop pipe.

Incremental: `daily` runs classification only for sources with no entry in
`inferred.json` (content-hash keyed like every cassette).

**KMEM comparison, honestly stated:** same surface UX (tags appear on
everything automatically); different guarantees — closed-vocabulary
classification with embedding-gated growth (they prompt-and-hope), provenance
separation (they can't tell user tags from machine tags), self-healing
retirement (their labels accrete forever).

## 3. T2 — curation & browsing UI

All live-server only; the published static site stays read-only with tags
redacted (unchanged).

1. **`/tags` page** (Library sub-surface): the vocabulary browser — every
   canonical tag with user/inferred counts (`156 + ~129`), sort by count/name,
   text filter, click-through to the filtered Library. Empty state explains
   the bootstrap path.
2. **Curation inbox** (`/tags` tab): renders merge proposals as decision
   cards — “`ai-agents` → `agent` · cosine 0.93 · 9 vs 22 sources · sample
   titles…” with **Accept / Reject**.
   - Accept → `POST /api/tags/alias` appends to `aliases.toml` + triggers
     reindex. Reject → recorded as an `ignore = [["a","b"], …]` pair in
     `aliases.toml`; `tags-suggest` skips ignored pairs on future runs so
     rejected proposals never resurface. One curation file holds all
     judgments (accepts as aliases, rejects as ignores, drops as drops).
   - `tags-suggest` additionally emits `proposals.json` next to the md report
     (same data, machine-readable) for this UI.
3. **Per-source tag editing** (SourceDetailPage):
   - inferred chips get an accept affordance: `~#memory ✓` →
     `POST /api/source/:sha/tags` inserts the tag into that note's
     frontmatter (principle 1's sanctioned exception, live server only, the
     server re-reads/writes YAML through the same parser the intake uses);
   - an add-tag box with **autocomplete over the vocabulary, reuse-first**
     (write-time prevention beats repair — the folksonomy literature's oldest
     result); free entry allowed, normalized on save.
4. **The acceptance loop is the cold-start engine**: every accepted tag
   becomes kNN training signal on the next build, so P1 vaults converge from
   "LLM-classified" toward "user-curated" exactly as fast as the user cares
   to click.

## 4. T3 — granularity & hierarchy

The generic-tag problem, now measured twice: 13.1% of tagged sources carry
ONLY top-decile-frequency tags, and the kNN backfill mirrors the skew
(post-drop real-vault run: `ai ~444`, `agent ~403`).

1. **Specificity score** (code): IDF over tag document-frequency. A source
   whose tags are all top-decile is *generic-only* and joins the backfill
   queue with a constraint: only vote tags with df below half the source's
   current minimum — i.e. inference may only ADD specificity, never more
   generality. (Extends the inferred channel to non-empty sources under this
   one rule; provenance display unchanged.)
2. **Implications** (Danbooru pattern): co-occurrence subsumption
   (Schmitz: propose `specific ⇒ generic` when P(generic|specific) ≥ 0.7 and
   P(specific|generic) ≤ 0.3) → proposed in the curation inbox → accepted
   into an `[implications]` section of `aliases.toml`. Facets render a
   two-level rollup (generic groups its specifics); search on the generic
   still matches. Generic tags are never split or deleted.

## 5. Parallel tracks (separate PRs, designed here for review)

### 5a. Theme deepening (operator-approved earlier)

- **Sub-communities**: within each top-level community, re-run Louvain on the
  induced subgraph at higher resolution (~2.5); communities of ≥3 members
  become children (`parent_id` in `themes.json`, schema-additive). Keyword
  labels per child; bilingual LLM labels reuse the existing `theme_label/v1`
  cassette path.
- **Theme summaries**: per top-level theme, one batched LLM synthesis over
  member card titles/claims WITH citations, passed through the existing
  deterministic citation verifier before it enters `themes.json`
  (`summary`, `summary_citations` fields). The literature's one validated
  organizational structure (GraphRAG community summaries), built the
  LazyGraphRAG way: cheap, projection, rebuildable.
- **Surfaces**: ThemeDetailPage gains children navigation + the cited
  summary; terrain legend groups by parent.

### 5b. Tier-0 URL entities (operator-approved: ship deterministic tier only)

- **Extraction** (index build, regex, zero LLM, zero network): from
  `source_url` + markdown link targets in the note body —
  `github:owner/repo`, `arxiv:2504.19413`, `doi:10.x/y`, `npm:pkg`,
  `crates:pkg`, `pypi:pkg`, `hn:item`. URL normalization (strip www/trailing
  slash/.git, arXiv version suffix) = the entire identity problem, solved by
  construction.
- **Projection**: `.ovp/entities/url-entities.json` — entity id → {kind,
  canonical URL, mentioning source sha256s}. SourceRow gains nothing; the
  file is joined at render time (keeps the index lean).
- **Surfaces**: entity chips on SourceDetail ("repo appears in 7 sources"),
  `/entity/:id` page (mentioning sources + citing claims via pack join),
  `find --entity`. Publish: included — URLs are already public content,
  unlike personal tags.
- **Tier-1 (Wikidata) stays experiment-gated** per the M34 kill-criteria
  design; nothing here builds toward it.

## 6. Sequencing

| Phase | Depends on | LLM cost | Personas served |
|---|---|---|---|
| T1 bootstrap | themes (shipped) | one-time batched classify + daily increment; 0-token floor exists | P1/P2 get the product |
| T2 curation UI | T1 (proposals.json) | 0 | all |
| T3 granularity | T2 inbox | 0 | P0 + matured P1 |
| 5a theme deepening | none | ~17 label + ~17 summary calls, cassette-cached | all |
| 5b URL entities | none | 0 | all (tech corpora especially) |

T1 → T2 is the product-critical path. 5a/5b are parallel-friendly.
