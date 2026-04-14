from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_api import count_objects, get_object_detail, get_topic_neighborhood, list_contradictions, list_objects


def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db


def _object_ids_from_claim_ids(*claim_id_lists: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for claim_ids in claim_id_lists:
        for claim_id in claim_ids:
            object_id = claim_id.split("::", 1)[0]
            if object_id and object_id not in seen:
                seen.add(object_id)
                ordered.append(object_id)
    return ordered


def build_object_page_payload(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    detail = get_object_detail(vault_dir, object_id)
    neighborhood = get_topic_neighborhood(vault_dir, object_id)
    neighbor_titles = {item["object_id"]: item["title"] for item in neighborhood["neighbors"]}
    relations = [
        {
            **item,
            "target_title": neighbor_titles.get(item["target_object_id"], item["target_object_id"]),
        }
        for item in detail["relations"]
    ]
    return {
        "screen": "object/page",
        **detail,
        "relations": relations,
        "claim_count": len(detail["claims"]),
        "relation_count": len(relations),
        "contradiction_count": len(detail["contradictions"]),
        "evidence_count": len(detail["evidence"]),
        "context": {
            "object_kind": detail["object"]["object_kind"],
            "source_slug": detail["object"]["source_slug"],
            "canonical_path": detail["object"]["canonical_path"],
        },
        "links": {
            "topic_path": f"/topic?id={object_id}",
            "events_path": f"/events?q={object_id}",
            "contradictions_path": f"/contradictions?q={object_id}",
        },
        "section_nav": [
            {"href": "#summary", "label": "Summary"},
            {"href": "#claims", "label": "Claims"},
            {"href": "#relations", "label": "Relations"},
            {"href": "#contradictions", "label": "Contradictions"},
        ],
    }


def build_topic_overview_payload(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    neighborhood = get_topic_neighborhood(vault_dir, object_id)
    detail = get_object_detail(vault_dir, object_id)
    return {
        "screen": "overview/topic",
        **neighborhood,
        "edge_count": len(neighborhood["edges"]),
        "neighbor_count": len(neighborhood["neighbors"]),
        "center_summary": detail["summary"]["summary_text"] if detail["summary"] else "",
        "links": {
            "center_object_path": f"/object?id={object_id}",
            "events_path": f"/events?q={object_id}",
            "contradictions_path": f"/contradictions?q={object_id}",
        },
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_event_dossier_payload(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    with sqlite3.connect(db_path) as conn:
        sql = """
            SELECT timeline_events.event_date, timeline_events.event_type, objects.object_id, objects.title, compiled_summaries.summary_text
            FROM timeline_events
            JOIN objects ON objects.object_id = timeline_events.slug
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
        """
        params: list[Any] = []
        if normalized_query:
            sql += """
                WHERE lower(objects.object_id) LIKE ? ESCAPE '\\'
                   OR lower(objects.title) LIKE ? ESCAPE '\\'
                   OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
            """
            params.extend(
                [
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                ]
            )
        sql += " ORDER BY timeline_events.event_date, objects.object_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()

    events = [
        {
            "event_date": row[0],
            "event_type": row[1],
            "object_id": row[2],
            "title": row[3],
            "summary_text": row[4] or "",
        }
        for row in rows
    ]
    dates = sorted({event["event_date"] for event in events})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["event_date"], []).append(event)
    date_sections = [{"date": date, "events": grouped[date]} for date in dates]
    return {
        "screen": "event/dossier",
        "events": events,
        "event_count": len(events),
        "dates": dates,
        "date_sections": date_sections,
        "query": query or "",
    }


def build_contradiction_browser_payload(
    vault_dir: Path | str,
    *,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    raw_items = list_contradictions(vault_dir, status=status, query=query)
    items = []
    for item in raw_items:
        object_ids = _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])
        items.append(
            {
                **item,
                "object_ids": object_ids,
                "object_links": [
                    {"object_id": object_id, "path": f"/object?id={object_id}"} for object_id in object_ids
                ],
            }
        )
    status_counts = Counter(item["status"] for item in items)
    return {
        "screen": "truth/contradictions",
        "items": items,
        "count": len(items),
        "open_count": status_counts.get("open", 0),
        "resolved_count": sum(count for status, count in status_counts.items() if status != "open"),
        "status": status or "",
        "query": query or "",
    }


def build_truth_dashboard_payload(vault_dir: Path | str) -> dict[str, Any]:
    objects = build_objects_index_payload(vault_dir, limit=12, offset=0)
    contradictions = build_contradiction_browser_payload(vault_dir)
    events = build_event_dossier_payload(vault_dir, limit=8)
    return {
        "screen": "truth/dashboard",
        "objects": {
            "count": objects["total_count"],
            "items": objects["items"],
        },
        "contradictions": {
            "count": contradictions["count"],
            "open_count": contradictions["open_count"],
            "items": contradictions["items"][:8],
        },
        "events": {
            "count": events["event_count"],
            "items": events["events"][:8],
            "dates": events["dates"],
        },
    }


def build_objects_index_payload(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
) -> dict[str, Any]:
    items = list_objects(vault_dir, limit=limit, offset=offset, query=query)
    total_count = count_objects(vault_dir, query=query)
    return {
        "screen": "objects/index",
        "items": items,
        "count": len(items),
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "query": query or "",
    }
