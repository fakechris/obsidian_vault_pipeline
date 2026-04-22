# Phase 30: Release Hygiene And Roadmap Reconciliation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the merged `main` branch installable, self-reporting, and roadmap-clean before starting the next semantic extraction phase.

**Architecture:** This is a small product-quality closeout, not a new knowledge feature. Keep all fixes in core release/runtime hygiene and docs. Do not add a new pipeline stage, semantic relation extractor, background scheduler, or UI surface in this phase.

**Tech Stack:** Python packaging via `pyproject.toml` / editable install, existing `ovp` CLI entry points, existing docs in `task_plan.md`, `progress.md`, README, and milestone plan files.

## Why This Phase Comes Next

Phase 28/29 closed the background-value and backlink-enforcement trust boundary. The next major product direction can now be pack-level semantic relation extraction, but the post-merge dogfood pass exposed one immediate quality issue:

- `python -m pip show obsidian-vault-pipeline` reports `0.8.6`
- `ovp --version` reports `0.3.2`

That means a user can install the latest OVP and still see a stale CLI version. This is small, but it undermines install confidence and should be fixed before opening a larger feature phase.

The roadmap docs also still describe PR #44 as open even though it is merged. That should be reconciled while the context is fresh.

## Non-Goals

- No semantic relation extraction.
- No Graphiti-style temporal truth model.
- No new canonical source of truth.
- No action-queue redesign.
- No new dashboard redesign.
- No release publishing to PyPI unless explicitly requested.

## Task 1: Fix CLI Version Reporting

**Files:**
- Modify: `src/ovp_pipeline/unified_pipeline_enhanced.py`
- Test: `tests/test_unified_pipeline_version.py` or an existing CLI/runtime test file if one already covers `ovp --version`

**Step 1: Write the failing test**

Add a test that proves `_get_version()` matches installed package metadata or `pyproject.toml`.

Expected behavior:

```python
from importlib.metadata import version

from ovp_pipeline.unified_pipeline_enhanced import _get_version


def test_unified_pipeline_version_matches_distribution_metadata():
    assert _get_version() == version("obsidian-vault-pipeline")
```

If importlib metadata is unavailable in the test environment, the helper should fall back to reading the nearest `pyproject.toml`.

**Step 2: Verify RED**

Run:

```bash
pytest tests/test_unified_pipeline_version.py::test_unified_pipeline_version_matches_distribution_metadata -q
```

Expected:

- FAIL before the fix because current CLI fallback returns `0.3.2`.

**Step 3: Implement minimal fix**

Change `_get_version()` so it tries in this order:

1. `importlib.metadata.version("obsidian-vault-pipeline")`
2. nearest repository `pyproject.toml`
3. current fallback only if both fail

Keep the fallback, but do not let it mask a valid editable install.

**Step 4: Verify GREEN**

Run:

```bash
pytest tests/test_unified_pipeline_version.py::test_unified_pipeline_version_matches_distribution_metadata -q
ovp --version
```

Expected:

- test passes
- `ovp --version` prints `ovp 0.8.6`

## Task 2: Reconcile Post-Merge Roadmap State

**Files:**
- Modify: `task_plan.md`
- Modify: `progress.md`
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Optional Modify: `README.md`

**Step 1: Update current branch and merge state**

Record:

- local `main` is aligned with `origin/main`
- PR #44 is merged
- merge commit is `81e6b9c Add background value and backlink enforcement (#44)`
- local backup branch exists for the pre-sync main pointer:
  - `backup/main-before-origin-main-sync-20260422-032720`

**Step 2: Update Phase 28/29 status**

Replace “PR open / remaining before merge” language with:

- Phase 28/29 complete
- merged to main
- review fixes included:
  - terminal queue statuses no longer appear as queued
  - malformed briefing payloads do not crash the UI
  - object extraction cannot bypass backlink checks without note paths
  - briefing value proof recomputes after productive-signal override
  - evidence/actionability recomputes after item merge

**Step 3: Add Phase 30 as active plan**

Point `task_plan.md` to this document as the canonical next plan.

**Step 4: Verify docs**

Run:

```bash
git diff --check
rg -n "PR #44 is open|Remaining before merge|Phase 28/29.*review" task_plan.md progress.md docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
```

Expected:

- `git diff --check` clean
- no stale “PR open” language remains for PR #44

## Task 3: Install/Smoke Verification Contract

**Files:**
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Optional Modify: `README.md`

**Step 1: Add a post-merge install smoke checklist**

Document the exact local commands:

```bash
git switch main
git fetch --prune
python -m pip install -e .
python -m pip show obsidian-vault-pipeline
ovp --version
ovp --check
ovp-packs --json
ovp-doctor --pack research-tech --json
```

**Step 2: Clarify expected version behavior**

Expected:

- package metadata and `ovp --version` agree
- editable install points to the current checkout
- `ovp --check` succeeds before running real pipeline work

**Step 3: Verify docs**

Run:

```bash
git diff --check
```

Expected:

- clean

## Task 4: Final Verification

Run:

```bash
ruff check src/ovp_pipeline/unified_pipeline_enhanced.py tests/test_unified_pipeline_version.py
pytest tests/test_unified_pipeline_version.py -q
pytest -q
git diff --check
python -m pip install -e .
ovp --version
ovp --check
ovp-doctor --pack research-tech --json
```

Expected:

- ruff passes
- focused version test passes
- full test suite passes
- install succeeds
- CLI version reports the installed package version
- `ovp --check` succeeds
- doctor command returns valid JSON

## Exit Criteria

Phase 30 is complete when:

1. local `main` is aligned with `origin/main`,
2. OVP installs from the current checkout,
3. `ovp --version` matches package metadata,
4. post-merge docs reflect that Phase 28/29 is merged,
5. the next major feature phase can start without stale roadmap state.

## Next Major Phase After This

After Phase 30, the next feature phase should be:

**Phase 31: Pack-Level Semantic Relation Extraction Contract**

That phase should add richer semantic relations only as a pack-owned extraction/review contract, not as hidden global memory. It should start from the existing trust boundaries:

- source attribution,
- brain-first lookup,
- backlink expectations,
- candidate/canonical review,
- action queue governance.
