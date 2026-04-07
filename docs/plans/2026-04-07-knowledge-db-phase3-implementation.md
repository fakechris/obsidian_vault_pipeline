# Knowledge DB Phase 3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add deterministic local chunk embeddings and read-only query helpers to `knowledge.db` without changing canonical resolution behavior.

**Architecture:** Phase 3 keeps the DB disposable and rebuild-driven. Each canonical page is chunked into small retrieval units, embedded with a local deterministic hashing model, and stored in `page_embeddings`. Query helpers embed the user query with the same local model, compute cosine similarity against chunk rows, and return ranked chunks. This remains a retrieval layer only; registry resolution stays deterministic.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, `array`, `hashlib`, existing knowledge index rebuild path, pytest.

### Task 1: Add failing tests for chunk embeddings and query helpers

**Files:**
- Modify: `tests/test_knowledge_index.py`

**Step 1: Write the failing tests**

Cover:
- rebuild creates `page_embeddings`
- one page with multiple `##` sections becomes multiple chunk rows
- `query_knowledge_index()` returns the most relevant chunk for a lexical-semantic query
- `ovp-knowledge-index --query ... --json` returns ranked results

**Step 2: Run tests to verify they fail**

Run:
- `pytest -q tests/test_knowledge_index.py -k "embedding or query"`

Expected:
- FAIL because chunk rows and query helper do not exist yet

### Task 2: Implement deterministic embeddings and query helper

**Files:**
- Modify: `src/openclaw_pipeline/knowledge_index.py`

**Step 1: Extend the schema**

Add `page_embeddings` with:
- `slug`
- `chunk_index`
- `section_title`
- `chunk_text`
- `embedding_blob`
- `embedding_model`

**Step 2: Implement chunking**

Behavior:
- split page body by `##` headings
- fallback to the whole body when no sections exist
- store one chunk row per section

**Step 3: Implement local deterministic embeddings**

Behavior:
- tokenize text
- hash tokens into a fixed-size float vector
- L2-normalize before storage
- serialize as float32 bytes

**Step 4: Implement query helper**

Behavior:
- embed query with the same local model
- score chunks by cosine similarity
- return ranked chunk payloads
- keep helper read-only and completely separate from link resolution

**Step 5: Run focused tests**

Run:
- `pytest -q tests/test_knowledge_index.py -k "embedding or query"`

Expected:
- PASS

### Task 3: Wire CLI query mode and run full verification

**Files:**
- Modify: `src/openclaw_pipeline/commands/knowledge_index.py`
- Modify: `tests/test_knowledge_index.py`

**Step 1: Extend CLI**

Add:
- `--query`
- `--limit`

Behavior:
- `--query` runs retrieval against `knowledge.db`
- default mode still rebuilds

**Step 2: Run focused tests**

Run:
- `pytest -q tests/test_knowledge_index.py`

Expected:
- PASS

**Step 3: Run full verification**

Run:
- `python3 -m compileall src/openclaw_pipeline`
- `pytest -q`
