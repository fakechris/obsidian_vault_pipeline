# Absorb And Refine Commands Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Introduce first-class `ovp-absorb`, `ovp-cleanup`, and `ovp-breakdown` commands that align the CLI surface with the new 6-layer knowledge architecture.

**Architecture:** Add thin command modules first, with explicit structured outputs and stable contracts. `ovp-absorb` initially wraps the existing deep-dive absorption flow, while `ovp-cleanup` and `ovp-breakdown` begin as deterministic analyzers that emit reviewable proposals rather than mutating canonical state blindly.

**Tech Stack:** Python 3.10+, argparse CLIs, existing registry/runtime helpers, pytest.

---

### Task 1: Add CLI contract tests for absorb/refine commands

**Files:**
- Create: `tests/test_absorb_refine_commands.py`

**Step 1: Write failing tests**

Cover:
- `ovp-absorb --help` exists and exposes deep-dive oriented arguments
- `ovp-cleanup --help` exists and exposes proposal-oriented arguments
- `ovp-breakdown --help` exists and exposes proposal-oriented arguments
- command mains return `0` for dry-run style proposal generation on a temp vault

**Step 2: Run tests to verify failure**

Run:
- `pytest -q tests/test_absorb_refine_commands.py`

**Step 3: Commit**

```bash
git add tests/test_absorb_refine_commands.py
git commit -m "test: cover absorb and refine command contracts"
```

### Task 2: Add absorb/refine command modules

**Files:**
- Create: `src/openclaw_pipeline/commands/absorb.py`
- Create: `src/openclaw_pipeline/commands/cleanup.py`
- Create: `src/openclaw_pipeline/commands/breakdown.py`
- Modify: `pyproject.toml`

**Step 1: Implement minimal CLI surfaces**

Required behavior:
- `ovp-absorb`
  - accepts `--file`, `--dir`, `--recent`, `--vault-dir`, `--dry-run`, `--auto-promote`, `--promote-threshold`, `--json`
  - delegates to the existing absorb worker (`AutoEvergreenExtractor`)
- `ovp-cleanup`
  - accepts `--vault-dir`, `--slug`, `--all`, `--dry-run`, `--json`
  - emits cleanup proposals, no canonical mutation yet
- `ovp-breakdown`
  - accepts `--vault-dir`, `--slug`, `--all`, `--dry-run`, `--json`
  - emits split/breakdown proposals, no canonical mutation yet

**Step 2: Add script entrypoints**

Add:
- `ovp-absorb`
- `ovp-cleanup`
- `ovp-breakdown`

**Step 3: Run tests**

Run:
- `pytest -q tests/test_absorb_refine_commands.py`

**Step 4: Commit**

```bash
git add pyproject.toml src/openclaw_pipeline/commands/absorb.py src/openclaw_pipeline/commands/cleanup.py src/openclaw_pipeline/commands/breakdown.py tests/test_absorb_refine_commands.py
git commit -m "feat: add absorb and refine command surfaces"
```

### Task 3: Add deterministic proposal analyzers for cleanup/breakdown

**Files:**
- Create: `src/openclaw_pipeline/refine.py`
- Modify: `src/openclaw_pipeline/commands/cleanup.py`
- Modify: `src/openclaw_pipeline/commands/breakdown.py`
- Test: `tests/test_refine_proposals.py`

**Step 1: Write failing tests**

Cover:
- large mixed evergreen note produces a breakdown proposal
- diary-driven or low-structure note produces a cleanup proposal
- proposals include `decision_type`, `slug`, `action`, `confidence`, and `reasons`

**Step 2: Implement minimal deterministic analyzers**

Use heuristics only:
- line count
- heading structure
- link density
- repeated temporal headings / diary-like patterns

Do not call LLM yet.

**Step 3: Run tests**

Run:
- `pytest -q tests/test_refine_proposals.py`

**Step 4: Commit**

```bash
git add src/openclaw_pipeline/refine.py src/openclaw_pipeline/commands/cleanup.py src/openclaw_pipeline/commands/breakdown.py tests/test_refine_proposals.py
git commit -m "feat: add structured cleanup and breakdown proposals"
```

### Task 4: Integrate absorb command with existing lifecycle contracts

**Files:**
- Modify: `src/openclaw_pipeline/commands/absorb.py`
- Modify: `src/openclaw_pipeline/auto_evergreen_extractor.py`
- Test: `tests/test_absorb_refine_commands.py`

**Step 1: Ensure absorb output is structured**

Dry-run and non-dry-run should return proposal/mutation summaries, not only print text.

**Step 2: Run tests**

Run:
- `pytest -q tests/test_absorb_refine_commands.py`

**Step 3: Commit**

```bash
git add src/openclaw_pipeline/commands/absorb.py src/openclaw_pipeline/auto_evergreen_extractor.py tests/test_absorb_refine_commands.py
git commit -m "refactor: expose structured absorb summaries"
```

### Task 5: Full verification

**Files:**
- Verify existing codebase and new command files

**Step 1: Run compile**

Run:
- `python3 -m compileall src/openclaw_pipeline`

**Step 2: Run tests**

Run:
- `pytest -q`

**Step 3: Commit**

```bash
git add -A
git commit -m "test: verify absorb and refine command integration"
```
