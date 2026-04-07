# Knowledge DB Phase 4 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add stable read tools on top of `knowledge.db` for search, page fetch, stats, and recent audit access without introducing any canonical write surface.

**Architecture:** Phase 4 does not add an MCP transport yet. Instead, it builds the read-tool semantics the future MCP layer will expose: keyword search from FTS, semantic query from embeddings, page retrieval from `pages_index`, stats from table counts, and recent audit reads from `audit_events`. These helpers remain read-only and will rebuild the DB on demand when missing.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, existing `knowledge_index.py`, argparse, pytest.

### Task 1: Add failing tests for read-tool helpers

**Files:**
- Modify: `tests/test_knowledge_index.py`

**Step 1: Write the failing tests**

Cover:
- `search_knowledge_index()` returns ranked pages from FTS content
- `get_knowledge_page()` returns canonical page metadata/body
- `knowledge_index_stats()` returns counts across core tables
- `recent_audit_events()` returns recent audit rows in descending order
- `ovp-knowledge-index` supports:
  - `--search`
  - `--get`
  - `--stats`
  - `--audit-recent`

**Step 2: Run tests to verify they fail**

Run:
- `pytest -q tests/test_knowledge_index.py -k "search_knowledge or get_knowledge_page or knowledge_index_stats or audit_recent"`

Expected:
- FAIL because these helpers and CLI modes do not exist yet

### Task 2: Implement read-only helpers

**Files:**
- Modify: `src/openclaw_pipeline/knowledge_index.py`

**Step 1: Implement helper functions**

Add:
- `search_knowledge_index(vault_dir, query, limit=10)`
- `get_knowledge_page(vault_dir, slug)`
- `knowledge_index_stats(vault_dir)`
- `recent_audit_events(vault_dir, limit=20, source_log=None)`

**Step 2: Behavior requirements**

- search uses `page_fts`
- get returns canonical slug/title/type/path/body/frontmatter
- stats returns table counts and DB path
- recent audit reads newest rows first and supports optional log filter
- all helpers rebuild the DB on demand if missing

**Step 3: Run focused tests**

Run:
- `pytest -q tests/test_knowledge_index.py -k "search_knowledge or get_knowledge_page or knowledge_index_stats or audit_recent"`

Expected:
- PASS

### Task 3: Extend CLI read modes and verify

**Files:**
- Modify: `src/openclaw_pipeline/commands/knowledge_index.py`
- Modify: `tests/test_knowledge_index.py`

**Step 1: Add CLI flags**

Add:
- `--search`
- `--get`
- `--stats`
- `--audit-recent`
- `--source-log`

**Step 2: Route each mode to the corresponding helper**

**Step 3: Run tests**

Run:
- `pytest -q tests/test_knowledge_index.py`

Expected:
- PASS

**Step 4: Run full verification**

Run:
- `python3 -m compileall src/openclaw_pipeline`
- `pytest -q`
