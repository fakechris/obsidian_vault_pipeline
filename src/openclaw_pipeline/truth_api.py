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


def _vault_relative_path(vault_dir: Path | str, path: str) -> str:
    resolved = resolve_vault_dir(vault_dir).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        return path
    try:
        return str(candidate.resolve().relative_to(resolved))
    except ValueError:
        return path


def _validate_page_args(*, limit: int, offset: int = 0) -> tuple[int, int]:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be >= 0")
    if limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be <= {MAX_PAGE_SIZE}")
    return limit, offset


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_moc_row(note_type: str, path: str) -> bool:
    return note_type == "moc" or "/10-Knowledge/Atlas/" in path or Path(path).name.startswith("MOC")


def _batch_object_rows(vault_dir: Path | str, object_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not object_ids:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    placeholders = ",".join("?" for _ in object_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id IN ({placeholders})
            ORDER BY object_id
            """,
            tuple(object_ids),
        ).fetchall()
    return {
        row[0]: {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
        }
        for row in rows
    }


def get_object_provenance_map(vault_dir: Path | str, object_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not object_ids:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    ordered_object_ids = list(dict.fromkeys(object_ids))
    object_rows = _batch_object_rows(vault_dir, ordered_object_ids)
    placeholders = ",".join("?" for _ in ordered_object_ids)
    with sqlite3.connect(db_path) as conn:
        mention_rows = conn.execute(
            f"""
            SELECT page_links.target_slug, pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            WHERE page_links.target_slug IN ({placeholders})
              AND pages_index.slug != page_links.target_slug
            ORDER BY page_links.target_slug, pages_index.slug
            """,
            tuple(ordered_object_ids),
        ).fetchall()

    provenance = {
        object_id: {
            "title": object_rows.get(object_id, {}).get("title", object_id),
            "evergreen_path": object_rows.get(object_id, {}).get("canonical_path", ""),
            "source_notes": [],
            "mocs": [],
        }
        for object_id in ordered_object_ids
    }
    for target_slug, slug, title, note_type, path in mention_rows:
        item = {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": _vault_relative_path(resolved_vault, path),
        }
        if _is_moc_row(note_type, path):
            provenance[target_slug]["mocs"].append(item)
        elif note_type != "evergreen":
            provenance[target_slug]["source_notes"].append(item)
    return provenance


def list_objects(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
) -> list[dict[str, Any]]:
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    with sqlite3.connect(db_path) as conn:
        sql = """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
        """
        params: list[Any] = []
        if normalized_query:
            sql += """
                WHERE lower(object_id) LIKE ? ESCAPE '\\'
                   OR lower(title) LIKE ? ESCAPE '\\'
                   OR lower(source_slug) LIKE ? ESCAPE '\\'
            """
            params.extend(
                [
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                ]
            )
        sql += " ORDER BY object_id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, tuple(params)).fetchall()

    return [
        {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
        }
        for row in rows
    ]


def count_objects(vault_dir: Path | str, *, query: str | None = None) -> int:
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    sql = "SELECT COUNT(*) FROM objects"
    params: list[Any] = []
    if normalized_query:
        sql += """
            WHERE lower(object_id) LIKE ? ESCAPE '\\'
               OR lower(title) LIKE ? ESCAPE '\\'
               OR lower(source_slug) LIKE ? ESCAPE '\\'
        """
        params.extend(
            [
                f"%{normalized_query}%",
                f"%{normalized_query}%",
                f"%{normalized_query}%",
            ]
        )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0


def _surface_page_query_clauses(*, note_type: str, normalized_query: str) -> tuple[str, list[Any]]:
    where = ["pages_index.note_type = ?"]
    params: list[Any] = [note_type]
    if normalized_query:
        where.append(
            """
            (
              lower(pages_index.slug) LIKE ? ESCAPE '\\'
              OR lower(pages_index.title) LIKE ? ESCAPE '\\'
              OR lower(objects.object_id) LIKE ? ESCAPE '\\'
              OR lower(objects.title) LIKE ? ESCAPE '\\'
            )
            """.strip()
        )
        params.extend([f"%{normalized_query}%"] * 4)
    return " AND ".join(where), params


def _list_surface_groups(
    vault_dir: Path | str,
    *,
    note_type: str,
    query: str | None,
    limit: int,
    object_list_key: str,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    where_sql, base_params = _surface_page_query_clauses(
        note_type=note_type,
        normalized_query=normalized_query,
    )

    with sqlite3.connect(db_path) as conn:
        selected_rows = conn.execute(
            f"""
            SELECT DISTINCT pages_index.slug
            FROM pages_index
            JOIN page_links ON page_links.source_slug = pages_index.slug
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE {where_sql}
            ORDER BY pages_index.slug
            LIMIT ?
            """,
            tuple([*base_params, limit]),
        ).fetchall()
        selected_slugs = [row[0] for row in selected_rows]
        if not selected_slugs:
            return []
        placeholders = ",".join("?" for _ in selected_slugs)
        rows = conn.execute(
            f"""
            SELECT pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path, objects.object_id, objects.title
            FROM pages_index
            JOIN page_links ON page_links.source_slug = pages_index.slug
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE pages_index.slug IN ({placeholders})
            ORDER BY pages_index.slug, objects.object_id
            """,
            tuple(selected_slugs),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for slug, title, row_note_type, path, object_id, object_title in rows:
        item = grouped.setdefault(
            slug,
            {
                "slug": slug,
                "title": title,
                "note_type": row_note_type,
                "path": _vault_relative_path(resolved_vault, path),
                object_list_key: [],
            },
        )
        item[object_list_key].append({"object_id": object_id, "title": object_title})
    return list(grouped.values())


def get_object_detail(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    escaped = _escape_like(object_id)

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
        mention_rows = conn.execute(
            """
            SELECT DISTINCT pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            WHERE page_links.target_slug = ?
              AND pages_index.slug != ?
            ORDER BY pages_index.slug
            """,
            (object_id, object_id),
        ).fetchall()

    mocs: list[dict[str, Any]] = []
    source_notes: list[dict[str, Any]] = []
    for slug, title, note_type, path in mention_rows:
        item = {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": _vault_relative_path(resolved_vault, path),
        }
        if _is_moc_row(note_type, path):
            mocs.append(item)
            continue
        if slug == object_id:
            continue
        if note_type != "evergreen":
            source_notes.append(item)

    return {
        "object": {
            "object_id": object_row[0],
            "object_kind": object_row[1],
            "title": object_row[2],
            "canonical_path": _vault_relative_path(resolved_vault, object_row[3]),
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
        "provenance": {
            "evergreen_path": _vault_relative_path(resolved_vault, object_row[3]),
            "source_notes": source_notes,
            "mocs": mocs,
        },
    }


def list_contradictions(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    query = """
        SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
        FROM contradictions
    """
    params: list[Any] = []
    where_clauses: list[str] = []
    if status:
        if status == "resolved":
            where_clauses.append("status != ?")
            params.append("open")
        else:
            where_clauses.append("status = ?")
            params.append(status)
    if normalized_query:
        where_clauses.append("lower(subject_key) LIKE ? ESCAPE '\\'")
        params.append(f"%{normalized_query}%")
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
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
    resolved_vault = resolve_vault_dir(vault_dir)
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
            "canonical_path": _vault_relative_path(resolved_vault, center[3]),
            "source_slug": center[4],
        },
        "neighbors": [
            {
                "object_id": row[0],
                "object_kind": row[1],
                "title": row[2],
                "canonical_path": _vault_relative_path(resolved_vault, row[3]),
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


def list_atlas_memberships(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items = _list_surface_groups(
        vault_dir,
        note_type="moc",
        query=query,
        limit=limit,
        object_list_key="members",
    )
    return [
        {
            "slug": item["slug"],
            "title": item["title"],
            "path": item["path"],
            "members": item["members"],
        }
        for item in items
    ]


def list_deep_dive_derivations(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items = _list_surface_groups(
        vault_dir,
        note_type="deep_dive",
        query=query,
        limit=limit,
        object_list_key="derived_objects",
    )
    return [
        {
            "slug": item["slug"],
            "title": item["title"],
            "note_type": item["note_type"],
            "path": item["path"],
            "derived_objects": item["derived_objects"],
        }
        for item in items
    ]
