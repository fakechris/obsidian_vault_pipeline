# BL-052 — Maintainer Vocabulary Matrix

**Status**: Audit complete (this doc is the deliverable; no code changes)
**Author**: 2026-05-04
**Plan reference**: [`2026-05-04-bl-052-maintainer-vocab-audit.md`](./2026-05-04-bl-052-maintainer-vocab-audit.md)

## Reading guide

Each row maps one Maintainer-shell URL to:

* the **HTML renderer** (in `commands/_ui_renderers.py`)
* the **view-model builder** (in `ui/view_models.py`)
* the **backing entity** (DB table, JSONL log, view, or in-memory stream)
* an **overlap classification** with companion routes:
  * **R — Real duplicate**: same data behind two surfaces; merge candidate
  * **C — Concept overlap, distinct sources**: two entities that talk about the same idea; needs naming clarity
  * **N — Distinct but mis-named**: two real things; current names don't differentiate
  * **K — Keep**: real distinction, current names already clear
* a **recommended action** (rename / merge / leave / promote-doc)

Auditor's note: classifications below are best-effort from code +
schema reading.  Stakeholder review (with @chris) decides
**do-now / defer / leave** per row before BL-053 starts.

## Matrix

### `/ops` Overview (root dashboard)

| Field | Value |
|---|---|
| Renderer | `_render_dashboard` |
| View-model | `_build_runtime_home_payload_from_query` → `truth_api.build_runtime_home_payload` |
| Backing | composite — `runtime_state`, `signals`, `objects`, `transactions` |
| Overlap | `K` |
| Action | leave |

### `/ops/workbench`

| Field | Value |
|---|---|
| Renderer | `_render_workbench_page` |
| View-model | `truth_api.list_review_actions` (review queue snapshot) |
| Backing | `audit_events` (review actions) |
| Overlap | with `/ops` (both self-describe as "operator hub" in copy) — **R**, low-impact |
| Action | rename copy on `/ops/workbench` to "Review Queue" so it doesn't duplicate the dashboard's framing.  No URL change. |

### Candidate streams (likely word collision)

#### `/ops/candidates` + `/ops/candidates/fragment`

| Field | Value |
|---|---|
| Renderer | `_render_candidates_page` (HTML) / `_fragment_from_page` (fragment) |
| View-model | `view_models.build_candidate_browser_payload` |
| Backing | `candidate_concepts` table (proposed concept slugs awaiting promotion) |
| Overlap | with `/ops/evolution` — **C** (both called "candidate", different entities) |
| Action | rename UI label to **"Concept Candidates"** to disambiguate from Evolution Candidates |

#### `/ops/candidates/review` (POST mutation)

| Field | Value |
|---|---|
| Handler | `truth_api.review_candidate_concept` |
| Backing | `candidate_concepts`; promotion writes `objects` + `claims` |
| Overlap | none (paired with `/ops/candidates`) |
| Action | leave |

#### `/ops/evolution` + `/ops/evolution/review`

| Field | Value |
|---|---|
| Renderer | `_render_evolution_browser_page` |
| View-model | `view_models.build_evolution_browser_payload` |
| Backing | `evolution_candidates` table (relation/object-evolution proposals — entirely different from concept candidates) |
| Overlap | **C** with `/ops/candidates`; both share the word "candidates" |
| Action | rename UI label + URL → **"Relation Proposals"** at `/ops/relation-proposals`; 301 from `/ops/evolution`.  Frees the word "candidate" for concept-level only. |

### Contradiction / open-question pair (concept overlap)

#### `/ops/contradictions` + `/ops/contradictions/resolve`

| Field | Value |
|---|---|
| Renderer | `_render_contradictions_page` |
| View-model | `view_models.build_contradiction_browser_payload` |
| Backing | `contradictions` table (typed binary tension between two claims) |
| Overlap | **C** with `/ops/open-questions/fragment` |
| Action | leave (real entity, clear table); document the distinction in `PRODUCT_SURFACES.md` |

