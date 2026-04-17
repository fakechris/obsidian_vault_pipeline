# Phase 16: Multi-Pack Runtime Hardening Closeout

Status: Implemented

## Goal

Turn the post-`Phase 15` system into a real multi-pack platform:

- execution resolves through pack-owned handler contracts,
- truth projection resolves through pack-owned builders,
- observation products resolve through pack-owned surfaces,
- core stops carrying `research-tech` defaults as disguised platform behavior.

## Delivered

- stage execution now resolves through one contract path across:
  - profile execution
  - autopilot execution
  - focused action execution
- workflow profiles are enforced at load time against the correct runtime adapter:
  - `pipeline_step`
  - `autopilot_stage`
- declared spec ownership is enforced for:
  - truth projections
  - stage handlers
  - observation surfaces
  - processor contracts
  - extraction profiles
  - operation profiles
  - wiki views
- processor contracts are now explicit pack-owned runtime metadata, surfaced in queue and shell views
- shared `knowledge.db` now supports pack-scoped truth projections without treating `research-tech` as the default domain truth
- compatibility fallback is constrained to unresolved contracts instead of silently inheriting once a pack has materialized its own data
- `research-tech` focused actions and observation surfaces now live in pack-owned modules instead of core helper shims
- shared shell pages preserve requested `pack` scope through:
  - dashboard
  - search
  - note/object/topic pages
  - signals / briefing / production
  - actions / evolution
  - renderer-internal links
- research-only routes and embedded affordances are now guarded by explicit pack contracts instead of implicit UI assumptions
- `doctor --pack --json` now exposes effective contracts for:
  - handlers
  - processor contracts
  - truth projection
  - observation surfaces
  - shell routes and mutations
  - embedded research capabilities
  - wiki views
  - object kinds
  - extraction / operation / workflow profiles
- legacy `knowledge.db` schemas are now rebuilt on read instead of failing deep in runtime code
- core `truth_api.py` no longer exports `research-tech`-named surface shims

## Exit Condition Check

Phase 16 needed to make the runtime safe for additional packs without ongoing core patching.

That condition is now satisfied because:

- new packs can resolve execution through declared handlers and processor contracts
- new packs can materialize truth through pack-owned projection builders inside the shared container
- new packs can expose shell surfaces through declared or inherited observation surface contracts
- unsupported research-only routes now fail explicitly instead of pretending every pack is `research-tech`
- contract errors surface in diagnostics and shared shell payloads instead of only at deep runtime call sites

## What Media Pack Should Assume Now

Media Pack should work from these stable contracts:

1. `stage handlers`
2. `processor contracts`
3. `truth projection`
4. `observation surfaces`

Media Pack should **not** patch:

- `knowledge_index.py`
- `truth_api.py`
- `ui_server.py`
- queue worker internals

If Media Pack needs to inspect its effective platform contract, it should use:

```bash
PYTHONPATH=src python3.13 -m openclaw_pipeline.commands.doctor --pack <pack-name> --json
```

## What Research Pack Should Assume Now

`research-tech` is no longer the hidden platform default.

Research Pack should keep evolving as an ordinary pack that happens to ship first-party domain semantics:

- research graph and synthesis work stays inside the pack
- research-only routes remain gated as research capabilities
- future research changes should extend pack contracts rather than reintroducing core shims

## Explicit Deferrals

These are not `Phase 16` blockers:

- new Media Pack domain semantics
- generalized cross-pack graph semantics
- richer cross-pack shell composition beyond current shared/research shell split
- new end-user product surfaces unrelated to pack/runtime hardening
- `Phase 17` product work

## Phase 17 Entry

Phase 17 should start from a closed assumption:

- execution contracts are explicit
- truth projection ownership is explicit
- observation surface ownership is explicit
- shell capability visibility is explicit

Phase 17 should build on those contracts instead of reopening multi-pack runtime ownership.
