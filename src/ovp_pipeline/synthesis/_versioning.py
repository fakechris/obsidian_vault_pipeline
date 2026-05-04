"""Versioning helpers shared by community + contradiction crystals
(BL-044, M13).

The crystal tables are append-only by primary-key construction
(``synthesized_at`` is part of the PK), but two pieces of bookkeeping
have to fire whenever a new version lands:

1. The previously-current row's ``superseded_by_synthesized_at``
   must be set so chain readers can navigate the version graph
   without scanning the whole table.

2. The old markdown file at the live path
   (``40-Resources/Crystals/<id>.md``) must move to
   ``70-Archive/Crystals/<safe-id>/<timestamp>.md`` so the live
   directory only ever contains the latest snapshot.

This module wraps both steps so the two synthesis modules stay
focused on prompt + LLM call logic.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


# Where superseded crystal markdowns land (relative to vault root).
# Inside, one subdirectory per crystal id (community sha or
# ``contradiction-<sha>``), each holding the historical snapshots.
ARCHIVE_DIR_REL: Path = Path("70-Archive") / "Crystals"


def _safe_archive_filename(synthesized_at: str) -> str:
    """ISO timestamps contain ``:`` which is unportable on Windows /
    surfaced strangely by macOS Finder.  Replace with ``-`` for the
    archive filename — the canonical timestamp survives in the
    file's frontmatter and the ``synthesized_at`` DB column.
    """
    return synthesized_at.replace(":", "-") + ".md"


def supersede_and_archive_previous(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    pack: str,
    key_value: str,
    new_synthesized_at: str,
    live_path: Path,
    archive_subdir: Path,
) -> str | None:
    """Mark the prior current version as superseded and archive its
    markdown.  Returns the prior ``synthesized_at`` or ``None`` if
    this is the first version.

    The DB update happens unconditionally; the file move is
    best-effort because the live markdown could be missing for
    legitimate reasons (a prior dry-run, an operator deletion, a
    fresh DB pointing at a vault that was never written to).  In
    those cases we log + skip the move and leave the DB pointer
    intact — version chain integrity matters more than file
    accounting.
    """
    cur = conn.execute(
        f"SELECT synthesized_at FROM {table} "  # noqa: S608  (table+col are static)
        f"WHERE pack = ? AND {key_column} = ? "
        f"  AND superseded_by_synthesized_at = '' "
        f"ORDER BY synthesized_at DESC LIMIT 1",
        (pack, key_value),
    )
    row = cur.fetchone()
    if row is None:
        return None
    prior = row[0]
    if prior == new_synthesized_at:
        # Same-timestamp re-synthesis (only possible at sub-second
        # granularity in tests).  The PK on (pack, key, synth_at)
        # would reject the new INSERT anyway; leave the prior row
        # alone rather than self-superseding.
        return prior
    conn.execute(
        f"UPDATE {table} "  # noqa: S608  (table is static)
        f"SET superseded_by_synthesized_at = ? "
        f"WHERE pack = ? AND {key_column} = ? AND synthesized_at = ?",
        (new_synthesized_at, pack, key_value, prior),
    )
    if live_path.exists():
        try:
            archive_subdir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_subdir / _safe_archive_filename(prior)
            live_path.rename(archive_path)
        except OSError as exc:
            logger.warning(
                "failed to archive %s → %s: %s — leaving live file in place",
                live_path, archive_subdir, exc,
            )
    else:
        logger.info(
            "no live markdown at %s to archive (DB pointer still updated)",
            live_path,
        )
    return prior
