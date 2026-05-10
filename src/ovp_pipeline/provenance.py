"""Provenance spine helpers (BL-055 / BL-056).

The ``provenance`` table is the durable audit trail for "where did
this object come from?" — it answers ``derived_via_stage`` +
``parent_object_id`` for every Canonical-State row that the system
produces.  Schema lives in :mod:`truth_store`; this module is the
canonical write path.

Layered fits with the rest of OVP's six-term architecture:

* The table is **Canonical State** (every row is durable, replayable).
* The rebuild's ``stage='ingest'`` rows are a **Projection** of the
  evergreen frontmatter ``source_url`` — they can be wiped + replayed.
* Stage emits (``stage='promote'`` / ``synthesize_*``) are also
  Canonical: they record an event that happened in time, with
  metadata about which run produced the row.

Insert is idempotent on the PK
``(pack, object_id, derived_via_stage, derived_at)`` so re-running
the same stage at the same wall-clock instant is safe.  A repeat
emit at a different timestamp creates a new audit row by design —
the sequence captures stage-touch history.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_provenance(
    conn: sqlite3.Connection,
    *,
    pack: str,
    object_id: str,
    derived_via_stage: str,
    source_url: str = "",
    source_fingerprint: str = "",
    parent_object_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    derived_at: str | None = None,
) -> None:
    """Write one provenance row.  Best-effort: if the table is
    missing or the schema is outdated, the call logs a warning and
    returns — provenance writes must never abort the calling stage's
    primary commit.

    ``derived_via_stage`` is free-form (so new stages don't need a
    schema bump) but should be one of:

    * ``ingest``        — rebuild populated from frontmatter
    * ``extract``       — LLM extracted a candidate concept
    * ``promote``       — candidate became canonical evergreen
    * ``synthesize_community_crystal``
    * ``synthesize_contradiction_crystal``
    * ``backfill``      — one-shot historical attribution

    Idempotent on PK: ``ON CONFLICT DO NOTHING``.
    """
    if not pack or not object_id or not derived_via_stage:
        return
    ts = derived_at or _utc_now_iso()
    # gemini PR #153 review fix: ``json.dumps`` was outside the try
    # block, so a non-serialisable metadata value would raise
    # ``TypeError`` and abort the caller's transaction — violating
    # the "never abort" contract documented above.  Move both the
    # serialisation and the INSERT inside one try, with a broader
    # final ``Exception`` handler so any unexpected failure (DB
    # corruption, encoding, etc.) is logged + swallowed too.
    try:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        conn.execute(
            """
            INSERT OR IGNORE INTO provenance
              (pack, object_id, source_url, source_fingerprint,
               derived_via_stage, derived_at, parent_object_id,
               metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack,
                object_id,
                source_url or "",
                source_fingerprint or "",
                derived_via_stage,
                ts,
                parent_object_id,
                metadata_json,
            ),
        )
    except sqlite3.OperationalError as exc:
        # Schema not present (fresh vault before first
        # ``ovp-knowledge-index`` run, or DB from before BL-055).
        # Skip silently — doctor will surface the gap.
        logger.warning(
            "provenance write skipped (%s): %s", derived_via_stage, exc,
        )
    except sqlite3.DatabaseError as exc:
        logger.warning(
            "provenance write failed — DB error (%s): %s",
            derived_via_stage, exc,
        )
    except (TypeError, ValueError) as exc:
        # Non-serialisable metadata or similar.  Best-effort
        # contract: log + swallow.
        logger.warning(
            "provenance write failed — bad metadata (%s): %s",
            derived_via_stage, exc,
        )
    except Exception as exc:  # noqa: BLE001 — never abort caller
        logger.warning(
            "provenance write failed — unexpected (%s): %s",
            derived_via_stage, exc,
        )


def bulk_upsert_provenance_ingest(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> None:
    """BL-060 owner: bulk-write ``stage='ingest'`` rows with the
    rebuild's stricter dedup semantics.

    Unlike :func:`upsert_provenance` (which dedups on the PK
    ``(pack, object_id, derived_via_stage, derived_at)`` so a re-emit
    at a *different* derived_at creates a new row), the rebuild's
    ingest pass dedups on the
    ``(pack, object_id, derived_via_stage='ingest', source_url)``
    tuple — re-running the rebuild at a later wall-clock should NOT
    write a fresh ingest row when the source URL hasn't changed.
    Otherwise rebuild noise would accumulate one ingest row per
    rebuild forever.

    This stricter dedup is implemented via ``WHERE NOT EXISTS`` rather
    than the ``INSERT OR IGNORE`` used by ``upsert_provenance``.

    Each ``rows`` dict carries:

    * ``pack`` (str)
    * ``object_id`` (str)
    * ``source_url`` (str, non-empty — caller filters out empty URLs)
    * ``source_fingerprint`` (str, 12-char SHA-256 prefix of source_url)
    * ``derived_at`` (ISO timestamp)
    * ``metadata_json`` (str, default ``"{}"``) — caller passes a
      pre-serialised JSON string so this helper stays cheap

    Used by:

    * ``rebuild_knowledge_index`` — the per-pack flush of ingest rows
    * ``ovp-backfill-objects-source-url --write-provenance`` — the
      backfill CLI's audit row when it fills a previously-empty
      ``source_url``

    Best-effort: same exception-swallowing contract as
    :func:`upsert_provenance` so a provenance write never aborts the
    rebuild's transaction.
    """
    if not rows:
        return
    try:
        # Stream the row→tuple expansion through a generator so the
        # rebuild's per-pack flush doesn't materialise both ``rows``
        # (caller's list) and ``params`` (intermediate list of 9-tuples)
        # at the same time.  ``executemany`` consumes the iterable
        # lazily.
        conn.executemany(
            """
            INSERT INTO provenance
              (pack, object_id, source_url, source_fingerprint,
               derived_via_stage, derived_at, parent_object_id,
               metadata_json)
            SELECT ?, ?, ?, ?, 'ingest', ?, NULL, ?
             WHERE NOT EXISTS (
               SELECT 1 FROM provenance
                WHERE pack = ?
                  AND object_id = ?
                  AND derived_via_stage = 'ingest'
                  AND source_url = ?
             )
            """,
            (
                (
                    row["pack"],
                    row["object_id"],
                    row["source_url"],
                    row["source_fingerprint"],
                    row["derived_at"],
                    row.get("metadata_json", "{}"),
                    row["pack"],
                    row["object_id"],
                    row["source_url"],
                )
                for row in rows
            ),
        )
    except sqlite3.OperationalError as exc:
        logger.warning("bulk provenance ingest skipped: %s", exc)
    except sqlite3.DatabaseError as exc:
        logger.warning("bulk provenance ingest failed — DB error: %s", exc)
    except Exception as exc:  # noqa: BLE001 — never abort caller
        logger.warning("bulk provenance ingest failed — unexpected: %s", exc)
