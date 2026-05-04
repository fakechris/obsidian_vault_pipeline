# M14 — Crystal Read Model / Curated Atlas

> Status: **Plan**.  Drafted after the M13 substrate (PRs #130–#136) shipped 329 community crystals + 1 contradiction crystal on the OVP vault.
>
> M13 made the synthesis layer **possible** — every Louvain community gets one LLM-synthesized crystal, every open contradiction gets one open-question crystal.  M14 makes it **usable**: 329 crystals are too many for a user-facing Atlas; users need a curated 20–50 entry surface with search and faceted expansion for the long tail.

## Diagnosis (from M13 substrate review)

After the production rebuild on `~/Documents/ovp-vault`:

- `graph_clusters` ≈ **329 Louvain communities** (median size 5; top community = 454 members).
- `community_crystals` ≈ 329 rows (1 LLM call each) once batch finishes.
- `contradiction_crystals` ≈ 1 row (only 1 open contradiction).
- Live directory `40-Resources/Crystals/` will hold 330 crystal markdowns + 4 legacy briefing-crystal files (to be archived in a M13 follow-up).

The **content quality is keep-worthy** — sampling reviews of the in-progress batch showed proper section structure, substantive synthesis, and identification of real tensions.  The **product surface is wrong** — no user wants to scan 329 entries on a knowledge-base homepage.

Three product gaps the data layer can't see:

1. **Volume**: 329 entries don't fit any known scan-pattern (Roam daily list, Notion gallery, Obsidian backlinks panel).  Top-of-funnel needs to be 20–50.
2. **Sampling bias on big communities**: The largest community has 454 members but the crystal is synthesized from 8.  Pre-PR-#136 the under-coverage was invisible to readers; PR-#136 added a visible disclosure but still doesn't *split* big communities into focused sub-topics.
3. **Ranking signals are unmodeled**: All 329 crystals have equal "weight" right now.  They aren't sorted by importance, recency, contradiction density, or downstream reuse.

## M14 goal

**Build a Crystal Read Model — a derived view that surfaces the 20–50 most useful crystals to the user, with the long tail accessible via search + facet expansion.**

Read model is *derived* — does not change the underlying `community_crystals` / `contradiction_crystals` substrate.  It can be rebuilt at any time from those tables + `reuse_events` + `relations` + `source_authority`.

## Sub-goals (BL-045 → BL-049)

| ID | Priority | Work item |
|---|---|---|
| BL-045 | P0 | **Ranking signals + scoring function**: pick 3–5 signals (community size, source authority sum, contradiction density, recent-reuse count, evergreen recency), define a weighted scoring function, persist the score on a `crystal_scores` view (or table). |
| BL-046 | P0 | **Curated Atlas read model**: top-N (configurable, default 30) crystals as the user-facing entry; surface label + 1-line teaser + score reasoning.  Renders to a single Atlas markdown OR a UI surface (TBD — see decision in §4). |
| BL-047 | P1 | **Long-tail facets**: tag index, entity-driven facet (which entities appear in the crystal), source-authority bucket.  Backed by SQL queries over the existing tables + `entity_aliases` view. |
| BL-048 | P1 | **Big-community splitter**: communities above a threshold (e.g. 50 members) re-run Louvain over the sub-graph induced by their members + relations, producing sub-communities.  Each sub-community gets its own crystal — addresses the sampling-bias gap. |
| BL-049 | P2 | **Reuse feedback loop**: `reuse_events` rows for crystals (when a crystal is opened, cited in a query, or pinned) — fed back into the score so the read model evolves with use. |

## Architecture sketch

```
                                  ┌──────────────────────────────────────────┐
                                  │  Layer 3: Read model (M14, NEW)          │
                                  │                                          │
                                  │  crystal_scores view                     │
                                  │  ─ score = w1 × size + w2 × authority    │
                                  │            + w3 × contradiction_density  │
                                  │            + w4 × reuse_recency          │
                                  │                                          │
                                  │  Atlas surface                           │
                                  │  ─ top-N by score                        │
                                  │  ─ teaser = label + 1-line summary       │
                                  │  ─ facet expand for long tail            │
                                  └──────────────────────────────────────────┘
                                          ↑
                                  ┌──────────────────────────────────────────┐
                                  │  Layer 2: Synthesis substrate (M13)      │
                                  │                                          │
                                  │  community_crystals (329 rows)           │
                                  │  contradiction_crystals (1 row)          │
                                  │  graph_clusters (329 Louvain comms)      │
                                  │  contradictions (1 open)                 │
                                  └──────────────────────────────────────────┘
                                          ↑
                                  ┌──────────────────────────────────────────┐
                                  │  Layer 1: Truth + entity (M11–M12)       │
                                  │                                          │
                                  │  objects, claims, relations              │
                                  │  source_authority, entities,             │
                                  │  entity_aliases                          │
                                  └──────────────────────────────────────────┘
```

## Three open questions (from in-flight review)

These need to be settled before BL-045 can land:

### Q1: How many crystals should the curated Atlas show?

**Hypotheses**:

- **20** — fits most "Top 20 …" scan patterns, forces hard prioritization.
- **30** — extends to allow per-domain coverage when the vault spans multiple topic clusters.
- **50** — comfortable upper bound for a single scrollable list.

Should be a **per-vault configurable** with a sensible default (lean toward 30).  Validation: pick the 30 highest-scored crystals after BL-045 ships, eyeball whether each entry feels worth the user's attention.  Cut to 20 if the bottom 10 feel weak; raise to 50 if cuts feel arbitrary.

### Q2: What signals drive the score?

**Candidates** (in proposed weight order):

1. **Community size** (already have): bigger Louvain communities reflect more vault attention.  Weak signal alone (size correlates with topic breadth, not depth).
2. **Source authority sum** (already have via `source_authority` table): sum the authority of all source evergreens' source slugs.  High-authority topics = topics where the user is reading high-signal sources.
3. **Contradiction density** (already have via `contradictions` table): communities with internal contradictions are more interesting (= more user attention deserved).
4. **Reuse recency** (NEW table needed; see BL-049): crystals that have been opened/cited recently outrank stale ones.  Without this, the Atlas is a static frozen-in-time view.
5. **Evergreen recency** (already have via `objects.created_at` if it exists; otherwise infer from filesystem mtime): communities that absorbed new evergreens recently.

**Weights** (to be tuned): `0.25 × size_norm + 0.30 × authority_norm + 0.20 × contradiction + 0.15 × reuse_recency + 0.10 × evergreen_recency`.  Each signal must be normalized to [0, 1] first.

### Q3: How does the long tail get accessed?

The 329 - 30 = ~299 long-tail crystals still exist; users need a way to find them when needed.

**Options** (not mutually exclusive):

- **Full-text search** over `community_crystals.body_md` via `page_fts` (already exists, just needs to register crystal bodies as a virtual page kind).
- **Tag facet** (`tags: [crystal, community]` is universal; need finer tags from LLM extraction or post-processing).
- **Entity-driven facet** (which entities appear in the crystal — exposes the same crystals organized by who/what they're about).
- **Adjacency expansion**: from a curated-Atlas crystal, "show me 5 related sub-topics" navigates into the long tail by community-graph adjacency.

**Recommendation**: build all four progressively, but ship FTS first (it's the lowest-effort, highest-leverage option — users already know how to search).

## Boundary: what is NOT in M14

Explicitly out of scope, to keep the milestone shippable:

- **No new LLM calls** for the read model.  Score signals are all derived from existing tables; the curated Atlas reuses crystal bodies verbatim.  M14 is a *cheap* milestone.
- **No re-synthesis** of existing crystals.  If a crystal's content is wrong, fix the prompt + rerun for that one community via `--cluster-id`.  M14 doesn't touch that path.
- **No vault structure changes**.  The read model is added alongside the substrate; no folders move.

## Migration / sequencing

1. PR #136 (M13 substrate finalization) merges → run `ovp-rerender-crystals` on the production vault to refresh on-disk format.
2. **M13 marked Done** (substrate shipped; quality bar met).
3. **M14 BL-045** lands: scoring view + reasonable defaults.
4. **M14 BL-046** lands: curated Atlas markdown surface (or UI route — see §4).
5. **M14 BL-047 / BL-048 / BL-049** in priority order based on what feels limiting once the curated Atlas is in production use.

## Validity threats

- **Score collapse**: if the weighted scoring produces a flat distribution where most crystals score ~0.5, the top-N is meaningless.  Mitigation: log score histograms during BL-045 and tune weights to produce a skewed distribution (long-tail few-high).
- **Cold-start reuse signal**: BL-049's reuse weight is 0 on first run because no `reuse_events` exist yet for crystals.  Either give it weight 0 in initial deployment or seed with synthetic reuse from existing wiki backlinks.
- **Big-community split (BL-048) re-introduces the volume problem**: if every 100-member community splits into 5 sub-communities, the substrate count doubles.  Mitigation: only split when the score distribution within a community is bimodal (signal that there's real internal structure to surface).

## What this milestone is NOT

It is not "make M13 better."  M13 *succeeded* — the substrate exists, the quality is good, the LLM cost was bounded ($1.50–$3 for 329 communities).  The substrate is the foundation; M14 is the product layer on top.

Once M14 ships, M13's 329 crystals stop being a "long list to scroll" and become "the indexable corpus the curated Atlas + search both consume."  Same data; different UX.
