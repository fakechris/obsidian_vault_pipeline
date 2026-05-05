# BL-050 — Reader / Maintainer Hard Split

**Status**: Active (in flight)
**Author**: 2026-05-04
**Milestone**: M16 (new — Surface Reshape)

## 1. Problem

Today the OVP UI is a single shell with ~37 routes mixed across two audiences:

* **Reader** wants to discover, read, and follow ideas: search, atlas,
  crystals, object lenses, graph map.
* **Maintainer** wants to run the pipeline and audit it: candidates,
  contradictions, signals, actions, runtime, evolution, production gaps.

Concretely the mix is broken:

1. `/` shows `Library Items: 7378` (a row count, not reading material), an
   `Open Workbench` link (operator), and a `Recent Knowledge` list of typed
   objects (extraction-flavored). A reader landing on `/` immediately
   reads as "this is a database admin page".
2. The single nav bar is `Library | Search | Workbench`. Reader sees
   `Workbench` and assumes the app is for engineers; Maintainer sees
   `Library` and gets no shortcut to the runtime view.
3. URLs are flat: `/atlas`, `/candidates`, `/contradictions`, `/signals`,
   `/actions`, `/clusters`, `/explore`, `/pulse`, `/evolution`,
   `/production`, `/objects`, `/reuse`, `/events`, `/runtime-state` are
   all peers. Nothing in the URL says "this one is for readers, this one
   is for ops".
4. Mode is an ambient query param (`?mode=operator`) controlled by
   `_is_reader_mode()` / `set_reader_mode()` scattered across 100+ call
   sites. It only flips one card visibility (`Workbench`); the nav and
   route set don't change.
5. The M14 reading substrate (576 community crystals + 30-row curated
   atlas + crystal page_fts) shipped one day ago and is **completely
   unreachable from `/`** — only via direct URL knowledge of
   `/atlas/curated`. ~$3 of LLM synthesis is invisible to the very user
   it was made for.

This is a structural problem, not a styling one. The two audiences need
two products at the URL + nav level, sharing the underlying truth_store
+ page_fts + crystal_scores layer.

## 2. Goal

Two visible products:

* **Reader** at `/` — discovery, reading, search, atlas, crystals. No DB
  counts. No pipeline state. No review forms.
* **Maintainer** at `/ops` — pipeline status, candidates, contradictions,
  signals, actions, runtime, audit, evolution, production gaps,
  pulse stream.

URL prefix decides shell. `path.startswith("/ops")` ⇒ Maintainer shell;
otherwise Reader shell. No ambient mode state.

Each shell has its own nav. The only cross-link is a small footer/header
hint: *"→ Maintenance"* on Reader, *"← Back to Library"* on Maintainer.

## 3. Route map (target)

### Reader subtree

```
/                     Reader home (rewritten)
/search               Reader search (FTS over pages + crystals)
/atlas                Atlas / MOC browser
/atlas/curated        Curated Atlas (top-N crystals)
/object               Object lens
/note                 Note view
/topic                Topic overview
/map                  Graph map
/explore              Explore stream
/asset                Asset
```

### Maintainer subtree (everything moved under `/ops`)

```
/ops                              Operator dashboard (existing)
/ops/candidates                   was /candidates
/ops/candidates/review            was /candidates/review
/ops/contradictions               was /contradictions
/ops/contradictions/resolve       was /contradictions/resolve
/ops/signals                      was /signals
/ops/actions                      was /actions
/ops/actions/run-next             was /actions/run-next
/ops/actions/run-batch            was /actions/run-batch
/ops/actions/retry                was /actions/retry
/ops/actions/dismiss              was /actions/dismiss
/ops/evolution                    was /evolution
/ops/evolution/review             was /evolution/review
/ops/production                   was /production
/ops/runtime-state                was /runtime-state (UI route; API stays)
/ops/pulse                        was /pulse
/ops/pulse/stream                 was /pulse/stream
/ops/events                       was /events
/ops/reuse                        was /reuse
/ops/open-questions               was /open-questions
/ops/writing-prompts              was /writing-prompts
/ops/summaries                    was /summaries
/ops/summaries/rebuild            was /summaries/rebuild
/ops/deep-dives                   was /deep-dives
/ops/briefing                     was /briefing
/ops/workbench                    was /workbench
/ops/clusters                     was /clusters (analytical view)
/ops/objects                      was /objects (audit table; reader uses /search instead)
```

All old top-level paths return **301 → `/ops/<same>`** for backward
compatibility with bookmarks and internal links.

### API routes

`/api/*` is left unchanged in this PR. APIs are mostly called by inline
JS and have low user-facing surface; reorganizing them adds noise without
landing user value. Defer to a small follow-up if needed.

### Nav

* **Reader nav**: `Library | Search | Atlas | Map`
* **Maintainer nav** (7 sections, collapsing 25 routes):
  * **Overview** → `/ops`
  * **Candidates** → `/ops/candidates`
  * **Contradictions** → `/ops/contradictions`
  * **Signals** → `/ops/signals`
  * **Actions** → `/ops/actions`
  * **Runtime** → `/ops/runtime-state`
  * **Audit** → `/ops/events`

