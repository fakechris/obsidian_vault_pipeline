# Phase 5: Internal Pack E2E Hardening

## Why This Phase Exists

Phase 4 finished the internal pack split:

- `research-tech` is the primary built-in pack
- `default-knowledge` is the compatibility pack
- workflow / query / review / materialize / docs are pack-aware

What is still missing is stronger runtime confidence.

The current repo had broad unit/integration coverage, but it did not yet have a
single pack-level end-to-end test that proves the internal pack loop actually
works as one coherent runtime.

That gap matters more than any external pack work.

## Goal

Before validating any external pack, prove that the internal pack runtime is
stable end-to-end.

The target is not “more tests” in the abstract. The target is:

- `research-tech` can run a realistic pack loop end-to-end
- `default-knowledge` still works as a compatibility layer
- core pack contracts can be changed without silently breaking derived surfaces

## Scope

### 1. Add Pack-Level E2E Coverage

Introduce explicit tests that run a small but real pack lifecycle:

- extraction
- extraction preview/dashboard
- truth/index rebuild
- materialized views
- review queue generation
- maintenance loop
- query surface

The first version should cover:

- `research-tech`
- `default-knowledge` compatibility regression

### 2. Keep Broad Regression Green

Pack e2e tests should run alongside the existing broad suite:

- `PYTHONPATH=src python3.13 -m pytest -q --ignore=tests/test_autopilot_contracts.py`

The current `watchdog` environment issue is still out of scope for this slice.

### 3. Defer External Pack Work

Explicitly do **not** start media/medical/external pack validation until this
internal e2e line is in place and stable.

## Done In Slice 1

Added `tests/test_pack_e2e.py` with two pack-level tests:

1. `research-tech` end-to-end runtime
   - extract
   - preview/dashboard
   - knowledge index / truth store
   - object/topic/event/contradiction materializers
   - contradiction/stale-summary review queues
   - contradiction resolution + summary rebuild
   - knowledge query surface

2. `default-knowledge` compatibility regression
   - extract
   - preview/dashboard
   - object/materialized view
   - review queue

## Done In Slice 2

Added `tests/test_pack_runtime_e2e.py` to validate orchestrated runtime behavior:

1. `research-tech/full` runtime orchestration
   - real pack profile stages are used as the execution sequence
   - `quality -> absorb` file-level gating is passed through the pipeline
   - the runtime completes the full ordered chain for the primary pack

2. `default-knowledge/full` compatibility orchestration
   - compatibility pack still slices and resumes correctly
   - `quality -> absorb` propagation still works under the compatibility path

## Verification

Focused:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_pack_e2e.py
PYTHONPATH=src python3.13 -m pytest -q tests/test_pack_runtime_e2e.py tests/test_pack_e2e.py
PYTHONPATH=src python3.13 -m pytest -q \
  tests/test_runtime_paths.py \
  tests/test_pack_runtime_e2e.py \
  tests/test_pack_e2e.py \
  tests/test_default_pack_compat.py \
  tests/test_query_tool.py \
  tests/test_build_views_command.py \
  tests/test_review_queue_command.py \
  tests/test_truth_store.py \
  tests/test_knowledge_index.py
```

Broad:

```bash
PYTHONPATH=src python3.13 -m pytest -q --ignore=tests/test_autopilot_contracts.py
```

Compile:

```bash
python3.13 -m compileall src/openclaw_pipeline
```

Autopilot environment recovery:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_autopilot_contracts.py
PYTHONPATH=src python3.13 -m pytest -q
```

## Milestone Status

- Phase 1 `Make Extraction Visible`: complete
- Phase 2 `Add Truth Store`: complete
- Phase 3 `Materialize + Review`: complete
- Phase 4 `Split Domain Packs`: complete
- Phase 5 `Internal Pack E2E Hardening`: started

This phase now has two solid slices:

- command-surface pack e2e
- orchestrated runtime pack e2e

That is enough to say the phase is materially underway, and the most important
runtime/testing gap is now closed:

- pack-level command/runtime e2e exists
- `autopilot` no longer breaks test collection when `watchdog` is absent
- the default `python3.13` full test run now passes

## What Still Remains In Phase 5

1. Add pack-level regression checks for future default-pack switching, if that
   decision is revisited later.
2. Optionally add a higher-fidelity subprocess-backed e2e for `ovp --pack research-tech --profile full`
   if the command-runtime contract becomes more complex than the current in-process orchestration tests.

## Commit / PR Boundary

This is now a good **commit** boundary and a good **PR** boundary.

The original Phase 5 blocker was internal pack confidence. That blocker is now
substantially addressed:

- internal pack command/runtime e2e exists
- compatibility pack regression exists
- full `python3.13` pytest is green again
