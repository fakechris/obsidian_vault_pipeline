# Phase 27: Background Intelligence Orchestration Closeout

Status: **Implemented**

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close Milestone 9A by making the existing action queue, worker, handler registry, governance resolver rules, and run ledger behave like one observable execution surface.

**Architecture:** Do not create a second workflow engine. Keep `ovp --full` / `ovp --incremental` as broad batch reconcilers. Keep `signals` and `briefing` as observation and prioritization surfaces. Route focused background work through the existing action queue and worker, with handler resolution coming from pack-declared execution contracts.

**Current state:** This phase is a closeout/hardening slice, not a greenfield build. The codebase already has:

- action queue ledger and JSONL persistence
- `/actions` and `/api/actions`
- `run_actions` worker command with `--once`, `--loop`, and `--safe-only`
- UI option to spawn a detached action worker
- governance-backed resolver rules
- auto-queue rules for `source_needs_deep_dive` and `deep_dive_needs_objects`
- `StageHandlerSpec` focused-action handlers
- `ExecutionContractSpec` binding handler specs to processor contracts
- focused handlers for:
  - `deep_dive_workflow`
  - `object_extraction_workflow`

## Non-Goals

- Do not add a hosted scheduler.
- Do not add harness/session memory.
- Do not add a new semantic relation extractor in this phase.
- Do not route UI pages directly into workflow execution.
- Do not replace `ovp --incremental` or `ovp --full`.

## Task 1: Planning And Documentation Closeout

**Files:**

- `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- `progress.md`
- `task_plan.md`

**Work:**

- Mark Phase 26 as complete and merged.
- Mark Milestone 7 as complete.
- Set the active next reference to this Phase 27 plan.
- Update the task plan so future work starts from Milestone 9A orchestration, not stale Phase 24/26 status.

**Verification:**

- Scan `progress.md`, `task_plan.md`, and the roadmap for stale old-phase active-status wording.
- Expected: no stale active-status references remain.

## Task 2: Worker Runtime Visibility

**Problem:** Phase 25 made long-running pipeline runs observable through the canonical run ledger, but queued focused actions still need a clear operator story: what worker is running, which action it is running, how long it has been running, and whether it is a one-shot or daemon worker.

**Files:**

- `src/ovp_pipeline/commands/run_actions.py`
- `src/ovp_pipeline/truth_api.py`
- `src/ovp_pipeline/ui/view_models.py`
- `src/ovp_pipeline/commands/ui_server.py`
- tests around runtime/action queue views

**Work:**

- Expose action-worker runtime state alongside pipeline runtime state.
- Show worker mode:
  - one-shot
  - loop / daemon
  - safe-only
- Show current action identity:
  - `action_id`
  - `action_kind`
  - `source_signal_id`
  - `target_ref`
- Make `/api/runtime`, `/`, and `/actions` agree on the same worker state.

**Exit condition:**

- An operator can answer: “is an action worker running, what is it doing, and how long has it been doing it?”

## Task 3: Focused Action Preconditions

**Problem:** The worker currently checks whether the source signal still exists. That is necessary but not sufficient. A focused handler should also expose a precondition result that can explain why an action is runnable, obsolete, blocked, or unsafe.

**Files:**

- `src/ovp_pipeline/handler_registry.py`
- `src/ovp_pipeline/focused_actions.py`
- `src/ovp_pipeline/packs/research_tech/focused_actions.py`
- `src/ovp_pipeline/truth_api.py`
- focused-action tests

**Work:**

- Add an explicit precondition contract for focused actions.
- Preserve the existing handler registry as the dispatcher.
- Before execution, record one of:
  - `ready`
  - `obsolete`
  - `blocked`
  - `unsafe`
- Store `blocked_reason` / `obsolete_reason` on the action queue item.
- Do not run a handler if its precondition fails.

**Exit condition:**

- A failed-to-run action does not look like a mysterious worker failure. It has a deterministic reason.

## Task 4: Queue / Signal / Briefing Convergence

**Problem:** `signals`, `briefing`, and `actions` already share lifecycle vocabulary, but Phase 27 should make the relationship explicit enough that the UI is a control plane rather than three related pages.

**Files:**

- `src/ovp_pipeline/truth_api.py`
- `src/ovp_pipeline/ui/view_models.py`
- `src/ovp_pipeline/commands/ui_server.py`
- `tests/test_truth_api.py`
- `tests/test_ui_view_models.py`
- `tests/test_ui_server.py`

**Work:**

- Ensure signal rows expose:
  - recommended action
  - queue status
  - action id
  - resolver rule
  - focused handler provider
  - current blocked / obsolete reason when present
- Ensure briefing priority items expose the same execution state.
- Ensure action queue rows expose:
  - source signal still active or obsolete
  - handler contract provider
  - processor contract provider
  - last result summary

**Exit condition:**

- The same action lifecycle can be understood from `/signals`, `/briefing`, and `/actions`.

## Task 5: Safe Batch Semantics

**Problem:** `Run 5 queued actions` and `Run 5 safe queued actions` exist, but the product contract should make the difference explicit and auditable.

**Files:**

- `src/ovp_pipeline/truth_api.py`
- `src/ovp_pipeline/commands/ui_server.py`
- `tests/test_truth_api.py`
- `tests/test_ui_server.py`

**Work:**

- Make batch execution payloads include:
  - attempted count
  - ran count
  - skipped unsafe count
  - obsolete count
  - failed count
  - stopped reason
- Render these counts after UI batch execution where practical.
- Keep `safe_only=True` grounded in governance/handler contract metadata.

**Exit condition:**

- Safe-only execution is not just a flag; it is visible as an operator-facing result.

## Task 6: Local Validation

**Work:**

- Install the current package locally.
- Run a representative incremental or focused queue validation against the local vault.
- Confirm:
  - auto-queue creates no duplicates
  - safe-only worker runs only safe actions
  - stale/blocked/obsolete states are observable
  - `/api/runtime`, `/`, `/signals`, `/briefing`, and `/actions` agree

**Verification:**

- `pytest tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py tests/test_watch_progress_command.py -q`
- `pytest -q`
- Real local validation notes in `progress.md`

## Implementation Notes

Implemented in the local working tree:

- action-worker runtime state is persisted to `60-Logs/action-worker.json`,
- `/api/runtime` and `/` expose focused action-worker state,
- focused actions evaluate preconditions before handler execution,
- action queue rows persist `blocked_reason` and `obsolete_reason`,
- queue / signal / briefing surfaces share lifecycle metadata,
- safe batch execution returns auditable counts.

## Closeout Criteria

Phase 27 is complete when:

- Milestone 9A no longer describes a planned architecture; it describes the actual action execution contract.
- There is still only one execution surface for focused background work: the action queue.
- The worker is observable as runtime state.
- Focused actions have explicit preconditions and deterministic blocked/obsolete reasons.
- Safe batch execution produces auditable counts.
- Signals, briefing, and actions expose the same queue/worker lifecycle truth.
