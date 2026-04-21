# Phase 24: Brain-First Lookup And Backlink Legibility

> **Status:** Complete in current branch

## Goal

Make new object/link creation more conservative and more legible by forcing the system to search existing vault truth before creating downstream knowledge, and by making the resulting backlink/candidate decisions obvious to operators.

## Why This Follows Phase 25

This phase remains valuable, but it should not proceed on top of a black-box runtime.

[[2026-04-18-phase25-observable-runtime-and-run-ledger|Phase 25]] now establishes:

- one canonical run ledger,
- honest counted progress,
- stale-run separation,
- unified watcher/API/UI runtime truth.

That runtime contract has been validated against a real `ovp --incremental` run, so this phase can continue into:

- brain-first lookup before object creation,
- backlink legibility,
- candidate vs canonical downstream boundary improvements.

## Implemented Scope

This phase intentionally stays inside the existing OVP architecture:

- Markdown notes remain canonical authoring artifacts.
- `knowledge.db` remains the derived runtime/index layer.
- `ConceptRegistry` remains the deterministic resolver for mention-to-object decisions.
- `ovp-ui` remains the operator surface.

The implementation does **not** add a new semantic relation extractor or a new background memory system. Instead, it closes the legibility gap around decisions the system already needs to make.

### Brain-First Lookup Contract

`truth_api.get_note_traceability(...)` now exposes a `brain_first_lookup` contract for each note chain:

- `decision = skip_existing` when promoted/canonical objects are already attached,
- `decision = reuse_existing` when the note links to existing canonical objects through `page_links`,
- `decision = create_candidate` when no existing object link is found and extraction may safely create candidates,
- `decision = inspect` when the current stage does not need object-creation routing.

The important boundary is that `page_links -> objects` can prove that a deep dive already points at existing brain truth even when no `evergreen_auto_promoted` event exists yet. In that case, the operator sees `reuse_existing` instead of a blind “create object” implication.

### Backlink Expectation Contract

`truth_api.get_note_traceability(...)` and `get_object_traceability(...)` now expose a `backlink_expectation` contract with:

- linked source note paths,
- linked deep dive paths,
- linked object ids,
- linked Atlas/MOC paths,
- a small status string such as `satisfied`, `missing_source_backlink`, or `missing_downstream_links`.

This is a legibility contract, not a hard enforcement layer. It makes the expected provenance/backlink shape visible before we add stricter write-time gates.

### Signal And UI Propagation

`research-tech` production signals now carry the lookup/backlink contracts in their payloads. In particular:

- `deep_dive_needs_objects` can point at existing canonical objects discovered through brain-first lookup,
- `production_gap` and extraction-trigger signals keep the traceability counts plus the new contracts,
- `/signals` renders the brain-first decision and backlink status directly on each row.

This keeps the single source of truth intact: the UI reads the signal payload; the signal payload reads traceability; traceability reads `knowledge.db` and existing audit/provenance records.
