# Phase 6: Operationalization And Engine Evaluation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `research-tech` from an internally-correct pack into an operationally-usable standard pack, while making an explicit storage-engine decision for the truth/index layer.

**Architecture:** Keep the current Python-native `knowledge.db` path on SQLite as the authoritative derived/truth store implementation for now. Add operational surfaces around `research-tech` (`doctor`, `SKILLPACK`, `VERIFY`, `recipes`, `export`) so the pack can be installed, verified, operated, and audited as a real runtime surface. Revisit PGlite only if the truth layer becomes cross-runtime, browser-embedded, or Postgres-specific semantics become a hard product requirement.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, current command/runtime surfaces, pytest, markdown docs under `docs/`.

## 1. Engine Decision: PGlite vs SQLite

### Current Repo Reality

The current store is:

- Python-native
- rebuilt by `knowledge_index.py`
- read by Python materializers, review runtime, query tooling, and derived maintenance
- explicitly a **derived / truth-aware index**, not the canonical authoring store

That matters because engine choice is downstream of runtime shape.

### What PGlite Is Good At

PGlite is stronger than SQLite when all of the following matter:

- browser or JS-first embedding is a first-order requirement
- Postgres SQL semantics/extensions are required across environments
- you need one engine family that can later scale to remote Postgres/Supabase with lower conceptual drift
- local/edge/browser runtimes are part of the core product

### What SQLite Is Still Better At Here

For this repo today, SQLite is the better engine because:

- the current system is Python-native, not JS-native
- stdlib `sqlite3` is zero-dependency and already integrated across runtime surfaces
- the current truth/index layer is rebuilt in-process and is not yet a cross-runtime sync substrate
- migration to PGlite would introduce a bridge/sidecar/WASM runtime boundary before there is a product need for it
- we already paid the integration cost to stabilize indexing, truth projection, and maintenance on SQLite

### Decision

Do **not** migrate to PGlite in this phase.

Instead:

- keep SQLite as the current engine
- make the engine choice explicit in docs and doctor output
- define the triggers that would justify a future PGlite migration

### Revisit Triggers

Only reopen PGlite migration if at least one of these becomes true:

1. The truth store must run in browser/edge/JS runtimes directly.
2. The same pack runtime must support both embedded local mode and remote Postgres mode with near-identical SQL behavior.
3. The store stops being a rebuildable derived layer and becomes a long-lived transactional system with stronger relational semantics than SQLite is comfortably providing.

## 2. Scope For This Phase

### Slice A: Research-Tech Operational Surface

Add:

- `ovp-doctor`
- `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- `docs/research-tech/RESEARCH_TECH_VERIFY.md`

The command should report:

- primary/compatibility pack roles
- default workflow pack
- installed pack surfaces for a selected pack
- operational docs/recipes presence
- optional vault health basics when `--vault-dir` is provided
- current storage engine choice and rationale summary

### Slice B: Research-Tech Recipes

Add a first recipe layer for the real source paths we already support:

- pinboard
- clippings
- github/repo
- paper/pdf
- web article

These recipes are not codegen; they are operator-facing runbooks that make ingestion reproducible.

### Slice C: Minimal Publish / Export Contract

Add an explicit export surface so `research-tech` can publish compiled artifacts without pretending to have a full publishing platform.

Start with a single command:

- `ovp-export`

Support minimal targets:

- `object-page`
- `topic-overview`
- `event-dossier`
- `contradictions`

This should reuse the existing view/materializer runtime and write explicit exported artifacts to a caller-selected output path.

## 3. Implementation Tasks

### Task 1: Add failing tests for doctor

**Files:**
- Create: `tests/test_doctor_command.py`
- Modify: `pyproject.toml`

Write failing tests for:

- reporting `research-tech` as primary and `default-knowledge` as compatibility
- showing default workflow pack
- JSON output shape
- optional vault structure checks

### Task 2: Implement `ovp-doctor`

**Files:**
- Create: `src/openclaw_pipeline/commands/doctor.py`
- Modify: `src/openclaw_pipeline/packs/loader.py`
- Modify: `src/openclaw_pipeline/runtime.py` if shared helpers are needed

Implement the minimal command to satisfy the tests and print deterministic JSON/text output.

### Task 3: Add failing tests for export contract

**Files:**
- Create: `tests/test_export_command.py`
- Modify: `pyproject.toml`

Write failing tests for:

- `ovp-export --target object-page --object-id ...`
- `ovp-export --target topic-overview`
- `ovp-export --target event-dossier`
- `ovp-export --target contradictions`

### Task 4: Implement `ovp-export`

**Files:**
- Create: `src/openclaw_pipeline/commands/export_artifact.py`
- Modify: `src/openclaw_pipeline/wiki_views/runtime.py` only if a small helper improves reuse

Use existing view builders/materializers. The export command should be a thin operational wrapper, not a second rendering pipeline.

### Task 5: Add operational docs and recipes

**Files:**
- Create: `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- Create: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Create: `docs/recipes/research-tech/pinboard.md`
- Create: `docs/recipes/research-tech/clippings.md`
- Create: `docs/recipes/research-tech/github-repo.md`
- Create: `docs/recipes/research-tech/paper-pdf.md`
- Create: `docs/recipes/research-tech/web-article.md`
- Modify: `README.md`
- Modify: `README_EN.md`

Keep these docs concrete and operator-facing.

### Task 6: Verification

Run:

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_doctor_command.py tests/test_export_command.py
PYTHONPATH=src python3.13 -m pytest -q
python3.13 -m compileall src/openclaw_pipeline
```

## 4. Milestone Outcome

If this phase lands successfully, `research-tech` becomes:

- not just a correct internal pack
- but an installable, operable, verifiable, exportable standard pack

That is the right boundary before any external pack work or any serious PGlite migration debate.
