"""Versioning helpers shared by community + contradiction crystals
(BL-044, M13).

The crystal tables are append-only by primary-key construction
(``synthesized_at`` is part of the PK), but several pieces of
bookkeeping must fire when a new version lands:

* The previously-current row's ``superseded_by_synthesized_at``
  must be set so chain readers can navigate the version graph
  without scanning the whole table.

* The new live markdown file must replace the prior one at
  ``40-Resources/Crystals/<safe-id>.md``.

* The prior live markdown should be archived to
  ``70-Archive/Crystals/<safe-id>/<sanitized-ts>.md``.

These steps span two systems (SQLite + filesystem) so they can't be
made fully atomic, but ``commit_crystal_version`` orders them so
the failure modes are recoverable:

  1. **Snapshot prior live content** into memory (no FS mutation).
  2. **DB transaction**: UPDATE prior row's supersede pointer +
     INSERT new row, both committed together or both rolled back.
  3. **Atomic-replace live file** via tempfile + ``os.replace``.
  4. **Archive prior content** (best-effort — DB has the body_md,
     so a missing archive is recoverable).

The pre-PR-133-review version did the file moves BEFORE the new DB
row was durable; a crash between archive-move and INSERT could
leave the live directory empty with no DB row matching either
the new or the archived version.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


# Where superseded crystal markdowns land (relative to vault root).
# Inside, one subdirectory per crystal id (community sha or
# ``contradiction-<sha>``), each holding the historical snapshots.
ARCHIVE_DIR_REL: Path = Path("70-Archive") / "Crystals"


def _safe_archive_filename(synthesized_at: str) -> str:
    """ISO timestamps contain ``:`` (and microseconds add ``.``)
    which are unportable on Windows.  Replace with ``-`` for the
    archive filename — the canonical timestamp survives in the
    file's frontmatter and the ``synthesized_at`` DB column.
    """
    return synthesized_at.replace(":", "-").replace(".", "-") + ".md"


def commit_crystal_version(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    pack: str,
    key_value: str,
    new_synthesized_at: str,
    insert_sql: str,
    insert_params: tuple,
    new_markdown: str,
    live_path: Path,
    archive_subdir: Path,
) -> str | None:
    """Commit one new crystal version with safe failure ordering.

    Returns the prior ``synthesized_at`` (the timestamp that this
    version supersedes) or ``None`` if this is the first version.

    Ordering guarantees:

    * The DB transaction (supersede + INSERT) is durably committed
      BEFORE any FS mutation.  A crash before commit leaves the
      live file untouched and the prior row un-superseded.
    * The new live markdown is written via tempfile +
      ``os.replace`` for atomic replacement on a single filesystem.
    * The archive write happens LAST.  A crash between
      ``os.replace`` and the archive write loses the archive copy
      but the prior body_md is still in the DB row, so it's
      reconstructable.

    See ``_versioning.py`` module docstring for the full rationale.
    """
    # Step 1: snapshot live file content so we can archive it after
    # the new version is durably persisted.  Read failure here is
    # not fatal — we proceed without an archive copy and rely on
    # the DB row's body_md for reconstruction.
    saved_content: str | None = None
    if live_path.exists():
        try:
            saved_content = live_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "failed to snapshot %s for archive (will skip archive step): %s",
                live_path, exc,
            )

    # Step 2: durable DB transaction.
    cur = conn.execute(
        f"SELECT synthesized_at FROM {table} "  # noqa: S608  static identifiers
        f"WHERE pack = ? AND {key_column} = ? "
        f"  AND superseded_by_synthesized_at = '' "
        f"ORDER BY synthesized_at DESC LIMIT 1",
        (pack, key_value),
    )
    row = cur.fetchone()
    prior_at: str | None = None
    try:
        if row is not None and row[0] != new_synthesized_at:
            # Microsecond timestamp resolution makes same-ts collision
            # impossible in production; the equality branch above only
            # exists for defensive tests where two synthesize calls
            # land on the exact same microsecond.  In that case we
            # skip the supersede update; the INSERT below fails on
            # the PK and the transaction rolls back cleanly.
            prior_at = row[0]
            conn.execute(
                f"UPDATE {table} "  # noqa: S608  static identifiers
                f"SET superseded_by_synthesized_at = ? "
                f"WHERE pack = ? AND {key_column} = ? AND synthesized_at = ?",
                (new_synthesized_at, pack, key_value, prior_at),
            )
        conn.execute(insert_sql, insert_params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # Step 3: atomic-replace the live file.
    live_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = live_path.with_name(live_path.name + ".tmp")
    tmp_path.write_text(new_markdown, encoding="utf-8")
    os.replace(tmp_path, live_path)

    # Step 4: archive the saved prior content (best-effort).
    if prior_at is not None and saved_content is not None:
        try:
            archive_subdir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_subdir / _safe_archive_filename(prior_at)
            archive_path.write_text(saved_content, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "failed to archive prior content to %s: %s — DB has body_md, "
                "archive is recoverable",
                archive_subdir, exc,
            )
    return prior_at
