# M24 — Lifecycle Contract Layer

> **Renamed from "Pipeline Reset"**, 2026-05-14.  The new name matches
> what M24 actually does: it adds a contract layer over the existing
> producers, it does not rewrite them.  The full rewrite lives in
> M25 (Maintainer Control Plane) and is conditional on M24's evidence.

## Context

M23 shipped the daily-knowledge-feedback digest.  Three structural
problems surfaced within the first week:

1. **Three drifted intake allowlists.**  `/ops/today`, `/digests`
   calendar, and the M23 digest's Layer 0 each maintained their own
   list of which `audit_event` types counted as "intake".  The same
   day rendered 27 / 7 / many across the three surfaces.
2. **Card-totals vs drilldown ledgers mismatch.**  `/ops/today` counts
   raw `audit_events`; `/ops/events` shows timeline projections.  Two
   different ledgers — the "See all N →" link from a card landed the
   operator on a list whose count did not match the card.
3. **Fabricated reasons for zero counts.**  Several places surfaced
   "No new intake today" when in fact the producer hadn't run,
   conflating "ran and produced nothing" with "didn't run".

M24.0 (PR #229, merged) shipped the **event-evidence registry**:
single source of truth for evidence classification, no scope drift,
no truth claims.  That fixed (1) and partially fixed (2).

M24.1–M24.4 build on top to fix (2) fully and dismantle (3).  The
deliverable is a **lifecycle contract layer** — a kernel and a
projection that together let every surface answer:

* "What state is this item in right now?"
* "What evidence are we using to claim that?"
* "If the count is 0, why?"

The Maintainer Control Plane rewrite (M25) consumes this layer; it
does not replace it.  If M25 slips, M24 still stands on its own —
the existing cards keep working, just with honest zeros and a
consistent ledger.

## Scope locks (Maintainer reset constraints)

User locked these on 2026-05-13 and they apply to the full M24/M25
sequence (≈ 4–6 weeks):

* **No new operator-facing features** during M24/M25 except the
  M24.0 stop-gap (already shipped).
* **No card renames in M24.**  `Absorb / Governance / Synthesis`
  stay; M25 renames them to the five-state vocabulary in
  `docs/operational-lifecycle.md`.
* **No new dashboards in M24.**  Existing surfaces gain honest-zero
  footers + consistent drilldowns; nothing new is added.
* **Registry classifies evidence, not truth.**  The lifecycle kernel
  derives truth from evidence; the kernel is the only module that
  makes truth claims.
* **No fabricated reasons for zero.**  Zero counts surface the
  ambiguity (see `docs/operational-lifecycle.md` §Honest-zero).
* **Producer audit (M24.2) is hot-path-only first.**  ≈ 7 producers
  on the article / clippings / github intake → absorb → promote
  path, not the full 20+ producer set.
* **Don't lock the Option B re-run yet.**  Design for it (kernel
  works with rebuilt evidence too), decide after clean-room run.
* **File names are locked.**
  * `event_evidence_registry.py` (already shipped).
  * `ops_lifecycle.py` (M24.1; *not* `pipeline_truth.py`,
    *not* `lifecycle_engine.py`).
  * `ops_state.py` for the Maintainer-readable projection
    (M24.1).

## Five stages

M24.1 — kernel + projection (this milestone covers M24.1 in detail;
M24.2/3/4 are sketched here and get their own plan docs once M24.1
lands).

| Stage | What ships | Gate to next |
|---|---|---|
| M24.1 | `ops_lifecycle.py` kernel + `ops_state.py` projection + tests | kernel passes 5-state classification on a known fixture vault |
| M24.2 | Producer audit (hot-path 7) + repair PRs | every hot-path producer emits the audit row the kernel expects |
| M24.3 | Honest-zero on every surface (digest + cards + drilldowns) | zero counts on `/ops/today`, `/digests`, daily digest, drilldowns all carry the ambiguity footer |
| M24.4 | `/ops/today` cards read from `ops_state` projection (not from raw `audit_events`) | card N === drilldown N for every card |
| M24.5 (optional) | Clean-room rebuild (Option B) of `audit_events` from primary evidence | only if M24.2/3/4 reveal large gaps |

---

## M24.1 — Kernel + projection (this plan)

### Goal

A module `ops_lifecycle.py` that, given a vault's `knowledge.db`,
returns each item's current lifecycle state from
`docs/operational-lifecycle.md`'s five-state vocabulary.  A second
module `ops_state.py` that materialises a Maintainer-readable view
of those states, refreshed on the same cadence the existing index
already runs (`ovp-knowledge-index`).

After M24.1 lands:

