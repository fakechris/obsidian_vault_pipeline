# Task Plan: OVP Roadmap Closeout, Phase 30, And Phase 31

## Runtime Note

- Current branch: `main`
- Current remote base: `81e6b9c Add background value and backlink enforcement (#44)`
- Local `main` is aligned with `origin/main`
- Pre-sync local main backup branch: `backup/main-before-origin-main-sync-20260422-032720`
- Latest local install: `python -m pip install -e .` -> installed editable `obsidian-vault-pipeline==0.8.6`
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

## Product Shape Addendum: 2026-04-29

Reference: `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

Active implementation backlog: `BACKLOG.md`

The current UI is too operator-first. It exposes real system power, but its first impression is runtime state, actions, signals, candidates, clusters, and run history. That makes the product feel more like an engineering console than a readable knowledge base.

LearnBuffett is now recorded as a product-shape reference:

- person / concept / company objects should feel like readable topic pages first
- graph should be a spatial map of the corpus, not primarily a cluster/debug report
- backlinks and evidence should support the reading flow instead of leading with raw operational tables
- operator surfaces should remain available, but behind an `/ops` framing

Reconciled product thesis:

> OVP remains an auditable knowledge compiler internally, but its default user-facing surface should be a reader-first, evidence-backed knowledge atlas.

## Backlog Reconciliation Addendum: 2026-04-29

The KSR project page in `/Users/chris/Documents/ovp-vault/` should not be treated as the backlog source of truth. It appears to be a recent task extraction from the latest research notes, so it is high-signal for KSR gaps but incomplete for prior roadmap history.

Use a four-input merge when deciding roadmap sequence:

- repo milestone history for what has shipped and what older product bets existed
- `docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md` for the trusted-reuse/compiler direction
- vault KSR page for recent Knowledge State Runtime task extraction
- `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md` for reader-first product shape

## Current Reader-Product Backlog

| Priority | Item | Status | Notes |
| --- | --- | --- | --- |
| P0 | Reader home / Knowledge Atlas | Proposed next | Make `/` answer what the corpus knows, what matters, what changed, and what to read next |
| P0 | Move current dashboard to `/ops` | Proposed next | Preserve runtime/action/operator value without making it first impression |
| P0 | Kind-aware object pages | Next | Person, concept, company/tool/project, event, claim templates |
| P1 | Mention/backlink rail | Next | LearnBuffett-style "linked to this page" context with excerpts and source jumps |
| P1 | Visual `/graph` map | Next | Spatial type-colored graph; keep analytical clusters as ops/debug surface |
| P1 | Reader-oriented search | Later | Group by kind and reading intent, show summary/evidence/reason |
| P1 | Trusted reuse loop | Keep | Still the north-star measurement layer |
| P1 | Evidence v2 | Keep | Needed for long-term trust |
| P2 | Policy promotion | Keep | Important, but after product entry is understandable |
| P2 | Reviewed semantic extractor | Later | Do not add more graph complexity before graph is readable |
| P2 | Query feedback loop | Later | Strong compounding loop, after reader surfaces are clearer |

The previous missing product claims are now closed by Phase 28/29:

> OVP has a single focused execution surface, but broader background intelligence still needs a clearer value proof: why a briefing item is useful, what evidence supports it, and what background policy allowed or skipped it.
>
> OVP exposes backlink expectations, but focused object writes do not yet enforce them before creating downstream knowledge.

## Current Phase

`Phase 30: Release Hygiene And Roadmap Reconciliation` is complete in this working tree.

`Phase 31: Pack-Level Semantic Relation Contract` is implemented as a minimal contract slice in this working tree.

Canonical plans:

- `docs/plans/2026-04-22-phase30-release-hygiene-and-roadmap-reconciliation.md`
- `docs/plans/2026-04-22-phase31-pack-semantic-relation-contract.md`

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
| Milestone 9 | In Progress | Background intelligence has observable execution foundations; Phase 28/29 closed value proof, policy legibility, and backlink enforcement |
| Milestone 9A | Complete | Single focused execution surface via action queue / worker / handler contracts |

## Completed Recent Phases

- `Phase 22`: active signal impact accounting
- `Phase 23`: inbound capture audit visibility
- `Phase 24`: brain-first lookup and backlink legibility
- `Phase 25`: observable runtime and run ledger
- `Phase 26`: candidate canonicalization workbench
- `Phase 27`: background intelligence orchestration closeout
- `Phase 28/29`: background value proof and backlink enforcement
- `Phase 30`: release hygiene and roadmap reconciliation
- `Phase 31`: pack-level semantic relation contract, without extractor or truth promotion

## Current TODO

- Phase 27 has been squash-merged to `origin/main` in PR #43.
- Phase 28/29 has been squash-merged to `origin/main` in PR #44.
- Phase 30 fixed local release hygiene:
  - `ovp --version` reads installed package metadata before falling back to repo metadata
  - roadmap/progress docs describe PR #44 as merged
  - `docs/research-tech/RESEARCH_TECH_VERIFY.md` documents local install smoke checks
- Phase 31 added the semantic relation contract boundary:
  - `research-tech` declares `research_semantic_relations`
  - `semantic_relation_candidate` is a review-queue artifact, not canonical graph truth
  - `ovp-doctor` exposes declared/effective semantic relation contracts
- Next major feature phase should build a reviewed semantic relation extractor that emits candidates only.

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

Post-merge local install verification:

- local `main` aligned to `origin/main` at `81e6b9c`
- `python -m pip install -e .` -> installed editable `obsidian-vault-pipeline==0.8.6`
- `python -m pip show obsidian-vault-pipeline` -> editable project location is this checkout
- `ovp --check` -> environment ready
- `ovp-packs --json` -> built-in `default-knowledge` and `research-tech` packs detected
- `ovp-doctor --pack research-tech --json` -> research-tech contracts detected
- fixed: `ovp --version` now reports package metadata instead of stale fallback `0.3.2`

Completed Phase 30/31 focused verification:

- `pytest tests/test_unified_pipeline_version.py::test_unified_pipeline_version_matches_distribution_metadata -q` -> `1 passed`
- `pytest tests/test_semantic_relation_contracts.py -q` -> `3 passed`
- `ruff check` on touched Python files/tests -> `All checks passed`
- `pytest tests/test_unified_pipeline_version.py tests/test_semantic_relation_contracts.py tests/test_doctor_command.py tests/test_artifact_registry.py -q` -> `12 passed`
- `pytest -q` -> `698 passed`
- `python -m pip install -e .` -> installed editable `obsidian-vault-pipeline==0.8.6`
- `ovp --version` -> `ovp 0.8.6`
- `ovp --check` -> environment ready
- `ovp-doctor --pack research-tech --json` -> valid JSON with `research_semantic_relations`

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

## Non-Goals For Phase 30/31

- No hosted scheduler.
- No harness/session memory backend.
- No new semantic relation extractor; Phase 31 declares the contract boundary only.
- No automatic relation promotion into canonical graph truth.
- No direct UI-to-workflow execution outside the action queue.
- No broad frontend redesign.

## Verification Habit

Before closing any implementation PR:

1. run targeted tests for the changed action/runtime path,
2. run `pytest tests/test_truth_api.py tests/test_ui_view_models.py tests/test_ui_server.py tests/test_watch_progress_command.py -q`,
3. run `pytest -q`,
4. inspect review automation and thread state,
5. merge only after no current blocking review threads remain.