Cross-link in the corner of each shell jumps to the other.

### Mode judgment

* Drop `_is_reader_mode()` / `set_reader_mode()` / `?mode=operator`.
* Replace with `_is_ops_path(path: str) -> bool` returning
  `path == "/ops" or path.startswith("/ops/")`.
* `_layout()` accepts a `shell` argument (`"reader"` or `"ops"`) chosen
  by the route handler from the request path.

## 4. Reader home content

```
+--------------------------------------------------+
| OVP Knowledge                                    |
|--------------------------------------------------|
| [ search box ............. ]  [Search]           |
+--------------------------------------------------+

Top topics (top 5 by crystal_scores)
  1. <label>     <one-line teaser>      → /note?path=...
  ...

Curated Atlas
  30 most reusable ideas in your vault →   → /atlas/curated

Recent crystals (synthesized in the last 7 days)
  - <label>      <synthesized_at>        → /note?path=...
  ...

Knowledge Map →  /map         (only if pack supports research nav)
```

Removed from `/`:
* `Library Items: <count>` stat card
* `Recent Knowledge` (typed objects) list
* `Open Workbench` card

## 5. Decomposition (single PR scope)

The original plan listed three sub-PRs (a/b/c). After review they are
combined into one PR because:

* the route migration and the home rewrite share so many layout helpers
  that splitting them duplicates work
* test updates in `test_ui_server.py` need to land alongside the route
  changes (tests would otherwise be red between PRs)
* the doc updates are small enough to ride along

PR contents in landing order:

1. **Shell layout helpers** — `_layout(shell=...)`, `_is_ops_path()`,
   `_reader_nav_items()` / `_ops_nav_items()`. Drop
   `_is_reader_mode` / `set_reader_mode` / `OPERATOR_ROUTES`.
2. **Route migration** — move 25 routes under `/ops/*` in
   `ui_server.py`. Add 301 redirects for the old paths. Each handler
   passes `shell="ops"` to its renderer.
3. **Reader home** — new `build_reader_home_payload` view-model and
   `_render_reader_home` renderer. `/` switches to it.
4. **Test updates** — sweep `tests/test_ui_server.py` for the moved
   URLs.
5. **Docs** — `docs/PRODUCT_SURFACES.md` adds a shell-ownership table;
   `BACKLOG.md` marks BL-050 Done and adds BL-046b / BL-049b follow-up
   rows.

## 6. Anti-scope

* No new visualisations (graph reader-shell, reading queue UI, etc.)
* No new reader-only data routes — uses what's in DB today.
* No LLM cost.
* No schema changes.
* No API path restructuring (`/api/*` paths unchanged).
* No deletion of any maintainer surface — only relocation.
* No reading-progress / "today's queue" / personalisation features —
  separate BL.

## 7. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Internal links in HTML still pointing at old top-level paths | After moving routes, grep for hard-coded `'/candidates'` / `'/signals'` / etc. in `_ui_renderers.py`; either route handlers always use `_shell_href` (which now scopes to `/ops/<path>` for ops routes) or update them inline. |
| `tests/test_ui_server.py` (104 tests) URL paths | One sweep with focused replacements: `"/candidates"` → `"/ops/candidates"`, etc. Ruff catches missed references via `_is_reader_mode` import errors. |
| Existing browser bookmarks / external links | 301 redirects from old paths to `/ops/<same>`. |
| Reader home loads `crystal_scores` on every request | `idx_crystal_scores_pack_score` on `(pack, score DESC)` makes top-5 LIMIT 5 a µs-level query. |
| Reader sees no crystals on a fresh vault | Empty-state hint with the same wording as the curated atlas markdown: *"No crystals scored yet. Run `ovp-synthesize-community-crystals` and `ovp-knowledge-index` to populate."* |

## 8. Success criteria

* Visiting `/` shows no row count, no Workbench link, no typed-object
  list. First fold shows search box + ≥1 crystal label + curated atlas
  link.
* Visiting `/atlas/curated` is reachable from `/` in one click.
* Visiting `/ops` shows maintainer nav (Overview / Candidates /
  Contradictions / Signals / Actions / Runtime / Audit) — none of the
  reader-side links.
* `/candidates` returns `301 → /ops/candidates`.
* `tests/test_ui_server.py` and `tests/test_curated_atlas_route.py`
  green; new `tests/test_reader_home.py` green.
* Grep for `_is_reader_mode` / `set_reader_mode` returns 0 results.

## 9. What BL-050 unblocks

* **BL-046b** crystal tag facet — clear home in Reader search.
* **BL-047b** crystal entity facet — same.
* **BL-049b** surface-side `reuse_events` emission — Reader pages emit
  reuse on click, closing the BL-045/049 feedback loop (today
  `reuse_recency_norm` is always 0).
* Future reader-only product features (reading queue, personalised
  recommendations, reading history) have a clean container.
* M9 pack-defined object kind `reader_layouts` can render purely in the
  Reader shell without worrying about ops-side mixing.
