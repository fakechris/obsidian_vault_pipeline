from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_api import (
    count_objects,
    get_object_detail,
    get_object_provenance_map,
    get_topic_neighborhood,
    list_atlas_memberships,
    list_contradictions,
    list_deep_dive_derivations,
    list_objects,
    list_stale_summaries,
)


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
        "provenance": detail["provenance"],
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
        "provenance": detail["provenance"],
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
            "event_kind": "dated_note" if row[1] == "page_date" else "dated_heading",
            "event_label": "Dated Note" if row[1] == "page_date" else "Dated Heading",
            "object_id": row[2],
            "title": row[3],
            "summary_text": row[4] or "",
        }
        for row in rows
    ]
    provenance_map = get_object_provenance_map(vault_dir, [event["object_id"] for event in events])
    for event in events:
        event["object_path"] = f"/object?id={event['object_id']}"
        event["provenance"] = provenance_map.get(
            event["object_id"],
            {"evergreen_path": "", "source_notes": [], "mocs": []},
        )
    dates = sorted({event["event_date"] for event in events})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["event_date"], []).append(event)
    date_sections = [{"date": date, "events": grouped[date]} for date in dates]
    event_type_counts = Counter(event["event_kind"] for event in events)
    return {
        "screen": "event/dossier",
        "events": events,
        "event_count": len(events),
        "dates": dates,
        "date_sections": date_sections,
        "event_type_counts": dict(event_type_counts),
        "model_notes": [
            "Event Dossier is a timeline over dated notes projected from indexed pages, not a separate event entity system.",
            "page_date rows come from note-level dates; heading_date rows come from dated section headings.",
        ],
        "query": query or "",
    }


def build_contradiction_browser_payload(
    vault_dir: Path | str,
    *,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    raw_items = list_contradictions(vault_dir, status=status, query=query)
    provenance_map = get_object_provenance_map(
        vault_dir,
        _object_ids_from_claim_ids(
            *(
                item["positive_claim_ids"] + item["negative_claim_ids"]
                for item in raw_items
            )
        ),
    )
    items = []
    for item in raw_items:
        object_ids = _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])
        source_notes: dict[str, dict[str, Any]] = {}
        mocs: dict[str, dict[str, Any]] = {}
        object_titles: dict[str, str] = {}
        for object_id in object_ids:
            provenance = provenance_map.get(
                object_id,
                {"title": object_id, "evergreen_path": "", "source_notes": [], "mocs": []},
            )
            object_titles[object_id] = provenance["title"]
            for note in provenance["source_notes"]:
                source_notes.setdefault(note["slug"], note)
            for moc in provenance["mocs"]:
                mocs.setdefault(moc["slug"], moc)
        items.append(
            {
                **item,
                "object_ids": object_ids,
                "object_titles": object_titles,
                "object_links": [
                    {"object_id": object_id, "path": f"/object?id={object_id}"} for object_id in object_ids
                ],
                "provenance": {
                    "source_notes": list(source_notes.values()),
                    "mocs": list(mocs.values()),
                },
            }
        )
    status_counts = Counter(item["status"] for item in items)
    return {
        "screen": "truth/contradictions",
        "items": items,
        "count": len(items),
        "open_count": status_counts.get("open", 0),
        "resolved_count": sum(count for status, count in status_counts.items() if status != "open"),
        "detection_notes": [
            "Contradictions are currently detected from page_summary claim polarity, not from full semantic contradiction analysis.",
            "Zero results do not prove consistency; they usually mean the current heuristic did not detect a conflict.",
        ],
        "empty_state": "Zero results usually means the current heuristic did not detect a conflict, not that the vault is globally contradiction-free.",
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


def build_atlas_browser_payload(vault_dir: Path | str, *, query: str | None = None) -> dict[str, Any]:
    items = list_atlas_memberships(vault_dir, query=query)
    enriched_items = []
    for item in items:
        preview_titles = [member["title"] for member in item["members"][:5]]
        enriched_items.append(
            {
                **item,
                "member_count": len(item["members"]),
                "preview_titles": preview_titles,
            }
        )
    return {
        "screen": "atlas/browser",
        "items": enriched_items,
        "count": len(enriched_items),
        "query": query or "",
    }


def build_derivation_browser_payload(vault_dir: Path | str, *, query: str | None = None) -> dict[str, Any]:
    items = list_deep_dive_derivations(vault_dir, query=query)
    enriched_items = []
    for item in items:
        preview_titles = [member["title"] for member in item["derived_objects"][:5]]
        enriched_items.append(
            {
                **item,
                "derived_object_count": len(item["derived_objects"]),
                "preview_titles": preview_titles,
            }
        )
    return {
        "screen": "derivations/browser",
        "items": enriched_items,
        "count": len(enriched_items),
        "query": query or "",
    }


def build_stale_summary_browser_payload(vault_dir: Path | str, *, query: str | None = None) -> dict[str, Any]:
    items = list_stale_summaries(vault_dir, query=query)
    return {
        "screen": "truth/stale-summaries",
        "items": items,
        "count": len(items),
        "query": query or "",
        "detection_notes": [
            "Stale summary review flags compiled summaries that are weak and have no outgoing supporting relations.",
            "This queue is deterministic and favors false negatives over false positives.",
        ],
    }
