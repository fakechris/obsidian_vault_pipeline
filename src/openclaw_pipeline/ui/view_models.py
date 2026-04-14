from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_api import get_object_detail, get_topic_neighborhood, list_contradictions, list_objects


def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db


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
    }


def build_topic_overview_payload(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    neighborhood = get_topic_neighborhood(vault_dir, object_id)
    return {
        "screen": "overview/topic",
        **neighborhood,
        "edge_count": len(neighborhood["edges"]),
        "neighbor_count": len(neighborhood["neighbors"]),
    }


def build_event_dossier_payload(vault_dir: Path | str, *, query: str | None = None) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    normalized_query = query.strip().lower() if query else ""
    with sqlite3.connect(db_path) as conn:
        if normalized_query:
            rows = conn.execute(
                """
                SELECT timeline_events.event_date, timeline_events.event_type, objects.object_id, objects.title, compiled_summaries.summary_text
                FROM timeline_events
                JOIN objects ON objects.object_id = timeline_events.slug
                LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
                WHERE lower(objects.object_id) LIKE ? OR lower(objects.title) LIKE ? OR lower(compiled_summaries.summary_text) LIKE ?
                ORDER BY timeline_events.event_date, objects.object_id
                """,
                (
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT timeline_events.event_date, timeline_events.event_type, objects.object_id, objects.title, compiled_summaries.summary_text
                FROM timeline_events
                JOIN objects ON objects.object_id = timeline_events.slug
                LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
                ORDER BY timeline_events.event_date, objects.object_id
                """
            ).fetchall()

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
    return {
        "screen": "event/dossier",
        "events": events,
        "event_count": len(events),
        "dates": dates,
        "query": query or "",
    }


def build_contradiction_browser_payload(vault_dir: Path | str, *, status: str | None = None) -> dict[str, Any]:
    items = list_contradictions(vault_dir, status=status)
    status_counts = Counter(item["status"] for item in items)
    return {
        "screen": "truth/contradictions",
        "items": items,
        "count": len(items),
        "open_count": status_counts.get("open", 0),
        "resolved_count": sum(count for status, count in status_counts.items() if status != "open"),
        "status": status or "",
    }


def build_truth_dashboard_payload(vault_dir: Path | str) -> dict[str, Any]:
    objects = build_objects_index_payload(vault_dir, limit=12, offset=0)
    contradictions = build_contradiction_browser_payload(vault_dir)
    events = build_event_dossier_payload(vault_dir)
    return {
        "screen": "truth/dashboard",
        "objects": {
            "count": objects["count"],
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
    return {
        "screen": "objects/index",
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "query": query or "",
    }
