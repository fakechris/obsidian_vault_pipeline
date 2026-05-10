"""BL-060 owner module for the ``relations`` canonical table.

Pre-refactor (≤ PR #191) three call sites wrote raw ``INSERT INTO
relations`` SQL with overlapping schemas:

  * ``knowledge_index.rebuild_knowledge_index`` — full 15-column row
    with quote-span fields populated by the LLM extractor.
  * ``relation_promotion._ensure_relation_row`` — 11-column row
    (no quote spans; promotion path doesn't have them) for newly-
    promoted relation candidates.
  * ``relation_promotion.replay_relation_promotions`` — same 11-column
    shape as ``_ensure_relation_row``, replays the
    ``relation_promoted`` audit log after rebuild clears the table.

This module owns all three write paths via ``bulk_insert_relations``
and ``upsert_relation_for_promotion``.  Direct ``INSERT INTO relations``
elsewhere is a violation enforced by
``tests/test_architecture_fitness.py::test_canonical_writes_have_single_owner``.

See ``docs/canonical-write-ownership.md``.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Sequence


# Full schema — what the rebuild inserts.  Quote-span columns
# (``quote_start_line``..``quote_end_char``) are present on the LLM
# extractor's output but absent from the promotion path.
RELATION_ROW_COLUMNS_FULL: tuple[str, ...] = (
    "pack",
    "source_object_id",
    "target_object_id",
    "relation_type",
    "evidence_source_slug",
    "quote_text",
    "locator",
    "content_hash",
    "retrieval_context",
    "quote_start_line",
    "quote_end_line",
    "quote_start_char",
    "quote_end_char",
    "status",
    "verified_at",
)

# Subset schema — what the promotion path supplies.  The 4 quote-span
# columns are filled with NULL/0 defaults via the column-list-omission
# trick in the SQL below.
RELATION_ROW_COLUMNS_PROMOTION: tuple[str, ...] = (
    "pack",
    "source_object_id",
    "target_object_id",
    "relation_type",
    "evidence_source_slug",
    "quote_text",
    "locator",
    "content_hash",
    "retrieval_context",
    "status",
    "verified_at",
)


def bulk_insert_relations(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[Any]],
) -> None:
    """Bulk-insert full-schema relation rows (15 columns).

    Used by the rebuild path.  Caller is responsible for clearing the
    pack-scoped existing rows before calling — the projection rebuild
    handles that elsewhere; this function only inserts.  ``rows`` is
    consumed lazily so the rebuild can stream a generator instead of
    materialising the full pack-scoped relation set.
    """
    conn.executemany(
        f"""
        INSERT INTO relations ({', '.join(RELATION_ROW_COLUMNS_FULL)})
        VALUES ({', '.join(['?'] * len(RELATION_ROW_COLUMNS_FULL))})
        """,
        rows,
    )


def upsert_relation_for_promotion(
    conn: sqlite3.Connection,
    *,
    pack: str,
    source_object_id: str,
    target_object_id: str,
    relation_type: str,
    evidence_source_slug: str,
    quote_text: str,
    locator: str,
    content_hash: str,
    retrieval_context: str,
    status: str,
    verified_at: str,
) -> None:
    """Insert a single relation row from the promotion / replay path.

    Only the 11 columns the promotion has access to are written;
    ``quote_start_line`` / ``quote_end_line`` / ``quote_start_char`` /
    ``quote_end_char`` default to whatever the schema declares (NULL
    or 0 today).

    ``_effective_relation_type`` and ``_edge_id`` semantics live in
    the caller (``relation_promotion``); this module is intentionally
    agnostic — it just writes the row it's handed.
    """
    conn.execute(
        f"""
        INSERT INTO relations ({', '.join(RELATION_ROW_COLUMNS_PROMOTION)})
        VALUES ({', '.join(['?'] * len(RELATION_ROW_COLUMNS_PROMOTION))})
        """,
        (
            pack,
            source_object_id,
            target_object_id,
            relation_type,
            evidence_source_slug,
            quote_text,
            locator,
            content_hash,
            retrieval_context,
            status,
            verified_at,
        ),
    )
