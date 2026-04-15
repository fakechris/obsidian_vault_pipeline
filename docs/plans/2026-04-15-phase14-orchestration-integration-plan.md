# Phase 14 Orchestration Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify `signals`, `briefing`, `recommended actions`, and existing `ovp` workflows behind one action-queue control plane so the product has many observation surfaces but only one execution surface.

**Architecture:** Keep `ovp --full` and existing pipeline/profile commands as the execution engine. Add an action queue ledger plus a thin worker/dispatcher that maps `recommended_action.kind` to existing workflow handlers. UI pages enqueue or review actions; they do not execute workflows directly.

**Tech Stack:** Python 3.13, existing `truth_api`, `ui_server`, `unified_pipeline_enhanced.py`, JSONL/audit logs, pytest.

**Architecture follow-up:** This orchestration plan now depends on a deeper runtime split captured in:

- [2026-04-15-ovp-layer-contract.md](2026-04-15-ovp-layer-contract.md)
- [2026-04-15-stage-handler-registry-design.md](2026-04-15-stage-handler-registry-design.md)

## Current State

The repo already has two partially overlapping systems:

1. **Execution surfaces**
   - `ovp --full --pack ...`
   - individual pipeline steps in `unified_pipeline_enhanced.py`
   - existing UI maintenance actions:
     - contradiction resolution
     - summary rebuild
     - evolution review

2. **Detection/intelligence surfaces**
   - `signals`
   - `briefing`
   - `recommended_action`
   - `production gaps`
   - `evolution candidates`

The risk is obvious: if every page learns how to launch workflows directly, the product ends up with many conflicting entrypoints, no single state machine, and poor auditability.

## Design Decision

There must be:

- **many observation surfaces**
  - dashboard
  - briefing
  - signals
  - contradictions
  - summaries
  - production

and exactly **one execution surface**

- action queue
- worker
- workflow handler registry

That means:

- `signals` detect
- `briefing` prioritizes
- `recommended_action` proposes
- `action queue` decides what will run
- `worker` executes
- existing `ovp` runtime performs the actual work

## System Model

```text
truth state
  -> signal sync
  -> policy
  -> action queue
  -> worker
  -> existing ovp workflow handlers
  -> truth state refresh
```

This makes the new product layer a control plane, not a second workflow system.

## Role Of `ovp --full`

`ovp --full` should remain the bulk reconciler and profile executor.

It should not be replaced by the queue system.

Instead:

1. **`ovp --full` remains the best entrypoint for broad batch runs**
   - full pack/profile execution
   - large inbox refreshes
   - overnight or operator-triggered reconciliation

2. **Queue/worker becomes the best entrypoint for targeted follow-up actions**
   - create a deep dive for one processed source note
   - extract evergreen objects for one deep dive
   - rebuild one object summary
   - later: repair one production-chain gap

3. **Both converge on the same truth state**
   - after execution, truth/index refresh happens
   - signals are re-synced
   - briefing/dashboard see the new state

So the relationship is:

- `ovp --full` = broad scheduled or manual batch execution
- `action queue` = focused incremental execution

They are complementary, not competing.

## Required New Layer

### Action Queue Ledger

Create a dedicated action ledger separate from `signals`.

Minimum fields:

- `action_id`
- `action_kind`
- `source_signal_id`
- `title`
- `target_ref`
- `object_ids`
- `note_paths`
- `status`
  - `queued`
  - `running`
  - `succeeded`
  - `failed`
  - `dismissed`
  - `obsolete`
- `created_at`
- `started_at`
- `finished_at`
- `error`
- `payload`

This must be separate from the signal ledger because:

- a signal is a detected state
- an action is a chosen response to that state

One signal can produce zero, one, or multiple actions over time.

### Worker / Dispatcher

Create a thin worker that:

1. reads queued actions
2. marks one as `running`
3. resolves `action_kind` to a workflow handler
4. executes
5. updates status
6. records audit output

The worker must never encode domain logic directly. It should only dispatch.

## Workflow Handler Registry

Recommended action kinds should map to existing workflow handlers.

Initial mappings:

- `review_contradiction`
  - already handled by existing contradiction review UI
  - keep manual for now

- `rebuild_summary`
  - map to existing `rebuild_compiled_summaries(...)`

- `deep_dive_workflow`
  - target: one processed source note
  - should call a focused execution path, not `ovp --full`

- `object_extraction_workflow`
  - target: one deep dive note
  - should call a focused absorb/object extraction path

- `inspect_production_gap`
  - review-only for now

## Policy Model

Do not force the user to enqueue every signal manually.

Signals should be split into three policy classes:

