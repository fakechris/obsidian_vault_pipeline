# Phase 7: DB Surface And UI Access Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users directly feel and inspect the truth/data layer in `knowledge.db`, instead of only seeing generated Markdown in the vault.

**Architecture:** Keep SQLite/`knowledge.db` as the authoritative derived/truth-aware runtime store. Add a thin read-only surface above it in three steps: queryable JSON endpoints/commands, browsable compiled DB views, and then a small local UI for object/topic/event/contradiction exploration. Do not replace Markdown views; make DB-backed views the primary inspection surface and Markdown the export/materialization surface.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, existing `knowledge_index.py`, existing materializers, existing pack runtime, pytest, lightweight local server/UI (prefer stdlib/very small dependency footprint).

## Problem Statement

Today the user can mainly feel the system through:

- vault Markdown
- materialized views exported back into Markdown
- CLI summaries (`ovp-doctor`, `ovp-export`, `ovp-query`)

This is enough for operators, but not enough for product feeling. The DB already contains:

- `objects`
- `claims`
- `relations`
- `compiled_summaries`
- `contradictions`
- `timeline_events`

But users still cannot easily:

- browse objects directly from the DB
- inspect relation neighborhoods interactively
- review contradiction/state changes in one place
- compare “truth rows” vs materialized Markdown outputs

That is the next product gap.

## Phase 7 Scope

### Slice A: Readable DB Surface

Add a first-class read/query layer above `knowledge.db`:

- list objects
- fetch object detail
- fetch relations for object
- fetch claims/evidence for object
- list contradictions
- fetch timeline/event dossier inputs

This should not require raw SQL from users.

### Slice B: Compiled DB Views

Add derived read-model builders that turn DB truth into structured JSON/markdown payloads suitable for UI:

- object detail payload
- topic neighborhood payload
- event dossier payload
- contradiction queue payload

These are not exports for publication; they are UI/view-model payloads.

### Slice C: Local UI

Add a minimal local UI/server for inspection:

- object browser
- topic graph/neighborhood browser
- contradiction review browser
- event dossier browser

This should be read-only in the first cut. Review actions can stay CLI-driven initially.

## Non-Goals

- No full multi-user web app
- No remote DB
- No PGlite migration
- No rewriting pack/materializer architecture
- No replacing vault Markdown as the authoring/export surface

## Milestone Definition

Phase 7 is complete when:

1. A user can run one local command and inspect DB-backed object/topic/event/contradiction views.
2. The UI is reading from `knowledge.db`, not re-parsing Markdown.
3. The same truth rows can still be materialized to Markdown/export through existing flows.
4. `research-tech` pack remains the default workflow pack.

## Task 1: Add failing tests for DB read surface

**Files:**
- Create: `tests/test_truth_api.py`
- Reference: `src/openclaw_pipeline/truth_store.py`
- Reference: `src/openclaw_pipeline/knowledge_index.py`

**Step 1: Write the failing tests**

Cover:

- listing objects
- fetching object detail with summary/claims/relations
- listing contradictions
- listing topic neighborhood by object/topic id

**Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_truth_api.py
```

Expected:
- import failure or missing function failures

**Step 3: Implement minimal DB read API**

Create a new module:

- `src/openclaw_pipeline/truth_api.py`

Add pure read helpers, e.g.:

- `list_objects(db_path, limit, offset)`
- `get_object_detail(db_path, object_id)`
- `list_contradictions(db_path, status="open")`
- `get_topic_neighborhood(db_path, object_id, depth=1)`

**Step 4: Run test to verify it passes**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_truth_api.py
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add tests/test_truth_api.py src/openclaw_pipeline/truth_api.py
git commit -m "feat: add truth api read surface"
```

## Task 2: Add CLI access to DB truth surface

**Files:**
- Create: `src/openclaw_pipeline/commands/truth_api.py`
- Modify: `pyproject.toml`
- Test: `tests/test_truth_api_command.py`

**Step 1: Write the failing test**

Cover:

- `ovp-truth objects`
- `ovp-truth object --id ...`
- `ovp-truth contradictions`
- `ovp-truth neighborhood --id ...`

