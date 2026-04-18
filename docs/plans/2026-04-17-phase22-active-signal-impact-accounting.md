# Phase 22: Active Signal Impact Accounting

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current passive signal surfaces into an active, trustworthy loop by making each signal explain what execution it triggered, where it currently sits, and whether it produced visible downstream knowledge changes.

**Architecture:** Keep the existing signal ledger, action queue, focused-action contracts, and UI shell. Do not widen into background note hooks, asynchronous entity detection, or temporal memory infrastructure. This phase should spend the current `signal -> recommended_action -> action_queue -> focused_action result -> truth refresh` pipeline on impact accounting and lifecycle legibility, so operators can see which signals actually improved the system and which ones stalled.

**Tech Stack:** Python 3.13, stdlib JSON/SQLite runtime, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, current `research-tech` observation surfaces, pytest.

**Status:** Complete

## Why This Is The Right Next Phase

`Phase 21` made the shell easier to operate.

What is still weak is the active loop itself:

- signals can recommend actions, but they do not clearly explain whether execution ever happened,
- queued/running/failed/succeeded state exists, but it is not yet shaped into a product-level impact summary,
- action results are stored, but users cannot quickly tell whether a signal led to visible downstream change,
- the workbench can show current signals, but not yet whether those signals were productive.

That makes the first slice of `Milestone 7` a traceable signal loop, not broader automation.

## Product Thesis

After `Phase 22`, an operator should be able to answer five questions from `/signals`, `/briefing`, and `/actions` without reading logs:

1. Did this signal create an action or not?
2. If it created one, what lifecycle state is it in now?
3. If execution finished, did it produce any visible downstream artifact or knowledge change?
4. Which signals are productive, stalled, failed, or still waiting?
5. What is the first useful sign that the signal loop is improving the workbench?

This phase is therefore about:

- **signal lifecycle legibility**
- **action/result impact accounting**
- **briefing-level visibility into productive vs stalled work**

## What Phase 22 Must Deliver

### 1. Signal Impact Contract v1

Each signal should expose an explicit `impact_summary` derived from:

- recommended action metadata
- action queue state
- action result payloads

The contract should cover at least:

- `impact_status`
- `lifecycle_stage`
- `action_kind`
- `action_status`
- `impact_label`
- `impact_detail`
- a small set of stable counts or artifacts when available

This should stay deterministic and be computed from existing runtime state.

### 2. Signal Browser Upgrade

`/signals` should surface the signal impact contract directly:

- productive signals should look different from merely queued ones,
- failed/stalled signals should be clearly legible,
- the page should explain the active loop instead of only listing signals.

This should remain a product-layer improvement over existing payloads, not a new signal engine.

### 3. Briefing Upgrade

`/briefing` should stop treating all signals equally.

It should summarize the active loop with categories such as:

- productive recently
- waiting in queue
- blocked / failed
- review-only / no execution path

The first useful sign of `Milestone 7` is that the orientation layer can tell whether the signal loop is producing downstream value.

### 4. Action Queue Legibility

`/actions` should explain action results in terms that line up with the signal impact contract, so the queue page and the signal page are describing the same lifecycle.

Do not introduce a second interpretation model on the action page.

### 5. Phase Closeout Without Widening Scope

This phase should end once impact accounting is visible and stable.

Do not widen into:

- note save/update hooks,
- generalized inbound signal capture,
- background enrichment workers,
- entity evolution,
- or benchmark infrastructure.

Those remain later slices of `Milestone 7`.

## What Phase 22 Should Not Do

Explicit deferrals:

- automatic capture on every note save/update
- new signal types unrelated to current trusted surfaces
- opaque “AI decided this matters” background processing
- temporal truth modeling
- harness/session memory capture
- graph synthesis or graph-triggered automation

## Recommended Implementation Shape

### Task 1: Write The Failing Impact-Contract Tests

**Files:**
- Modify: `tests/test_truth_api.py`
- Modify: `tests/test_ui_view_models.py`
- Modify: `tests/test_ui_server.py`

**Deliverable:**

- failing tests for signal impact summaries
- failing tests for briefing/action payload exposure
- failing tests for UI rendering of impact status

### Task 2: Implement Signal Impact Accounting In `truth_api.py`

**Files:**
- Modify: `src/openclaw_pipeline/truth_api.py`
- Test: `tests/test_truth_api.py`

**Deliverable:**

- stable, deterministic impact summary attached to signals
- queue/result-derived lifecycle status
- minimal artifact/count extraction from focused action results

### Task 3: Spend The Contract In View Models

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Test: `tests/test_ui_view_models.py`

**Deliverable:**

- `/signals` payloads expose impact-aware counts and summaries
- `/briefing` compiled sections distinguish productive vs stalled loop outcomes
- `/actions` payload aligns with the same impact vocabulary

### Task 4: Render The Impact Loop In The Shell

**Files:**
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- signals, briefing, and actions pages render the new impact/lifecycle summaries
- the shell explains what happened, not only what exists

### Task 5: Verify And Close Out

**Files:**
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Modify: `progress.md`
- Modify: `task_plan.md`

**Deliverable:**

- verify docs cover the new impact-accounting surfaces
- milestone plan points to the next real `Milestone 7` slice after this one
- `Phase 22` is ready to close cleanly

## Exit Condition

`Phase 22` is complete when all of the following are true:

1. signals expose a stable impact/lifecycle summary grounded in current queue/result state,
2. `/signals` makes productive vs stalled vs failed signal outcomes legible,
3. `/briefing` summarizes signal-loop productivity instead of only recent signal presence,
4. `/actions` and `/signals` describe execution using the same lifecycle language,
5. focused tests lock the contract so future signal heuristics cannot silently erase impact visibility.

## Closeout

`Phase 22` is complete.

What landed:

- `truth_api.py` now attaches deterministic `impact_summary` contracts to:
  - signal rows
  - action queue rows
- the impact contract currently distinguishes:
  - `review_only`
  - `ready`
  - `waiting`
  - `running`
  - `productive`
  - `completed`
  - `failed`
  - `stalled`
- `/signals` now exposes impact-aware counts and item-level lifecycle explanations instead of only queue badges
- `/actions` now uses the same lifecycle vocabulary as `/signals`
- `/briefing` now exposes:
  - `loop_summary`
  - a leading `signal_loop` compiled section
  - a more useful `first_useful_sign` when a productive signal exists

What this phase intentionally did **not** do:

- add note-save hooks
- widen signal detection
- introduce background entity intelligence
- reopen temporal truth or harness memory

Next real gap inside `Milestone 7`:

- selected inbound signal capture on save/update
- stronger audit visibility for what changed, linked, or was skipped
- still without widening into opaque background automation
