"""BL-060 owner module for the ``objects`` and ``claims`` canonical
tables, plus (BL-061) the ``evergreen_revisions`` audit table.

Every write to any of these tables goes through one of the helpers
below.  Direct ``INSERT INTO objects`` / ``UPDATE objects`` / ``INSERT
INTO claims`` / ``INSERT INTO evergreen_revisions`` SQL outside this
module is a violation tracked by
``tests/test_architecture_fitness.py::test_canonical_writes_have_single_owner``.

See ``docs/canonical-write-ownership.md`` for the full ownership map
and the rationale (PR #185 root cause: three independent modules wrote
``objects.source_url`` with no shared helper).

Locking is **not** the responsibility of helpers in this module —
callers are expected to be inside a ``knowledge_db_write_lock`` already.
These helpers are pure SQL wrappers; the only thing they enforce is
the single-writer architectural invariant.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)


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

    ``rows`` is consumed lazily by sqlite3.Connection.executemany so
    callers can pass a generator expression to avoid materialising
    the full per-pack row set in memory.
    """
    conn.executemany(
        f"""
        INSERT INTO objects ({', '.join(OBJECT_ROW_COLUMNS)})
        VALUES ({', '.join(['?'] * len(OBJECT_ROW_COLUMNS))})
        """,
        rows,
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
    ``insert_objects``; ``rows`` is consumed lazily."""
    conn.executemany(
        f"""
        INSERT INTO claims ({', '.join(CLAIM_ROW_COLUMNS)})
        VALUES ({', '.join(['?'] * len(CLAIM_ROW_COLUMNS))})
        """,
        rows,
    )


# ---------------------------------------------------------------------------
# evergreen_revisions (BL-061)
# ---------------------------------------------------------------------------

# Stage labels for ``change_type``.  Free-form for forward-compat; the
# values below are the ones the writers in the codebase emit today.
CHANGE_TYPE_EXTRACT = "extract"
CHANGE_TYPE_PROMOTE = "promote"
CHANGE_TYPE_LLM_REWRITE = "llm_rewrite"
CHANGE_TYPE_EDITOR_EDIT = "editor_edit"
CHANGE_TYPE_ROLLBACK = "rollback"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_evergreen_revision(
    conn: sqlite3.Connection,
    *,
    pack: str,
    object_id: str,
    content_md: str,
    change_type: str,
    changed_by: str = "",
    change_note: str = "",
    derived_at: str | None = None,
) -> int | None:
    """BL-061: append one revision row capturing the prose state of an
    evergreen at the moment a writer mutated it.

    Returns the ``version`` integer assigned to the new row, or
    ``None`` if the revision could not be persisted (schema missing
    on a stale DB, etc. — best-effort contract, matches
    :func:`provenance.upsert_provenance`).

    Versioning: monotonically increasing per ``(pack, object_id)``.
    The first revision recorded for an evergreen is ``version=1``.
    Concurrent writers within the same ``knowledge_db_write_lock``
    transaction get sequential versions; cross-transaction races
    are not possible (the lock serialises all writes).

    ``change_type`` is free-form so future stages don't need a
    schema bump, but should be one of the constants above
    (``extract`` / ``promote`` / ``llm_rewrite`` / ``editor_edit``
    / ``rollback``).

    ``changed_by`` is a free-form audit tag (employee email,
    ``"agent:auto_evergreen_extractor"``, ``"cli:ovp-rollback-evergreen"``).
    """
    if not pack or not object_id or not change_type:
        return None
    ts = derived_at or _utc_now_iso()
    try:
        # Resolve the next version for this (pack, object_id) inside
        # the same transaction so a re-entrant writer in the same
        # tx gets a fresh version each call.  ``COALESCE(MAX, 0) + 1``
        # is safe under the per-DB write lock; cross-tx serialisation
        # is the caller's responsibility (already provided by
        # ``knowledge_db_write_lock`` everywhere this function is used).
        cursor = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 "
            "FROM evergreen_revisions WHERE pack = ? AND object_id = ?",
            (pack, object_id),
        )
        next_version = int(cursor.fetchone()[0])
        conn.execute(
            """
            INSERT INTO evergreen_revisions
              (pack, object_id, version, content_md, change_type,
               changed_by, derived_at, change_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack,
                object_id,
                next_version,
                content_md,
                change_type,
                changed_by,
                ts,
                change_note,
            ),
        )
        return next_version
    except sqlite3.OperationalError as exc:
        # Schema not present (vault from before BL-061; rebuild
        # bumps KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION 6→7 and the
        # next ``ensure_knowledge_db_current`` call creates the
        # table).  Best-effort contract: skip the write so the
        # caller's primary mutation isn't aborted.
        logger.warning(
            "evergreen_revisions write skipped (%s): %s", change_type, exc,
        )
        return None
    except sqlite3.DatabaseError as exc:
        logger.warning(
            "evergreen_revisions write failed — DB error (%s): %s",
            change_type, exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — never abort caller
        logger.warning(
            "evergreen_revisions write failed — unexpected (%s): %s",
            change_type, exc,
        )
        return None
