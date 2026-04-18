# Phase 25: Observable Runtime And Run Ledger

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current black-box pipeline feel with one canonical run ledger that answers "what is happening now?" for operators, while keeping `pipeline.jsonl` as the append-only evidence stream.

**Status:** Complete

Related: [[2026-04-14-local-knowledge-workbench-milestone|Local Knowledge Workbench Milestone]], [[2026-04-18-phase24-brain-first-lookup-and-backlink-legibility|Phase 24]], [[2026-04-17-phase22-active-signal-impact-accounting|Phase 22]]

## What Landed

- `txn.py`
  - canonical `run_ledger` contract
  - active vs stale classification
  - current-step progress fields
  - heartbeat support
- `unified_pipeline_enhanced.py`
  - transaction start now records pack/profile/planned steps
  - `run_command(...)` heartbeats during long subprocess stages
  - short timeouts no longer get swallowed by a fixed 5-second poll interval
  - `pinboard_process` now exposes counted file progress and file-level events
  - `absorb` now runs as a direct workflow with counted per-file progress instead of a subprocess black box
  - CLI now exposes `ovp --incremental` as the explicit daily entrypoint
- `auto_evergreen_extractor.py`
  - absorb workflow now reports per-file progress callbacks
  - recent-mode target collection is unique and ordered
- operator readers now consume the same runtime truth:
  - `watch_progress`
  - `truth_api.get_runtime_status(...)`
  - dashboard payload/runtime API
  - root UI shell `Current Workflow` card

## What This Changes For Operators

Operators no longer need to stitch together:

- transaction JSON
- `pipeline.jsonl`
- `ps`
- ad hoc watcher heuristics

to infer current workflow state.

The canonical run ledger now answers:

1. what run is active,
2. which step is active,
3. whether the run is stale,
4. counted progress when the denominator is real,
5. which item is being processed now,
6. what the last meaningful event was.

## Real Validation

This phase was closed only after synthetic tests **and** a real local incremental run.

Validated command:

```bash
ovp --incremental --pack research-tech --vault-dir /path/to/vault
```

Verified during the live run:

- `watch_progress`
- `/api/runtime`
- `/`

all reported the same active run and progress state for:

- `run_id = pipeline-20260418-081131-ca15f0f3`
- `current_step = pinboard_process`
- `progress_summary = 8/29 files processed`
- `current_item = 2026-04-12_NousResearch_hermes-agent-self.md`

The root shell also had to be hardened during validation:

- `/api/runtime` was already fast and correct
- `/` was too heavy for a live operator workflow, so the home shell was switched to a runtime-first payload
- the heavier full dashboard contract remains available in code, but the default home route now prioritizes current workflow truth over expensive aggregate surfaces

## Verification

- `PYTHONPATH=src pytest -q`
  - `620 passed`
- real-vault runtime validation:
  - `watch_progress` showed active txn, counted progress, current item, and stale-run count from the canonical run ledger
  - `/api/runtime` returned the same active run and step progress
  - `/` rendered the same run id and progress in under a second using the runtime-first home shell
