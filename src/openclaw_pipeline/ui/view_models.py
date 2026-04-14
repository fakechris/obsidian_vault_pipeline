from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_store import CONTRADICTION_HEURISTIC_NOTE
from ..truth_api import (
    count_objects,
    get_object_detail,
    get_note_provenance,
    get_object_provenance_map,
    get_review_context,
    get_topic_neighborhood,
    list_review_actions,
    list_atlas_memberships,
    list_contradictions,
    list_deep_dive_derivations,
    list_objects,
    list_stale_summaries,
    search_vault_surface,
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
    review_context = get_review_context(vault_dir, [object_id])
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
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=[object_id], limit=8),
        "stale_summary_details": list_stale_summaries(vault_dir, object_ids=[object_id], limit=10),
        "open_contradiction_ids": [
            item["contradiction_id"] for item in detail["contradictions"] if item["status"] == "open"
        ],
        "links": {
            "topic_path": f"/topic?id={object_id}",
            "events_path": f"/events?q={object_id}",
            "contradictions_path": f"/contradictions?q={object_id}",
            "summaries_path": f"/summaries?q={object_id}",
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
    scoped_object_ids = [object_id, *[item["object_id"] for item in neighborhood["neighbors"]]]
    review_context = get_review_context(
        vault_dir,
        scoped_object_ids,
    )
    scoped_stale_summaries = list_stale_summaries(vault_dir, object_ids=scoped_object_ids, limit=50)
    scoped_contradictions = [
        item
        for item in list_contradictions(vault_dir, limit=100)
        if set(item["positive_claim_ids"] + item["negative_claim_ids"])
        and any(claim_id.split("::", 1)[0] in set(scoped_object_ids) for claim_id in item["positive_claim_ids"] + item["negative_claim_ids"])
        and item["status"] == "open"
    ]
    return {
        "screen": "overview/topic",
        **neighborhood,
        "edge_count": len(neighborhood["edges"]),
        "neighbor_count": len(neighborhood["neighbors"]),
        "center_summary": detail["summary"]["summary_text"] if detail["summary"] else "",
        "provenance": detail["provenance"],
        "review_context": review_context,
        "review_history": list_review_actions(
            vault_dir,
            object_ids=scoped_object_ids,
            limit=8,
        ),
        "scoped_object_ids": scoped_object_ids,
        "scoped_stale_summary_ids": [item["object_id"] for item in scoped_stale_summaries],
        "scoped_open_contradiction_ids": [item["contradiction_id"] for item in scoped_contradictions],
        "links": {
            "center_object_path": f"/object?id={object_id}",
            "events_path": f"/events?q={object_id}",
            "contradictions_path": f"/contradictions?q={object_id}",
            "summaries_path": f"/summaries?q={object_id}",
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
            SELECT timeline_events.event_date, timeline_events.event_type, timeline_events.heading,
                   timeline_events.payload_json, objects.object_id, objects.title, compiled_summaries.summary_text
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
        _build_timeline_event_item(row)
        for row in rows
    ]
    provenance_map = get_object_provenance_map(vault_dir, [event["object_id"] for event in events])
    scoped_object_ids = [event["object_id"] for event in events]
    review_context = get_review_context(vault_dir, scoped_object_ids)
    scoped_stale_summaries = list_stale_summaries(vault_dir, object_ids=scoped_object_ids, limit=100)
    scoped_contradictions = [
        item
        for item in list_contradictions(vault_dir, limit=200)
        if any(claim_id.split("::", 1)[0] in set(scoped_object_ids) for claim_id in item["positive_claim_ids"] + item["negative_claim_ids"])
        and item["status"] == "open"
    ]
    for event in events:
        event["object_path"] = f"/object?id={event['object_id']}"
        event["review_links"] = {
            "object_path": event["object_path"],
            "topic_path": f"/topic?id={event['object_id']}",
            "contradictions_path": f"/contradictions?q={event['object_id']}",
            "summaries_path": f"/summaries?q={event['object_id']}",
        }
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
    row_type_counts = Counter(event["row_type"] for event in events)
    semantic_roles = Counter(event["semantic_role"] for event in events)
    return {
        "screen": "event/dossier",
        "events": events,
        "event_count": len(events),
        "dates": dates,
        "date_sections": date_sections,
        "event_type_counts": dict(event_type_counts),
        "timeline_contract": {
            "timeline_kind": "dated_note_projection",
            "row_type_counts": dict(row_type_counts),
            "semantic_roles": dict(semantic_roles),
        },
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=scoped_object_ids, limit=8),
        "scoped_object_ids": list(dict.fromkeys(scoped_object_ids)),
        "scoped_stale_summary_ids": [item["object_id"] for item in scoped_stale_summaries],
        "scoped_open_contradiction_ids": [item["contradiction_id"] for item in scoped_contradictions],
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
        "detection_contract": {
            "model": "page_summary_polarity",
            "confidence": "heuristic",
            "status_buckets": {
                "open": status_counts.get("open", 0),
                "reviewed": sum(count for row_status, count in status_counts.items() if row_status != "open"),
            },
        },
        "detection_notes": [
            "Contradictions are currently detected from page_summary claim polarity, not from full semantic contradiction analysis.",
            "Zero results do not prove consistency; they usually mean the current heuristic did not detect a conflict.",
            CONTRADICTION_HEURISTIC_NOTE,
        ],
        "empty_state": "Zero results usually means the current heuristic did not detect a conflict, not that the vault is globally contradiction-free.",
        "status": status or "",
        "query": query or "",
    }


def _build_timeline_event_item(row: tuple[Any, ...]) -> dict[str, Any]:
    payload = json.loads(row[3] or "{}")
    event_type = str(row[1])
    title = str(row[5])
    heading = str(row[2] or "").strip()
    if event_type == "page_date":
        timeline_anchor_kind = "note"
        timeline_anchor_label = str(payload.get("title") or title)
        semantic_role = "note_date_projection"
        event_kind = "dated_note"
        event_label = "Dated Note"
    else:
        timeline_anchor_kind = "heading"
        timeline_anchor_label = heading or str(payload.get("title") or title)
        semantic_role = "heading_date_projection"
        event_kind = "dated_heading"
        event_label = "Dated Heading"
    return {
        "event_date": row[0],
        "event_type": event_type,
        "row_type": event_type,
        "event_kind": event_kind,
        "event_label": event_label,
        "semantic_role": semantic_role,
        "timeline_anchor_kind": timeline_anchor_kind,
        "timeline_anchor_label": timeline_anchor_label,
        "object_id": row[4],
        "title": title,
        "summary_text": row[6] or "",
    }


def build_truth_dashboard_payload(vault_dir: Path | str) -> dict[str, Any]:
    objects = build_objects_index_payload(vault_dir, limit=12, offset=0)
    contradictions = build_contradiction_browser_payload(vault_dir)
    events = build_event_dossier_payload(vault_dir, limit=8)
    stale_summaries = build_stale_summary_browser_payload(vault_dir)
    priorities: list[dict[str, Any]] = []
    for item in contradictions["items"][:4]:
        priorities.append(
            {
                "kind": "contradiction",
                "label": item["subject_key"],
                "path": f"/contradictions?q={item['subject_key']}",
                "detail": f"{len(item['object_ids'])} objects in scope",
            }
        )
    for item in stale_summaries["items"][:4]:
        priorities.append(
            {
                "kind": "stale_summary",
                "label": item["title"],
                "path": item["object_path"],
                "detail": ", ".join(item["reason_codes"]),
            }
        )
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
        "stale_summaries": {
            "count": stale_summaries["count"],
            "items": stale_summaries["items"][:8],
        },
        "recent_review_actions": list_review_actions(vault_dir, limit=8),
        "priorities": priorities[:8],
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
    review_context = get_review_context(vault_dir, [item["object_id"] for item in items])
    return {
        "screen": "truth/stale-summaries",
        "items": items,
        "count": len(items),
        "query": query or "",
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=[item["object_id"] for item in items], limit=8),
        "detection_notes": [
            "Stale summary review flags compiled summaries that are weak and have no outgoing supporting relations.",
            "This queue is deterministic and favors false negatives over false positives.",
        ],
    }


def build_search_payload(vault_dir: Path | str, *, query: str) -> dict[str, Any]:
    results = search_vault_surface(vault_dir, query=query)
    return {
        "screen": "search/results",
        **results,
        "object_count": len(results["objects"]),
        "note_count": len(results["notes"]),
    }


def build_note_page_payload(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    provenance = get_note_provenance(vault_dir, note_path=note_path)
    return {
        "screen": "note/page",
        "note_path": note_path,
        "provenance": provenance,
    }
