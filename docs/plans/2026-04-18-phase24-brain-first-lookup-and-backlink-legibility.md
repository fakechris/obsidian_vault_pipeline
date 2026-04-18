# Phase 24: Brain-First Lookup And Backlink Legibility

> **Status:** Planned, gated on Phase 25 runtime validation

## Goal

Make new object/link creation more conservative and more legible by forcing the system to search existing vault truth before creating downstream knowledge, and by making the resulting backlink/candidate decisions obvious to operators.

## Why It Is Gated

This phase remains valuable, but it should not proceed on top of a black-box runtime.

`Phase 25` now establishes:

- one canonical run ledger,
- honest counted progress,
- stale-run separation,
- unified watcher/API/UI runtime truth.

Only after that runtime contract is validated against a real `ovp --incremental` run should the project continue into:

- brain-first lookup before object creation,
- backlink legibility,
- candidate vs canonical downstream boundary improvements.
