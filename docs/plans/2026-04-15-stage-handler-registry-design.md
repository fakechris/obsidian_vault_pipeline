# Stage Handler Registry Design

**Goal:** Define the first concrete runtime extraction step needed after pack metadata extraction: make OVP execution paths resolve through an explicit handler registry instead of directly importing current `research-tech` workflow implementations.

**Status:** Design only. No code contract is changed by this document.

## 1. Background

OVP already completed an important first platform split:

- packs can declare object kinds,
- packs can declare workflow profiles,
- packs can declare extraction, operation, and wiki view surfaces,
- runtime selection can choose `--pack` and `--profile`.

That was the correct first step.

But it only extracted the **descriptive layer**.

Below that layer, execution still relies on direct knowledge-pack assumptions:

- workflow stage strings are executed by core `if/elif` branching,
- focused action handlers import current deep-dive and evergreen workflows directly,
- the queue worker does not resolve handlers through a pack-aware contract,
- autopilot still assumes the current stage implementation graph.

This creates an illusion of full pack generality while preserving a deeper coupling to the first in-repo domain system.

## 2. Why This Design Is Needed Now

The immediate trigger is not abstract architecture work. It is external domain pressure.

### What the media pack exercise exposed

The media domain effort is already pushing on a different set of needs:

- different object model
- different workflow DAG
- different review gates
- different truth projection requirements
- different product surfaces

That is expected and desirable.

However, the current runtime does not fail at the metadata level. It fails one level deeper.

The current problems exposed by media are:

1. an external pack can declare stages, but cannot yet declare how those stages execute in a first-class way,
2. the action queue can enqueue generic actions, but execution still resolves to current knowledge handlers,
3. there is no stable contract for saying:
   - this stage is batch-oriented,
   - this action is focused,
   - this handler is safe for autopilot,
   - this handler belongs to this pack,
4. processor mode and quality evaluation are still outside the core runtime contract.

This means an external media system can currently reuse:

- pack metadata
- queue direction
- shell direction

but cannot yet cleanly reuse:

- execution dispatch
- focused follow-up workflows
- stage-level handler ownership

Without this layer, external packs will keep drifting toward “register surfaces here, implement the real system elsewhere.”

## 3. Problem Statement

OVP currently has three execution paths that should converge, but do not yet share an explicit dispatch contract.

### Path A: profile execution

`WorkflowProfile.stages` is declared in pack metadata, but core still executes stage names through direct branching.

### Path B: autopilot execution

Autopilot checks whether a profile includes stages such as `absorb`, `moc`, and `knowledge_index`, but still calls the current in-repo handlers directly.

### Path C: action queue execution

The queue system already has the correct product direction:

- many observation surfaces
- one execution surface

But queue dispatch still resolves `action_kind` to concrete `research-tech` flow imports inside `truth_api.py`.

These three paths should converge on one handler contract.

## 4. Design Goal

Introduce a **Stage Handler Registry** that becomes the execution bridge between:

- pack/profile declarations,
- queue actions,
- focused workflow handlers,
- broad batch runtime entrypoints.

The registry should answer:

- what handler executes a given stage,
- what handler executes a given focused action,
- which pack owns that handler,
- whether it is safe for autopilot or safe-only queue execution,
- what target scope it expects,
- how core should call it.

## 5. Non-Goals

This slice is intentionally narrow.

It does **not** fully solve:

- pack-aware truth projection,
- pack-aware UI payload builders,
- pack-aware signal semantics,
- media truth/object integration,
- full processor control-plane implementation,
- full LLM/rule mode orchestration.

It only extracts the execution dispatch layer.

## 6. Target Contract

The runtime should distinguish two handler families.

### A. Stage handlers

Used for:

- profile execution
- broad or profile-scoped pipeline runs
- autopilot stage resolution

Examples:

- `articles`
- `quality`
- `absorb`
- `moc`
- `knowledge_index`

### B. Focused action handlers

Used for:

- queue worker execution
- single-note or single-object follow-up actions

Examples:

- `deep_dive_workflow`
- `object_extraction_workflow`
- later:
  - `topic_refresh_workflow`
  - `brief_rebuild_workflow`
  - `repair_production_gap`

Both families should be resolved from one registry contract.

## 7. Proposed Data Model

Current `StageHandlerSpec` is too thin to carry real runtime meaning.

