# Phase 4: Research Tech Pack Split

## Goal

Start Phase 4 by introducing a first-class `research-tech` pack without breaking the
existing `default-knowledge` runtime contract.

This is an architecture split, not a semantic rewrite.

## Why This Slice

Phase 1-3 already established the foundation:

- extraction visibility
- truth-aware `knowledge.db`
- materializers
- review and maintenance loops

The next risk is structural: all of the technical research semantics still live under
`default-knowledge`. If media or medical packs are added on top of that, core and pack
boundaries will blur again.

The smallest defensible Phase 4 slice is:

1. create `research-tech`
2. make it loadable through the pack system
3. preserve `default-knowledge` as a compatibility pack for current CLI defaults

## Scope

### In Scope

- new `src/openclaw_pipeline/packs/research_tech/` package
- pack entrypoint registration
- loader tests for `research-tech`
- compatibility behavior for `default-knowledge`
- docs describing the split and current transitional contract

### Out Of Scope

- changing CLI defaults away from `default-knowledge`
- media pack implementation
- shrinking `default-knowledge` to its final minimal surface
- moving truth store semantics between packs

## Intended Transitional Contract

For this slice:

- `research-tech` becomes the first explicit standard domain pack
- `default-knowledge` remains the default CLI/runtime pack
- `default-knowledge` is allowed to delegate to `research-tech`

That keeps runtime stable while making the new pack real and reviewable.

## Implementation Steps

1. Add failing tests that require:
   - `load_pack("research-tech")` to work
   - `research-tech` to expose the current technical knowledge pack surface
   - `default-knowledge` compatibility to remain intact
2. Create `packs/research_tech/` and move or reuse the current pack specs there
3. Make `default-knowledge` a compatibility wrapper over the shared `research-tech`
   semantics for now
4. Register both entrypoints in `pyproject.toml`
5. Run focused and broad verification

## Definition Of Done

- `research-tech` is loadable as a first-class pack
- current `default-knowledge` workflows still work unchanged
- pack loader and compatibility tests cover the split
- documentation explains the transitional status clearly
- runtime can report pack roles and compatibility relationships without reading source

## Completion Status

Completed in this phase:

- `research-tech` added as a first-class built-in pack
- `default-knowledge` retained as a compatibility pack over the same core semantics
- workflow, object projection, query, extraction visibility, review, and view surfaces made pack-aware
- pack role metadata (`primary` vs `compatibility`) added to the runtime contract
- `ovp-packs` added as a user-visible pack introspection command

Still intentionally out of scope:

- changing CLI defaults away from `default-knowledge`
- implementing media or medical packs
- shrinking `default-knowledge` to a final minimal demo-only surface

## Commit Boundary

This slice is a valid standalone PR if all of the above are true. It establishes the
Phase 4 direction without mixing in unrelated domain work.
