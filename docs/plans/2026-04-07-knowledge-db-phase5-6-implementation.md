# Knowledge DB Phase 5-6 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire `knowledge.db` refresh into the real pipeline/autopilot flow and expose a stable stdio read surface for external consumers.

**Architecture:** Phase 5 adds `knowledge_index` as a first-class derived refresh step after canonical refresh (`moc`) in the main pipeline and AutoPilot success path. Phase 6 does not add a full MCP dependency yet; instead it adds transport-agnostic read tool semantics plus a simple stdio JSONL service and tool discovery JSON. This keeps the surface read-only and easy to wrap by a future MCP server.

**Tech Stack:** Python 3.10+, argparse, stdlib `json`, `sqlite3`, `io`, existing pipeline/autopilot command orchestration, pytest.

### Task 1: Add failing tests for pipeline/autopilot knowledge index refresh

**Files:**
- Modify: `tests/test_runtime_paths.py`
- Modify: `tests/test_autopilot_contracts.py`

**Step 1: Write the failing tests**

Cover:
- `build_execution_plan()` appends `knowledge_index` after `moc`
- `EnhancedPipeline.step_knowledge_index()` invokes the knowledge index command
- AutoPilot successful processing includes a `knowledge_index` stage after `moc`

**Step 2: Run tests to verify they fail**

Run:
- `pytest -q tests/test_runtime_paths.py tests/test_autopilot_contracts.py -k knowledge`

Expected:
- FAIL because the stage does not exist yet

### Task 2: Implement Phase 5 wiring

**Files:**
- Modify: `src/openclaw_pipeline/unified_pipeline_enhanced.py`
- Modify: `src/openclaw_pipeline/autopilot/daemon.py`

**Step 1: Add `knowledge_index` pipeline step**

Behavior:
- append to `PIPELINE_STEPS`
- place after `moc`
- detect success via `knowledge.db` existence/mtime

**Step 2: Add AutoPilot refresh**

Behavior:
- run after MOC refresh in the success path
- record `knowledge_index` in task stages
- keep it read-only/derived

**Step 3: Run focused tests**

Run:
- `pytest -q tests/test_runtime_paths.py tests/test_autopilot_contracts.py -k knowledge`

Expected:
- PASS

### Task 3: Add stdio read surface and tool discovery

**Files:**
- Modify: `src/openclaw_pipeline/knowledge_index.py`
- Modify: `src/openclaw_pipeline/commands/knowledge_index.py`
- Modify: `tests/test_knowledge_index.py`

**Step 1: Write failing tests**

Cover:
- tool discovery JSON contains:
  - `knowledge_search`
  - `knowledge_query`
  - `knowledge_get`
  - `knowledge_stats`
  - `knowledge_audit_recent`
- a request dispatcher routes those tool names
- stdio serve loop can process one JSONL request and emit one JSON response
- CLI supports:
  - `--tools-json`
  - `--serve`

**Step 2: Implement minimal transport**

Behavior:
- `knowledge_tools_json()` returns tool discovery payload
- `dispatch_knowledge_tool(vault_dir, tool_name, args)` executes read-only helpers
- `serve_knowledge_index(vault_dir, stdin, stdout)` processes JSONL requests until EOF

**Step 3: Run tests**

Run:
- `pytest -q tests/test_knowledge_index.py -k "tools_json or dispatch_knowledge_tool or serve_knowledge_index"`

Expected:
- PASS

### Task 4: Full verification

**Files:**
- Verify all changed files

**Step 1: Run compile**

Run:
- `python3 -m compileall src/openclaw_pipeline`

**Step 2: Run tests**

Run:
- `pytest -q`
