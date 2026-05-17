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
| BL-110 | P1 | **Maintainer UI module split** | Split the two ~8000-line files by product surface. Target: `ui/view_models/{today,items,digest,chats,reader,atlas}.py` and `commands/renderers/{today,items,digest,chats,reader}.py`. Behaviour-preserving moves + a thin back-compat shim if any import path is external. Outcome: changing `/ops/today` no longer touches an 8000-line file; clears the two file-size fitness failures for these modules. |
| BL-111 | P1 | **SQLite boundary cleanup** | NOT a big migration first. Define a data-access facade / repository layer — e.g. `ops_state_store`, `audit_events_store`, `digest_store`, `chat_store` — plus an allowlist policy so `test_architecture_fitness`'s sqlite-boundary check stops being a permanent red. Business modules call the store API; raw `sqlite3` confined to the data layer. Converge read/write rules per table so schema changes have bounded blast radius and the Authority/Projection boundary is enforceable. |
| BL-112 | P2 | **`unified_pipeline_enhanced.py` split** | ~3805 lines mixing CLI args, orchestration, stage artifacts, `step_absorb`/`step_quality`/`step_articles`, batch handling, runtime status, error handling. Split into `pipeline/{orchestrator,artifacts,results}.py` + `pipeline/stages/{articles,quality,absorb}.py`. Outcome: absorb-fallback / quality-artifact / step-contract edits stop happening inside one giant orchestration file; clears the 3805/3500 fitness failure. |

## Sequence

1. **BL-110** first — highest maintenance risk after M26; the UI
   mega-files block clean review of every future maintainer change.
2. **BL-111** — converge data access so schema/projection edits stop
   being project-wide; pairs well with BL-110 (split modules call the
   new stores instead of raw SQL).
3. **BL-112** — pipeline orchestration split; valuable but the
   pipeline file is less hot than the UI ones day-to-day.

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
