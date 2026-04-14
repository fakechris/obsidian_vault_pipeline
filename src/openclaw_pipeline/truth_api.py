from __future__ import annotations

from datetime import UTC, datetime
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from .runtime import VaultLayout, resolve_vault_dir

MAX_PAGE_SIZE = 500
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_REVIEW_AUDIT_LOG_NAME = "review-actions"
CONTRADICTION_STATUS_EXPLANATIONS = {
    "open": "Active contradiction awaiting review.",
    "resolved_keep_positive": "Reviewed and the positive claim set remains the preferred interpretation.",
    "resolved_keep_negative": "Reviewed and the negative claim set remains the preferred interpretation.",
    "dismissed": "Reviewed and dismissed as not worth keeping in the active contradiction queue.",
    "needs_human": "Requires deeper human judgment before the contradiction can be considered closed.",
}


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


def _parse_frontmatter(markdown: str) -> dict[str, Any]:
    fenced_match = _FENCED_FRONTMATTER_RE.match(markdown)
    if fenced_match:
        raw_frontmatter = fenced_match.group(1)
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except yaml.YAMLError:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}
    if not markdown.startswith("---\n"):
        return {}
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return {}
    raw_frontmatter = markdown[4:end]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _read_note_frontmatter(vault_dir: Path | str, relative_path: str) -> dict[str, Any]:
    resolved = resolve_vault_dir(vault_dir)
    note_path = (resolved / relative_path).resolve()
    try:
        note_path.relative_to(resolved.resolve())
    except ValueError:
        return {}
    if not note_path.is_file():
        return {}
    return _parse_frontmatter(note_path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def record_review_action(
    vault_dir: Path | str,
    *,
    event_type: str,
    payload: dict[str, Any],
    slug: str = "",
    session_id: str = "ovp-ui",
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = {
        "timestamp": timestamp,
        "session_id": session_id,
        "event_type": event_type,
        "slug": slug,
        **payload,
    }
    _append_jsonl(layout.logs_dir / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl", event)
    if layout.knowledge_db.exists():
        with sqlite3.connect(layout.knowledge_db) as conn:
            conn.execute(
                """
                INSERT INTO audit_events (source_log, event_type, slug, session_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _REVIEW_AUDIT_LOG_NAME,
                    event_type,
                    slug,
                    session_id,
                    timestamp,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            conn.commit()
    return event


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


def get_review_context(vault_dir: Path | str, object_ids: list[str]) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {
            "object_count": 0,
            "source_note_count": 0,
            "moc_count": 0,
            "contradiction_count": 0,
            "open_contradiction_count": 0,
            "stale_summary_count": 0,
            "latest_event_date": "",
            "source_notes": [],
            "mocs": [],
            "stale_summary_object_ids": [],
            "contradiction_object_ids": [],
            "recent_review_actions": [],
        }

    provenance_map = get_object_provenance_map(vault_dir, normalized_object_ids)
    source_notes: dict[str, dict[str, Any]] = {}
    mocs: dict[str, dict[str, Any]] = {}
    for provenance in provenance_map.values():
        for note in provenance["source_notes"]:
            source_notes.setdefault(note["slug"], note)
        for moc in provenance["mocs"]:
            mocs.setdefault(moc["slug"], moc)

    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    with sqlite3.connect(db_path) as conn:
        stale_rows = conn.execute(
            f"""
            SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
                   COALESCE(rel.outgoing_count, 0) AS outgoing_count
            FROM objects
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            LEFT JOIN (
                SELECT source_object_id, COUNT(*) AS outgoing_count
                FROM relations
                GROUP BY source_object_id
            ) AS rel ON rel.source_object_id = objects.object_id
            WHERE objects.object_id IN ({placeholders})
            ORDER BY objects.object_id
            """,
            tuple(normalized_object_ids),
        ).fetchall()
        event_row = conn.execute(
            f"""
            SELECT MAX(event_date)
            FROM timeline_events
            WHERE slug IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchone()
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, positive_claim_ids_json, negative_claim_ids_json, status
            FROM contradictions
            ORDER BY contradiction_id
            """
        ).fetchall()

    stale_summaries: list[dict[str, Any]] = []
    for object_id, title, summary_text, outgoing_count in stale_rows:
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        stale_summaries.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "object_path": f"/object?id={object_id}",
            }
        )
    stale_summary_object_ids = [item["object_id"] for item in stale_summaries]

    contradiction_ids: list[str] = []
    open_contradiction_ids: list[str] = []
    contradiction_object_ids: set[str] = set()
    object_id_set = set(normalized_object_ids)
    for contradiction_id, positive_json, negative_json, status in contradiction_rows:
        claim_ids = json.loads(positive_json) + json.loads(negative_json)
        matched_object_ids = {
            claim_id.split("::", 1)[0]
            for claim_id in claim_ids
            if claim_id.split("::", 1)[0] in object_id_set
        }
        if not matched_object_ids:
            continue
        contradiction_ids.append(str(contradiction_id))
        contradiction_object_ids.update(matched_object_ids)
        if status == "open":
            open_contradiction_ids.append(str(contradiction_id))

    return {
        "object_count": len(normalized_object_ids),
        "source_note_count": len(source_notes),
        "moc_count": len(mocs),
        "contradiction_count": len(contradiction_ids),
        "open_contradiction_count": len(open_contradiction_ids),
        "stale_summary_count": len(stale_summaries),
        "latest_event_date": str(event_row[0] or ""),
        "source_notes": list(source_notes.values()),
        "mocs": list(mocs.values()),
        "stale_summary_object_ids": stale_summary_object_ids,
        "contradiction_object_ids": sorted(contradiction_object_ids),
        "recent_review_actions": list_review_actions(vault_dir, object_ids=normalized_object_ids, limit=5),
    }


def _claim_details_map(vault_dir: Path | str, claim_ids: list[str]) -> dict[str, dict[str, Any]]:
    normalized_claim_ids = list(dict.fromkeys(claim_id for claim_id in claim_ids if claim_id))
    if not normalized_claim_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_claim_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT claims.claim_id, claims.object_id, objects.title, claims.claim_kind, claims.claim_text, claims.confidence
            FROM claims
            JOIN objects ON objects.object_id = claims.object_id
            WHERE claims.claim_id IN ({placeholders})
            ORDER BY claims.claim_id
            """,
            tuple(normalized_claim_ids),
        ).fetchall()
    return {
        row[0]: {
            "claim_id": row[0],
            "object_id": row[1],
            "object_title": row[2],
            "claim_kind": row[3],
            "claim_text": row[4],
            "confidence": row[5],
        }
        for row in rows
    }


def _claim_evidence_map(vault_dir: Path | str, claim_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    normalized_claim_ids = list(dict.fromkeys(claim_id for claim_id in claim_ids if claim_id))
    if not normalized_claim_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_claim_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT claim_id, source_slug, evidence_kind, quote_text
            FROM claim_evidence
            WHERE claim_id IN ({placeholders})
            ORDER BY claim_id, source_slug, evidence_kind
            """,
            tuple(normalized_claim_ids),
        ).fetchall()
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for claim_id, source_slug, evidence_kind, quote_text in rows:
        evidence_map.setdefault(claim_id, []).append(
            {
                "source_slug": source_slug,
                "evidence_kind": evidence_kind,
                "quote_text": quote_text or "",
            }
        )
    return evidence_map


def _rank_contradiction_evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    rank = 1
    for polarity, claims in (("positive", item["positive_claims"]), ("negative", item["negative_claims"])):
        for claim in claims:
            for evidence in claim["evidence"]:
                ranked.append(
                    {
                        "rank": rank,
                        "polarity": polarity,
                        "claim_id": claim["claim_id"],
                        "object_id": claim["object_id"],
                        "object_title": claim["object_title"],
                        "evidence_kind": evidence["evidence_kind"],
                        "quote_text": evidence["quote_text"],
                        "source_slug": evidence["source_slug"],
                    }
                )
                rank += 1
    return ranked


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


def search_vault_surface(
    vault_dir: Path | str,
    *,
    query: str,
    object_limit: int = 25,
    note_limit: int = 25,
) -> dict[str, Any]:
    normalized_query = query.strip()
    object_limit, _ = _validate_page_args(limit=object_limit, offset=0)
    note_limit, _ = _validate_page_args(limit=note_limit, offset=0)
    if not normalized_query:
        return {
            "query": "",
            "objects": [],
            "notes": [],
        }
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    escaped_query = _escape_like(normalized_query.lower())
    with sqlite3.connect(db_path) as conn:
        object_rows = conn.execute(
            """
            SELECT DISTINCT objects.object_id, objects.object_kind, objects.title, objects.canonical_path, objects.source_slug
            FROM objects
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            LEFT JOIN claims ON claims.object_id = objects.object_id
            WHERE lower(objects.object_id) LIKE ? ESCAPE '\\'
               OR lower(objects.title) LIKE ? ESCAPE '\\'
               OR lower(objects.source_slug) LIKE ? ESCAPE '\\'
               OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
               OR lower(claims.claim_text) LIKE ? ESCAPE '\\'
            ORDER BY objects.object_id
            LIMIT ?
            """,
            (
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                object_limit,
            ),
        ).fetchall()
        note_rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE lower(slug) LIKE ? ESCAPE '\\'
               OR lower(title) LIKE ? ESCAPE '\\'
               OR lower(path) LIKE ? ESCAPE '\\'
               OR lower(body) LIKE ? ESCAPE '\\'
            ORDER BY
              CASE note_type
                WHEN 'evergreen' THEN 0
                WHEN 'deep_dive' THEN 1
                WHEN 'moc' THEN 2
                ELSE 3
              END,
              slug
            LIMIT ?
            """,
            (
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                note_limit,
            ),
        ).fetchall()

    objects = [
        {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
        }
        for row in object_rows
    ]
    notes = [
        {
            "slug": row[0],
            "title": row[1],
            "note_type": row[2],
            "path": _vault_relative_path(resolved_vault, row[3]),
        }
        for row in note_rows
    ]
    return {
        "query": normalized_query,
        "objects": objects,
        "notes": notes,
    }


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


def _find_note_by_source(vault_dir: Path, *, source_url: str, exclude_path: str) -> dict[str, str] | None:
    search_roots = [
        vault_dir / "50-Inbox" / "03-Processed",
        vault_dir / "50-Inbox" / "02-Processing",
        vault_dir / "50-Inbox" / "01-Raw",
    ]
    resolved_exclude = str((vault_dir / exclude_path).resolve())
    for root in search_roots:
        if not root.exists():
            continue
        for candidate in sorted(root.rglob("*.md")):
            if str(candidate.resolve()) == resolved_exclude:
                continue
            frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
            if str(frontmatter.get("source", "")).strip() != source_url:
                continue
            title = str(frontmatter.get("title") or candidate.stem).strip()
            return {
                "title": title,
                "path": str(candidate.resolve().relative_to(vault_dir.resolve())),
            }
    return None


def _find_note_from_pipeline_log(vault_dir: Path, *, note_path: str) -> dict[str, str] | None:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    if not log_path.exists():
        return None
    article_file: str | None = None
    archived_path: str | None = None
    target_absolute = str((vault_dir / note_path).resolve())
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") == "article_processed":
            output = str(event.get("output", "")).strip()
            if output == target_absolute or output.endswith(note_path):
                article_file = str(event.get("file", "")).strip()
                continue
        if event.get("event_type") == "source_archived_to_processed":
            archived = str(event.get("archived", "")).strip()
            source = str(event.get("source", "")).strip()
            if article_file and (archived.endswith(article_file) or source.endswith(article_file)):
                archived_path = archived
    if not archived_path:
        return None
    candidate = Path(archived_path)
    if not candidate.is_absolute():
        candidate = (vault_dir / archived_path).resolve()
    if not candidate.is_file():
        return None
    frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
    return {
        "title": str(frontmatter.get("title") or candidate.stem).strip(),
        "path": str(candidate.resolve().relative_to(vault_dir.resolve())),
    }


def _find_derived_notes_from_pipeline_log(vault_dir: Path, *, note_path: str) -> list[dict[str, str]]:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    if not log_path.exists():
        return []
    target_name = Path(note_path).name
    derived: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") != "article_processed":
            continue
        file_name = str(event.get("file", "")).strip()
        if file_name != target_name:
            continue
        output = str(event.get("output", "")).strip()
        if not output:
            continue
        candidate = Path(output)
        if not candidate.is_absolute():
            candidate = (vault_dir / output).resolve()
        if not candidate.is_file():
            continue
        relative_path = str(candidate.resolve().relative_to(vault_dir.resolve()))
        if relative_path in seen_paths:
            continue
        seen_paths.add(relative_path)
        frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
        derived.append(
            {
                "title": str(frontmatter.get("title") or candidate.stem).strip(),
                "path": relative_path,
            }
        )
    return derived


def list_review_actions(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_object_ids = set(object_id for object_id in (object_ids or []) if object_id)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source_log, event_type, slug, session_id, timestamp, payload_json
            FROM audit_events
            WHERE source_log = ?
            ORDER BY timestamp DESC
            LIMIT 200
            """,
            (_REVIEW_AUDIT_LOG_NAME,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for source_log, event_type, slug, session_id, timestamp, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        action_object_ids = [
            str(value)
            for value in payload.get("object_ids", [])
            if isinstance(value, str) and value
        ]
        if normalized_object_ids and not normalized_object_ids.intersection(action_object_ids):
            continue
        items.append(
            {
                "source_log": source_log,
                "event_type": event_type,
                "slug": slug,
                "session_id": session_id,
                "timestamp": timestamp,
                "object_ids": action_object_ids,
                "contradiction_ids": [
                    str(value)
                    for value in payload.get("contradiction_ids", [])
                    if isinstance(value, str) and value
                ],
                "status": str(payload.get("status") or ""),
                "note": str(payload.get("note") or ""),
                "rebuilt_object_ids": [
                    str(value)
                    for value in payload.get("rebuilt_object_ids", [])
                    if isinstance(value, str) and value
                ],
                "objects_rebuilt": int(payload.get("objects_rebuilt") or 0),
            }
        )
        if len(items) >= limit:
            break
    return items


def get_note_provenance(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    frontmatter = _read_note_frontmatter(resolved_vault, note_path)
    source_url = str(frontmatter.get("source", "")).strip()
    original_source_note = None
    if source_url:
        original_source_note = _find_note_by_source(
            resolved_vault,
            source_url=source_url,
            exclude_path=note_path,
        )
    if original_source_note is None:
        original_source_note = _find_note_from_pipeline_log(resolved_vault, note_path=note_path)
    derived_deep_dives = _find_derived_notes_from_pipeline_log(resolved_vault, note_path=note_path)
    return {
        "note_path": note_path,
        "original_source_note": original_source_note,
        "derived_deep_dives": derived_deep_dives,
    }


def _page_row_by_path(vault_dir: Path | str, note_path: str) -> dict[str, str]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE path = ?
            LIMIT 1
            """,
            (str((resolved_vault / note_path).resolve()),),
        ).fetchone()
    if row:
        return {
            "slug": row[0],
            "title": row[1],
            "note_type": row[2],
            "path": _vault_relative_path(resolved_vault, row[3]),
        }
    return {
        "slug": Path(note_path).stem,
        "title": Path(note_path).stem,
        "note_type": "note",
        "path": note_path,
    }


def _deep_dive_objects_for_path(vault_dir: Path | str, note_path: str) -> list[dict[str, str]]:
    source_name = Path(note_path).name
    items = list_deep_dive_derivations(vault_dir, limit=MAX_PAGE_SIZE)
    for item in items:
        if Path(item["path"]).name == source_name:
            return item["derived_objects"]
    return []


def _atlas_pages_for_object_ids(vault_dir: Path | str, object_ids: list[str]) -> list[dict[str, str]]:
    atlas_pages: dict[str, dict[str, str]] = {}
    for provenance in get_object_provenance_map(vault_dir, object_ids).values():
        for item in provenance["mocs"]:
            atlas_pages.setdefault(item["slug"], item)
    return list(atlas_pages.values())


def get_note_traceability(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    note = _page_row_by_path(vault_dir, note_path)
    provenance = get_note_provenance(vault_dir, note_path=note_path)
    deep_dives: list[dict[str, str]] = []
    source_notes: list[dict[str, str]] = []

    if note["note_type"] == "deep_dive":
        deep_dives = [note]
        if provenance["original_source_note"]:
            source_notes = [provenance["original_source_note"]]
    else:
        deep_dives = provenance["derived_deep_dives"]
        if provenance["original_source_note"]:
            source_notes = [provenance["original_source_note"]]

    object_map: dict[str, dict[str, str]] = {}
    for deep_dive in deep_dives:
        for item in _deep_dive_objects_for_path(vault_dir, deep_dive["path"]):
            object_map.setdefault(item["object_id"], item)
    objects = list(object_map.values())
    atlas_pages = _atlas_pages_for_object_ids(vault_dir, [item["object_id"] for item in objects])
    return {
        "note": note,
        "source_notes": source_notes,
        "deep_dives": deep_dives,
        "objects": objects,
        "atlas_pages": atlas_pages,
        "counts": {
            "source_notes": len(source_notes),
            "deep_dives": len(deep_dives),
            "objects": len(objects),
            "atlas_pages": len(atlas_pages),
        },
    }


def get_object_traceability(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    detail = get_object_detail(vault_dir, object_id)
    deep_dives = [item for item in detail["provenance"]["source_notes"] if item["note_type"] == "deep_dive"]
    source_note_map: dict[str, dict[str, str]] = {}
    for deep_dive in deep_dives:
        original = get_note_provenance(vault_dir, note_path=deep_dive["path"])["original_source_note"]
        if original:
            source_note_map.setdefault(original["path"], original)
    return {
        "object": detail["object"],
        "evergreen_note": {
            "title": detail["object"]["title"],
            "path": detail["provenance"]["evergreen_path"],
        },
        "source_notes": list(source_note_map.values()),
        "deep_dives": deep_dives,
        "atlas_pages": detail["provenance"]["mocs"],
        "counts": {
            "source_notes": len(source_note_map),
            "deep_dives": len(deep_dives),
            "atlas_pages": len(detail["provenance"]["mocs"]),
        },
    }


def list_production_chains(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = (query or "").strip().lower()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE note_type = 'deep_dive'
            ORDER BY note_type, slug
            """
        ).fetchall()

    candidates: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for slug, title, note_type, path in rows:
        relative_path = _vault_relative_path(resolved_vault, path)
        seen_paths.add(relative_path)
        candidates.append(
            {
                "slug": str(slug),
                "title": str(title),
                "note_type": str(note_type),
                "path": relative_path,
                "stage_label": "deep_dive",
            }
        )

    processed_root = resolved_vault / "50-Inbox" / "03-Processed"
    if processed_root.exists():
        for candidate in sorted(processed_root.rglob("*.md")):
            relative_path = str(candidate.resolve().relative_to(resolved_vault.resolve()))
            if relative_path in seen_paths:
                continue
            frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
            candidates.append(
                {
                    "slug": candidate.stem,
                    "title": str(frontmatter.get("title") or candidate.stem).strip(),
                    "note_type": "note",
                    "path": relative_path,
                    "stage_label": "source_note",
                }
            )

    items: list[dict[str, Any]] = []
    for candidate in candidates:
        relative_path = candidate["path"]
        chain = get_note_traceability(vault_dir, note_path=relative_path)
        if normalized_query:
            haystacks = [
                str(candidate["title"]).lower(),
                str(candidate["slug"]).lower(),
                relative_path.lower(),
                *(item["title"].lower() for item in chain["deep_dives"]),
                *(item["title"].lower() for item in chain["objects"]),
                *(item["title"].lower() for item in chain["atlas_pages"]),
                *(item["title"].lower() for item in chain["source_notes"]),
            ]
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
        items.append(
            {
                "slug": candidate["slug"],
                "title": candidate["title"],
                "note_type": candidate["note_type"],
                "path": relative_path,
                "stage_label": candidate["stage_label"],
                "traceability": chain,
            }
        )
        if len(items) >= limit:
            break
    return items


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

    items = [
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
    claim_map = _claim_details_map(
        vault_dir,
        [
            claim_id
            for item in items
            for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
        ],
    )
    evidence_map = _claim_evidence_map(
        vault_dir,
        [
            claim_id
            for item in items
            for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
        ],
    )
    for item in items:
        object_ids = list(
            dict.fromkeys(
                claim_id.split("::", 1)[0]
                for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
            )
        )
        item["positive_claims"] = [
            {
                **claim_map[claim_id],
                "evidence": evidence_map.get(claim_id, []),
            }
            for claim_id in item["positive_claim_ids"]
            if claim_id in claim_map
        ]
        item["negative_claims"] = [
            {
                **claim_map[claim_id],
                "evidence": evidence_map.get(claim_id, []),
            }
            for claim_id in item["negative_claim_ids"]
            if claim_id in claim_map
        ]
        item["detection_model"] = "page_summary_polarity"
        item["detection_confidence"] = "heuristic"
        item["status_bucket"] = "open" if item["status"] == "open" else "reviewed"
        item["status_explanation"] = CONTRADICTION_STATUS_EXPLANATIONS.get(
            item["status"],
            "Reviewed contradiction state.",
        )
        item["scope_summary"] = {
            "object_count": len(object_ids),
            "positive_claim_count": len(item["positive_claims"]),
            "negative_claim_count": len(item["negative_claims"]),
            "source_note_count": len(
                {
                    evidence["source_slug"]
                    for claim in item["positive_claims"] + item["negative_claims"]
                    for evidence in claim["evidence"]
                }
            ),
        }
        item["ranked_evidence"] = _rank_contradiction_evidence(item)
        item["review_history"] = list_review_actions(vault_dir, object_ids=object_ids, limit=5)
    return items


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
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = (query or "").strip().lower()

    with sqlite3.connect(db_path) as conn:
        deep_dive_rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE note_type = 'deep_dive'
            ORDER BY slug
            """
        ).fetchall()
        object_rows = conn.execute(
            """
            SELECT object_id, title
            FROM objects
            ORDER BY object_id
            """
        ).fetchall()
        audit_rows = conn.execute(
            """
            SELECT payload_json
            FROM audit_events
            WHERE event_type = 'evergreen_auto_promoted'
            """
        ).fetchall()

    object_titles = {row[0]: row[1] for row in object_rows}
    grouped_promotions: dict[str, dict[str, dict[str, str]]] = {}
    for (payload_json,) in audit_rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        source_name = str(payload.get("source") or "").strip()
        object_id = str(payload.get("mutation", {}).get("target_slug") or payload.get("concept") or "").strip()
        if not source_name or not object_id:
            continue
        title = object_titles.get(object_id, object_id)
        grouped_promotions.setdefault(source_name, {})[object_id] = {
            "object_id": object_id,
            "title": title,
        }

    items: list[dict[str, Any]] = []
    for slug, title, note_type, path in deep_dive_rows:
        relative_path = _vault_relative_path(resolved_vault, path)
        source_name = Path(relative_path).name
        derived_objects = list(grouped_promotions.get(source_name, {}).values())
        if normalized_query:
            haystacks = [
                slug.lower(),
                title.lower(),
                relative_path.lower(),
                *(
                    value.lower()
                    for item in derived_objects
                    for value in (item["object_id"], item["title"])
                ),
            ]
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
        items.append(
            {
                "slug": slug,
                "title": title,
                "note_type": note_type,
                "path": relative_path,
                "derived_objects": sorted(derived_objects, key=lambda item: item["object_id"]),
            }
        )
        if len(items) >= limit:
            break
    return items


def list_stale_summaries(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    object_ids: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    sql = """
        SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
               COALESCE(rel.outgoing_count, 0) AS outgoing_count
        FROM objects
        LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
        LEFT JOIN (
            SELECT source_object_id, COUNT(*) AS outgoing_count
            FROM relations
            GROUP BY source_object_id
        ) AS rel ON rel.source_object_id = objects.object_id
    """
    params: list[Any] = []
    where_clauses: list[str] = []
    if object_ids:
        normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
        if not normalized_object_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_object_ids)
        where_clauses.append(f"objects.object_id IN ({placeholders})")
        params.extend(normalized_object_ids)
    if normalized_query:
        where_clauses.append(
            """
            (
                lower(objects.object_id) LIKE ? ESCAPE '\\'
                OR lower(objects.title) LIKE ? ESCAPE '\\'
                OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
            )
            """.strip()
        )
        params.extend([f"%{normalized_query}%"] * 3)
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY objects.object_id LIMIT ?"
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        latest_event_rows = conn.execute(
            """
            SELECT slug, MAX(event_date)
            FROM timeline_events
            GROUP BY slug
            """
        ).fetchall()
    latest_event_map = {str(slug): str(event_date or "") for slug, event_date in latest_event_rows}

    items: list[dict[str, Any]] = []
    for object_id, title, summary_text, outgoing_count in rows:
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        reason_codes: list[str] = ["no_outgoing_relations"]
        reason_texts: list[str] = ["No outgoing relations currently support this summary."]
        if not summary:
            reason_codes.append("summary_missing")
            reason_texts.append("Compiled summary is empty.")
        elif len(summary) < 40:
            reason_codes.append("summary_too_short")
            reason_texts.append("Compiled summary is too short to stand on its own.")
        if summary and summary.lower() == str(title).strip().lower():
            reason_codes.append("summary_repeats_title")
            reason_texts.append("Compiled summary repeats the title instead of adding substance.")
        items.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "object_path": f"/object?id={object_id}",
                "reason_codes": reason_codes,
                "reason_texts": reason_texts,
                "review_history": list_review_actions(vault_dir, object_ids=[str(object_id)], limit=5),
                "latest_event_date": latest_event_map.get(str(object_id), ""),
            }
        )
    return items