#### `/ops/open-questions/fragment`

| Field | Value |
|---|---|
| Renderer | `_render_open_questions_fragment` |
| View-model | `_build_open_questions_payload` (reads `60-Logs/open-questions.jsonl`) |
| Backing | `open-questions.jsonl` — free-form unresolved questions written by the (planned) query-feedback loop |
| Overlap | **C** with `/ops/contradictions`; both surface "things that aren't settled" but at different granularities and from different sources |
| Action | rename UI label to **"Query Followups"** to free "open question" for the Reader-shell contradiction-crystal alias (BL-051).  No URL change. |

### "What's happening" triplet (real duplicate territory)

#### `/ops/pulse` + `/ops/pulse/fragment` + `/ops/pulse/stream`

| Field | Value |
|---|---|
| Renderer | `_render_pulse_page` / `_render_pulse_fragment` |
| View-model | `pulse.initial_positions` + `tail_events` |
| Backing | in-memory pulse-event stream (not a table) |
| Overlap | **K** vs `/ops/events` (live stream vs durable log) |
| Action | leave; the distinction is real — copy already says "Live activity stream" |

#### `/ops/events`

| Field | Value |
|---|---|
| Renderer | `_render_events_page` |
| View-model | `view_models.build_event_dossier_payload` |
| Backing | `audit_events` table (durable log) |
| Overlap | **R** with the maintainer nav label "Audit" (which links here) |
| Action | rename nav label `Audit` → **`Events`** so the link label and page title agree.  This is the single most-confusing label in maintainer nav today. |

### Signal → Action chain (correctly distinct)

#### `/ops/signals`

| Field | Value |
|---|---|
| Renderer | `_render_signals_page` |
| View-model | `view_models.build_signal_browser_payload` |
| Backing | `60-Logs/signals.jsonl` |
| Overlap | **K** with `/ops/actions` (signals trigger actions; clear chain) |
| Action | leave; consider promoting the chain in copy ("each signal can be turned into an action") |

#### `/ops/actions` + `/ops/actions/fragment` + `/ops/actions/{run-next,run-batch,retry,dismiss,enqueue}`

| Field | Value |
|---|---|
| Renderer | `_render_actions_page` |
| View-model | `view_models.build_action_queue_payload` |
| Backing | `action_queue` table |
| Overlap | **K** with `/ops/signals` |
| Action | leave |

### Compiled content trio (all real, three names justified)

#### `/ops/briefing` + `/ops/briefing/fragment`

| Field | Value |
|---|---|
| Renderer | `_render_briefing_page` |
| View-model | `view_models.build_briefing_payload` |
| Backing | composite — daily orientation assembled from `objects`, `signals`, `compiled_summaries` at request time |
| Overlap | **K** vs `/ops/summaries` (orientation page vs per-object summary) |
| Action | document the distinction explicitly in `PRODUCT_SURFACES.md` so future readers don't conflate |

#### `/ops/summaries` + `/ops/summaries/rebuild`

| Field | Value |
|---|---|
| Renderer | `_render_stale_summaries_page` |
| View-model | `view_models.build_stale_summary_browser_payload` |
| Backing | `compiled_summaries` table |
| Overlap | **K** vs briefing / deep dives |
| Action | leave; rename UI title `Stale Summaries` → **`Compiled Summaries`** (current title implies only the stale subset is shown) |

#### `/ops/deep-dives`

| Field | Value |
|---|---|
| Renderer | `_render_derivations_page` |
| View-model | `view_models.build_derivation_browser_payload` |
| Backing | `deep_dive_derivations` view (links source notes to the canonical objects derived from them) |
| Overlap | **K** vs briefing / summaries |
| Action | leave |

### Production / Reuse (telemetry surfaces)

#### `/ops/production`

| Field | Value |
|---|---|
| Renderer | `_render_production_browser_page` |
| View-model | `view_models.build_production_browser_payload` |
| Backing | `production_chains` view — tracks which raw sources made it through to canonical state |
| Overlap | **C** with `/ops/evolution`; both literally describe "process" but talk about different processes (ingest funnel vs relation evolution) |
| Action | rename UI title → **`Source Production Chain`** so the noun pair is explicit |

