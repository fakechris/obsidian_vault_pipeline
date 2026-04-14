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
    return {
        "screen": "object/page",
        **detail,
        "claim_count": len(detail["claims"]),
        "relation_count": len(detail["relations"]),
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


def build_event_dossier_payload(vault_dir: Path | str) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
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
    }


def build_contradiction_browser_payload(vault_dir: Path | str) -> dict[str, Any]:
    items = list_contradictions(vault_dir)
    status_counts = Counter(item["status"] for item in items)
    return {
        "screen": "truth/contradictions",
        "items": items,
        "count": len(items),
        "open_count": status_counts.get("open", 0),
        "resolved_count": sum(count for status, count in status_counts.items() if status != "open"),
    }


def build_objects_index_payload(vault_dir: Path | str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    items = list_objects(vault_dir, limit=limit, offset=offset)
    return {
        "screen": "objects/index",
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
    }
