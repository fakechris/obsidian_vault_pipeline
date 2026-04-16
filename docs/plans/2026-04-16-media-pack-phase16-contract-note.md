# Media Pack: Phase 16 Contract Note

Status: Active guidance

## Why This Exists

`Phase 16` is the platform-hardening pass after `Phase 15`.

The goal is to let a pack such as media reuse:

- queue and worker lifecycle
- audit and rebuild discipline
- local UI shell
- derived SQLite container

without patching core runtime modules.

## What Media Pack Should Assume Now

Media Pack should treat these as the supported pack-owned insertion points:

1. `stage handlers`
   - batch/profile execution
   - autopilot stage execution
   - focused queue actions

2. `truth projection`
   - `objects`
   - `claims`
   - `claim_evidence`
   - `relations`
   - `compiled_summaries`
   - `contradictions`
   - optional `graph_edges`
   - optional `graph_clusters`

3. `observation surfaces`
   - `signals`
   - `briefing`
   - `production_chains`
   - later domain-specific surfaces

## What Media Pack Should Not Do

Do not patch:

- `knowledge_index.py`
- `truth_api.py`
- `ui_server.py`
- queue worker internals

Do not assume that media should inherit `research-tech` semantics such as:

- evergreen-centric object meaning
- current contradiction semantics
- current event dossier semantics
- current production-chain semantics

Core owns the container, queue, audit, rebuild lifecycle, and UI shell.
Media Pack should own domain meaning.

## Compatibility Behavior

Compatibility fallback is intentionally narrow.

If a compatibility pack does **not** declare its own:

- stage handlers
- truth projection
- observation surfaces

then OVP may resolve those contracts from `compatibility_base`.

Once a pack declares its own contract, OVP should treat that contract as authoritative.

## What Phase 16 Will Tighten

Phase 16 should make these rules more explicit, not less:

1. execution dispatch resolves through pack-owned handlers
2. truth projection resolves through pack-owned builders
3. observation surfaces resolve through pack-owned builders
4. core stops carrying `research-tech` defaults in disguise

## Immediate Guidance For Media Pack

If media work has already started:

- keep pack code inside the media pack
- implement pack-owned handlers instead of adding core branches
- implement a media truth projection builder instead of reusing research object semantics
- implement media observation surfaces only where media has real product meaning

If media needs to inspect the current runtime contract, use:

```bash
PYTHONPATH=src python3.13 -m openclaw_pipeline.commands.doctor --pack <pack-name> --json
```

This now exposes:

- declared handlers
- effective handlers after compatibility fallback
- declared truth projection
- effective truth projection
- declared/effective observation surfaces
