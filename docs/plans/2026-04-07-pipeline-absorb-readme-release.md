# Pipeline Absorb And README Release Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the shipped Absorb/Refine/knowledge.db layers into the daily pipeline and autopilot, then rewrite the CN/EN README files so the documented model matches the actual runtime.

**Architecture:** Keep `Absorb` in the main daily path and keep `Refine` opt-in. The unified pipeline should run `absorb -> registry_sync -> moc -> [optional refine] -> knowledge_index`, while autopilot should run `absorb -> moc -> [optional refine] -> knowledge_index`. Runtime behavior and docs must describe the same contract.

**Tech Stack:** Python CLI commands, argparse, pytest, markdown documentation.

### Task 1: Lock orchestration contracts with tests

**Files:**
- Modify: `tests/test_runtime_paths.py`
- Modify: `tests/test_autopilot_contracts.py`

**Step 1: Write failing tests**

- Add a full-pipeline planning test that expects `--with-refine` to place `refine` before `knowledge_index`.
- Add a step-command test that expects the absorb step to call `openclaw_pipeline.commands.absorb`.
- Add an autopilot contract test that expects the success path to use `absorb`, and to include `refine` only when explicitly enabled.

**Step 2: Run focused tests to verify failure**

Run: `pytest -q tests/test_runtime_paths.py tests/test_autopilot_contracts.py`

Expected: failures showing missing `with_refine` support and legacy `auto_evergreen_extractor` orchestration.

### Task 2: Rewire the unified pipeline

**Files:**
- Modify: `src/openclaw_pipeline/unified_pipeline_enhanced.py`

**Step 1: Implement minimal orchestration changes**

- Replace the legacy evergreen subprocess with `openclaw_pipeline.commands.absorb`.
- Add a `refine` step implementation that runs cleanup and breakdown in write mode.
- Add `--with-refine` CLI support and ensure `knowledge_index` stays last so the derived DB reflects final canonical state.
- Preserve compatibility for users still passing `evergreen` as a step alias.

**Step 2: Re-run focused tests**

Run: `pytest -q tests/test_runtime_paths.py tests/test_autopilot_contracts.py`

Expected: pipeline-related tests pass or only autopilot tests still fail.

### Task 3: Rewire autopilot

**Files:**
- Modify: `src/openclaw_pipeline/autopilot/daemon.py`

**Step 1: Implement minimal autopilot changes**

- Replace legacy evergreen extraction with `Absorb`.
- Add optional refine execution controlled by `--with-refine`.
- Keep refine off by default.
- Update stage names and logs to reflect `absorb`.

**Step 2: Re-run focused tests**

Run: `pytest -q tests/test_runtime_paths.py tests/test_autopilot_contracts.py`

Expected: focused orchestration tests pass.

### Task 4: Align CLI surface and docs

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `README_EN.md`

**Step 1: Align compatibility entry points**

- Point `ovp-evergreen` at the absorb command as a compatibility alias.

**Step 2: Rewrite README CN/EN**

- Document the six-layer model as implemented now.
- Describe `ovp --full` and `ovp-autopilot` with the real absorb/optional-refine/knowledge-index behavior.
- Document `ovp-knowledge-index` and the `knowledge.db` derived layer clearly.
- Remove stale wording that still implies legacy extractor-only orchestration.

**Step 3: Run doc-facing smoke checks**

Run:
- `ovp --help`
- `ovp-autopilot --help`
- `ovp-knowledge-index --help`

Expected: help output reflects the rewritten command surface.

### Task 5: Fresh verification, commit, tag, push

**Files:**
- Modify: `pyproject.toml` (version bump)

**Step 1: Run full verification**

Run:
- `python3 -m compileall src/openclaw_pipeline`
- `pytest -q`

Expected: all pass.

**Step 2: Commit and release**

- Stage only relevant code/doc files.
- Commit with a release-appropriate message.
- Bump version for the new release.
- Create a new git tag.
- Push branch and tag.
