# Phase 10: Event And Contradiction Model Hardening

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the `events` and `contradictions` surfaces semantically trustworthy instead of merely browseable.

**Architecture:** Do not jump straight to a new contradiction detector or event ontology. First harden the payload contract that leaves `truth_api.py` and `ui/view_models.py`, so the UI and future model work share a stable vocabulary for timeline rows, detection semantics, confidence, and status buckets. Once the contract is explicit, later slices can improve the underlying heuristics without changing every surface again.

**Tech Stack:** Python 3.13, SQLite via stdlib `sqlite3`, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, `knowledge_index.py`, pytest.

## Problem

Phase 9 finished the review workbench, but two core surfaces still lean on soft explanation text instead of stable model fields:

- `Event Dossier` still reads like a generic timeline even though it is a projection over dated notes and dated headings.
- `Contradictions` still exposes a queue whose semantics are implied by prose rather than represented as explicit payload fields.

That is a product problem. Users cannot tell which semantics are hard guarantees and which are current heuristic limits.

## Scope

### Slice A: Event Timeline Contract

Define explicit event row semantics:

- raw row type (`page_date` vs `heading_date`)
- semantic role (`note_date_projection` vs `heading_date_projection`)
- timeline anchor kind (`note` vs `heading`)
- timeline anchor label

Add an event-level contract block summarizing:

- timeline kind
- row type counts
- semantic role counts

This slice is about trustworthy labeling, not deeper event inference.

### Slice B: Contradiction Detection Contract

Define explicit contradiction semantics:

- detection model
- detection confidence level
- status bucket (`open` vs `reviewed`)

Add a contradiction-level contract block summarizing:

- model name
- confidence semantics
- open vs reviewed counts

This slice is about making the current heuristic legible, not replacing it yet.

### Slice C: UI Contract Rendering

Render the new contracts directly in `ovp-ui`:

- `Timeline Contract` on `/events`
- `Detection Contract` on `/contradictions`
- per-row semantic hints where needed

The UI should stop relying on scattered explanatory copy alone.

## Non-Goals

- no LLM-based contradiction detection rewrite,
- no new event entity store,
- no graph UI,
- no batch workflow changes,
- no new queue types.

## Exit Criteria

Phase 10 reaches its first checkpoint when:

1. `events` payloads expose stable timeline semantics,
2. `contradictions` payloads expose stable detection semantics,
3. the UI renders those semantics explicitly,
4. tests lock the contract so later heuristic work cannot silently regress product meaning.

## Execution Order

1. Add failing tests for event timeline contract fields and contradiction detection contract fields.
2. Implement minimal payload changes in `truth_api.py` and `ui/view_models.py`.
3. Render the new contract sections in `ui_server.py`.
4. Re-run focused UI tests.
5. Re-run full `pytest` and `compileall`.
