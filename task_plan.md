# Task Plan: OVP Roadmap Closeout And Phase 28/29

## Runtime Note

- Current branch: `phase28-29-background-value-backlinks`
- Current remote base: `75febbd Add Phase 27 orchestration closeout`
- Last full verification on merged Phase 27 branch: `pytest -q` -> `689 passed`
- Untracked local files intentionally not part of the roadmap work:
  - `.review-venv/`
  - `AGENTS.md`
  - `important_files_diff.txt`

## Current Product State

OVP is now a usable local knowledge workbench with:

- explicit truth/object browsing
- source-to-knowledge traceability
- review console surfaces
- candidate/canonical review actions
- observable pipeline run ledger
- active signal loop
- action queue and focused action worker execution surface

The current missing product claims are:

> OVP has a single focused execution surface, but broader background intelligence still needs a clearer value proof: why a briefing item is useful, what evidence supports it, and what background policy allowed or skipped it.

> OVP exposes backlink expectations, but focused object writes do not yet enforce them before creating downstream knowledge.

## Current Phase

`Phase 28/29: Background Value And Backlink Enforcement` is implemented and verified locally.

Canonical plan:

- `docs/plans/2026-04-21-phase28-29-background-value-and-backlink-enforcement.md`

## Roadmap Status

| Milestone | Status | Notes |
| --- | --- | --- |
| Milestone 0 | Complete | Template and baseline vault structure |
| Milestone 1 | Complete | Knowledge DB foundation |
| Milestone 2 | Complete | Truth and source projection |
| Milestone 3 | Complete | UI access to local truth |
| Milestone 4 | Complete | Event/contradiction semantics |
| Milestone 5 | Complete | Production traceability |
| Milestone 6 | Complete | Product shell and operator UX |
| Milestone 7 | Complete | Active signal loop, runtime visibility, candidate/canonical workbench |
| Milestone 8 | Complete | Knowledge evolution layer |
| Milestone 9 | In Progress | Background intelligence has observable execution foundations; Phase 28 now closes value proof and policy legibility |
| Milestone 9A | Complete | Single focused execution surface via action queue / worker / handler contracts |

## Completed Recent Phases

- `Phase 22`: active signal impact accounting
- `Phase 23`: inbound capture audit visibility
- `Phase 24`: brain-first lookup and backlink legibility
- `Phase 25`: observable runtime and run ledger
- `Phase 26`: candidate canonicalization workbench
- `Phase 27`: background intelligence orchestration closeout

## Current TODO

- Phase 27 has been squash-merged to `origin/main` in PR #43.
- Phase 28 adds background-intelligence value proof and policy legibility.
- Phase 29 enforces backlink expectations before focused object writes.
- Remaining before PR:
  - commit and open PR when requested

Latest completed Phase 27 verification:

- `pytest tests/test_run_actions_command.py tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py tests/test_watch_progress_command.py -q` -> `263 passed`
- `ruff check ...` on touched Python files -> `All checks passed`
- `python -m pip install -e .` -> installed editable `obsidian-vault-pipeline==0.8.6`
- installed-command validation with `python -m ovp_pipeline.commands.run_actions --once --safe-only` -> blocked missing target with persisted worker state
- `git diff --check` -> clean
- `pytest -q` -> `689 passed`

Completed Phase 28/29 verification:

- `ruff check src/ovp_pipeline/truth_api.py src/ovp_pipeline/packs/research_tech/surfaces.py src/ovp_pipeline/ui/view_models.py src/ovp_pipeline/commands/ui_server.py tests/test_truth_api.py tests/test_ui_server.py` -> `All checks passed!`
- `pytest tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py -q` -> `255 passed`
- `pytest -q` -> `690 passed`
- `git diff --check` -> clean

## Implemented In Phase 27

- `/api/runtime` now includes action-worker state from `60-Logs/action-worker.json`.
- `run_actions` writes worker state for one-shot and loop execution.
- `/` renders both the current broad workflow and the focused action worker.
- focused actions now run preconditions before handler execution.
- action queue items persist deterministic `blocked_reason` and `obsolete_reason`.
- `/signals`, `/briefing`, and `/actions` share action lifecycle state.
- safe batch execution returns attempted / ran / skipped unsafe / obsolete / blocked / failed / stopped counts.
- `/actions` exposes handler provider, processor provider, source-signal activity, precondition state, and last result.

## Decisions

| Decision | Rationale |
| --- | --- |
| Do not continue in Milestone 7 | Phase 26 closed the remaining candidate/canonical operator gap. |
| Do not create a second execution system | Phase 27 kept the existing action queue as the single focused execution surface. |
| Treat Phase 27 as closeout/hardening, not greenfield | The implementation hardened action queue, worker, handler registry, focused action contracts, and auto-queue rather than adding a new engine. |
| Keep `ovp --incremental` / `ovp --full` as broad reconcilers | The action queue is for focused follow-up execution, not batch replacement. |
| Defer richer semantic extraction | It should enter later as a pack-level extraction contract after value proof and backlink trust boundaries are closed. |

## Non-Goals For The Next Phase

- No hosted scheduler.
- No harness/session memory backend.
- No new semantic relation extractor; Phase 29 only enforces the existing backlink contract.
- No direct UI-to-workflow execution outside the action queue.
- No broad frontend redesign.

## Verification Habit

Before opening or closing the Phase 27 implementation PR:

1. run targeted tests for the changed action/runtime path,
2. run `pytest tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py tests/test_watch_progress_command.py -q`,
3. run `pytest -q`,
4. inspect review automation and thread state,
5. merge only after no current blocking review threads remain.
