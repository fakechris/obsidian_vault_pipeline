# BL-001 Reader Shell Route Split Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/` a reader-first Library home and move the current operator dashboard to `/ops`.

**Architecture:** This is a Layer 3 access-surface change only. It must reuse existing payloads and renderers, preserve the current dashboard behavior under `/ops`, and avoid introducing new state or schema. User-facing copy must use product language: Library, Map, Workbench.

**Tech Stack:** Python HTTP server in `src/ovp_pipeline/commands/ui_server.py`, pytest-based HTTP smoke tests in `tests/test_ui_server.py` and related UI tests.

## Product Rules

- Do not put internal architecture terms on the reader homepage.
- Avoid terms such as projection, derived, canonical, KSR, claim lifecycle, hot path, resolver, and governance.
- Primary navigation should read as a product, not as an engineering console:
  - `Library`
  - `Map`
  - `Workbench`
- `/ops` owns the current operator dashboard and maintenance workflows.
- `/` should answer: what is in my knowledge library, what is worth reading, how things connect, and where maintenance work lives.

## Task 1: Lock The Route Contract

**Files:**

- Modify: `tests/test_ui_server.py`

**Step 1: Write failing tests**

Add tests proving:

- `/` renders a reader-first Library page.
- `/` does not render the old dashboard-only `Workflow Map` page.
- top navigation includes `Library`, `Map`, and `Workbench`.
- `/ops` renders the existing dashboard content, including `Workflow Map`.

**Step 2: Verify RED**

Run:

```bash
python -m pytest tests/test_ui_server.py::test_ui_server_root_serves_reader_library_home tests/test_ui_server.py::test_ui_server_ops_route_serves_operator_dashboard -q
```

Expected: fail because `/` still renders the current dashboard and `/ops` is not routed.

## Task 2: Implement Minimal Route Split

**Files:**

- Modify: `src/ovp_pipeline/commands/ui_server.py`

**Step 1: Implement**

- Add a reader-home renderer that uses existing dashboard payload fields.
- Change `GET /` to render the reader-home renderer.
- Add `GET /ops` to render the current `_render_dashboard(payload)`.
- Change shell nav to `Library`, `Map`, `Workbench`.
- Keep existing routes such as `/workbench`, `/explore`, `/objects`, `/search`, `/briefing`, and `/actions`.

**Step 2: Verify GREEN**

Run:

```bash
python -m pytest tests/test_ui_server.py::test_ui_server_root_serves_reader_library_home tests/test_ui_server.py::test_ui_server_ops_route_serves_operator_dashboard -q
```

Expected: pass.

## Task 3: Preserve Existing UI Behavior

**Files:**

- Modify tests as needed only to reflect the intentional route split.

**Step 1: Run targeted regression tests**

Run:

```bash
python -m pytest tests/test_ui_server.py tests/test_workbench.py tests/test_explore_ui.py tests/test_ui_smoke.py -q
```

Expected: pass.

**Step 2: Update backlog state**

Modify `BACKLOG.md`:

- mark `BL-000` as done
- mark `BL-001` as active

## Task 4: Final Verification And PR

Run:

```bash
git diff --check
python -m pytest tests/test_ui_server.py tests/test_workbench.py tests/test_explore_ui.py tests/test_ui_smoke.py -q
```

Then commit and open a PR for `BL-001`.
