# Phase 9: Review Workbench Completion

**Goal:** Turn the current provenance-aware truth UI into a real local operator console for knowledge maintenance. Phase 8 made the system browseable and minimally actionable. Phase 9 finishes the review loop by making queue membership explainable, review actions auditable, and maintenance entry points visible from the surfaces where users already work.

## Current Gap

The system already has:

- contradiction resolution in the UI,
- stale summary rebuild in the UI,
- batch actions,
- provenance on object/topic/event/note surfaces,
- dashboard queue visibility.

But the workbench still feels partial:

- a contradiction row can be resolved without showing the exact claim pair that triggered it,
- a stale summary row can be rebuilt without showing why it was flagged,
- review actions are not visible as a history trail inside the UI,
- object/topic/event pages show review context counts but not the recent maintenance activity behind those counts.

## Scope

### Slice A: Review History And Audit Trail

Expose recent review actions in the truth UI:

- contradiction resolution history
- stale summary rebuild history
- object-scoped history on object/topic/event pages
- dashboard preview of recent review activity

The history should come from persisted audit data, not ad-hoc in-memory state.

### Slice B: Contradiction Evidence Drill-Down

For each contradiction item, show:

- positive claim texts
- negative claim texts
- affected object titles
- recent resolution history when available

This should make it obvious why a contradiction exists and what was previously done about it.

### Slice C: Stale Summary Rationale Drill-Down

For each stale summary item, show deterministic reason codes and human-readable explanations such as:

- no outgoing relations
- summary too short
- summary repeats title
- summary missing

This should make rebuild decisions explainable instead of opaque.

### Slice D: Review Entry Points From Existing Pages

Strengthen the current object/topic/event pages by surfacing:

- recent review history
- direct links to the contradiction and stale-summary queues
- queue-specific context for the current object scope

## Non-Goals

- no new hosted service or background worker,
- no LLM-driven contradiction detection rewrite,
- no event ontology rebuild,
- no full queue prioritization engine yet.

## Milestone Definition

Phase 9 reaches its first stable checkpoint when:

1. users can see why contradiction and stale summary rows exist,
2. users can see what review actions were recently taken,
3. object/topic/event pages expose enough maintenance context that queue actions feel connected to the rest of the workbench,
4. the normal maintenance path does not require dropping to CLI for explanation.

## Execution Order

1. Add failing tests for review history and rationale payloads.
2. Persist UI review actions into the audit layer.
3. Add truth API helpers for review history and rationale-rich queue payloads.
4. Render history and drill-down context in contradiction/stale-summary/object/topic/event views.
5. Re-run targeted UI suites and then the full pytest suite.
