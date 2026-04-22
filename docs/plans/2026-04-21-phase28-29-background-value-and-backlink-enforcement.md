# Phase 28/29: Background Value And Backlink Enforcement Implementation Plan

Status: **Implemented**

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the next two roadmap gaps by proving background intelligence value in the briefing surface and enforcing backlink provenance before focused object writes.

**Architecture:** Keep `signals`, `briefing`, and `actions` on the existing signal ledger, action queue, and run ledger. Do not add a second scheduler, hidden memory backend, or semantic relation extractor. Phase 28 adds value/provenance/policy fields to the current briefing payload. Phase 29 reuses the existing `backlink_expectation` contract as a focused-action precondition for object extraction.

**Tech Stack:** Python, SQLite-backed `knowledge.db`, JSONL ledgers in `60-Logs`, pack governance contracts, pytest.

## Implementation Notes

- Briefing snapshots now expose `first_useful_sign_check` with value status, evidence count, and actionability.
- Briefing insights and priority items now expose `value_kind`, `value_reason`, `evidence_count`, and `actionability`.
- Briefing snapshots now expose `background_policy` derived from effective governance signal rules and current action queue state.
- `/briefing` now renders `Value Proof` and `Background Policy` sections.
- `object_extraction_workflow` now blocks before handler dispatch when the target deep dive has an unsatisfied `backlink_expectation`.

## Task 1: Roadmap State Closeout

**Files:**

- Modify: `task_plan.md`
- Modify: `progress.md`
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`

**Steps:**

1. Mark Phase 27 as merged in PR #43.
2. Update verification counts to the post-review result: `689 passed`.
3. Set Phase 28/29 as the current active plan.
4. Preserve the existing non-goals: no hosted scheduler, no harness/session memory, no hidden semantic relation extractor.

**Verification:**

- Scan `task_plan.md`, `progress.md`, and the roadmap for stale active Phase 27 closeout wording.
- Expected: no stale active Phase 27 wording remains.

## Task 2: Phase 28 Background Intelligence Value Contract

**Files:**

- Modify: `src/ovp_pipeline/packs/research_tech/surfaces.py`
- Modify: `src/ovp_pipeline/ui/view_models.py`
- Modify: `src/ovp_pipeline/commands/ui_server.py`
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_server.py`

**Behavior:**

- Every briefing insight and priority item should expose why it is useful:
  - `value_kind`
  - `value_reason`
  - `evidence_count`
  - `actionability`
- The briefing snapshot should expose `first_useful_sign_check`:
  - `status`
  - `kind`
  - `reason`
  - `evidence_count`
  - `actionability`
- The briefing snapshot should expose `background_policy`:
  - governed signal types
  - auto-queue enabled types
  - review-only types
  - active auto-queued signal counts
  - skipped signal counts and reasons

**TDD Steps:**

1. Add a failing test that `get_briefing_snapshot(...)` returns `first_useful_sign_check` with evidence and actionability.
2. Add a failing test that `get_briefing_snapshot(...)` returns a `background_policy` summary derived from governance rules and active signals.
3. Add a failing UI test that `/briefing` renders value proof and background policy.
4. Implement the smallest payload helpers in `research_tech.surfaces`.
5. Pass the targeted tests.

## Task 3: Phase 29 Backlink Enforcement At Write Time

**Files:**

- Modify: `src/ovp_pipeline/truth_api.py`
- Test: `tests/test_truth_api.py`

**Behavior:**

- `object_extraction_workflow` must not run when its target deep dive has an unsatisfied `backlink_expectation`.
- The precondition should block before handler dispatch.
- The blocked action should persist:
  - `status=blocked`
  - `precondition_status=blocked`
  - `blocked_reason=backlink_expectation_failed:<status>:<note_path>`
- `deep_dive_workflow` should not be blocked by this gate, because a source note naturally starts with missing downstream links.

**TDD Steps:**

1. Add a failing test that queues `object_extraction_workflow` for a deep dive with no source backlink.
2. Verify the handler is not called and the action is blocked with a backlink reason.
3. Add a passing-control test or extend an existing object extraction test to prove a deep dive with source provenance can still run.
4. Implement a small focused-action backlink precondition helper that calls `get_note_traceability(...)`.
5. Pass the targeted tests.

## Task 4: Verification

Run:

```bash
ruff check src/ovp_pipeline/truth_api.py src/ovp_pipeline/packs/research_tech/surfaces.py src/ovp_pipeline/ui/view_models.py src/ovp_pipeline/commands/ui_server.py tests/test_truth_api.py tests/test_ui_server.py
pytest tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py -q
pytest -q
git diff --check
```

Expected:

- Ruff passes.
- Targeted tests pass.
- Full suite passes.
- No whitespace errors.

Verified locally:

- `ruff check src/ovp_pipeline/truth_api.py src/ovp_pipeline/packs/research_tech/surfaces.py src/ovp_pipeline/ui/view_models.py src/ovp_pipeline/commands/ui_server.py tests/test_truth_api.py tests/test_ui_server.py` -> `All checks passed!`
- `pytest tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py -q` -> `255 passed`
- `pytest -q` -> `690 passed`
- `git diff --check` -> clean
