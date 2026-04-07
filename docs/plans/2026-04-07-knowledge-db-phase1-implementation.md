# Knowledge DB Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a derived `knowledge.db` index layer that can be rebuilt from canonical vault state without introducing a second source of truth.

**Architecture:** Phase 1 adds a small SQLite subsystem only for derived indexing. It scans canonical Evergreen pages, stores a searchable page index plus structured links, and exposes a rebuild CLI. The database is always disposable and regenerated from markdown + registry state.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, existing runtime helpers, registry-aware graph/link/frontmatter parsers, pytest.

### Task 1: Add failing tests for the derived knowledge index contract

**Files:**
- Create: `tests/test_knowledge_index.py`
- Modify: `tests/test_runtime_paths.py`

**Step 1: Write the failing tests**

Cover:
- `VaultLayout` exposes a deterministic `knowledge_db` path under `60-Logs/knowledge.db`
- rebuilding the index from a temp vault creates `knowledge.db`
- the rebuilt database contains:
  - `pages_index`
  - `page_fts`
  - `page_links`
- `pages_index.slug` uses canonical `note_id`
- a wikilink in markdown becomes a resolved `page_links` row

**Step 2: Run tests to verify they fail**

Run:
- `pytest -q tests/test_knowledge_index.py tests/test_runtime_paths.py -k knowledge`

Expected:
- FAIL because `knowledge_db` path and index module do not exist yet

**Step 3: Commit**

```bash
git add tests/test_knowledge_index.py tests/test_runtime_paths.py
git commit -m "test: cover derived knowledge index contract"
```

### Task 2: Add the SQLite knowledge index core

**Files:**
- Create: `src/openclaw_pipeline/knowledge_index.py`
- Modify: `src/openclaw_pipeline/runtime.py`

**Step 1: Implement the database builder**

Required behavior:
- create `knowledge.db` at `60-Logs/knowledge.db`
- initialize schema with:
  - `pages_index`
  - `page_fts`
  - `page_links`
- rebuild from canonical markdown under `10-Knowledge/Evergreen`
- use canonical `note_id` / slug identity
- resolve links through the existing registry-aware graph/link parsing stack
- database can be deleted and rebuilt with no data loss

**Step 2: Run focused tests**

Run:
- `pytest -q tests/test_knowledge_index.py tests/test_runtime_paths.py -k knowledge`

Expected:
- PASS

**Step 3: Commit**

```bash
git add src/openclaw_pipeline/knowledge_index.py src/openclaw_pipeline/runtime.py tests/test_knowledge_index.py tests/test_runtime_paths.py
git commit -m "feat: add derived knowledge index builder"
```

### Task 3: Add a thin CLI for rebuilding and inspecting the index

**Files:**
- Create: `src/openclaw_pipeline/commands/knowledge_index.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_knowledge_index.py`

**Step 1: Write failing command tests**

Cover:
- `ovp-knowledge-index --help` exists
- `ovp-knowledge-index --rebuild --json` rebuilds the DB and prints row counts

**Step 2: Implement the minimal command**

Required behavior:
- `--vault-dir`
- `--rebuild`
- `--json`
- returns structured counts for indexed pages and links

**Step 3: Run tests**

Run:
- `pytest -q tests/test_knowledge_index.py`

Expected:
- PASS

**Step 4: Commit**

```bash
git add pyproject.toml src/openclaw_pipeline/commands/knowledge_index.py tests/test_knowledge_index.py
git commit -m "feat: add knowledge index rebuild command"
```

### Task 4: Full verification for Phase 1

**Files:**
- Verify all changed files

**Step 1: Run compile**

Run:
- `python3 -m compileall src/openclaw_pipeline`

**Step 2: Run tests**

Run:
- `pytest -q`

**Step 3: Commit**

```bash
git add -A
git commit -m "test: verify derived knowledge index phase 1"
```