Require JSON output.

**Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_truth_api_command.py
```

**Step 3: Implement minimal command**

Add a read-only command:

- `ovp-truth`

Requirements:

- accepts `--vault-dir`
- resolves `knowledge.db`
- outputs JSON
- no write actions in this slice

**Step 4: Run test to verify it passes**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_truth_api_command.py
```

**Step 5: Commit**

```bash
git add tests/test_truth_api_command.py src/openclaw_pipeline/commands/truth_api.py pyproject.toml
git commit -m "feat: add truth api cli"
```

## Task 3: Build UI view-model payloads

**Files:**
- Create: `src/openclaw_pipeline/ui/view_models.py`
- Test: `tests/test_ui_view_models.py`

**Step 1: Write the failing test**

Cover:

- object page payload shape
- topic neighborhood payload shape
- event dossier payload shape
- contradiction browser payload shape

**Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_view_models.py
```

**Step 3: Implement minimal view-model builders**

Translate truth rows into UI-shaped payloads:

- labels
- summaries
- relation lists
- evidence snippets
- contradiction statuses

Do not render HTML here.

**Step 4: Run test to verify it passes**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_view_models.py
```

**Step 5: Commit**

```bash
git add tests/test_ui_view_models.py src/openclaw_pipeline/ui/view_models.py
git commit -m "feat: add db-backed ui view models"
```

## Task 4: Add minimal local UI server

**Files:**
- Create: `src/openclaw_pipeline/commands/ui_server.py`
- Create: `tests/test_ui_server.py`

**Step 1: Write the failing test**

Cover:

- route registration
- object route returns payload
- contradiction route returns payload
- topic route returns payload

Prefer response contract tests over browser tests in the first cut.

**Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_server.py
```

**Step 3: Implement minimal server**

Start with a simple local HTTP server:

- stdlib `http.server` or similarly light approach
- read-only JSON endpoints first
- optional static HTML shell that fetches JSON and renders lists/detail panes

Expose command:

- `ovp-ui --vault-dir ...`

**Step 4: Run test to verify it passes**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_server.py
```

**Step 5: Commit**

```bash
git add tests/test_ui_server.py src/openclaw_pipeline/commands/ui_server.py
git commit -m "feat: add local db ui server"
```

## Task 5: Add browser-level smoke tests

**Files:**
- Create: `tests/test_ui_smoke.py`

**Step 1: Write the failing test**

Smoke-check:

- object page loads
- contradictions page loads
- topic page loads
- event page loads

Use sample `knowledge.db` fixtures.

**Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_smoke.py
```

**Step 3: Implement only what is needed to pass**

Do not expand into a design system or multi-page app. Keep first cut small and readable.

**Step 4: Run test to verify it passes**

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_smoke.py
```

**Step 5: Commit**

```bash
git add tests/test_ui_smoke.py
git commit -m "test: add db ui smoke coverage"
```

## Task 6: Wire the UI into operator docs

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Modify: `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`

**Step 1: Write docs updates**

Document:

- what DB-backed UI is for
- how it differs from Markdown materialization
- how to launch it
- what routes/screens exist

**Step 2: Verify commands/examples**

Run:

```bash
ovp-doctor --pack research-tech --json
ovp-truth objects --vault-dir /path/to/vault
ovp-ui --vault-dir /path/to/vault
```

**Step 3: Commit**

```bash
git add README.md README_EN.md docs/research-tech/RESEARCH_TECH_VERIFY.md docs/research-tech/RESEARCH_TECH_SKILLPACK.md
git commit -m "docs: add db ui operating guide"
```

## Recommended Execution Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6

This order is deliberate:

- first make DB truth readable
- then make it scriptable
- then make it renderable
- then expose it via UI
- then smoke-test it
- finally document it

## Product Interpretation

Relative to GBrain, this phase intentionally focuses on:

- **feeling the system through the DB**
- **operational inspection**
- **compiled knowledge browsing**

It does **not** yet try to match:

- full publish layer
- richer multi-tenant product shell
- installable recipe runtime

Those can come later. The immediate gap is simpler: users need to see and navigate the truth store directly.
