# BL-051 — Vocabulary Unification + Map Cleanup

**Status**: Proposed
**Author**: 2026-05-04
**Milestone**: M16 (Surface Reshape) — follow-up to BL-050

## 1. Problem

After BL-050 the URL split is clean, but the words on the page aren't. Five terms describe (or refer to) the same underlying file:

* **Community** — backend Louvain partition (graph_clusters)
* **Crystal** — LLM-synthesized markdown derived from a community or a contradiction
* **Top Topics** — home section showing top-5 by `crystal_scores`
* **Curated Atlas** — same `crystal_scores` ranking, top-30, on its own page
* **Recent Crystals** — same files, sorted by `synthesized_at`

A reader landing on `/` sees "Top Topics" + "Curated Atlas" + "Recent Crystals" and asks: *are these three different things?* They aren't — three windows over the same 576 markdowns.

Plus:

* `/map` renders ~300+ nodes with overlapping labels in a single static svg → unreadable.
* `/atlas` (the old mechanical MOC browser) and `/atlas/curated` (the new top-N) live next to each other under the same `Atlas` nav label, even though they're philosophically different surfaces (one is a diagnostic of `graph_clusters`, the other is curated reading entry).

## 2. Layering rule

The fix is not to mass-rename — that breaks URL history, wikilinks, and CLI muscle memory. The fix is a **strict layering** between what the user sees and what the system stores.

| Layer | Stable name | Whether to change |
|---|---|---|
| **DB tables** | `community_crystals`, `contradiction_crystals`, `crystal_scores` | **No** — append-only PK history, schema migrations cost more than they save |
| **Filesystem** | `40-Resources/Crystals/<safe-id>.md` | **No** — Obsidian wikilinks resolve by filename; renaming breaks every backlink + every pre-existing crystal frontmatter pointer |
| **Frontmatter `type:`** | `community_crystal`, `contradiction_crystal`, `curated_atlas` | **No** — load-bearing for projection_labels |
| **Python class / module names** | `CommunityCrystal`, `community_crystal.py`, `crystal_scoring.py`, `curated_atlas.py` | **No** — internal API, no user impact |
| **CLI names** | `ovp-synthesize-community-crystals`, `ovp-rescore-crystals`, `ovp-build-curated-atlas`, `ovp-list-crystals`, `ovp-rerender-crystals` | **No** — operator tooling, shell history matters |
| **HTML titles, card headers, body copy** | "Top Topics", "Curated Atlas", "Recent Crystals", "[crystal] xyz" in search | **Yes — unify to "Topic"** |
| **Reader-shell URL paths** | `/atlas`, `/atlas/curated` | **Yes** — add `/topics` as canonical; old paths 301 → new |
| **Reader-shell nav labels** | `Atlas` link in nav | **Yes — rename to "Topics"** |

**One sentence**: keep every name the system stores; change every name the user reads.

## 3. The user-visible vocabulary (target)

A reader after BL-051 sees exactly two reader-facing nouns related to the synthesis layer:

* **Topic** — one synthesized markdown note about a clustered idea.
  Surfaced as: a Topic title + 1-line teaser + click-through. Internally a Topic is a `community_crystal` or `contradiction_crystal` row, but the user never reads either label.

* **Topics** (plural, used as a page name) — the curated entry-point list of all topics, ranked by `crystal_scores`.

Old name → new label:

| Old (visible to user today) | New |
|---|---|
| `Top Topics` (home, top 5) | **Top Topics** (kept) |
| `Recent Crystals` (home, last 7d) | **Recent Topics** |
| `Curated Atlas` (page title) | **Featured Topics** (full list, default 30) |
| `Open Curated Atlas →` (home button) | **See all top 30 →** |
| `Atlas` (reader nav label) | **Topics** |
| `[crystal] xyz` / `[contradiction] xyz` (search result prefix) | **[topic] xyz** / **[open question] xyz** |
| `Recent Crystals (last 7 days)` (home heading) | **Recent Topics (last 7 days)** |

The "atlas" word is retired from the Reader shell entirely. The old `/atlas` MOC browser (the mechanical 312-component listing) moves under `/ops/atlas` because it's a diagnostic, not a reading destination.

## 4. URL migration

| Current | After BL-051 | Reason |
|---|---|---|
| `/atlas/curated` (HTML) | **`/topics`** (HTML) | Reader-facing canonical |
| — | `/atlas/curated` returns `301 → /topics` | Back-compat for already-shipped PR #148 bookmarks |
| `/api/atlas/curated` | `/api/topics` | Mirror the URL change |
| — | `/api/atlas/curated` returns `301 → /api/topics` | Back-compat |
| `/atlas` (old MOC browser) | **Stays in Reader shell** as a power-user diagnostic.  Not promoted in nav, but reachable by direct URL. | Decision 3 |

After BL-051: the reader nav says "Topics" (linked to `/topics`).  `/atlas` (old MOC browser) is no longer in nav but still resolves directly for power users.

## 5. Reader home redesign

Drop one redundant card; renumber the rest:

```
Before                                After
──────────────────────────────────    ────────────────────────────────────
[hero + search]                       [hero + search]
[Top Topics] (top 5)                  [Top Topics] (top 5 + "See all 30 →")
[Curated Atlas] (link to /atlas/...)  ─ removed (folded into Top Topics)
[Knowledge Map] (research only)       [Knowledge Map] (research only)
[Recent Crystals] (last 7 days)       [Recent Topics] (last 7 days)
```