#### `/ops/reuse/fragment`

| Field | Value |
|---|---|
| Renderer | `_render_reuse_report_fragment` |
| View-model | `commands/reuse_report.build_reuse_report_payload` |
| Backing | `reuse_events` table (BL-049) |
| Overlap | **K** |
| Action | leave |

#### `/ops/writing-prompts/fragment`

| Field | Value |
|---|---|
| Renderer | `_render_writing_prompts_fragment` |
| View-model | `_build_writing_prompts_payload` (reads `00-Polaris/Writing-Prompts.md`) |
| Backing | `Writing-Prompts.md` markdown file |
| Overlap | **K** |
| Action | leave |

### Diagnostic / cluster surfaces (mostly fine)

#### `/ops/clusters` + `/ops/cluster?id=`

| Field | Value |
|---|---|
| Renderer | `_render_clusters_page` / `_render_cluster_detail_page` |
| View-model | `view_models.build_cluster_browser_payload` / `build_cluster_detail_payload` |
| Backing | `graph_clusters` table |
| Overlap | **K** vs Reader-side `/atlas` — `/atlas` is the BL-050 power-user MOC browser; `/ops/clusters` is the diagnostic with full edge-kind breakdown |
| Action | leave; the Reader/Maintainer split makes the distinction clear |

#### `/ops/objects`

| Field | Value |
|---|---|
| Renderer | `_render_objects_index` |
| View-model | `view_models.build_objects_index_payload` |
| Backing | `objects` table |
| Overlap | **K** with Reader-side `/search` (different query intent) |
| Action | leave |

#### `/ops/runtime-state` (API only)

| Field | Value |
|---|---|
| Handler | `truth_api.get_operational_runtime_state` |
| Backing | `runtime_state` ledger + in-flight transactions |
| Overlap | none |
| Action | leave |

## Aggregate findings

**Real duplicates / mergers (R)** — 1 row:
* `Audit` nav label / `/ops/events` page title — same target, different word.

**Concept overlaps that need disambiguation (C)** — 4 rows:
* `Candidates` (concept) vs `Evolution Candidates` (relation)
* `Contradictions` (table) vs `Open Questions` (JSONL)
* `Production` (source funnel) vs `Evolution` (relation evolution)
* (above three already counted)

**Distinct but mis-named (N)** — 1 row:
* `Stale Summaries` (page title currently implies subset; actually shows all)

**Keep (K)** — remaining ~12 rows.

## Recommended consolidations (BL-053 candidates)

In priority order:

1. **Rename nav `Audit` → `Events`** (one-line; closes the most-visible R-class duplicate)
2. **Rename `Evolution Candidates` → `Relation Proposals`** + URL move (frees the word "candidate" for concept-level only — closes the largest C-class overlap)
3. **Rename `Stale Summaries` → `Compiled Summaries`** (page title only; closes the N-class)
4. **Rename `Open Questions` (fragment) → `Query Followups`** (frees "open question" for Reader-side contradiction crystals)
5. **Rename `Workbench` page title → `Review Queue`** (closes the small R-class against `/ops`)
6. **Rename `Production` page title → `Source Production Chain`** (closes the C-class against Evolution)
7. **Add a "Compiled Content" doc section** in `PRODUCT_SURFACES.md` distinguishing Briefing / Summaries / Deep Dives explicitly

Items 1–4 are mechanical (1 file each).  Items 5–7 are copy + doc.

## What this audit deliberately did NOT do

* No code changes.  The matrix is the entire deliverable.
* No CLI rename consideration.  ops surfaces only.
* No backend table renames.  Storage stays stable.
* No URL changes outside the explicit migrations recommended above.

Ready for stakeholder pass.  Mark each row as **do-now / defer /
leave** and that becomes the BL-053 implementation backlog.
