# Phase 21: Product Shell And Operator UX

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current post-Phase-20 workbench into a clearer operator product where first-time users can see the main workflows, move between surfaces intentionally, and act without reconstructing the shell in their head.

**Architecture:** Keep the existing truth/runtime/contracts intact. Do not introduce a frontend rewrite, new routing framework, or new product shell backend. Spend the current `truth_api.py` + `ui/view_models.py` + `commands/ui_server.py` stack on clearer shell-level workflow grouping, stronger cross-surface affordances, and denser page-level operator context.

**Tech Stack:** Python 3.13, stdlib HTTP UI shell, `ui/view_models.py`, `commands/ui_server.py`, existing payload contracts, pytest.

**Status:** Complete

## Why This Is The Right Next Phase

`Phase 19` solved entry products.  
`Phase 20` solved semantic trust and production-chain legibility.

What is still weak is the shell itself:

- the home/dashboard still reads like a pile of useful cards, not a clear workflow map,
- cross-surface navigation still depends too much on remembering which route to open,
- operator actions exist, but the UI does not yet make the common flows feel explicit,
- the product is trustworthy now, but not yet obviously easy to operate.

That makes `Milestone 6` the highest-leverage next step.

## Product Thesis

After `Phase 21`, a first-time user should be able to answer four questions without reading docs:

1. Where should I start?
2. Which route should I open to inspect, review, trace, or search?
3. What should I do next from this page?
4. Which surfaces are shared-shell surfaces vs research-only surfaces?

This phase is therefore about:

- **workflow-first navigation**
- **operator affordance density**
- **page-level next-step clarity**

## What Phase 21 Must Deliver

### 1. Dashboard Workflow IA

The home/dashboard should expose the main workbench workflows as named groups, not only as raw cards:

- orient
- inspect
- review
- trace
- explore

This should be a stable payload contract plus a visible UI block near the top of `/`.

### 2. Cross-Surface Operator Affordances

Key pages should expose a consistent “what to do next from here” strip, not only local card links:

- object
- topic
- events
- contradictions
- signals
- production

The goal is not more links. The goal is more legible actions.

### 3. Clearer Shared-Shell vs Research-Shell Semantics

The shell should make it more obvious when a page is:

- shared-shell available
- inherited from `research-tech`
- research-specific / hidden for compatibility packs

This should remain driven by existing contracts, not by new ad-hoc booleans.

### 4. Denser, Better Ordered Page Context

The shell should favor:

- immediate state summary,
- next-step affordances,
- then detailed evidence/review blocks.

This is ordering and grouping work, not a visual redesign phase.

## What Phase 21 Should Not Do

Do not:

- reopen event/contradiction semantics,
- reopen production-chain modeling,
- add temporal truth,
- build a new UI stack,
- redesign the graph experience,
- or widen into background intelligence / signal automation.

## Recommended Implementation Shape

### Task 1: Add Dashboard Workflow Groups

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- stable dashboard `workflow_groups`
- visible “workflow map” block on `/`
- workflow links preserve pack scope

### Task 2: Add Consistent Operator Action Rails

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- note/object/topic/event/contradiction/signal/production pages expose a small, consistent action rail
- rails are contract-driven or payload-driven, not stringly scattered in templates

### Task 3: Reorder And Tighten Page Shells

**Files:**
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- shell pages lead with current-state + next-step context
- operator sections become easier to scan
- shared-shell/research-shell status remains explicit

### Task 4: Verify And Close Out

**Files:**
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Modify: `progress.md`
- Modify: `task_plan.md`

**Deliverable:**

- verify docs cover workflow IA and operator rails
- milestone sequencing points to the next real gap after shell UX
- `Phase 21` is ready to close cleanly

## Exit Condition

`Phase 21` is complete when all of the following are true:

1. `/` exposes clear workflow groups instead of only a pile of cards.
2. Key workbench pages expose explicit next-step/operator rails.
3. Shared-shell vs research-shell availability remains clear from the product surface.
4. A first-time user can navigate the main workflows without reading plan docs.

## Closeout

`Phase 21` is complete.

What landed:

- dashboard `workflow_groups` and a visible `Workflow Map` on `/`
- consistent `Next Actions` operator rails on:
  - note
  - object
  - topic
  - events
  - contradictions
  - signals
  - production
- page-shell ordering that now leads with:
  - a lead compiled section
  - explicit next actions
  - then contract/status and deeper detail blocks

Verification basis:

- focused red/green tests for workflow groups, operator rails, and shell ordering
- full `tests/test_ui_view_models.py` and `tests/test_ui_server.py` suite green

Next real gap after shell UX:

- `Milestone 7: Active Signal Loop`
