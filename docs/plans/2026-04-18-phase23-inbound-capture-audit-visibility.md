# Phase 23: Inbound Capture Audit Visibility

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the active signal loop from lifecycle visibility into deterministic inbound-capture visibility, so operators can tell what a newly saved or updated note actually triggered, what downstream artifacts appeared, and what was only surfaced as a candidate or left untouched.

**Architecture:** Keep the current signal ledger, action queue, shared shell, and pipeline/refine logs. Do not add file-save hooks, background entity workers, temporal memory, or opaque capture intelligence. This phase should spend existing `pipeline.jsonl` and `refine-mutations.jsonl` audit data on three product surfaces only: `note/page`, `signals/browser`, and `briefing/intelligence`.

**Tech Stack:** Python 3.13, stdlib JSON/runtime, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, existing pipeline/refine logs, pytest.

**Status:** Complete

## Why This Was The Right Next Phase

`Phase 22` answered:

- did a signal queue anything,
- what lifecycle state is it in,
- did the queue ever produce visible downstream change.

What still stayed hidden was the inbound side:

- did the note get picked up at all,
- did it only stage/archive,
- did it create a deep dive,
- did it surface candidates,
- did it auto-promote an evergreen object,
- or did it stop before any downstream artifact appeared.

That gap matters because `Milestone 7` is not only about action execution. It is about making note/save/update activity legible as an improving knowledge loop.

## Product Thesis

After `Phase 23`, an operator should be able to answer three additional questions without opening raw logs:

1. Was this note actually captured by the inbound pipeline?
2. What downstream artifacts or candidates did that capture produce?
3. Did the signal surface inherit any concrete inbound evidence from its source note?

This phase is therefore about:

- **note-level inbound capture summaries**
- **signal-level audit legibility**
- **briefing-level visibility into recent captured input**

## What Phase 23 Delivered

### 1. Note Inbound Capture Contract v1

`truth_api.py` now exposes a deterministic `get_note_inbound_capture_summary(...)` contract derived from existing logs.

The contract currently includes:

- `status`
- `captured_event_count`
- `produced_artifact_count`
- `candidate_count`
- `error_count`
- `skipped_count`
- `latest_timestamp`
- `summary`
- `items`

The v1 event set is intentionally narrow and deterministic:

- `source_staged_for_processing`
- `source_archived_to_processed`
- `source_restored_to_raw`
- `article_processed`
- `article_abstained`
- `article_error`
- `candidates_upserted`
- `candidate_upsert_error`
- `evergreen_auto_promoted`
- `evergreen_created`
- `evergreen_error`
- `refine_mutation_applied`

### 2. Signal-Level Capture Summary

`list_signals(...)` now attaches `capture_summary` derived from the signal’s backing `note_paths`.

This makes the signal browser able to distinguish:

- signals with observed inbound capture but no downstream artifact yet,
- signals with productive capture history,
- signals with no capture audit attached at all.

### 3. Note Page Upgrade

`note/page` now includes a first-class `Inbound Capture` compiled section.

This section sits alongside:

- `Current State`
- `Evidence Traceability`
- `Production Chain`
- `Where To Go Next`

and turns previously implicit pipeline activity into a readable page-level product artifact.

### 4. Briefing Upgrade

`/briefing` now includes an `Inbound Capture` compiled section built from recent signals that actually carry capture audit.

This keeps the orientation layer grounded in concrete note activity rather than only queue state.

### 5. Signal Browser Upgrade

`/signals` now renders item-level inbound capture summaries directly.

The page can now explain both:

- what the signal is asking for next,
- what has already happened on the inbound side.

## What Phase 23 Intentionally Did Not Do

Explicit deferrals:

- automatic file-save hooks
- generalized capture on every edit event
- background entity extraction workers
- temporal truth or memory infrastructure
- benchmark/evaluation layers

## Exit Condition

`Phase 23` is complete when all of the following are true:

1. note pages expose stable inbound capture summaries from existing logs,
2. signals expose inherited inbound capture summaries from their backing notes,
3. `/briefing` surfaces recent capture audit as a compiled section,
4. `/signals` renders capture visibility directly instead of forcing log inspection,
5. focused tests lock the new contract and rendering behavior.

## Closeout

`Phase 23` is complete.

What landed:

- `truth_api.py` now exposes `get_note_inbound_capture_summary(...)`
- signal rows now carry `capture_summary`
- note pages now expose an `Inbound Capture` compiled section
- briefing pages now expose an `Inbound Capture` compiled section
- signals pages now render item-level inbound capture summaries

What this phase intentionally did **not** do:

- add save hooks
- widen capture detection into a new background engine
- reopen shell UX or temporal-truth work

Next real gap inside `Milestone 7`:

- tighten brain-first lookup before object creation/link creation
- make backlink enforcement more explicit when new downstream objects are created
- keep the loop deterministic and operator-legible
