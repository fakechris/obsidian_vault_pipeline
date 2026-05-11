"""REVS: read-side helpers for BL-061 ``evergreen_revisions``.

Three entry points:

* :func:`list_evergreen_revisions` — return all revision rows for
  ``(pack, object_id)``, newest-first, with content trimmed for
  list-view rendering.
* :func:`get_evergreen_revision` — return a single revision row by
  ``(pack, object_id, version)``, content_md included verbatim for
  the rollback path.
* :func:`rollback_evergreen` — read revision content_md, write it
  back to the canonical_path on disk, append a new
  ``change_type='rollback'`` revision row referencing the source
  version (BL-061's documented rollback contract).

All three are read-only on the truth store except
``rollback_evergreen`` which adds one new revision row via
:func:`truth_store_writers.record_evergreen_revision` — the
single-writer invariant for ``evergreen_revisions`` is preserved
(only the owner module writes there; this function is a thin
wrapper).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import VaultLayout, resolve_vault_dir


@dataclass(frozen=True)
class RevisionRow:
    """One row of the ``evergreen_revisions`` table.

    Frozen so the renderer can't mutate by accident.  ``content_md``
    is the full snapshot — caller decides whether to trim for
    list-view or use verbatim for rollback / diff.
    """

    pack: str
    object_id: str
    version: int
    content_md: str
    change_type: str
    changed_by: str
    derived_at: str
    change_note: str


def list_evergreen_revisions(
    vault_dir: Path | str,
    *,
    pack: str,
    object_id: str,
    limit: int | None = None,
) -> list[RevisionRow]:
    """Return every revision row for ``(pack, object_id)``, newest
    first.

    Returns an empty list when:

    * knowledge.db doesn't exist (fresh vault — operator should
      run ``ovp-knowledge-index`` first)
    * the ``evergreen_revisions`` table doesn't exist (pre-BL-061
      schema)
    * no rows match — typically because the evergreen pre-dates
      BL-061 + BL-067 hooks (legacy evergreens have no revisions
      until they're rewritten)

    ``limit`` truncates the result set (most-recent N revisions);
    None returns every row.
    """
    resolved = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved)
    if not layout.knowledge_db.exists():
        return []
    sql = (
        "SELECT pack, object_id, version, content_md, change_type, "
        "changed_by, derived_at, change_note "
        "FROM evergreen_revisions "
        "WHERE pack = ? AND object_id = ? "
        "ORDER BY version DESC"
    )
    params: tuple[Any, ...] = (pack, object_id)
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params = (*params, limit)
    try:
        with sqlite3.connect(layout.knowledge_db) as conn:
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Schema missing (pre-BL-061 DB) — return empty list rather
        # than raise so callers can still render the page without a
        # revision tab.
        return []
    return [
        RevisionRow(
            pack=str(p),
            object_id=str(o),
            version=int(v),
            content_md=str(c or ""),
            change_type=str(ct or ""),
            changed_by=str(cb or ""),
            derived_at=str(d or ""),
            change_note=str(n or ""),
        )
        for (p, o, v, c, ct, cb, d, n) in rows
    ]


def get_evergreen_revision(
    vault_dir: Path | str,
    *,
    pack: str,
    object_id: str,
    version: int,
) -> RevisionRow | None:
    """Return the revision row for ``(pack, object_id, version)``,
    or ``None`` when no such row exists.

    Used by the rollback CLI to read ``content_md`` for the target
    version.  ``content_md`` is returned verbatim — the caller is
    expected to write it back to the canonical_path with no
    transformations.
    """
    resolved = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved)
    if not layout.knowledge_db.exists():
        return None
    try:
        with sqlite3.connect(layout.knowledge_db) as conn:
            row = conn.execute(
                "SELECT pack, object_id, version, content_md, change_type, "
                "changed_by, derived_at, change_note "
                "FROM evergreen_revisions "
                "WHERE pack = ? AND object_id = ? AND version = ?",
                (pack, object_id, version),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    p, o, v, c, ct, cb, d, n = row
    return RevisionRow(
        pack=str(p),
        object_id=str(o),
        version=int(v),
        content_md=str(c or ""),
        change_type=str(ct or ""),
        changed_by=str(cb or ""),
        derived_at=str(d or ""),
        change_note=str(n or ""),
    )


def rollback_evergreen(
    vault_dir: Path | str,
    *,
    pack: str,
    object_id: str,
    target_version: int,
    canonical_path: str | None = None,
    changed_by: str = "cli:rollback",
) -> dict[str, Any]:
    """Restore an evergreen's content_md to a prior revision and
    append a ``change_type='rollback'`` row to
    ``evergreen_revisions``.

    Steps:

    1. Load the target revision via :func:`get_evergreen_revision`.
    2. Resolve the canonical_path — caller may pass it explicitly,
       otherwise read from ``objects.canonical_path``.
    3. Write the target revision's ``content_md`` to disk.
    4. Append a new revision row with ``change_type='rollback'``
       and a change_note that references the source version (so
       audit replay knows where the content came from).

    Returns a result dict with the new revision version, the
    canonical_path written to, and the source version — caller
    surfaces this to the operator.

    Raises:

    * ``ValueError`` if the target revision doesn't exist
    * ``FileNotFoundError`` if the canonical_path can't be resolved
    * ``OSError`` propagated from the file write (disk full /
      permissions / etc.)
    """
    target = get_evergreen_revision(
        vault_dir, pack=pack, object_id=object_id, version=target_version,
    )
    if target is None:
        raise ValueError(
            f"No revision found for pack={pack!r} object_id={object_id!r} "
            f"version={target_version}"
        )
    resolved = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved)

    # Resolve canonical_path: explicit > objects lookup > error.
    target_path: str | None = canonical_path
    if not target_path:
        if not layout.knowledge_db.exists():
            raise FileNotFoundError(
                "knowledge.db missing; pass --canonical-path explicitly"
            )
        try:
            with sqlite3.connect(layout.knowledge_db) as conn:
                row = conn.execute(
                    "SELECT canonical_path FROM objects "
                    "WHERE pack = ? AND object_id = ?",
                    (pack, object_id),
                ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0]:
            target_path = str(row[0])
    if not target_path:
        raise FileNotFoundError(
            f"Could not resolve canonical_path for {pack}::{object_id}; "
            "pass --canonical-path explicitly or rebuild knowledge index"
        )

    path = Path(target_path)
    if not path.is_absolute():
        path = resolved / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(target.content_md, encoding="utf-8")

    # Append the rollback revision.  Same single-writer module the
    # rest of BL-061 uses; this CLI is a callsite, not a writer.
    from .truth_store_writers import (
        CHANGE_TYPE_ROLLBACK,
        record_evergreen_revision,
    )

    new_version: int | None = None
    with sqlite3.connect(layout.knowledge_db) as conn:
        new_version = record_evergreen_revision(
            conn,
            pack=pack,
            object_id=object_id,
            content_md=target.content_md,
            change_type=CHANGE_TYPE_ROLLBACK,
            changed_by=changed_by,
            change_note=f"rolled_back_to_version={target_version}",
        )
        conn.commit()

    return {
        "status": "rolled_back",
        "pack": pack,
        "object_id": object_id,
        "source_version": target_version,
        "new_version": new_version,
        "canonical_path": str(path),
        "changed_by": changed_by,
    }
