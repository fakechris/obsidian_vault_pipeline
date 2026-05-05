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
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    try:
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
        # Anything unexpected — log + swallow so the calling stage's
        # primary commit isn't aborted.
        logger.warning(
            "provenance write failed (%s): %s", derived_via_stage, exc,
        )
