# Phase 13: Knowledge Evolution Layer

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add explicit evolution links so OVP can represent how understanding changes over time instead of only showing isolated contradictions, stale summaries, and production-chain state.

**Architecture:** Start with deterministic evolution candidates grounded in existing trusted surfaces: object/topic scope overlap, source/deep-dive/object traceability, timeline proximity, contradiction/stale signals, and explicit lexical cues. Persist candidate links with typed semantics, route them through the review workbench before treating them as accepted truth, and expose accepted links plus pending candidates on object/topic pages. Do not start with free-form LLM ontology work; Phase 13 should make time-aware knowledge change legible and reviewable first.

**Tech Stack:** Python 3.13, SQLite via stdlib `sqlite3`, existing `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, current logs and review patterns, pytest.

---

## Problem

Today OVP can show:

- contradictions,
- stale summaries,
- production gaps,
- provenance,
- signals,
- traceability.

But it still cannot answer the more important question:

**how did this understanding change?**

That limitation is acceptable for a static workbench, but it becomes a blocker for:

- time-sensitive domains like Media Pack,
- fast-moving topics where updates enrich or replace prior views,
- background intelligence that should explain change, not just show queues,
- graph intelligence that should cluster and synthesize evolving knowledge, not just static objects.

Without explicit evolution links, the system collapses several distinct phenomena into coarse buckets:

- contradiction,
- stale,
- recent,
- related.

That is too weak.

## Scope

Phase 13 should ship in three slices.

### Slice A: Deterministic Evolution Candidates

Introduce typed evolution candidates using only explainable signals.

Link types in scope:

- `replaces`
- `enriches`
- `confirms`
- `challenges`

Candidate sources in scope:

- same object/topic touched by newer notes,
- same deep dive / object production chain with newer downstream material,
- contradiction pairs that are strong `challenges`,
- stale-summary pairs that are strong `replaces`,
- explicit lexical cues in summaries or titles such as:
  - `update`
  - `revised`
  - `no longer`
  - `instead`
  - `builds on`
  - `confirms`
  - `contrary`

Each candidate must carry:

- stable ID,
- source note or object scope,
- prior item,
- later item,
- link type,
- reason codes,
- evidence snippets,
- confidence bucket,
- timestamps.

This slice is about candidate generation, not auto-acceptance.

### Slice B: Evolution Review Workbench

Add a dedicated review surface for evolution candidates.

The UI should let the operator:

- inspect candidate type and scope,
- read the evidence trail,
- accept the proposed link,
- reject it,
- downgrade or change the link type where appropriate.

Accepted evolution links become first-class truth-layer records.
Rejected links remain auditable but should not appear as accepted knowledge.

This should reuse existing review patterns:

- queue browser,
- status transitions,
- review audit trail,
- dashboard prioritization.

### Slice C: Evolution Surfaces On Object And Topic Pages

Expose accepted evolution links and pending candidate summaries directly where users inspect knowledge.

Object/topic pages should gain an `Evolution` section with:

- `Superseded by` / `Replaces`
- `Enriched by`
- `Confirmed by`
- `Challenged by`
- small timeline of accepted evolution links
- pending-candidate summary count with a link to the review queue

This slice should make “what changed” visible without requiring users to think in terms of raw queue items.

## Non-Goals

Phase 13 should **not** attempt:

- full entity ontology redesign,
- graph clustering,
- embedding-based similarity linking,
- free-form LLM evolution inference across the whole vault,
- brand foundation / tone modeling,
- harness-runtime optimization as a primary deliverable.

Those belong later.

## Data Contract

Add a new evolution record shape in the truth layer.

Proposed fields:

- `evolution_id`
- `status`
  - `candidate`
  - `accepted`
  - `rejected`
- `link_type`
  - `replaces`
  - `enriches`
  - `confirms`
  - `challenges`
- `subject_kind`
  - `object`
  - `topic`
  - `note`
- `subject_id`
- `earlier_ref`
- `later_ref`
- `earlier_date`
- `later_date`
- `reason_codes`
- `confidence`
- `evidence`
- `source_paths`
- `review_note`
- `reviewed_at`

If an existing table cleanly fits, reuse it. Otherwise add a dedicated persisted store that mirrors current review conventions.

## Files

- Modify: `src/ovp_pipeline/truth_api.py`
- Modify: `src/ovp_pipeline/ui/view_models.py`
- Modify: `src/ovp_pipeline/commands/ui_server.py`
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`
- Test: `tests/test_ui_smoke.py`

If persistence or log mirroring needs a separate helper module, create the smallest possible addition under `src/ovp_pipeline/`.

## Task 1: Evolution Candidate API

Add deterministic truth-layer functions:

- `list_evolution_candidates(...)`
- `review_evolution_candidate(...)`
- `list_evolution_links(...)`

Candidate generation should start from current trusted inputs only:

- object/topic scope overlap,
- production-chain adjacency,
- contradiction rows,
- stale-summary rows,
- note title / summary lexical cues.

Return stable rows with typed reason codes and evidence.

## Task 2: Persistence And Review Audit

Persist review outcomes for evolution candidates so accepted links survive refreshes and can be audited later.

Reuse existing patterns for:

- status updates,
- audit trail rows,
- mirrored operator-visible history.

Accepted links must become queryable separately from raw candidates.

## Task 3: Evolution Browser View Models

Add view-model builders for:

- evolution queue page,
- object evolution section,
- topic evolution section,
- dashboard evolution summaries.

Payloads should provide:

- counts by link type,
- counts by status,
- strongest candidates,
- accepted evolution timeline,
- source/evidence links.

## Task 4: Evolution Review UI

Add:

- `/evolution`
- `/api/evolution`
- review action endpoint(s) matching the current workbench pattern.

The page should support:

- search,
- filter by status,
- filter by link type,
- review actions,
- links back to source notes, deep dives, objects, and topics.

## Task 5: Object And Topic Evolution Sections

Render `Evolution` on:

- `/object?id=...`
- `/topic?id=...`

Show:

- accepted links grouped by type,
- compact reason/evidence text,
- timeline-oriented ordering,
- pending-candidate count with link to `/evolution`.

## Task 6: Media-Pack Readiness Check

Before closing the phase, verify that the link semantics help with time-sensitive knowledge.

At minimum, confirm the system can express these cases:

- newer note confirms earlier note,
- newer note enriches earlier note,
- newer note challenges earlier note,
- newer note replaces earlier note.

This is not a separate Media Pack feature. It is the acceptance check that the evolution model is useful for one time-sensitive domain.

## Verification

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_truth_api.py -k evolution
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_view_models.py -k evolution
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_server.py -k evolution tests/test_ui_smoke.py -k 'evolution or object or topic'
PYTHONPATH=src python3.13 -m pytest -q
python3.13 -m compileall src/ovp_pipeline
```

## Exit Condition

Phase 13 first round is done when:

1. evolution links exist as explicit typed records,
2. candidates are generated deterministically and are reviewable from the UI,
3. accepted links are visible on object/topic pages,
4. the system can distinguish `replaces`, `enriches`, `confirms`, and `challenges`,
5. the result is useful for a time-sensitive pack without depending on speculative graph intelligence.