Net: one fewer card, no "this looks like the same thing" confusion.

## 6. `/map` fix

The current `/map` renders every node from up to 24 clusters in a static svg, with all labels permanently visible. With ~30 nodes per cluster average that's ~700 labels stacked → unreadable.

Three concrete changes (cheapest first):

1. **Cap node count to 50** by default (currently effectively unbounded under the cluster cap). Pull top-N by node degree or by membership in top-scored clusters.
2. **Hover-only labels**: show node label on hover/click, not always. Today every label is rendered into the static svg via `<text>` tags.
3. **"Show all" escape hatch**: a small toggle that lifts the cap to (say) 200 with a warning.

If those three together still look bad we'll consider replacing the static svg with a force-directed canvas, but that's a bigger lift and I want to see whether the cheap fixes are enough first.

## 7. CLI / docs hygiene (small)

* `BACKLOG.md` and `PRODUCT_SURFACES.md` — every Reader-shell row that says "Crystal" / "Curated Atlas" → "Topic" / "Topics page". Internal-storage rows keep current names.
* `GLOSSARY.md` — add a "Topic vs community_crystal" entry that names the layering rule explicitly so future me doesn't redo this thinking.
* `ARCHITECTURE.md` — touch one paragraph that talks about M14 to use the new terms (it's already mostly OK).
* CLI help text inside `--help` for `ovp-build-curated-atlas` etc. — leave the command name, but update the description: *"Build the Top Topics page (top-N synthesized topics ranked by crystal_scores)"*.
* Internal `community_crystal.py` / `crystal_scoring.py` / etc. — **untouched**.

## 8. Anti-scope

* Not renaming any DB table, schema column, or filesystem path
* Not renaming Python classes or modules
* Not renaming any CLI entry point
* Not renaming any frontmatter `type:` value
* Not removing `/atlas` — it 301s
* Not changing search ranking or `crystal_scores` math
* Not migrating existing crystal markdown frontmatter

## 9. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Reader has bookmarks on `/atlas/curated` from PR #148 / yesterday's manual visits | 301 redirect; old URL in test surfaces too |
| Internal docs (other than the ones listed) still say "Crystal" in body text | Doc lint allowlist: `community_crystal` / `contradiction_crystal` / `crystal_scores` (the table name) explicitly allowed; bare "Crystal" or "Curated Atlas" warns. Initially warn-only. |
| Tests assert old strings ("Recent Crystals", "Curated Atlas", "Open Curated Atlas") | Sweep tests in same PR. ~10-15 assertion updates. |
| Wikilinks inside crystal markdowns reference "Curated Atlas" | grep the live vault — current rendered markdown is regenerated by `ovp-rerender-crystals`; one re-run after the renderer change refreshes the body |
| User confused that nav says "Topics" but URL still has `atlas` after they click | Once `/topics` is the canonical URL, the visible URL on click matches the label. The 301 only fires for old bookmarks. |
| `/map` cap of 50 hides a node the user wanted to see | "Show all" escape hatch + the existing `/ops/clusters` analytical view remain unchanged |

## 10. Test plan

* All `test_ui_server.py` / `test_ui_smoke.py` / `test_curated_atlas_route.py` assertions that compare against the strings `Recent Crystals`, `Curated Atlas`, `Open Curated Atlas`, `[crystal]`, `[contradiction]` get updated.
* New test: `/atlas/curated` returns 301 to `/topics` (GET); `/api/atlas/curated` returns 301 to `/api/topics`.
* New test: `/atlas` returns 301 to `/ops/atlas`.
* New test: `/map` with > 50 candidate nodes caps at 50 unless `?show_all=1`.
* New test: `/topics` returns the same payload as the old `/atlas/curated` (parity).

## 11. PR shape

Single PR (BL-051), ≈8 files:

1. `view_models.py` — `build_curated_atlas_payload` adds a `?show_all=` toggle for /map; rename screen to `topics/list`; reader-home payload drops the standalone curated-atlas card.
2. `_ui_renderers.py` — `_render_curated_atlas_page` renames its strings; `_render_reader_home` cleanup; `/map` renderer adds hover-only labels + cap.
3. `ui_server.py` — add `/topics` + `/api/topics` routes, 301s for the three old paths, move `/atlas` under `/ops/atlas`.
4. `crystal_fts.py` — change FTS title prefix `[crystal]` → `[topic]`, `[contradiction]` → `[open question]`.
5. `BACKLOG.md` — BL-051 row, BL-050 row updated to "Done".
6. `PRODUCT_SURFACES.md` — Reader / Maintainer route table updated.
7. `GLOSSARY.md` — new "Topic vs community_crystal" entry.
8. `tests/` — sweep across the test files listed above.

Estimate: **half a day**, no LLM, no schema, no vault writes.

## 12. Confirmed decisions (locked in 2026-05-04)

1. **Page name**: **"Featured Topics"** (`/topics`).
2. **Search prefixes**: `[topic]` for community crystals, `[open question]` for contradiction crystals.
3. **`/atlas` route**: **stays in Reader shell** as a power-user diagnostic; not moved under `/ops/`.  Adjust nav accordingly: reader nav links say "Topics" (canonical entry) but `/atlas` still resolves for hardcore navigation.
4. **`/map`**: 50-node default cap **with `?show_all=1` escape hatch**.
5. **CLI rename**: skipped — `ovp-build-curated-atlas` and friends keep their names.
