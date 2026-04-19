# Stage Checkout Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend stage artifact checkout from the quality/absorb handoff into a small, deterministic runtime cache contract.

**Architecture:** Pipeline stages get explicit cache policy. Source and queue-consuming stages are record-only and must not be skipped from old state. Deterministic derived stages can checkout a matching stage artifact when the input digest, algorithm digest, pack, and profile match, and any declared output paths still exist.

**Tech Stack:** Python, pytest, OVP runtime ledger, `StageArtifactStore`.

## Tasks

### Task 1: Lock Runtime Cache Policy With Tests

**Files:**
- Modify: `tests/test_runtime_paths.py`
- Modify: `tests/test_stage_artifacts.py`

**Steps:**
1. Add a test proving `run_pipeline()` skips a cacheable deterministic stage on a matching artifact.
2. Add a test proving `pinboard` remains record-only and still executes even if an artifact exists.
3. Add a test proving a cache artifact with missing declared output paths is ignored.
4. Add a test proving successful deterministic stage execution writes a reusable artifact.

### Task 2: Add Generic Stage Artifact Helpers

**Files:**
- Modify: `src/ovp_pipeline/stage_artifacts.py`
- Modify: `src/ovp_pipeline/unified_pipeline_enhanced.py`

**Steps:**
1. Add output-path validation for stage artifact manifests.
2. Add a stage policy table: `checkout` for deterministic derived stages, `record_only` for external/source stages.
3. Add stable stage input collection and algorithm digests.
4. Keep quality-specific artifact output shape for absorb compatibility.

### Task 3: Integrate Checkout Into Pipeline Loop

**Files:**
- Modify: `src/ovp_pipeline/unified_pipeline_enhanced.py`

**Steps:**
1. Before handler dispatch, attempt checkout only for non-dry-run `checkout` stages.
2. On cache hit, write a completed ledger step with `cache_hit`, `skipped`, fingerprint, and artifact path.
3. After successful deterministic execution, write the completed artifact.
4. Never auto-skip `pinboard`, `pinboard_process`, `clippings`, or `articles`.

### Task 4: Verify

**Commands:**
- `PYTHONPATH=src pytest tests/test_stage_artifacts.py tests/test_runtime_paths.py -q`
- `PYTHONPATH=src pytest -q`