### Auto-queue

Use for deterministic, low-risk follow-up steps:

- `source_needs_deep_dive`
- `deep_dive_needs_objects`

### Needs Review

Use for semantically riskier tasks:

- `contradiction_open`
- many `production_gap` cases
- later, complex evolution actions

### Ignore / Dismiss

Used for low-value or intentionally skipped states.

This lets the product avoid the “click every signal one by one” trap.

## Idempotency Rules

### Signal idempotency

Keep current deterministic `signal_id`.

Signals continue to represent the current active state, not a user-triggered event.

### Action idempotency

Create actions with a deterministic uniqueness rule:

- `signal_id`
- `action_kind`
- `target_ref`
- `payload_hash`

If the same active signal is seen again, do not enqueue a duplicate action.

### Worker precondition checks

Before running any queued action, the worker must re-check that the signal condition still holds.

If the condition is already satisfied:

- do not execute
- mark the action `obsolete` or `already_satisfied`

This is mandatory for safe retries and restarts.

## History Model

The current signal ledger behaves like an active snapshot.

To support queue semantics properly, the system should distinguish:

1. **active signals**
2. **signal history**
3. **action queue**
4. **action execution history**

Phase 14 does not need the full historical model immediately, but the queue design must not block it.

## Recommended Phase 14 Execution Order

### Task 1: Lock orchestration contract in docs and tests

**Files:**
- Create: `docs/plans/2026-04-15-phase14-orchestration-integration-plan.md`
- Modify later: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`

**Step 1: Document one execution surface**

Write the orchestration contract:
- observation surfaces vs execution surface
- `ovp --full` role
- action queue role
- worker role

**Step 2: Commit**

```bash
git add docs/plans/2026-04-15-phase14-orchestration-integration-plan.md
git commit -m "docs: define phase14 orchestration integration"
```

### Task 2: Introduce action queue ledger

**Files:**
- Modify: `src/openclaw_pipeline/truth_api.py`
- Test: `tests/test_truth_api.py`

**Step 1: Write failing tests**

Cover:
- queue insertion
- deterministic dedupe
- state transitions

**Step 2: Implement minimal queue storage**

Use the existing JSONL/audit style first. Do not introduce a new DB abstraction yet.

**Step 3: Verify**

Run focused tests, then full tests.

### Task 3: Add a worker/dispatcher

**Files:**
- Modify: `src/openclaw_pipeline/commands/*.py`
- Modify: `src/openclaw_pipeline/truth_api.py`
- Test: `tests/test_ui_server.py`, `tests/test_truth_api.py`

**Step 1: Add worker contract**

The worker should:
- fetch queued action
- re-check preconditions
- dispatch by `action_kind`
- record outcome

This dispatch should not be implemented as another local dict of hard-coded handlers.
It should be extracted through the Stage Handler Registry design so queue execution,
profile execution, and autopilot all converge on the same handler contract.

**Step 2: Keep handlers thin**

Do not duplicate pipeline logic in the worker.

### Task 4: Wire the first two actionable workflow bridges

**Files:**
- Modify: `src/openclaw_pipeline/unified_pipeline_enhanced.py`
- Modify: `src/openclaw_pipeline/truth_api.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_runtime_paths.py`, `tests/test_truth_api.py`, `tests/test_ui_smoke.py`

**Step 1: Add focused workflow entrypoints**

Needed handlers:
- one source note -> deep dive
- one deep dive -> evergreen/object extraction

These should reuse existing workflow primitives as much as possible.
They should be registered as focused handlers, not hidden behind `truth_api.py` imports.

**Step 2: Queue auto-policy**

Auto-queue:
- `source_needs_deep_dive`
- `deep_dive_needs_objects`

### Task 5: Surface queue state in UI

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_smoke.py`

Show:
- queued
- running
- succeeded
- failed
- obsolete

`signals` and `briefing` should explain whether the recommended action is:
- already queued
- manual only
- completed

## Non-Goals For This Slice

Do not do these yet:

- full background daemon orchestration
- multi-worker concurrency
- speculative auto-run for contradictions
- a second workflow engine
- replacing `ovp --full`
- heavy graph intelligence work

## Exit Criteria

Phase 14 orchestration integration is good enough when:

1. `signals` and `briefing` surface recommended actions.
2. There is a real action queue ledger.
3. At least one worker path executes queued actions.
4. `source_needs_deep_dive` and `deep_dive_needs_objects` can auto-queue safely.
5. The worker re-checks preconditions and avoids duplicate execution.
6. `ovp --full` remains the batch reconciler instead of being bypassed or duplicated.