* Code can ask `lifecycle_state_of(item)` → one of `Received |
  Extracted | Accepted | Synthesized | NeedsAction`, with an
  evidence trail.
* `ops_state` is queryable like any other projection table.
* No operator-visible surface changes yet.  M24.3 wires the surfaces.

### Stage 1.A — `ops_lifecycle.py` kernel

**Location:** `src/ovp_pipeline/ops_lifecycle.py` (top-level module,
not under `commands/` — it is platform code, the same as
`event_evidence_registry.py`).

**Public surface (locked):**

```python
@dataclass(frozen=True)
class LifecycleState:
    item_id: str            # source slug, object_id, or cluster_id
    item_kind: str          # "source" | "object" | "cluster"
    state: str              # "Received"|"Extracted"|"Accepted"|"Synthesized"|"NeedsAction"
    sub_state: str | None   # "Prepared"|"Projected" or None
    evidence: tuple[str, ...]  # event_type strings, newest first
    last_evidence_at: str   # ISO timestamp of newest evidence row
    needs_action_reason: str | None  # populated when state=="NeedsAction"

def lifecycle_state_of(conn, item_kind, item_id) -> LifecycleState | None
def lifecycle_states_for_kind(conn, item_kind) -> Iterator[LifecycleState]
def lifecycle_counts(conn) -> dict[str, int]  # state → count, for cards
```

**Rules (these are the contract, not implementation hints):**

* The kernel reads only from `audit_events`, `objects`,
  `evergreen_revisions`, `community_crystals`,
  `contradiction_crystals`, and `graph_clusters`.  No reading from
  markdown, no calling out to producers.
* The state derivation function is **pure** given those tables —
  same inputs, same output, always.  No `datetime.now()` inside
  the kernel; freshness checks take an `as_of` argument.
* Classification of each evidence row goes through
  `event_evidence_registry.classify()` — the kernel never hardcodes
  event_type strings.
* When evidence is inconsistent (e.g. registry projection says
  Accepted but no `evergreen_auto_promoted` row exists), the kernel
  sets `sub_state="Projected"` and surfaces the disagreement in
  `evidence`.  It never silently picks one ledger over the other.

**Tests (`tests/test_ops_lifecycle.py`):**

* Fixture vault with one source per state.
  * `received_only.md` — only intake events.
  * `extracted_only.md` — intake + absorb_route_decision.
  * `extracted_prepared.md` — intake + extraction_complete, no
    upsert (Prepared internal sub-state).
  * `accepted.md` — intake + extraction + promote.
  * `synthesized_fresh.md` — accepted + community_crystal fresh.
  * `synthesized_stale.md` — accepted + community_crystal older
    than newest revision (state should be Accepted, not
    Synthesized).
  * `needs_action_failure.md` — failure-category event.
  * `needs_action_governance.md` — open contradiction.
  * `projected_no_promote.md` — `objects` row exists but no
    `evergreen_auto_promoted` event (sub_state Projected).
* Assert state, sub_state, evidence ordering, last_evidence_at.
* Assert `lifecycle_counts` matches.

### Stage 1.B — `ops_state.py` projection

**Location:** `src/ovp_pipeline/ops_state.py`.

**Schema (new table in `knowledge.db`):**

```sql
CREATE TABLE ops_state (
    pack TEXT NOT NULL,
    item_kind TEXT NOT NULL,        -- "source"|"object"|"cluster"
    item_id TEXT NOT NULL,
    state TEXT NOT NULL,            -- five-state vocabulary
    sub_state TEXT,                 -- "Prepared"|"Projected" or NULL
    last_evidence_at TEXT NOT NULL,
    evidence_event_types_json TEXT NOT NULL,
    needs_action_reason TEXT,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (pack, item_kind, item_id)
);
CREATE INDEX ops_state_by_state ON ops_state(pack, state);
CREATE INDEX ops_state_by_last_evidence ON ops_state(pack, last_evidence_at DESC);
```

**Refresh entrypoint:** `ovp-ops-state --rebuild [--pack research-tech]`.
Reuses the same orchestration `ovp-knowledge-index` uses; lands on
the existing post-pipeline DAG step list, not a new cron.

**Rules:**

* Full rebuild only in M24.1.  Incremental refresh ships in M24.4 if
  needed.
* Idempotent.  Calling `--rebuild` twice yields byte-identical rows.
* Adds **one** new step to the unified pipeline DAG, after
  `knowledge_index`, gated on `--full`.  No new scheduler.

**Tests (`tests/test_ops_state.py`):**

* Build the same fixture vault as `test_ops_lifecycle`.
* Run `ovp-ops-state --rebuild`.
* Assert row count, state distribution, idempotency.
* Assert that the projection answer matches the kernel answer for
  every item (the projection cannot lie about state relative to the
  kernel).

