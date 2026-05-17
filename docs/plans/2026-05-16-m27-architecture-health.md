# M27 — Maintainer Architecture Health

> **Status**: backlog lock, 2026-05-16.
>
> **Not a product gap and NOT an M26 blocker.** M26's daily-observability
> semantic fixes shipped and are dogfooded. M27 is the maintainability
> debt that M26 made impossible to keep ignoring: `tests/test_architecture_fitness.py`
> is **2 failed, 7 passed** —
>
> * file-size limits: `commands/_ui_renderers.py` 7931 / 5000,
>   `ui/view_models.py` 7952 / 5000,
>   `unified_pipeline_enhanced.py` 3805 / 3500
> * sqlite boundary: many non-data-layer modules `import` / use
>   `sqlite3` directly
>
> M26 added significant code to the already-oversized maintainer-UI
> files, so continued feature work there is progressively harder to
> review and riskier to change. No user-visible change in M27 — it
> makes future features cheaper and safer.

## Why now

The two UI mega-files are the most acute risk after M26: every
`/ops/*` and digest/chat/reader change lands in an ~8000-line file,
so review cannot bound blast radius and small edits routinely touch
unrelated surfaces. The sqlite sprawl is the next risk: schema /
projection edits have project-wide blast radius and the
Authority / Projection boundary is unenforceable.

## Backlog (priority BL-110 > BL-111 > BL-112)

| ID | Priority | Work item | Notes |
| --- | --- | --- | --- |
| BL-110 | P1 | ✅ **Done** — **Maintainer UI module split** (#262 view_models, #263 _ui_renderers).  Each ~8000-line file → a package: `_constants` leaf + topologically-layered `_layer*` + per-surface modules + `__init__` re-export with a `ModuleType.__setattr__` monkeypatch shim (zero call-site/test churn).  Every file < the 3000 default; both stale 5000 ratchet entries removed (tightened, not relocated).  `test_file_size_limits` cleared for both. |
| BL-111 | P1 | ✅ **Done (policy codification)** — **SQLite boundary policy**, grouped + rationalised.  Made `test_no_direct_sqlite_in_non_data_modules` green by recording the rule's TRUE boundary, NOT relaxing it: (a) prefix-aware allowlist matcher so BL-110's package splits keep their pre-existing grant (path-shape change, not a new exception); (b) the 15 genuine pre-existing violators grouped into named sets — `ui_projection_payload_builders`, `ops_projection_readers`, `projection_writers`, `thin_projection_clis` — each with a rationale stating it reads/writes the DERIVED `knowledge.db` projection, never canonical Authority (vault markdown + registries), same category already baselined (`digest_inputs`/`materializers/*`/`synthesis/_shared`).  The actual facade migration is **BL-111b** (below), deliberately deferred per "NOT a big migration first". |
| BL-111b | P2 | **Data-access facade migration** (the real refactor BL-111 deferred).  Introduce `ops_state_store` / `audit_events_store` / `digest_store` / `chat_store` so business code never hand-writes SQL; migrate the BL-111 allowlisted groups onto them and shrink the allowlist as each lands.  Start from the highest-value targets: **duplicated SQL** (same table queried from many modules) and **hot paths** (`absorb_router` 1131L, `auto_evergreen_extractor` 2081L, `ops_lifecycle` 713L).  Each migration is behaviour-preserving + full-suite gated; the allowlist is the ratchet (entries only ever removed, never added). |
| BL-112 | P2 | **`unified_pipeline_enhanced.py` split** | ~3805 lines mixing CLI args, orchestration, stage artifacts, `step_absorb`/`step_quality`/`step_articles`, batch handling, runtime status, error handling. Split into `pipeline/{orchestrator,artifacts,results}.py` + `pipeline/stages/{articles,quality,absorb}.py`. Outcome: absorb-fallback / quality-artifact / step-contract edits stop happening inside one giant orchestration file; clears the 3805/3500 fitness failure. |

## Sequence

1. ✅ **BL-110** — done (#262, #263). UI mega-files split.
2. ✅ **BL-111** — done (policy codification): sqlite-boundary check
   green by recording the true boundary; facade migration split out
   as **BL-111b**.
3. **BL-112** — pipeline orchestration split; the last remaining
   `test_file_size_limits` red (`unified_pipeline_enhanced.py`
   3805/3500).
4. **BL-111b** — the deferred data-access facade migration; P2,
   runs after BL-112 (or independently — no ordering dependency).

## Non-Goals

* No behaviour change, no new product surface. Pure structure.
* BL-111 is **not** a schema migration or an ORM adoption — it is a
  boundary/facade + allowlist so the fitness check is enforceable.
* Do not relax the fitness limits to make the check pass — the limits
  are the forcing function; M27 fixes the structure they flag.

## Success Criteria

* `tests/test_architecture_fitness.py` is fully green (file-size +
  sqlite-boundary) without weakening the limits.
* Full suite stays green across each split (behaviour-preserving).
* A change to one maintainer surface (e.g. `/ops/today`) no longer
  edits an 8000-line file or hand-writes SQL outside the data layer.
