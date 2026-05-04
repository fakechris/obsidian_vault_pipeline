# M14 — Curated Atlas (Crystal Read Model)

> Status: **Plan**.  Drafted after the M13 substrate (PRs #130–#138) shipped 329 community crystals + 1 contradiction crystal on the OVP vault, and the M15 architecture-language cleanup (PR #139) settled the vocabulary.
>
> M13 made the synthesis layer *exist*: every Louvain community has one LLM-synthesized crystal, every open contradiction has one open-question crystal.  M14 makes it *usable*: 329 crystals are too many for a user-facing entry; users need a curated 20–50 crystal surface, with the long tail accessible via search and facets.
>
> The original M14 plan (PR #137) was closed as superseded by M15.  This rewrite uses the six-term architecture vocabulary from [ARCHITECTURE.md](../../ARCHITECTURE.md): every M14 artifact is one of *Source / Candidate / Canonical State / Projection / Access Surface*, governed by the Governance Control Plane.

## Diagnosis (from the M13 production rebuild)

After the targeted M13 rebuild on `~/Documents/ovp-vault`:

- `graph_clusters` carries **329 Louvain communities** (median size 5; top community = 454 members).
- `community_crystals` holds 329 current rows + 3 v2 rows from the smoke-test resume.
- `contradiction_crystals` holds 1 row (the only open contradiction on the vault).
- `40-Resources/Crystals/` carries 330 files; 4 legacy briefing-crystal files have been archived to `70-Archive/Crystals/Legacy/`.

Quality holds:

- Body length: 899–2931 chars, median 1781 (target was 800–1500 字).
- 326/329 (99.1%) of crystals carry all three required sections (`概念核心 / 关键张力 / 可执行启发`).
- Zero preamble / code-fence / empty-body failures across the full corpus.
- Sampling disclosure (PR #136) appears on the 129 under-covered communities; machine-appended `## 相关笔记` section guarantees backlinks regardless of LLM citation behaviour.

The substrate is keep-worthy.  The product surface is wrong: 329 entries don't fit any known scan pattern.

## Where M14 fits in the architecture

In the [six-term contract](../../ARCHITECTURE.md):

| Artifact | Architecture role |
| --- | --- |
| Louvain community (`graph_clusters` row, kind = `louvain_community`) | **Projection** — derived from `relations` over Canonical State |
| Community crystal (`community_crystals` row + `40-Resources/Crystals/<sha>.md`) | **Projection** — LLM-synthesized from one community |
| Contradiction crystal | **Projection** — LLM-synthesized from one open contradiction row |
| Crystal score (M14 BL-045, NEW) | **Projection** — derived from existing Projections + Canonical State signals |
| Curated Atlas (M14 BL-046, NEW) | **Access Surface** — read-mostly; ranking is computed in a Projection |
| Long-tail facets (M14 BL-047, NEW) | **Access Surface** subroutes (FTS + tag + entity) |
| Sub-community split (M14 BL-048, NEW) | **Projection** rebuild — re-runs Louvain on a sub-graph; never writes Canonical State |
| Reuse-feedback table (M14 BL-049, NEW) | New row kind in **Projections** (`reuse_events` already exists) |

M14 introduces **no new Canonical State**.  Everything new is a Projection, an Access Surface, or a Governance hook.  Re-deriving M14 artifacts from Canonical State must always be possible — that's the architectural test the [ARCHITECTURE.md template](../../ARCHITECTURE.md#term-projection) requires.

## M14 goal

> **Build a Curated Atlas — an Access Surface that ranks the crystal corpus into a 20-50 entry top-of-funnel and exposes the long tail through search + facets.**

The ranking lives in a new Projection (`crystal_scores`); the surface lives at `/atlas` (UI) and as a markdown export.  No LLM cost.  No re-synthesis.  No vault structure changes.

## Sub-goals

| ID | Priority | Work item |
| --- | --- | --- |
| BL-045 | P0 | **`crystal_scores` Projection.**  Compute a per-crystal score from 3–5 signals (community size, source-credibility sum, contradiction density, reuse recency, evergreen recency).  Persist as a derived table; rebuilt by `ovp-knowledge-index`.  Schema-version bumped so existing DBs trigger rebuild. |
| BL-046 | P0 | **Curated Atlas Access Surface.**  Top-N (configurable, default 30) crystals on a new `/atlas/curated` route + a markdown export at `40-Resources/CuratedAtlas.md`.  Each entry: label + 1-line teaser + score reasoning + click-through to the crystal markdown. |
| BL-047 | P1 | **Long-tail facets.**  Existing `page_fts` extended to index crystal bodies (add a virtual page kind for crystals).  Tag facet from existing crystal tags.  Entity facet via `entity_aliases` join.  All three are Access Surface subroutes. |
| BL-048 | P1 | **Big-community splitter.**  Communities above a threshold (default 50 members) re-run Louvain over their internal sub-graph; each sub-community gets its own crystal.  Resolves the sampling-bias issue (top community = 454 members, sample = 8). |
| BL-049 | P2 | **Reuse-feedback loop.**  New `reuse_events` rows when a crystal is opened, cited in a query, or pinned.  Feed into the BL-045 score so the curated Atlas evolves with use. |

## Architecture sketch

```
                                Access Surfaces
                                /atlas/curated   <-- BL-046
                                /atlas/search    <-- BL-047 (FTS + facets)
                                /crystals/<id>   <-- existing markdown render
                                       ^
                                       |  reads only
                                       |
                                Projections
   crystal_scores  <-- BL-045          <-- existing community_crystals
   sub_community_crystals  <-- BL-048   <-- existing contradiction_crystals
   reuse_events (extended) <-- BL-049   <-- existing graph_clusters
                                       ^
                                       |  rebuildable from
                                       |
                                Canonical State
                                vault markdown · objects · claims · evidence
                                relations · contradictions · entity_aliases
                                source_credibility (table source_authority)


Governance Control Plane (cross-cutting)
  - rebuild policy for crystal_scores / sub_community_crystals
  - schema migration triggers for the new Projection tables
  - reuse_events write boundary (Surfaces emit events; never write Canonical State)
```

## Open questions to settle before BL-045

### Q1 — How many crystals should the Curated Atlas show?

**Hypotheses**: 20 (forces hard prioritization), 30 (allows multi-domain coverage), 50 (upper bound for one scrollable list).

**Recommendation**: per-vault configurable; default 30.  Validate by inspecting the 30 highest-scored crystals on the OVP vault — cut to 20 if the bottom 10 feel weak; raise to 50 if cuts feel arbitrary.

### Q2 — What signals drive the score?

Candidates (in proposed weight order):

1. **Community size** (already on `graph_clusters`): bigger Louvain communities = more vault attention. Weak signal alone.
2. **Source-credibility sum** (via the `source_authority` table — note: the table name is unrelated to the architecture term `Authority`; it stores per-source credibility scores). Sum the credibility of all source evergreens' source slugs.
3. **Contradiction density** (via `contradictions` table): crystal communities containing open contradictions are higher-attention.
4. **Reuse recency** (BL-049 adds the data): crystals opened/cited recently outrank stale ones.
5. **Evergreen recency** (`objects.created_at` or filesystem mtime): communities that absorbed new evergreens recently.

Weights: tentative `0.25 × size_norm + 0.30 × credibility_norm + 0.20 × contradiction + 0.15 × reuse_recency + 0.10 × evergreen_recency`. Each signal normalized to [0, 1].

### Q3 — How does the long tail get accessed?

**Recommendation**: ship FTS first (lowest-effort highest-leverage). Tag and entity facets follow as enhancements.  Adjacency expansion ("show me 5 related sub-topics") emerges naturally once the community-graph data is exposed at the surface.

## Boundary: what is NOT in M14

Explicitly out of scope, per the M15 architecture vocabulary:

- **No new Canonical State.**  Every M14 artifact is a Projection or an Access Surface.
- **No new LLM calls.**  Score signals all come from existing tables; the curated Atlas reuses crystal bodies verbatim.
- **No re-synthesis.**  If a crystal's content is wrong, fix the prompt and re-run for that one community via `--cluster-id`.  M14 doesn't touch the synthesis path.
- **No vault structure changes.**  The new Projections live alongside `community_crystals` / `contradiction_crystals`; no folders move.

## Sequencing

1. Land **BL-045** first.  Once the score is durable, every downstream surface has a stable signal to consume.
2. **BL-046** (Curated Atlas).  Prove the surface with the OVP vault before spending design time on facets.
3. **BL-047** (FTS + facets) and **BL-048** (splitter) in priority order driven by what feels limiting once the curated Atlas is in production use.
4. **BL-049** (reuse loop) last.  Cold-start with weight 0; turn it on once data accumulates.

## Validity threats

- **Score collapse**: if the weighted score produces a flat distribution, top-N is meaningless.  Log score histograms during BL-045 and tune weights toward a long-tail-skewed distribution.
- **Cold-start reuse**: BL-049's signal is 0 on first run.  Either keep its weight at 0 initially or seed from existing wiki backlink counts.
- **BL-048 re-introduces volume**: if every 100-member community splits into 5, the substrate count doubles.  Mitigation: only split when the within-community score distribution is bimodal (real internal structure).
- **`source_authority` confusion**: developers may misread "source credibility" as "the architecture's Canonical State authority".  The migration note in [ARCHITECTURE.md](../../ARCHITECTURE.md) makes this explicit; documentation lint will keep the boundary visible.

## What this milestone is NOT

Not "make M13 better".  M13 succeeded — the substrate is high-quality and bounded-cost.  M14 is the product layer on top.

Once M14 ships, the 329 crystals stop being "a long list to scroll" and become the indexable corpus that the Curated Atlas + search both consume.  Same data, different surface.
