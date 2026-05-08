"""ovp-backfill-objects-source-url — populate ``objects.source_url``
in the truth store for legacy evergreens that still carry an empty
column.

Why this exists
---------------

BL-054 added ``objects.source_url`` and wired the rebuild to write
into it from frontmatter.  ``ovp-backfill-provenance`` is the
source-of-truth backfill: it walks ``audit_events.evergreen_auto_promoted``
rows, locates each evergreen's source article on disk, and writes
``source_url:`` into the *evergreen's* frontmatter.  A subsequent
``ovp-knowledge-index`` rebuild then propagates that into
``objects.source_url``.

That two-step path leaves a column-only gap whenever:

- Frontmatter was written but a rebuild hasn't run yet
- The audit-event-walk strategy missed an evergreen (no
  ``evergreen_auto_promoted`` row for it; the row was lost in a
  DB restore; the source filename can't be located on disk
  anymore)
- A pre-rebuild snapshot of the DB is in use

Live-vault snapshot at the time this CLI was written: 9,461
objects total, 2,883 (~30%) with empty ``source_url``.  The
column-only gap blocks BL-054's ``source_diversity_norm`` and
the post-BL-029 ``/object`` Source chain card from rendering
useful URL info.

What it does
------------

For every ``objects`` row with empty ``source_url``, try these
strategies in order until one resolves a non-empty URL:

1. **Frontmatter** — read the evergreen markdown at
   ``canonical_path``; pull ``source_url:`` (or the legacy
   ``source:`` alias) directly from its YAML frontmatter.
2. **Provenance** — look up any prior ``provenance`` row for
   the same ``(pack, object_id)`` whose ``source_url`` is
   non-empty.  This catches evergreens that were promoted via a
   stage hook (BL-056) which wrote provenance but didn't update
   the denormalised column.
3. **Audit-event walk** — same path
   ``ovp-backfill-provenance`` uses: find the
   ``evergreen_auto_promoted`` row whose ``concept`` matches
   this object_id; resolve its ``source`` filename to a real
   file in the vault; read that file's frontmatter ``source:``
   URL.

Each successful resolution writes ``UPDATE objects SET
source_url = ?`` for that ``(pack, object_id)``.  When
``--write-provenance`` is set, also upsert a fresh
``stage='ingest'`` row in the ``provenance`` table so future
queries are consistent.

Idempotent.  Re-running with ``--dry-run`` after a real run
should report zero writes.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

from ..runtime import (
    VaultLayout,
    format_utc_timestamp,
    read_markdown_frontmatter,
    resolve_vault_dir,
    utc_now,
)
from .backfill_provenance import _build_evergreen_to_source_from_audit

logger = logging.getLogger(__name__)


# Reverse priority of ``backfill_provenance._URL_FIELD_PRIORITY``:
# we read from the *evergreen* (which carries ``source_url`` as its
# canonical field) rather than from the *source* article (where
# ``source:`` is the canonical field).  Other variants (``url``,
# ``github``, ``twitter``, ``arxiv``) are tail-tier fallbacks for
# legacy / hand-edited evergreens that copied a URL into a custom
# field.
_FRONTMATTER_URL_KEYS = (
    "source_url",
    "source",
    "url",
    "github",
    "twitter",
    "arxiv",
)


def _frontmatter_source_url(canonical_path: str) -> str:
    """Read evergreen frontmatter and return the first non-empty URL.

    Returns ``""`` when the file is missing, empty, or has no URL
    field.  Never raises on read errors — backfill is best-effort.

    Catches ``Exception`` deliberately: ``read_markdown_frontmatter``
    runs strict ``yaml.safe_load`` which raises ``YAMLError`` (not a
    subclass of ``ValueError``) on real-vault frontmatter that
    pre-BL-058a tools wrote without quoting (e.g. ``source_anchor:
    @nekocode/agent`` — a leading ``@`` is invalid YAML).  A
    backfill CLI must not abort the whole pass on one malformed
    file; it should skip and report.
    """
    if not canonical_path:
        return ""
    path = Path(canonical_path)
    if not path.is_file():
        return ""
    try:
        fm = read_markdown_frontmatter(path)
    except Exception:  # noqa: BLE001 — see docstring
        return ""
    for key in _FRONTMATTER_URL_KEYS:
        candidate = str(fm.get(key, "") or "").strip()
        if candidate:
            return candidate
    return ""


def _provenance_source_url(
    conn: sqlite3.Connection, pack: str, object_id: str,
) -> str:
    """Look up any prior ``provenance`` row for this object that
    already has a ``source_url`` recorded.  Picks the most recent
    by ``derived_at``."""
    row = conn.execute(
        """
        SELECT source_url
        FROM provenance
        WHERE pack = ? AND object_id = ? AND source_url != ''
        ORDER BY derived_at DESC
        LIMIT 1
        """,
        (pack, object_id),
    ).fetchone()
    return str(row[0]) if row else ""


def _iter_objects_with_empty_source_url(
    conn: sqlite3.Connection,
) -> Iterable[tuple[str, str, str]]:
    """Yield ``(pack, object_id, canonical_path)`` for every row that
    needs a backfill."""
    yield from conn.execute(
        """
        SELECT pack, object_id, canonical_path
        FROM objects
        WHERE source_url = '' OR source_url IS NULL
        ORDER BY pack, object_id
        """,
    )


def _resolve_source_url(
    *,
    pack: str,
    object_id: str,
    canonical_path: str,
    conn: sqlite3.Connection,
    audit_index: dict[str, dict[str, str]],
) -> tuple[str, str]:
    """Run the three resolution strategies in order.

    Returns ``(source_url, strategy_label)``.  ``source_url`` is
    ``""`` when nothing resolved.  ``strategy_label`` is one of
    ``"frontmatter"``, ``"provenance"``, ``"audit"``, or ``""``.
    """
    fm_url = _frontmatter_source_url(canonical_path)
    if fm_url:
        return fm_url, "frontmatter"
    prov_url = _provenance_source_url(conn, pack, object_id)
    if prov_url:
        return prov_url, "provenance"
    audit_info = audit_index.get(object_id)
    if audit_info and audit_info.get("source_url"):
        return audit_info["source_url"], "audit"
    return "", ""


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Backfill ``objects.source_url`` for legacy evergreens "
            "with empty values.  Tries frontmatter → provenance → "
            "audit_events in order; writes the SQL column directly."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written but make no changes.",
    )
    parser.add_argument(
        "--write-provenance",
        action="store_true",
        help=(
            "Also write a fresh ``stage='ingest'`` row into the "
            "provenance table for every backfilled object so future "
            "lookups don't fall through to strategy 3 again."
        ),
    )
    args = parser.parse_args(argv)
    vault = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault)
    db_path = layout.knowledge_db
    if not db_path.exists():
        print(f"no knowledge.db at {db_path}", file=sys.stderr)
        return 1

    counts = {
        "scanned": 0,
        "frontmatter": 0,
        "provenance": 0,
        "audit": 0,
        "unresolved": 0,
        "writes": 0,
    }

    conn = sqlite3.connect(db_path)
    try:
        # Short-circuit before the expensive audit-event walk.  The
        # audit-event index requires an ``rglob('*.md')`` scan of the
        # whole vault (3,877 unique stems on the live vault); skipping
        # it on idempotent re-runs is the difference between a
        # sub-second exit and a 5+ second one.
        rows = list(_iter_objects_with_empty_source_url(conn))
        counts["scanned"] = len(rows)
        if not rows:
            logger.info("no objects with empty source_url; nothing to do")
            audit_index: dict[str, dict[str, str]] = {}
        else:
            audit_index = _build_evergreen_to_source_from_audit(db_path, vault)
            logger.info(
                "loaded %d audit_events evergreen → source mappings",
                len(audit_index),
            )
        for pack, object_id, canonical_path in rows:
            source_url, strategy = _resolve_source_url(
                pack=pack,
                object_id=object_id,
                canonical_path=canonical_path,
                conn=conn,
                audit_index=audit_index,
            )
            if not source_url:
                counts["unresolved"] += 1
                continue
            counts[strategy] += 1
            if args.dry_run:
                continue
            conn.execute(
                "UPDATE objects SET source_url = ? WHERE pack = ? AND object_id = ?",
                (source_url, pack, object_id),
            )
            counts["writes"] += 1
            if args.write_provenance:
                # Idempotent — same dedup guard rebuild_knowledge_index
                # uses; never inserts a duplicate ingest row for the same
                # ``(pack, object_id, source_url)`` triple.
                from .backfill_provenance import _make_fingerprint

                derived_at = format_utc_timestamp(utc_now())
                conn.execute(
                    """
                    INSERT INTO provenance
                      (pack, object_id, source_url, source_fingerprint,
                       derived_via_stage, derived_at, parent_object_id,
                       metadata_json)
                    SELECT ?, ?, ?, ?, 'ingest', ?, NULL,
                           '{"via":"ovp-backfill-objects-source-url"}'
                    WHERE NOT EXISTS (
                      SELECT 1 FROM provenance
                       WHERE pack = ?
                         AND object_id = ?
                         AND derived_via_stage = 'ingest'
                         AND source_url = ?
                    )
                    """,
                    (
                        pack, object_id, source_url,
                        _make_fingerprint(source_url),
                        derived_at,
                        pack, object_id, source_url,
                    ),
                )
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    label = "dry-run" if args.dry_run else "applied"
    print(f"=== Backfill summary ({label}) ===")
    print(f"  empty source_url scanned:        {counts['scanned']}")
    print(f"  resolved from frontmatter:       {counts['frontmatter']}")
    print(f"  resolved from provenance:        {counts['provenance']}")
    print(f"  resolved from audit_events:      {counts['audit']}")
    print(f"  unresolved (left empty):         {counts['unresolved']}")
    print(f"  rows written to objects:         {counts['writes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
