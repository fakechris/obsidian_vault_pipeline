# Page IA — post-BL-029 chain

The maintainer UI was originally laid out around an
`Evergreen + Deep Dive` chain: a 6-dim LLM rewrite produced
`*_深度解读.md` archives (`auto_article_processor.ArticleProcessor`)
which an `absorb v1` step then promoted into evergreen objects.
Several pages, signal types, and links surfaced the deep-dive
intermediate stage as a first-class concept.

BL-029 (v0.13.0) deleted the producer.  BL-054/055/058 introduced
the replacement chain:

```
Source URL                       (objects.source_url, BL-054)
  → Source File                  (50-Inbox/03-Processed/...md, resolved
                                  via source_dedup.find_existing_by_url, BL-058)
  → Pipeline Stages              (provenance table, BL-055; today only
                                  stage='ingest' rows are written, BL-056
                                  fills extract/promote/synthesize_*)
  → Evergreen Markdown           (10-Knowledge/Evergreen/<title>.md;
                                  objects.canonical_path)
```

The deep-dive intermediate stage is gone.  Source URL is the
input; Evergreen is the canonical output; everything in between
is observable in `provenance`.

This document is the canonical map of which page answers which
question along that chain.  It exists so the next maintainer
doesn't have to reverse-engineer the IA from the renderer.

## Reader shell (`/`, `/search`, `/topics`, `/atlas`, `/map`, `/note`, `/object`, `/topic`)

Reader pages are read-only consumers of the canonical store.
None of them mutate Truth or write Governance events.

| URL | Stage in chain | Answers |
| --- | --- | --- |
| `/` | (entry point) | What's worth reading? |
| `/search?q=` | (cross-stage) | Find by query across notes + objects |
| `/topics` | Synthesis (crystals) | Curated topic clusters |
| `/atlas` | MOC adjacency | Atlas browser — entry by Atlas page |
| `/map` | Graph cluster overview | Where does this concept live in the graph? |
| `/note?path=` | Active staging or evergreen file | Raw markdown view — closest to the source |
| `/object?id=` | Canonical evergreen | Object detail + Source chain card (this is the page that answers the full chain question; see PR #183) |
| `/topic?id=` | Object neighborhood | Object as part of a cluster — neighbors + relations |

The big one is `/object`.  After PR #183 (Commit 4 of the cluster
+ object overhaul) the right rail of `/object` carries:

1. **Source chain** card — pipeline lineage in the order data
   flows: Source URL → Source File → Pipeline Stages →
   Evergreen Markdown.  Built by `truth_api.get_object_source_chain`.
   Includes a `legacy archive` warning chip when
   `objects.canonical_path` still points at a pre-BL-029
   `*_深度解读.md` file (re-run `absorb` to refresh).
2. **Discoverable from** card (renamed from `Sources & Backlinks`)
   — inbound non-Atlas wikilinks + Atlas membership + related
   objects.  Built by `view_models._build_source_backlink_rail`.
3. **Context** card — object kind, source slug, canonical path.
4. **Production Chain** card — chain_status / missing_stages
   from `get_object_traceability`.  Different question from
   Source chain: this one answers "is this evergreen still
   missing its source/atlas anchors?", whereas Source chain
   answers "what was the lineage that produced it?".
5. **Relations** + (research shell) **Contradictions** + **Stale
   Summary Signals**.

## Maintainer shell (`/ops/...`)

Maintainer pages are by-time, by-transaction, or by-domain
projections of the audit + queue tables.  Mutating routes
(`*/review`, `*/resolve`, `*/rebuild`, `actions/*`) emit
Governance events, never write Canonical State directly.

| URL | Stage | Answers | Notes |
| --- | --- | --- | --- |
| `/ops` | Foyer | Is anything broken? | PR #179 |
| `/ops/today` | by day | What did the pipeline do today? | PR #179 |
| `/ops/timeline` | by week | Multi-day rollup | PR #179 |
| `/ops/runs` | by transaction | Per-run timeline | PR #179 |
| `/ops/pulse`, `/ops/pulse/stream` | live tail | Real-time logs | PR #179 |
| `/ops/events` | by audit row | Drill into individual events | PR #179 |
| `/ops/objects` | Canonical objects | Browse all objects (type-facet rail) | PR #177 |
| `/ops/clusters` | Graph clusters | Browse clusters | Paginated in PR #182 |
| `/ops/cluster?id=` | Single cluster | Force-directed view | Interactive D3 in PR #182 |
| `/ops/queue` | Pending review | Triage landing | PR #179 |
| `/ops/queue/concepts` | Promote / merge / reject | Concept candidate review | PR #179 |
| `/ops/queue/contradictions` | Resolve | Contradiction review | PR #179 |
| `/ops/queue/signals` | Queue / dismiss | Signal review | PR #179 |
| `/ops/queue/actions` | Run / retry / dismiss | Action queue | PR #179 |
| `/ops/summaries`, `/ops/briefing` | Compiled-content review | Reviewer / triage | (no change) |
| `/ops/contradictions`, `/ops/signals`, `/ops/actions`, `/ops/evolution`, `/ops/production` | Domain-scoped projections | Per-domain review queues | (no change) |
| `/ops/workbench` | Reviewer / triage | Combined workbench iframe host | (no change) |
| `/ops/deep-dives` | (deleted) | 301 → `/ops/today` | BL-029 + PR #179 + PR #182 |
| `/ops/derivations` | (deleted) | Removed in PR #182 sweep | — |

## What the deletions left behind

A few internal labels survive even though the UX surface
doesn't:

- `signal_type='source_needs_deep_dive'` — kept as a stable
  back-compat handle so pack governance / handler registry
  / processor contracts don't have to churn.  The signal now
  fires when a processed source note has no derived evergreen
  objects (the legacy `deep_dive_needs_objects` intermediate
  stage is gone).
- `action_kind='deep_dive_workflow'` — same: an internal label,
  not a UX surface.  Action handler is still wired through the
  registry so existing pack manifests keep importing.
- `audit_events.kind='deep_dive_created'` — historical rows
  still in the table; the producer is gone, so no new rows are
  written.

When in doubt, the rule is: anything user-facing should
reference the post-BL-029 chain (Source URL → ... → Evergreen).
Anything in handlers, registries, or audit history can keep its
legacy label as long as the new behavior is correct.

## Out of scope for this overhaul

- BL-056 stage-emit hooks for `extract` / `promote` /
  `synthesize_*` — Pipeline Stages will fill in once those
  emit calls land.  Until then most vaults show one `ingest`
  row per object.
- LLM re-classification of v1 evergreens (BL-030 Phase 2).
- Backfill `objects.source_url` for legacy evergreens that
  still don't carry it.
- Retrofitting `/map` to use the new D3 force solver from
  `_graph_visualizers.py` — the module is reusable; the
  retrofit is a separate change.
