"""BL-060 owner module for the ``objects`` and ``claims`` canonical tables.

Every write to either table goes through one of the helpers below.
Direct ``INSERT INTO objects`` / ``UPDATE objects`` / ``INSERT INTO claims``
SQL outside this module is a violation tracked by
``tests/test_architecture_fitness.py::test_canonical_writes_have_single_owner``.

See ``docs/canonical-write-ownership.md`` for the full ownership map and
the rationale (PR #185 root cause: three independent modules wrote
``objects.source_url`` with no shared helper).

Locking is **not** the responsibility of helpers in this module — callers
are expected to be inside a ``knowledge_db_write_lock`` already.  These
helpers are pure SQL wrappers; the only thing they enforce is the
single-writer architectural invariant.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Sequence


# ---------------------------------------------------------------------------
# objects
# ---------------------------------------------------------------------------

# Tuple shape for ``insert_objects`` rows.  Mirrors the SQL VALUES order;
# callers (today only ``rebuild_knowledge_index``) build these from
# ``truth_projection.objects[*].to_row()``.
OBJECT_ROW_COLUMNS: tuple[str, ...] = (
    "pack",
    "object_id",
    "object_kind",
    "title",
    "canonical_path",
    "source_slug",
    "source_url",
)


def insert_objects(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[Any]],
) -> None:
    """Bulk-insert evergreen registry rows.

    Caller is responsible for deleting the prior per-pack rows before
    calling — the rebuild does this in ``execute_truth_projection_builder``
    via the projection's pack-scoped clear.  This function only handles
    the insert phase.
    """
    conn.executemany(
        f"""
        INSERT INTO objects ({', '.join(OBJECT_ROW_COLUMNS)})
        VALUES ({', '.join(['?'] * len(OBJECT_ROW_COLUMNS))})
        """,
        list(rows),
    )


def update_object_source_url(
    conn: sqlite3.Connection,
    *,
    pack: str,
    object_id: str,
    source_url: str,
    source: str,
) -> None:
    """Update ``objects.source_url`` for a single (pack, object_id).

    The ``source`` argument is a free-form audit tag (``'rebuild'``,
    ``'backfill'``, ``'mcp_edit'``, ...) — not written to the row, but
    documented at the call site so future debugging knows which writer
    last touched the column.  If you need a durable record of which
    writer wrote it, emit a ``provenance`` row at ``stage='backfill'``
    via ``provenance.upsert_provenance``.
    """
    _ = source  # documentation-only; see docstring
    conn.execute(
        "UPDATE objects SET source_url = ? WHERE pack = ? AND object_id = ?",
        (source_url, pack, object_id),
    )


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------

CLAIM_ROW_COLUMNS: tuple[str, ...] = (
    "pack",
    "claim_id",
    "object_id",
    "claim_kind",
    "claim_text",
    "confidence",
)


def insert_claims(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[Any]],
) -> None:
    """Bulk-insert claims rows.  Same caller-clears-first contract as
    ``insert_objects``."""
    conn.executemany(
        f"""
        INSERT INTO claims ({', '.join(CLAIM_ROW_COLUMNS)})
        VALUES ({', '.join(['?'] * len(CLAIM_ROW_COLUMNS))})
        """,
        list(rows),
    )
