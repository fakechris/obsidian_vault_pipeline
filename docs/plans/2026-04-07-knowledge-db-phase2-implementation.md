# Knowledge DB Phase 2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend `knowledge.db` with structured derived rows for sidecars, timeline-like note events, and audit logs, while keeping the database fully rebuildable from canonical/log files.

**Architecture:** Phase 2 keeps `knowledge.db` as a disposable derived layer. The rebuild step will now mirror three more sources: link-resolution JSON sidecars into `raw_data`, note-level date signals into `timeline_events`, and JSONL logs into `audit_events`. No canonical writes are introduced; rebuilding the DB remains a pure projection pass.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, existing runtime helpers, frontmatter/link parsers, JSONL log files, pytest.

### Task 1: Add failing tests for structured derived tables

**Files:**
- Modify: `tests/test_knowledge_index.py`

**Step 1: Write the failing tests**

Cover:
- rebuild creates:
  - `raw_data`
  - `timeline_events`
  - `audit_events`
- a link-resolution sidecar in `60-Logs/link-resolution/*.json` becomes a `raw_data` row
- note-level date signals become `timeline_events` rows keyed by canonical slug
- `pipeline.jsonl` and `refine-mutations.jsonl` become `audit_events` rows

**Step 2: Run tests to verify they fail**

Run:
- `pytest -q tests/test_knowledge_index.py -k "structured or knowledge_index_cli"`

Expected:
- FAIL because the new tables and counts do not exist yet

### Task 2: Implement structured rebuild support

**Files:**
- Modify: `src/openclaw_pipeline/knowledge_index.py`

**Step 1: Extend the schema**

Add tables:
- `raw_data`
- `timeline_events`
- `audit_events`

**Step 2: Implement raw sidecar mirroring**

Source:
- `60-Logs/link-resolution/*.json`

Behavior:
- one row per sidecar file
- source name should be stable and explicit
- payload stored intact as JSON text

**Step 3: Implement timeline event extraction**

Source:
- evergreen note frontmatter/body

Behavior:
- always emit at least a page-level date event when a canonical day/date exists
- additionally emit rows for markdown headings that look like dates under timeline/history sections

**Step 4: Implement audit event mirroring**

Sources:
- `60-Logs/pipeline.jsonl`
- `60-Logs/refine-mutations.jsonl`

Behavior:
- preserve original payload JSON
- store event type, source log, timestamp/session when present

**Step 5: Run focused tests**

Run:
- `pytest -q tests/test_knowledge_index.py -k "structured or knowledge_index_cli"`

Expected:
- PASS

### Task 3: Verify CLI payload and full suite

**Files:**
- Modify: `tests/test_knowledge_index.py`

**Step 1: Extend CLI expectations**

Cover:
- `ovp-knowledge-index --json` includes counts for:
  - pages
  - links
  - raw records
  - timeline events
  - audit events

**Step 2: Run focused tests**

Run:
- `pytest -q tests/test_knowledge_index.py`

Expected:
- PASS

**Step 3: Run full verification**

Run:
- `python3 -m compileall src/openclaw_pipeline`
- `pytest -q`
