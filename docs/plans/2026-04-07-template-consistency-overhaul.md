# Template Consistency Overhaul Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the template scripts into a logically self-consistent system with one runtime model, one identity system, and a graph pipeline that remains stable under daily automation.

**Architecture:** Treat the vault filesystem as the durable content store and the registry slug as the only canonical concept identity. Every writer and every consumer must resolve through the same vault context and the same slug-based contract. Graph, MOC, candidate lifecycle, and autopilot all become consumers or mutators of that shared contract instead of maintaining parallel truth systems.

**Tech Stack:** Python 3.10+, argparse CLIs, markdown/frontmatter parsing, SQLite queue, pytest.

---

## Progress Snapshot (2026-04-07)

- Completed: unified vault runtime layout and removed the biggest `Path.cwd()` / `--vault-dir` contract leaks from the main pipeline, graph CLI, article processor, GitHub processor, and paper processor.
- Completed: restored graph scanning for relative vault paths such as `../vault`, and added regression tests.
- Completed: aligned `pinboard_history` / `pinboard_days` planning with the actual processing chain by inserting `pinboard_process` into those modes.
- Completed: fixed CLI help for `ovp-github` / `ovp-paper` so they no longer crash on missing `litellm` before argument parsing.
- Completed: unified graph identity around canonical slug normalization, explicit `note_id` frontmatter, and registry-aware target resolution to avoid resolvable `unknown` nodes.
- Completed: restored legacy registry `search()` behavior with deterministic lexical fallback instead of silent empty results.
- Completed: added Autopilot queue deduplication and repaired the `ovp-repair --autopilot` state model so it now understands `processing` / `started_at`.
- In progress: candidate lifecycle closure, link migration side effects, and Atlas/MOC refresh contracts after promote/merge/reject.

---

### Task 1: Lock the system model

**Files:**
- Modify: `src/openclaw_pipeline/unified_pipeline_enhanced.py`
- Modify: `src/openclaw_pipeline/auto_article_processor.py`
- Modify: `src/openclaw_pipeline/auto_github_processor.py`
- Modify: `src/openclaw_pipeline/auto_paper_processor.py`
- Test: `tests/test_runtime_contracts.py`

**Step 1: Write failing tests for vault path consistency**

Cover:
- CLI `--vault-dir` drives logs, transactions, output roots
- no module-level `Path.cwd()` state leaks into runtime behavior

**Step 2: Implement a shared vault context helper**

Add a small runtime helper that:
- resolves the vault path once
- derives logs, transactions, reports, output roots from that resolved path
- is passed into pipeline/logger/worker code

**Step 3: Refactor pipeline entrypoints to use the helper**

Update the main pipeline and major processors so they no longer compute log/output paths at import time.

**Step 4: Run focused tests**

Run:
- `pytest tests/test_runtime_contracts.py -q`

**Step 5: Commit**

Commit message:
- `git commit -m "refactor: unify vault runtime paths"`

### Task 2: Repair the stage contract chain

**Files:**
- Modify: `src/openclaw_pipeline/unified_pipeline_enhanced.py`
- Test: `tests/test_pipeline_contracts.py`

**Step 1: Write failing tests for main pipeline step order**

Cover:
- `--full` includes pinboard routing
- `--pinboard-days` and `--pinboard-history` include `pinboard_process`
- success is tied to step-specific output contracts, not placeholder counters

**Step 2: Make step planning deterministic**

Extract a planner that returns the exact ordered steps for each mode.

**Step 3: Replace fake success checks**

Each step must declare:
- expected durable output
- how to verify it
- what counts as partial failure

**Step 4: Run focused tests**

Run:
- `pytest tests/test_pipeline_contracts.py -q`

**Step 5: Commit**

Commit message:
- `git commit -m "fix: align pipeline step contracts"`

### Task 3: Make graph scanning work on real vault paths

**Files:**
- Modify: `src/openclaw_pipeline/graph/frontmatter.py`
- Modify: `src/openclaw_pipeline/graph/link_parser.py`
- Modify: `src/openclaw_pipeline/graph/daily_delta.py`
- Test: `tests/test_graph_paths.py`

**Step 1: Write failing tests for relative vault paths**

Cover:
- `../vault` is not filtered as hidden
- parse_directory finds markdown files for relative and absolute paths

**Step 2: Fix hidden-path filtering**

Skip actual hidden files only; never skip `.` or `..`.

**Step 3: Run focused tests**

Run:
- `pytest tests/test_graph_paths.py -q`

**Step 4: Commit**

Commit message:
- `git commit -m "fix: restore graph scanning for relative vault paths"`

### Task 4: Unify graph identity with registry identity

**Files:**
- Modify: `src/openclaw_pipeline/graph/frontmatter.py`
- Modify: `src/openclaw_pipeline/graph/link_parser.py`
- Modify: `src/openclaw_pipeline/graph/graph_builder.py`
- Modify: `src/openclaw_pipeline/concept_registry.py`
- Test: `tests/test_graph_identity.py`

**Step 1: Write failing tests for note identity**

Cover:
- source note id comes from frontmatter/slug contract, not raw stem drift
- wikilink target normalization matches registry slug rules
- graph builder does not create uncontrolled `unknown` nodes for resolvable concepts

**Step 2: Centralize slug normalization**

Expose one canonical normalization function from registry or a dedicated identity module.

**Step 3: Make graph builder registry-aware**

Prefer:
- exact active slug
- alias redirect
- canonical surface match

