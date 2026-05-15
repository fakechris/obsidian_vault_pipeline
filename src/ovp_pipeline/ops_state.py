"""Maintainer-readable lifecycle projection (M24.1, 2026-05-14).

Materializes the output of :mod:`ovp_pipeline.ops_lifecycle` into a
queryable ``ops_state`` table inside ``knowledge.db``.  The kernel is
the truth; this module is its persisted, indexed view.

Why a projection rather than running the kernel at request time:

* Cards on ``/ops/today`` and ``/digests`` need O(1) lookups, not a
  full re-classification of every audit row.
* The Maintainer Control Plane (M25) will paginate / filter / sort
  by state; doing that against ``audit_events`` directly forces the
  kernel into every page render.
* Pipeline DAG steps need a fixed-cost dependency to track; the
  projection lands one row per item per pack and is idempotent.

Refresh model
-------------

* Full rebuild only in M24.1.  Idempotent — calling ``rebuild`` twice
  produces byte-identical row content (modulo ``refreshed_at``).
* Triggered by ``ovp-ops-state --rebuild`` or by the ``ops_state``
  DAG step in :mod:`unified_pipeline_enhanced` (added after
  ``knowledge_index``).
* Incremental refresh (only items whose evidence changed since last
  build) is M24.4 work, not M24.1.  Don't reach for it until the
  full rebuild's wall-time is actually measured on the operator
  vault.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from .ops_lifecycle import (
    ALL_ITEM_KINDS,
    ALL_STATES,
    LifecycleState,
    lifecycle_states_for_kind,
)


OPS_STATE_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS ops_state (
    pack TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    state TEXT NOT NULL,
    sub_state TEXT,
    last_evidence_at TEXT NOT NULL,
    evidence_event_types_json TEXT NOT NULL,
    needs_action_reason TEXT,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (pack, item_kind, item_id)
);
CREATE INDEX IF NOT EXISTS idx_ops_state_by_state
    ON ops_state(pack, state);
CREATE INDEX IF NOT EXISTS idx_ops_state_by_last_evidence
    ON ops_state(pack, last_evidence_at DESC);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the ``ops_state`` table + indexes if they don't exist.

    Safe to call on every rebuild; the ``IF NOT EXISTS`` guards make
    this idempotent across schema upgrades.
    """
    conn.executescript(OPS_STATE_SCHEMA)


def _row_from_state(state: LifecycleState, refreshed_at: str) -> tuple:
    return (
        state.pack,
        state.item_kind,
        state.item_id,
        state.state,
        state.sub_state,
        state.last_evidence_at,
        json.dumps(list(state.evidence), ensure_ascii=False),
        state.needs_action_reason,
        refreshed_at,
    )


def rebuild(
    conn: sqlite3.Connection,
    *,
    pack: str,
    as_of: str = "",
) -> dict[str, int]:
    """Truncate + repopulate ``ops_state`` rows for ``pack``.

    Returns a per-state count dict (the same shape
    :func:`ops_lifecycle.lifecycle_counts` returns) so the DAG step
    can log the result without re-querying.

    Idempotent in content: two consecutive calls with no audit-log
    changes produce identical row content (the ``refreshed_at``
    column is the only thing that moves).
    """
    ensure_schema(conn)
    refreshed_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    conn.execute("DELETE FROM ops_state WHERE pack = ?", (pack,))

    # M25.6 perf fix: build the audit-events index ONCE for the
    # whole rebuild, share across all three item kinds.  Before
    # this, each kind triggered its own kernel-internal index
    # build, which on the operator vault (9.5k objects × 36k
    # audit rows) timed out the M24.1 step at 5 minutes.
    from .ops_lifecycle import _build_audit_index
    audit_index = _build_audit_index(conn)

    counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    rows: list[tuple] = []
    for kind in ALL_ITEM_KINDS:
        for state in lifecycle_states_for_kind(
            conn, kind, pack=pack, as_of=as_of,
            audit_index=audit_index,
        ):
            rows.append(_row_from_state(state, refreshed_at))
            counts[state.state] = counts.get(state.state, 0) + 1

    if rows:
        conn.executemany(
            "INSERT INTO ops_state "
            "  (pack, item_kind, item_id, state, sub_state, "
            "   last_evidence_at, evidence_event_types_json, "
            "   needs_action_reason, refreshed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    conn.commit()
    return counts


def rebuild_db_path(
    db_path: Path | str,
    *,
    pack: str,
    as_of: str = "",
) -> dict[str, int]:
    """Convenience wrapper for callers that pass a path instead of a conn."""
    with sqlite3.connect(str(db_path)) as conn:
        return rebuild(conn, pack=pack, as_of=as_of)


def counts_from_projection(
    conn: sqlite3.Connection, *, pack: str
) -> dict[str, int]:
    """O(1) per-state count read from the projection.

    Use this from M25's card-rendering code path so the cards never
    re-run the kernel.  Falls back to zeros for missing states so
    callers can plot the five buckets without preprocessing.
    """
    ensure_schema(conn)
    counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    rows = conn.execute(
        "SELECT state, COUNT(*) FROM ops_state "
        " WHERE pack = ? "
        " GROUP BY state",
        (pack,),
    ).fetchall()
    for state, count in rows:
        if state in counts:
            counts[state] = int(count)
    return counts


__all__ = [
    "OPS_STATE_SCHEMA",
    "counts_from_projection",
    "ensure_schema",
    "rebuild",
    "rebuild_db_path",
]
