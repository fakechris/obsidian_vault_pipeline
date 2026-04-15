# Phase 12 Active Signal Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn current maintenance findings into first-class active signals that can be inspected, prioritized, and later extended into note-save and background-intelligence triggers.

**Architecture:** Start with a deterministic signal ledger built from existing trusted surfaces: open contradictions, stale summaries, and production-chain gaps. Persist those signals into a dedicated log and mirrored `audit_events` rows, then expose them through `truth_api`, `ovp-ui`, and the dashboard before attempting richer asynchronous extraction.

**Tech Stack:** Python 3.13, SQLite via stdlib `sqlite3`, existing `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, pytest.

---

## Scope

This first slice of Phase 12 does **not** try to ship full asynchronous intelligence. It establishes the minimum product contract:

1. signals are first-class records,
2. signals have stable IDs and types,
3. signals can be listed and filtered from the truth layer,
4. the local UI can show them as a distinct operator surface,
5. the dashboard can surface them as a new active-system layer.

The second slice extends that contract so the ledger is not only a snapshot of current queue-worthy state. It must also capture recent maintenance transitions:

- contradiction review actions,
- summary rebuild actions.

The third slice adds extraction-trigger signals that answer “what should happen next?” for the current production chain:

- source note exists but still needs a deep dive,
- deep dive exists but still needs downstream objects.

The fourth slice adds a briefing-ready snapshot so later daily or session-start intelligence can reuse one stable summary surface instead of rebuilding ad hoc summaries everywhere.

## Signal Types In Scope

- `contradiction_open`
- `stale_summary`
- `production_gap`
- `contradiction_reviewed`
- `summary_rebuilt`
- `source_needs_deep_dive`
- `deep_dive_needs_objects`

These are chosen because they already come from deterministic system state. This avoids pretending we have richer intelligence before the substrate is ready.
The first three are state signals. The next two are change signals derived from review actions. The last two are extraction-trigger signals derived from the current production chain.

## Files

- Modify: `src/openclaw_pipeline/truth_api.py`
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`
- Test: `tests/test_ui_smoke.py`

## Task 1: Signal Ledger API

Add deterministic signal computation and persistence:

- `sync_signal_ledger(vault_dir)`
- `list_signals(vault_dir, signal_type=None, query=None, limit=...)`
- `list_production_gaps(...)`

Persist the current ledger to:

- `60-Logs/signals.jsonl`
- mirrored `audit_events` rows with `source_log='signals'`

Each signal row should include:

- `signal_id`
- `signal_type`
- `detected_at`
- `status`
- `title`
- `detail`
- `source_path`
- `source_label`
- `object_ids`
- `note_paths`
- `downstream_effects`
- a typed `payload`

## Task 2: Signal Browser View Models

Add `build_signal_browser_payload(...)` and wire dashboard signal summaries through it.

The browser payload should provide:

- current count
- type counts
- type explanations
- filtered items

The dashboard should show:

- signal count
- sample signal items
- signals as a first-class section, not buried inside review queues

## Task 3: Signal Browser UI

Add:

- `/signals`
- `/api/signals`

The HTML page should support:

- search by text
- filter by signal type
- visible signal-type explanations
- links back to the originating maintenance surface or source note
- downstream links where they exist

## Task 4: Milestone Update

Update the master milestone doc so `Milestone 7: Active Signal Loop` is no longer “not started.” It should be marked `In Progress` and describe this first slice as:

- deterministic signal ledger
- operator-visible signal browser
- dashboard signal surfacing

## Verification

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_truth_api.py -k signal
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_view_models.py -k 'signal or dashboard'
PYTHONPATH=src python3.13 -m pytest -q tests/test_ui_server.py -k signals tests/test_ui_smoke.py -k 'signals or dashboard'
PYTHONPATH=src python3.13 -m pytest -q
python3.13 -m compileall src/openclaw_pipeline
```

## Exit Condition

Phase 12 first slice is done when:

1. signals exist as stable persisted rows,
2. users can browse them directly in the UI,
3. the dashboard surfaces them alongside review queues,
4. this is implemented without inventing speculative heuristics beyond the current trusted sources.
