# Phase 25: Observable Runtime And Run Ledger

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current pipeline runtime from a black-box script chain into an operator-visible workflow with one canonical run-state ledger, explicit work-unit progress, stable heartbeat semantics, and evidence-backed stage diagnostics.

**Architecture:** Keep the current pipeline runner, stage handlers, transaction files, and `pipeline.jsonl`, but stop asking operators to infer state by stitching them together manually. This phase introduces one **run ledger** as the single source of truth for current workflow state. The ledger answers "what is happening now?" while `pipeline.jsonl` remains the append-only evidence trail answering "what happened?". `watch_progress`, `ovp-ui`, and future operator surfaces must read the same ledger instead of maintaining parallel interpretations.

**Tech Stack:** Python 3.13, stdlib JSON, current transaction runtime, `unified_pipeline_enhanced.py`, `txn.py`, `commands/watch_progress.py`, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, pytest.

**Status:** Planned

## Why This Must Precede More Intelligence Work

`Phase 24` was planned to tighten brain-first lookup and backlink legibility inside `Milestone 7`.

That is still useful, but it is **not** the highest-risk gap anymore.

The system currently fails a more basic product requirement:

- operators cannot tell whether a long-running workflow is healthy or stuck,
- there is no trustworthy percentage or work-unit progress,
- stage transitions are coarse and sometimes silent,
- long steps such as `fix_links` and `absorb` look frozen even while doing work,
- "what is happening now?" currently requires combining:
  - a transaction file,
  - `pipeline.jsonl`,
  - `ps`,
  - and an out-of-band watcher.

That violates both principles now driving the project:

1. **Single source of truth**
2. **Observable first**

So the next step should not be more semantics. It should be a runtime contract hardening phase.

## Approach Options Considered

### Option A: Better Watchers Only

Keep the current runtime shape and keep improving `watch_progress`.

Pros:

- cheap,
- low-risk,
- good for immediate operator relief.

Cons:

- does not create a canonical source of truth,
- still forces the product to infer runtime state from side effects,
- does not solve silent stage internals or missing progress units.

### Option B: Per-Stage Ad Hoc Logging

Patch each slow stage to emit more events into `pipeline.jsonl`.

Pros:

- improves local debugging,
- uses current infrastructure.

Cons:

- pushes "current truth" into an append-only log,
- makes every surface re-implement interpretation logic,
- does not create one stable operator contract.

### Option C: Canonical Run Ledger Plus Evidence Stream

Introduce a first-class run ledger for live state and keep `pipeline.jsonl` as event evidence.

Pros:

- gives the product one authoritative answer for current workflow state,
- supports progress percentage only when the data is real,
- lets watcher, CLI, and UI stop disagreeing,
- creates a stable foundation for future queue/orchestration work.

Cons:

- requires touching runtime contracts rather than only UI/watcher code.

**Recommendation:** Option C.

## Product Thesis

After `Phase 25`, an operator should be able to answer these questions from one canonical runtime state object:

1. Is the workflow queued, running, blocked, completed, failed, or stale?
2. Which step is active right now?
3. How many work units does that step have, and how many are done?
4. What file/object/work item is being processed now?
5. What was the last meaningful piece of progress?
6. If the workflow is slow, is it actually progressing or has it stopped making forward motion?

This phase is therefore about:

- **canonical run-state truth**
- **real work-unit progress**
- **heartbeat and stall semantics**
- **evidence-backed operator visibility**

## What Phase 25 Must Deliver

### 1. Run Ledger Contract v1

Add a canonical runtime ledger for each pipeline run.

Minimum top-level fields:

- `run_id`
- `run_state`
- `workflow_profile`
- `pack_name`
- `started_at`
- `updated_at`
- `heartbeat_at`
- `current_step`
- `last_meaningful_event`
- `stale`
- `blocked_reason`
- `error_summary`

Minimum current-step fields:

- `step_name`
- `step_state`
- `step_started_at`
- `step_heartbeat_at`
- `work_units_total`
- `work_units_done`
- `work_units_failed`
- `current_item`
- `progress_percent`
- `progress_summary`

`progress_percent` must only be populated when the system has a real denominator.
No fake percentages based on phase count.

### 2. Stage Progress Contract v1

Every long-running step must expose explicit work units or explicitly declare that it cannot.

Minimum support for:

- `pinboard`
- `pinboard_process`
- `clippings`
- `articles`
- `quality`
- `fix_links`
- `absorb`
- `registry_sync`
- `moc`
- `knowledge_index`

At minimum, each step should declare:

- `progress_mode`: `counted | indeterminate`
- `work_units_total`
- `work_units_done`
- `current_item`
- heartbeat updates while running

For `absorb`, counted progress should be based on the qualified-file batch, not inferred from Evergreen count deltas.

