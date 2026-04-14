from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .runtime import VaultLayout, resolve_vault_dir

MAX_PAGE_SIZE = 500


def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db


def _validate_page_args(*, limit: int, offset: int = 0) -> tuple[int, int]:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be >= 0")
    if limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be <= {MAX_PAGE_SIZE}")
    return limit, offset


def list_objects(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
) -> list[dict[str, Any]]:
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    db_path = _db_path(vault_dir)
    normalized_query = query.strip().lower() if query else ""
    with sqlite3.connect(db_path) as conn:
        if normalized_query:
            rows = conn.execute(
                """
                SELECT object_id, object_kind, title, canonical_path, source_slug
                FROM objects
                WHERE lower(object_id) LIKE ? OR lower(title) LIKE ? OR lower(source_slug) LIKE ?
                ORDER BY object_id
                LIMIT ? OFFSET ?
                """,
                (
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    limit,
                    offset,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT object_id, object_kind, title, canonical_path, source_slug
                FROM objects
                ORDER BY object_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

    return [
        {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": row[3],
            "source_slug": row[4],
        }
        for row in rows
    ]


def get_object_detail(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    escaped = object_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    with sqlite3.connect(db_path) as conn:
        object_row = conn.execute(
            """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        if object_row is None:
            raise ValueError(f"Unknown object_id: {object_id}")

        summary_row = conn.execute(
            """
            SELECT object_id, summary_text, source_slug
            FROM compiled_summaries
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        claim_rows = conn.execute(
            """
            SELECT claim_id, claim_kind, claim_text, confidence
            FROM claims
            WHERE object_id = ?
            ORDER BY claim_id
            """,
            (object_id,),
        ).fetchall()
        evidence_rows = conn.execute(
            """
            SELECT claim_id, source_slug, evidence_kind, quote_text
            FROM claim_evidence
            WHERE claim_id IN (
                SELECT claim_id FROM claims WHERE object_id = ?
            )
            ORDER BY claim_id, evidence_kind
            """,
            (object_id,),
        ).fetchall()
        relation_rows = conn.execute(
            """
            SELECT source_object_id, target_object_id, relation_type, evidence_source_slug
            FROM relations
            WHERE source_object_id = ?
            ORDER BY target_object_id
            """,
            (object_id,),
        ).fetchall()
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
            FROM contradictions
            WHERE positive_claim_ids_json LIKE ? ESCAPE '\\' OR negative_claim_ids_json LIKE ? ESCAPE '\\'
            ORDER BY subject_key
            """,
            (f'%"{escaped}::%', f'%"{escaped}::%'),
        ).fetchall()

    return {
        "object": {
            "object_id": object_row[0],
            "object_kind": object_row[1],
            "title": object_row[2],
            "canonical_path": object_row[3],
            "source_slug": object_row[4],
        },
        "summary": (
            {
                "object_id": summary_row[0],
                "summary_text": summary_row[1],
                "source_slug": summary_row[2],
            }
            if summary_row
            else None
        ),
        "claims": [
            {
                "claim_id": row[0],
                "claim_kind": row[1],
                "claim_text": row[2],
                "confidence": row[3],
            }
            for row in claim_rows
        ],
        "evidence": [
            {
                "claim_id": row[0],
                "source_slug": row[1],
                "evidence_kind": row[2],
                "quote_text": row[3],
            }
            for row in evidence_rows
        ],
        "relations": [
            {
                "source_object_id": row[0],
                "target_object_id": row[1],
                "relation_type": row[2],
                "evidence_source_slug": row[3],
            }
            for row in relation_rows
        ],
        "contradictions": [
            {
                "contradiction_id": row[0],
                "subject_key": row[1],
                "positive_claim_ids": json.loads(row[2]),
                "negative_claim_ids": json.loads(row[3]),
                "status": row[4],
                "resolution_note": row[5] or "",
                "resolved_at": row[6] or "",
            }
            for row in contradiction_rows
        ],
    }


def list_contradictions(vault_dir: Path | str, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    query = """
        SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
        FROM contradictions
    """
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY subject_key LIMIT ?"
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    return [
        {
            "contradiction_id": row[0],
            "subject_key": row[1],
            "positive_claim_ids": json.loads(row[2]),
            "negative_claim_ids": json.loads(row[3]),
            "status": row[4],
            "resolution_note": row[5] or "",
            "resolved_at": row[6] or "",
        }
        for row in rows
    ]


def get_topic_neighborhood(vault_dir: Path | str, object_id: str, *, depth: int = 1) -> dict[str, Any]:
    if depth != 1:
        raise ValueError("Only depth=1 is currently supported")

    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
        center = conn.execute(
            """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        if center is None:
            raise ValueError(f"Unknown object_id: {object_id}")

        edge_rows = conn.execute(
            """
            SELECT source_object_id, target_object_id, relation_type, evidence_source_slug
            FROM relations
            WHERE source_object_id = ?
            ORDER BY target_object_id
            """,
            (object_id,),
        ).fetchall()
        neighbor_ids = [row[1] for row in edge_rows]
        if neighbor_ids:
            placeholders = ",".join("?" for _ in neighbor_ids)
            neighbor_rows = conn.execute(
                f"""
                SELECT object_id, object_kind, title, canonical_path, source_slug
                FROM objects
                WHERE object_id IN ({placeholders})
                ORDER BY object_id
                """,
                tuple(neighbor_ids),
            ).fetchall()
        else:
            neighbor_rows = []

    return {
        "center": {
            "object_id": center[0],
            "object_kind": center[1],
            "title": center[2],
            "canonical_path": center[3],
            "source_slug": center[4],
        },
        "neighbors": [
            {
                "object_id": row[0],
                "object_kind": row[1],
                "title": row[2],
                "canonical_path": row[3],
                "source_slug": row[4],
            }
            for row in neighbor_rows
        ],
        "edges": [
            {
                "source_object_id": row[0],
                "target_object_id": row[1],
                "relation_type": row[2],
                "evidence_source_slug": row[3],
            }
            for row in edge_rows
        ],
    }