### Stage 1.C — CLI surface

Two new entry points in `pyproject.toml`:

```
ovp-ops-state = "ovp_pipeline.commands.ops_state_cli:main"
ovp-lifecycle-show = "ovp_pipeline.commands.ops_state_cli:show_main"
```

* `ovp-ops-state --rebuild` — runs the projection rebuild.
* `ovp-ops-state --show-counts [--pack P]` — prints the
  five-state count distribution.  Same data the M25 cards will
  read.
* `ovp-lifecycle-show <kind> <id>` — prints the kernel's full
  evidence trail for one item.  Debugging surface for M24.2.

### Stage 1.D — DAG wiring

Add one step to `unified_pipeline_enhanced.py`:

```python
("ops_state", "Rebuild ops_state projection"),
```

* Runs after `knowledge_index`.
* `--check` validates that `ops_state` schema is current.
* `--dry-run` reports row counts without writing.

### Stage 1.E — Audit-event additions (M24.1 scope, minimal)

The kernel needs **one** new audit row that doesn't exist today, to
make the Prepared internal sub-state observable:

* `absorb_pending_upsert` — fired by `auto_evergreen_extractor` when
  extraction completes but the candidate hasn't been upserted yet.

Adding this row is in scope for M24.1 because the kernel design
depends on it.  Registering the event_type in the registry (`absorb`
category, `user_visible=False`) is part of the same PR.

No other producer changes in M24.1.  Producer-audit at scale lives
in M24.2.

### Non-goals for M24.1

* **No surface changes.**  `/ops/today`, `/digests`, daily digest
  bodies are unchanged.  M24.3 wires them.
* **No card renames.**  M25's job.
* **No `/ops/items` route.**  M25's job.
* **No removal of `event_types_filter=...` query param.**  The M24.0
  drilldown stays.

---

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Kernel disagrees with reality on real operator vault (lifecycle states are wrong) | Fixture-vault tests cover every state and sub-state.  After lands, run `ovp-lifecycle-show` against the live vault on a sample of 20 items, spot-check by hand. |
| `ops_state` rebuild is slow on large vaults | Rebuild is full-table for M24.1; benchmark on the operator vault before claiming done.  Incremental refresh waits for M24.4 only if the full rebuild times out. |
| `absorb_pending_upsert` row leaks into Maintainer surfaces | Registered with `user_visible=False`.  Tested in `tests/test_event_evidence_registry.py`. |
| Option B clean-room rebuild becomes necessary mid-M24 | Kernel is pure and reads only schema-stable tables.  A rebuild of `audit_events` from primary evidence yields a kernel answer using the same code path — no kernel changes needed for Option B. |
| Producer audit (M24.2) reveals so many gaps that the kernel reports widespread `Projected` sub-state | Acceptable.  That **is** the diagnostic M24.2 needs.  Surface it; do not paper over it. |

## Open issues to flag (post-Stage-1)

* **Cross-pack lifecycle.**  Does an evergreen promoted into one pack
  but referenced in another count as Accepted in both?  Default
  answer: state is per-pack, evidence is read pack-scoped.  Lock
  this in `ops_lifecycle.py` docstring when the kernel lands.
* **NeedsAction priority ordering.**  Today multiple
  failure/governance rows can pile up on one item.  The kernel
  returns *all* in `evidence`; M25 decides the display order.  Not
  M24.1's call.
* **Time-window scoping for cards.**  Cards today show "today's"
  counts.  `ops_state` carries `last_evidence_at` — the M24.4 card
  query reads `WHERE last_evidence_at >= :window_start`.  Window
  semantics inherit M23's digest config (operator-local day).

## Acceptance checklist (M24.1)

* [ ] `docs/operational-lifecycle.md` merged on main.
* [ ] `src/ovp_pipeline/ops_lifecycle.py` + tests merged.
* [ ] `src/ovp_pipeline/ops_state.py` + tests merged.
* [ ] `event_evidence_registry` knows `absorb_pending_upsert`.
* [ ] `auto_evergreen_extractor` emits `absorb_pending_upsert` when
      extraction completes without an upsert.
* [ ] `ovp-ops-state --rebuild` runs on the operator vault under 30s
      (full rebuild, ~50k audit rows).
* [ ] `ovp-lifecycle-show source 2026-05-12-some-article` returns a
      sensible evidence trail.
* [ ] Full `pytest` run stays green (modulo the two known pre-existing
      architecture-fitness failures).

---

*Plan author: Claude (M24.1).  User-locked constraints captured.
Per scope-lock: ship M24.1, then re-plan M24.2 against M24.1's
real-vault output, not against this doc's assumptions.*