### 3. Runtime/Event Boundary Contract

Clarify the relationship between:

- run ledger
- transaction record
- `pipeline.jsonl`

Recommended split:

- **run ledger / transaction** = current truth
- **pipeline.jsonl** = evidence trail
- **watcher/UI** = readers of current truth plus recent evidence

This phase should collapse duplicate interpretations, not add another layer.

### 4. Operator Surface Unification

`watch_progress`, `ovp-ui`, and future runtime views should all read the same run ledger.

Minimum visible operator answers:

- current step
- real progress
- current item
- last meaningful event
- stale vs healthy distinction
- explicit incomplete downstream tail:
  - e.g. `absorb` running, `registry_sync` not started yet

### 5. Repair And Cleanup Semantics

Make stale / abandoned runs explicit and repairable without guessing.

Minimum requirements:

- stale transaction classification is deterministic
- active vs stale runs are clearly separated
- repair tooling can reconcile abandoned run ledgers cleanly
- the operator can tell whether a new run is safe to start

## What Phase 25 Must Not Do

Explicit deferrals:

- new memory backends
- temporal truth
- more graph synthesis
- broader background autonomy
- reopening shell IA work

This is not a semantic phase. It is a runtime legibility phase.

## Recommended Implementation Shape

### Task 1: Write The Failing Runtime-Ledger Tests

**Files:**
- Modify: `tests/test_runtime_paths.py`
- Modify: `tests/test_watch_progress_command.py`
- Add: `tests/test_txn_runtime_ledger.py`
- Add or Modify: `tests/test_truth_api.py`
- Add or Modify: `tests/test_ui_view_models.py`

**Deliverable:**

- failing tests for canonical run ledger shape
- failing tests for counted vs indeterminate progress
- failing tests for stale/active separation
- failing tests proving watcher/UI read the same runtime truth

### Task 2: Implement Canonical Run Ledger Updates

**Files:**
- Modify: `src/ovp_pipeline/txn.py`
- Modify: `src/ovp_pipeline/unified_pipeline_enhanced.py`
- Modify: stage-related runtime helpers as needed
- Test: `tests/test_txn_runtime_ledger.py`

**Deliverable:**

- one canonical run ledger contract
- current-step progress fields
- heartbeat updates during long-running stages

### Task 3: Add Step-Level Progress Accounting

**Files:**
- Modify: `src/ovp_pipeline/unified_pipeline_enhanced.py`
- Modify: `src/ovp_pipeline/commands/absorb.py`
- Modify: `src/ovp_pipeline/auto_evergreen_extractor.py`
- Modify any other step entry points that need progress callbacks
- Test: `tests/test_runtime_paths.py`

**Deliverable:**

- real work-unit progress for long-running steps
- current-item reporting where available
- explicit `indeterminate` state where true counts are impossible

### Task 4: Unify Operator Readers

**Files:**
- Modify: `src/ovp_pipeline/commands/watch_progress.py`
- Modify: `src/ovp_pipeline/truth_api.py`
- Modify: `src/ovp_pipeline/ui/view_models.py`
- Modify: `src/ovp_pipeline/commands/ui_server.py`
- Test: `tests/test_watch_progress_command.py`
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_view_models.py`

**Deliverable:**

- watcher, API, and UI all read the same current-state contract
- operator surfaces show honest progress rather than inferred guesses

### Task 5: Verify With Real Incremental Workflow

**Files:**
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Modify: `progress.md`
- Modify: `task_plan.md`

**Deliverable:**

- a real local `ovp --incremental` validation checklist
- explicit operator checks for:
  - `pinboard`
  - `pinboard_process`
  - `clippings`
  - `articles`
  - `quality`
  - `fix_links`
  - `absorb`
  - `registry_sync`
  - `moc`
  - `knowledge_index`

### Task 6: Reposition Phase 24

**Files:**
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Modify: `docs/plans/2026-04-18-phase24-brain-first-lookup-and-backlink-legibility.md`

**Deliverable:**

- `Phase 25` becomes the next execution slice
- `Phase 24` remains planned but is explicitly gated on runtime observability hardening

## Exit Condition

`Phase 25` is complete when all of the following are true:

1. one canonical run ledger exists for current pipeline state,
2. long-running steps expose honest progress semantics with real denominators when available,
3. watcher, API, and UI read the same runtime truth,
4. operators can tell active vs stale vs blocked runs without checking `ps` manually,
5. a real `ovp --incremental` run can be observed end-to-end through the same contract.

## Closeout Target

If `Phase 25` lands cleanly:

- `Milestone 7` becomes trustworthy enough to continue,
- `Phase 24` can proceed on top of a non-black-box runtime,
- and future active/background intelligence work no longer has to compensate for missing workflow truth.