It should evolve into a richer execution descriptor with fields equivalent to:

- `name`
- `pack`
- `handler_kind`
  - `profile_stage`
  - `focused_action`
- `stage`
- `action_kind`
- `description`
- `entrypoint`
- `target_mode`
  - `batch`
  - `single_note`
  - `single_object`
  - `pack_scope`
- `supports_autopilot`
- `safe_to_run`
- `requires_truth_refresh`
- `requires_signal_resync`

Not every field must ship in the first code slice, but the design should target this shape.

## 8. Runtime Behavior

### Profile execution path

Current flow:

- resolve pack
- resolve profile
- iterate stage names
- core decides how to execute each string

Target flow:

- resolve pack
- resolve profile
- for each stage:
  - resolve handler from registry
  - execute through a common runtime adapter
  - collect result and audit

### Autopilot path

Current flow:

- check whether profile includes stage names
- call current in-repo helper methods directly

Target flow:

- resolve handlers for supported stages
- execute only handlers marked compatible with autopilot

### Action queue path

Current flow:

- load queued action
- map `action_kind` via an in-function dict
- call direct imports

Target flow:

- load queued action
- resolve focused action handler from registry
- re-check preconditions
- execute through common runtime adapter
- apply refresh policy

## 9. Common Runtime Adapter

Core still needs one thin adapter around handlers.

That adapter should own:

- uniform call signature normalization
- audit/logging
- result normalization
- error classification
- optional truth refresh
- optional signal resync

This keeps handler code domain-owned while preserving one execution discipline.

## 10. Research-Tech First Migration

The first implementation should not try to support every hypothetical domain.

It should migrate current `research-tech` execution first.

### First handlers to register

Stage handlers:

- `articles`
- `quality`
- `absorb`
- `moc`
- `refine`
- `knowledge_index`

Focused action handlers:

- `deep_dive_workflow`
- `object_extraction_workflow`

The success condition for the first code slice is:

- current behavior remains the same,
- but dispatch no longer relies on hard-coded stage/action branches in the core runtime.

## 11. Relationship To The Processor Control Plane

The handler registry is not the full processor control plane.

It is the execution bridge that must exist before processor metadata becomes truly operational.

Relationship:

- handler registry says **who executes what**
- processor control plane says **how that processor is shaped**
  - mode
  - inputs
  - outputs
  - quality hooks

The media project already exposed the need for the second layer, but OVP should extract the first one first.

## 12. Relationship To Media

This design does not implement media support directly.

What it does is remove the first blocking illusion:

- before: external media packs could declare workflow surfaces but not participate cleanly in execution dispatch
- after this slice: external packs can at least plug focused and staged execution into the same runtime shell

What remains unsolved after this slice:

- media truth projection
- media event semantics
- media topic and briefing surfaces
- media-specific UI payload builders

That is acceptable.

The objective of this slice is not “make media work.”

The objective is:

> make OVP execution no longer accidentally synonymous with `research-tech`.

## 13. Milestone Adjustment

This design implies a small but important update to the current milestone narrative.

### Milestone 9A should be split conceptually into three sub-slices

#### Slice 9A-1: Stage Handler Registry

Extract execution dispatch from current in-repo handler coupling.

#### Slice 9A-2: Pack-Aware Truth Projection

Move domain truth building behind contracts.

#### Slice 9A-3: Pack-Aware Observation Surfaces

Move `signals`, `briefing`, `production`, and related payload builders behind pack/domain-aware contracts.

This is a sequencing adjustment, not a strategy reversal.

## 14. Expected Code Scope For The First Implementation Slice

This should be a medium-sized runtime refactor, not a major rewrite.

Likely touched areas:

- `packs/base.py`
- `packs/loader.py`
- new execution/registry module
- `unified_pipeline_enhanced.py`
- `autopilot/daemon.py`
- `truth_api.py`
- `research-tech` pack registration
- focused tests for dispatch and queue behavior

This should remain a focused execution-layer change.

It should not simultaneously rewrite:

- truth store projection
- DB schema semantics
- UI semantics

## 15. Success Condition

This design is successful when:

1. current `research-tech` batch and focused flows still work,
2. queue dispatch no longer directly hard-codes current knowledge handlers,
3. future external packs have a real runtime insertion point below metadata and above truth projection.
