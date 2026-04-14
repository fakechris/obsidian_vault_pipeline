# Phase 11: Knowledge Production Traceability

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the full knowledge production chain legible from the product itself: source note -> processed note -> deep dive -> evergreen object -> Atlas/MOC placement.

**Architecture:** Reuse the current vault-first architecture. Do not introduce a separate provenance database or new ontology layer. Instead, compute stable production-chain payloads from existing sources: `pages_index`, `page_links`, `objects`, `audit_events`, and the current note/object provenance helpers. Surface those payloads first on existing `note` and `object` pages before adding any new dedicated browser.

**Tech Stack:** Python 3.13, SQLite via stdlib `sqlite3`, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, `knowledge_index.py`, pytest.

## Problem

The product already lets users move between notes, deep dives, objects, and Atlas pages, but it still makes them mentally reconstruct the production chain themselves.

Today a user can often answer:

- “what is this object?”
- “which deep dives mention it?”
- “which MOC links to it?”

But the product still makes it too hard to answer:

- “what did this source note actually produce?”
- “which deep dive promoted this object?”
- “which Atlas pages are downstream of this note?”
- “where in the chain am I right now?”

That is the next major gap between a good local browser and a real knowledge workbench.

## Scope

### Slice A: Stable Production Chain Payloads

Add explicit production-chain payloads for the most important surfaces:

- `get_note_traceability()`
- `get_object_traceability()`

They should expose, in stable fields:

- current artifact
- upstream source notes
- deep dives
- derived objects
- downstream Atlas/MOC reach
- simple counts for each stage

This slice is about trustworthy chain shape, not yet richer ranking.

### Slice B: Product Rendering On Existing Pages

Render the new traceability payloads directly on:

- `/note`
- `/object`

The user should not need to learn a new route before they can understand production flow.

### Slice C: Traceability Browser Follow-Up

After stable payloads exist on note/object pages, add a dedicated traceability browser only if the embedded views prove insufficient.

This is explicitly later. The first job is to make the production chain visible where users already are.

## Non-Goals

- no new graph visualization,
- no hosted product shell,
- no new extraction algorithm,
- no background briefing system,
- no evolution links yet,
- no rewrite of `knowledge_index`.

## Exit Criteria

Phase 11 reaches its first checkpoint when:

1. source notes and deep dives can show what they produced,
2. objects can show which source notes and deep dives produced them,
3. downstream Atlas/MOC reach is visible from the same surface,
4. users no longer have to manually infer the chain from multiple disconnected pages.

## Execution Order

1. Add failing tests for note/object production-chain payloads.
2. Implement minimal `truth_api` helpers over current data sources.
3. Surface the chain on `/note` and `/object`.
4. Run focused UI tests.
5. Run full `pytest` and `compileall`.
