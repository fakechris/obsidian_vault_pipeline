# Phase 24: Brain-First Lookup And Backlink Legibility

> **Status:** Planned, ready after Phase 25 runtime validation

## Goal

Make new object/link creation more conservative and more legible by forcing the system to search existing vault truth before creating downstream knowledge, and by making the resulting backlink/candidate decisions obvious to operators.

## Why This Follows Phase 25

This phase remains valuable, but it should not proceed on top of a black-box runtime.

[[2026-04-18-phase25-observable-runtime-and-run-ledger|Phase 25]] now establishes:

- one canonical run ledger,
- honest counted progress,
- stale-run separation,
- unified watcher/API/UI runtime truth.

That runtime contract has been validated against a real `ovp --incremental` run, so this phase can continue into:

- brain-first lookup before object creation,
- backlink legibility,
- candidate vs canonical downstream boundary improvements.