Only create placeholder nodes when the link is truly unresolved and explicitly flagged.

**Step 4: Run focused tests**

Run:
- `pytest tests/test_graph_identity.py -q`

**Step 5: Commit**

Commit message:
- `git commit -m "refactor: unify graph identity with registry slugs"`

### Task 5: Close the concept lifecycle

**Files:**
- Modify: `src/openclaw_pipeline/promote_candidates.py`
- Modify: `src/openclaw_pipeline/concept_registry.py`
- Modify: `src/openclaw_pipeline/auto_evergreen_extractor.py`
- Modify: `src/openclaw_pipeline/auto_article_processor.py`
- Test: `tests/test_candidate_lifecycle.py`

**Step 1: Write failing lifecycle tests**

Cover:
- candidate create/promote/merge/reject updates registry consistently
- candidate files are written and cleaned deterministically
- promote/merge emits enough information for later link migration and MOC refresh

**Step 2: Add explicit lifecycle side effects**

Promote/merge/reject should return structured mutation results instead of hidden side effects.

**Step 3: Ensure candidate creation is idempotent**

Repeated extraction should update evidence counters, not explode the queue.

**Step 4: Run focused tests**

Run:
- `pytest tests/test_candidate_lifecycle.py -q`

**Step 5: Commit**

Commit message:
- `git commit -m "feat: make candidate lifecycle deterministic"`

### Task 6: Collapse registry/repair/migrate duplication

**Files:**
- Modify: `src/openclaw_pipeline/rebuild_registry.py`
- Modify: `src/openclaw_pipeline/commands/rebuild_registry.py`
- Modify: `src/openclaw_pipeline/migrate_broken_links.py`
- Modify: `src/openclaw_pipeline/commands/migrate_broken_links.py`
- Modify: `src/openclaw_pipeline/repair.py`
- Modify: `src/openclaw_pipeline/commands/repair.py`
- Test: `tests/test_registry_repair_commands.py`

**Step 1: Write failing command contract tests**

Cover:
- one rebuild implementation
- one migrate implementation
- repair checks real queue statuses

**Step 2: Convert legacy modules into wrappers or remove duplicated logic**

There should be one source implementation and thin CLI wrappers only.

**Step 3: Run focused tests**

Run:
- `pytest tests/test_registry_repair_commands.py -q`

**Step 4: Commit**

Commit message:
- `git commit -m "refactor: collapse duplicate registry maintenance commands"`

### Task 7: Make AutoPilot converge instead of drift

**Files:**
- Modify: `src/openclaw_pipeline/autopilot/queue.py`
- Modify: `src/openclaw_pipeline/autopilot/watcher.py`
- Modify: `src/openclaw_pipeline/autopilot/daemon.py`
- Test: `tests/test_autopilot_queue.py`
- Test: `tests/test_autopilot_daemon.py`

**Step 1: Write failing tests for dedupe and retry**

Cover:
- same file is not enqueued twice while pending/processing
- repair sees real processing statuses
- fallback retry re-runs generation or clearly marks no-op
- auto-commit scopes only task-related files

**Step 2: Add queue uniqueness and safer task state transitions**

Use SQLite constraints or guarded inserts.

**Step 3: Scope follow-up automation to the task**

Evergreen extraction and MOC updates should run against the affected files or day range, not global recent-month scans.

**Step 4: Run focused tests**

Run:
- `pytest tests/test_autopilot_queue.py tests/test_autopilot_daemon.py -q`

**Step 5: Commit**

Commit message:
- `git commit -m "fix: make autopilot task processing idempotent"`

### Task 8: Rewrite docs around the real runtime model

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `CLAUDE.md`
- Modify: `CONTRIBUTING.md`
- Modify: `PUSH_GUIDE.md`

**Step 1: Remove legacy script-first instructions**

The docs must only describe supported console scripts and current subcommand syntax.

**Step 2: Document the source-of-truth model**

Include:
- vault filesystem as content store
- registry slug as concept identity
- graph as derived view

**Step 3: Add operator guidance**

Document:
- how to run reconciliation
- how to verify drift
- what autopilot guarantees and what it does not

**Step 4: Run verification**

Run:
- `pytest -q`
- `.review-venv/bin/ovp --help`
- `.review-venv/bin/ovp-graph --help`
- `.review-venv/bin/ovp-promote-candidates --help`

**Step 5: Commit**

Commit message:
- `git commit -m "docs: align docs with the supported runtime model"`

### Task 9: Full-system verification

**Files:**
- Modify: `tests/test_end_to_end_smoke.py`

**Step 1: Add end-to-end smoke coverage**

Cover:
- sample raw input through deep dive + registry + graph + MOC
- relative vault path
- no fake success on missing outputs

**Step 2: Run the complete suite**

Run:
- `pytest -q`

**Step 3: Run CLI verification**

Run:
- `.review-venv/bin/ovp --help`
- `.review-venv/bin/ovp-graph --help`
- `.review-venv/bin/ovp-promote-candidates --help`
- `.review-venv/bin/ovp-rebuild-registry --help`
- `.review-venv/bin/ovp-migrate-links --help`

**Step 4: Commit**

Commit message:
- `git commit -m "test: add end-to-end consistency smoke coverage"`

Plan complete and saved to `docs/plans/2026-04-07-template-consistency-overhaul.md`. I’ll execute it in this session, starting with the first-stage runtime and graph contract fixes.
