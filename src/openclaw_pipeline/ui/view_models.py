from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_store import CONTRADICTION_HEURISTIC_NOTE
from ..truth_api import (
    CONTRADICTION_STATUS_EXPLANATIONS,
    SIGNAL_TYPE_EXPLANATIONS,
    count_objects,
    get_briefing_snapshot,
    get_graph_cluster_detail,
    get_object_detail,
    get_object_traceability,
    get_note_provenance,
    get_note_traceability,
    get_object_provenance_map,
    get_review_context,
    get_topic_neighborhood,
    list_evolution_candidates,
    list_evolution_links,
    list_review_actions,
    list_atlas_memberships,
    list_action_queue,
    list_contradictions,
    list_deep_dive_derivations,
    list_graph_clusters,
    list_objects,
    list_production_gaps,
    list_production_chains,
    list_signals,
    list_stale_summaries,
    search_vault_surface,
)

DEFAULT_EVENT_DOSSIER_LIMIT = 50
DEFAULT_TRACEABILITY_BROWSER_LIMIT = 50


def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db


def _existing_object_rows(vault_dir: Path | str, object_ids: list[str]) -> dict[str, str]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT object_id, title
            FROM objects
            WHERE object_id IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchall()
    return {str(object_id): str(title) for object_id, title in rows}


def _object_scope_paths(vault_dir: Path | str, object_ids: list[str]) -> dict[str, str]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT object_id, canonical_path
            FROM objects
            WHERE object_id IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchall()
    return {str(object_id): str(path or "") for object_id, path in rows}


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


def _edge_kind_parts(edge_kind: str) -> tuple[str, str]:
    family, sep, subtype = str(edge_kind).partition(":")
    if not sep:
        return (family, "")
    return (family, subtype)


def _derive_cluster_structural_label(
    *,
    center_title: str,
    edge_summary_items: list[dict[str, Any]],
) -> dict[str, str]:
    contradiction_item = next((item for item in edge_summary_items if item["edge_family"] == "contradiction"), None)
    if contradiction_item is not None:
        return {
            "kind": "contradiction_cluster",
            "title": f"Contradiction cluster around {center_title}",
            "reason": f"{contradiction_item['count']} contradiction edges are present in the local graph.",
        }
    dominant = edge_summary_items[0] if edge_summary_items else None
    if dominant is None:
        return {
            "kind": "reference_cluster",
            "title": f"Reference cluster around {center_title}",
            "reason": "No internal edge structure has been materialized yet.",
        }
    if dominant["edge_family"] == "relation":
        return {
            "kind": "relation_cluster",
            "title": f"Relation cluster around {center_title}",
            "reason": f"{dominant['count']} {dominant['display_name']} dominate the local graph.",
        }
    return {
        "kind": "mixed_cluster",
        "title": f"Mixed graph cluster around {center_title}",
        "reason": f"Dominant edge family is {dominant['edge_family']}.",
    }


def _build_relation_pattern_items(edge_summary_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "edge_kind": item["edge_kind"],
            "subtype": item["edge_subtype"],
            "display_name": item["display_name"],
            "count": item["count"],
        }
        for item in edge_summary_items
        if item["edge_family"] == "relation"
    ]


def _relation_pattern_preview(relation_pattern_items: list[dict[str, Any]]) -> str:
    if not relation_pattern_items:
        return ""
    preview_items = relation_pattern_items[:2]
    preview = ", ".join(f"{item['display_name']} ({item['count']})" for item in preview_items)
    if len(relation_pattern_items) > 2:
        return f"{preview}, +{len(relation_pattern_items) - 2} more"
    return preview


def _top_counter_items(
    counts: Counter[str],
    item_map: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return [
        {**item_map[key], "object_count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if key in item_map
    ][:limit]


def _collect_cluster_provenance(
    vault_dir: Path | str,
    member_object_ids: list[str],
) -> dict[str, Any]:
    provenance_map = get_object_provenance_map(vault_dir, member_object_ids)
    source_note_counts: Counter[str] = Counter()
    source_note_items: dict[str, dict[str, Any]] = {}
    moc_counts: Counter[str] = Counter()
    moc_items: dict[str, dict[str, Any]] = {}
    for provenance in provenance_map.values():
        for note in provenance["source_notes"]:
            slug = str(note["slug"])
            source_note_items.setdefault(slug, note)
            source_note_counts[slug] += 1
        for moc in provenance["mocs"]:
            slug = str(moc["slug"])
            moc_items.setdefault(slug, moc)
            moc_counts[slug] += 1
    return {
        "source_note_counts": source_note_counts,
        "source_note_items": source_note_items,
        "moc_counts": moc_counts,
        "moc_items": moc_items,
    }


def _build_related_cluster_items(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    requested_pack: str,
    current_source_note_items: dict[str, dict[str, Any]],
    current_moc_items: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    current_source_slugs = set(current_source_note_items)
    current_moc_slugs = set(current_moc_items)
    if not current_source_slugs and not current_moc_slugs:
        return []
    related_items: list[dict[str, Any]] = []
    for row in list_graph_clusters(vault_dir, pack_name=requested_pack, limit=200):
        if str(row["cluster_id"]) == cluster_id:
            continue
        member_object_ids = [str(member["object_id"]) for member in row["members"]]
        provenance = _collect_cluster_provenance(vault_dir, member_object_ids)
        shared_source_slugs = sorted(current_source_slugs & set(provenance["source_note_items"]))
        shared_moc_slugs = sorted(current_moc_slugs & set(provenance["moc_items"]))
        if not shared_source_slugs and not shared_moc_slugs:
            continue
        reason_parts: list[str] = []
        if shared_source_slugs:
            reason_parts.append(f"{len(shared_source_slugs)} shared source notes")
        if shared_moc_slugs:
            reason_parts.append(f"{len(shared_moc_slugs)} shared atlas pages")
        score = len(shared_source_slugs) * 10 + len(shared_moc_slugs) * 5 + int(row["member_count"])
        related_items.append(
            {
                "cluster_id": str(row["cluster_id"]),
                "pack": requested_pack,
                "label": str(row["label"]),
                "display_title": f"Cluster around {row['center_title']}",
                "detail_path": (
                    f"/cluster?id={quote(str(row['cluster_id']), safe='')}"
                    f"&pack={quote(requested_pack, safe='')}"
                ),
                "member_count": int(row["member_count"]),
                "shared_source_count": len(shared_source_slugs),
                "shared_moc_count": len(shared_moc_slugs),
                "shared_source_titles": [
                    str(current_source_note_items.get(slug, provenance["source_note_items"].get(slug, {})).get("title", slug))
                    for slug in shared_source_slugs
                ][:3],
                "shared_moc_titles": [
                    str(current_moc_items.get(slug, provenance["moc_items"].get(slug, {})).get("title", slug))
                    for slug in shared_moc_slugs
                ][:3],
                "reason": ", ".join(reason_parts),
                "score": score,
            }
        )
    related_items.sort(key=lambda item: (-item["score"], item["label"].lower(), item["cluster_id"]))
    return related_items[:5]


def _build_production_summary(vault_dir: Path | str, object_ids: list[str]) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    object_traceability = [get_object_traceability(vault_dir, object_id) for object_id in normalized_object_ids]
    source_note_counts: Counter[str] = Counter()
    deep_dive_counts: Counter[str] = Counter()
    atlas_page_counts: Counter[str] = Counter()
    source_note_items: dict[str, dict[str, str]] = {}
    deep_dive_items: dict[str, dict[str, str]] = {}
    atlas_page_items: dict[str, dict[str, str]] = {}
    missing_source_object_ids: list[str] = []
    missing_deep_dive_object_ids: list[str] = []
    missing_atlas_object_ids: list[str] = []

    for traceability in object_traceability:
        object_id = traceability["object"]["object_id"]
        if not traceability["source_notes"]:
            missing_source_object_ids.append(object_id)
        if not traceability["deep_dives"]:
            missing_deep_dive_object_ids.append(object_id)
        if not traceability["atlas_pages"]:
            missing_atlas_object_ids.append(object_id)
        for item in traceability["source_notes"]:
            source_note_items.setdefault(item["path"], item)
            source_note_counts[item["path"]] += 1
        for item in traceability["deep_dives"]:
            deep_dive_items.setdefault(item["slug"], item)
            deep_dive_counts[item["slug"]] += 1
        for item in traceability["atlas_pages"]:
            atlas_page_items.setdefault(item["slug"], item)
            atlas_page_counts[item["slug"]] += 1

    def _top_items(
        counts: Counter[str],
        item_map: dict[str, dict[str, str]],
    ) -> list[dict[str, Any]]:
        ordered = sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        return [
            {
                **item_map[key],
                "object_count": count,
            }
            for key, count in ordered
            if key in item_map
        ][:5]

    signals: list[dict[str, Any]] = []
    if missing_source_object_ids:
        signals.append(
            {
                "code": "missing_source_notes",
                "count": len(missing_source_object_ids),
                "label": "Missing source notes",
                "object_ids": missing_source_object_ids,
            }
        )
    if missing_deep_dive_object_ids:
        signals.append(
            {
                "code": "missing_deep_dives",
                "count": len(missing_deep_dive_object_ids),
                "label": "Missing deep dives",
                "object_ids": missing_deep_dive_object_ids,
            }
        )
    if missing_atlas_object_ids:
        signals.append(
            {
                "code": "missing_atlas_reach",
                "count": len(missing_atlas_object_ids),
                "label": "Missing Atlas / MOC reach",
                "object_ids": missing_atlas_object_ids,
            }
        )

    return {
        "object_count": len(normalized_object_ids),
        "counts": {
            "source_notes": len(source_note_items),
            "deep_dives": len(deep_dive_items),
            "atlas_pages": len(atlas_page_items),
        },
        "top_source_notes": _top_items(source_note_counts, source_note_items),
        "top_deep_dives": _top_items(deep_dive_counts, deep_dive_items),
        "top_atlas_pages": _top_items(atlas_page_counts, atlas_page_items),
        "signals": signals,
    }


def _build_production_weak_points(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    return list_production_gaps(vault_dir, query=query, limit=limit)


def _build_evolution_section(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    link_type: str | None = None,
    status: str = "candidate",
    scoped_object_ids: list[str] | None = None,
) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in (scoped_object_ids or []) if object_id))
    canonical_paths = {path for path in _object_scope_paths(vault_dir, normalized_object_ids).values() if path}
    reviewed_links = list_evolution_links(
        vault_dir,
        object_ids=normalized_object_ids or None,
        query=query,
        link_type=link_type,
    )
    reviewed_evolution_ids = {str(item["evolution_id"]) for item in reviewed_links}
    accepted_links = [item for item in reviewed_links if item["status"] == "accepted"]
    rejected_links = [item for item in reviewed_links if item["status"] == "rejected"]
    candidate_items = [
        item
        for item in list_evolution_candidates(
            vault_dir,
            object_ids=normalized_object_ids or None,
            query=query,
            link_type=link_type,
            status="candidate",
        )
        if item["evolution_id"] not in reviewed_evolution_ids
    ]
    if normalized_object_ids:
        filtered_items: list[dict[str, Any]] = []
        for item in candidate_items:
            refs = (str(item["earlier_ref"]), str(item["later_ref"]))
            if item["subject_kind"] == "object" and item["subject_id"] in normalized_object_ids:
                filtered_items.append(item)
                continue
            if any(
                ref.startswith(f"claim://{object_id}::") or ref == f"object://{object_id}"
                for object_id in normalized_object_ids
                for ref in refs
            ):
                filtered_items.append(item)
                continue
            if any(path in canonical_paths for path in item["source_paths"]):
                filtered_items.append(item)
        candidate_items = filtered_items
        accepted_links = [
            item for item in accepted_links
            if set(item.get("object_ids", [])).intersection(normalized_object_ids)
        ]
        rejected_links = [
            item for item in rejected_links
            if set(item.get("object_ids", [])).intersection(normalized_object_ids)
        ]
    if status == "accepted":
        candidate_items = []
    elif status == "rejected":
        candidate_items = []
        accepted_links = []
    elif status == "candidate":
        pass
    else:
        # keep all sections visible on the default "all" view
        status = "all"
    return {
        "accepted_links": accepted_links,
        "rejected_links": rejected_links,
        "candidate_items": candidate_items,
        "candidate_count": len(candidate_items),
        "accepted_count": len(accepted_links),
        "rejected_count": len(rejected_links),
        "link_types": sorted(
            {
                *(item["link_type"] for item in candidate_items),
                *(str(item.get("link_type") or "") for item in accepted_links),
                *(str(item.get("link_type") or "") for item in rejected_links),
            }
        ),
        "status": status,
    }


def build_signal_browser_payload(
    vault_dir: Path | str,
    *,
    signal_type: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    items = list_signals(vault_dir, signal_type=signal_type, query=query)
    return {
        "screen": "signals/browser",
        "items": items,
        "count": len(items),
        "query": query or "",
        "signal_type": signal_type or "",
        "type_counts": dict(Counter(item["signal_type"] for item in items)),
        "signal_type_explanations": SIGNAL_TYPE_EXPLANATIONS,
    }


def build_action_queue_payload(
    vault_dir: Path | str,
    *,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    items = list_action_queue(vault_dir, status=status, query=query)
    return {
        "screen": "actions/browser",
        "items": items,
        "count": len(items),
        "query": query or "",
        "status": status or "",
        "status_counts": dict(Counter(str(item["status"]) for item in items)),
        "queued_safe_count": sum(1 for item in items if item.get("status") == "queued" and item.get("safe_to_run")),
        "failed_count": sum(1 for item in items if item.get("status") == "failed"),
        "failure_buckets": dict(
            Counter(
                str(item.get("failure_bucket") or "")
                for item in items
                if item.get("status") == "failed" and str(item.get("failure_bucket") or "")
            )
        ),
    }


def build_briefing_payload(vault_dir: Path | str) -> dict[str, Any]:
    return {
        "screen": "briefing/intelligence",
        **get_briefing_snapshot(vault_dir),
    }


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
        "production_chain": get_object_traceability(vault_dir, object_id),
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
        "evolution": _build_evolution_section(vault_dir, status="all", scoped_object_ids=[object_id]),
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
        "production_summary": _build_production_summary(vault_dir, scoped_object_ids),
        "review_context": review_context,
        "review_history": list_review_actions(
            vault_dir,
            object_ids=scoped_object_ids,
            limit=8,
        ),
        "evolution": _build_evolution_section(vault_dir, status="all", scoped_object_ids=scoped_object_ids),
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
    effective_limit = DEFAULT_EVENT_DOSSIER_LIMIT if limit is None else limit
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
        sql += " ORDER BY timeline_events.event_date DESC, objects.object_id"
        if effective_limit is not None:
            sql += " LIMIT ?"
            params.append(effective_limit)
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
    dates = sorted({event["event_date"] for event in events}, reverse=True)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["event_date"], []).append(event)
    cluster_sections = [
        {
            "date": date,
            "clusters": _cluster_timeline_events(grouped[date]),
        }
        for date in dates
    ]
    event_type_counts = Counter(event["event_kind"] for event in events)
    row_type_counts = Counter(event["row_type"] for event in events)
    semantic_roles = Counter(event["semantic_role"] for event in events)
    return {
        "screen": "event/dossier",
        "events": events,
        "event_count": len(events),
        "cluster_count": sum(len(section["clusters"]) for section in cluster_sections),
        "dates": dates,
        "cluster_sections": cluster_sections,
        "event_type_counts": dict(event_type_counts),
        "limit": effective_limit,
        "is_limited": effective_limit is not None,
        "timeline_contract": {
            "timeline_kind": "dated_note_projection",
            "row_type_counts": dict(row_type_counts),
            "semantic_roles": dict(semantic_roles),
        },
        "production_summary": _build_production_summary(vault_dir, scoped_object_ids),
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


def build_evolution_browser_payload(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    status: str = "all",
    link_type: str | None = None,
) -> dict[str, Any]:
    evolution = _build_evolution_section(vault_dir, query=query, link_type=link_type, status=status)
    type_counts = Counter(
        item["link_type"]
        for item in [
            *evolution["candidate_items"],
            *evolution["accepted_links"],
            *evolution["rejected_links"],
        ]
    )
    return {
        "screen": "evolution/browser",
        "query": query or "",
        "status": status,
        "link_type": link_type or "",
        "items": evolution["candidate_items"],
        "candidate_items": evolution["candidate_items"],
        "accepted_links": evolution["accepted_links"],
        "rejected_links": evolution["rejected_links"],
        "candidate_count": evolution["candidate_count"],
        "accepted_count": evolution["accepted_count"],
        "rejected_count": evolution["rejected_count"],
        "count": evolution["candidate_count"] + evolution["accepted_count"] + evolution["rejected_count"],
        "type_counts": dict(type_counts),
        "link_types": evolution["link_types"],
    }


def build_cluster_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_TRACEABILITY_BROWSER_LIMIT,
) -> dict[str, Any]:
    items = list_graph_clusters(vault_dir, pack_name=pack_name, query=query, limit=limit)
    cluster_kind_counts = Counter(item["cluster_kind"] for item in items)
    largest_cluster_size = max((int(item["member_count"]) for item in items), default=0)
    enriched_items = []
    for item in items:
        requested_pack = pack_name or str(item["pack"])
        detail = build_cluster_detail_payload(
            vault_dir,
            cluster_id=str(item["cluster_id"]),
            pack_name=requested_pack,
        )
        review_context = detail["review_context"]
        dominant_edge_kind = next(
            iter(
                sorted(
                    detail["edge_kind_counts"].items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ),
            None,
        )
        priority_score = (
            review_context["open_contradiction_count"] * 100
            + review_context["stale_summary_count"] * 40
            + int(item["member_count"]) * 10
            + len(detail["edges"]) * 3
            + review_context["source_note_count"]
            + review_context["moc_count"]
        )
        if review_context["open_contradiction_count"] > 0 or review_context["stale_summary_count"] > 0:
            priority_band = "attention"
            priority_reason = (
                f"{review_context['open_contradiction_count']} open contradictions, "
                f"{review_context['stale_summary_count']} stale summaries"
            )
        elif dominant_edge_kind is not None:
            priority_band = "active"
            priority_reason = f"dominant edge kind {dominant_edge_kind[0]} ({dominant_edge_kind[1]})"
        else:
            priority_band = "reference"
            priority_reason = f"{review_context['source_note_count']} source notes in scope"
        enriched_items.append(
            {
                **item,
                "row_pack": str(item["pack"]),
                "pack": requested_pack,
                "detail_path": detail["cluster"]["detail_path"],
                "center_object_path": detail["cluster"]["center_object_path"],
                "member_links": detail["cluster"]["member_links"],
                "display_title": detail["display_title"],
                "relation_pattern_preview": detail["relation_pattern_preview"],
                "priority_score": priority_score,
                "priority_band": priority_band,
                "priority_reason": priority_reason,
                "top_summary_bullet": detail["summary_bullets"][0] if detail["summary_bullets"] else "",
                "dominant_edge_kind": dominant_edge_kind[0] if dominant_edge_kind is not None else "",
            }
        )
    enriched_items.sort(
        key=lambda item: (
            -int(item["priority_score"]),
            str(item["label"]).lower(),
            str(item["cluster_id"]),
        )
    )
    return {
        "screen": "graph/clusters",
        "requested_pack": pack_name or "",
        "query": query or "",
        "limit": limit,
        "is_limited": True,
        "items": enriched_items,
        "count": len(enriched_items),
        "cluster_kind_counts": dict(cluster_kind_counts),
        "largest_cluster_size": largest_cluster_size,
        "model_notes": [
            "Graph clusters currently come from pack-owned graph seed projections, not from a final semantic clustering model.",
            "Current research-tech clusters are relation/contradiction connected components over pack-scoped truth rows.",
        ],
    }


def build_cluster_detail_payload(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    pack_name: str | None = None,
) -> dict[str, Any]:
    detail = get_graph_cluster_detail(vault_dir, cluster_id, pack_name=pack_name)
    cluster = detail["cluster"]
    requested_pack = pack_name or str(cluster["pack"])
    member_index = {str(member["object_id"]): member for member in cluster["members"]}
    member_object_ids = [str(member["object_id"]) for member in cluster["members"]]
    detail_path = (
        f"/cluster?id={quote(str(cluster['cluster_id']), safe='')}"
        f"&pack={quote(requested_pack, safe='')}"
    )
    enriched_cluster = {
        **cluster,
        "detail_path": detail_path,
        "center_object_path": f"/object?id={quote(str(cluster['center_object_id']), safe='')}",
        "member_links": [
            {
                **member,
                "path": f"/object?id={quote(str(member['object_id']), safe='')}",
            }
            for member in cluster["members"]
        ],
    }
    enriched_edges = [
        {
            **edge,
            "source_title": member_index.get(str(edge["source_object_id"]), {}).get(
                "title",
                str(edge["source_object_id"]),
            ),
            "target_title": member_index.get(str(edge["target_object_id"]), {}).get(
                "title",
                str(edge["target_object_id"]),
            ),
            "source_path": f"/object?id={quote(str(edge['source_object_id']), safe='')}",
            "target_path": f"/object?id={quote(str(edge['target_object_id']), safe='')}",
        }
        for edge in detail["edges"]
    ]
    edge_kind_counts = Counter(edge["edge_kind"] for edge in enriched_edges)
    edge_summary_items = [
        {
            "edge_kind": edge_kind,
            "edge_family": _edge_kind_parts(edge_kind)[0],
            "edge_subtype": _edge_kind_parts(edge_kind)[1],
            "display_name": (
                "contradiction links"
                if _edge_kind_parts(edge_kind)[0] == "contradiction"
                else (
                    f"{_edge_kind_parts(edge_kind)[1].replace('_', ' ')} links"
                    if _edge_kind_parts(edge_kind)[0] == "relation" and _edge_kind_parts(edge_kind)[1]
                    else edge_kind.replace(":", " ")
                )
            ),
            "count": count,
        }
        for edge_kind, count in sorted(edge_kind_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    object_kind_counts = Counter(
        str(member["object_kind"])
        for member in cluster["members"]
        if member.get("object_kind")
    )
    review_context = get_review_context(vault_dir, member_object_ids)
    open_contradictions = [
        {
            "contradiction_id": item["contradiction_id"],
            "subject_key": item["subject_key"],
            "object_ids": _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"]),
            "path": f"/contradictions?q={quote(str(item['subject_key']), safe='')}",
        }
        for item in list_contradictions(vault_dir, status="open", limit=20)
        if set(_object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])) & set(member_object_ids)
    ][:5]
    stale_summaries = list_stale_summaries(vault_dir, object_ids=member_object_ids, limit=5)
    provenance = _collect_cluster_provenance(vault_dir, member_object_ids)
    source_note_counts = provenance["source_note_counts"]
    source_note_items = provenance["source_note_items"]
    moc_counts = provenance["moc_counts"]
    moc_items = provenance["moc_items"]

    top_edge_kind = next(iter(sorted(edge_kind_counts.items(), key=lambda item: (-item[1], item[0]))), None)
    kind_summary = ", ".join(
        f"{kind} {count}"
        for kind, count in sorted(object_kind_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    summary_bullets = [
        f"{cluster['member_count']} objects in a {cluster['cluster_kind']} cluster centered on {cluster['center_title']}.",
    ]
    if top_edge_kind:
        summary_bullets.append(
            f"{len(enriched_edges)} internal edges across {len(edge_kind_counts)} edge kinds; dominant edge kind is {top_edge_kind[0]} ({top_edge_kind[1]})."
        )
    if kind_summary:
        summary_bullets.append(f"Object kinds in scope: {kind_summary}.")
    if review_context["source_note_count"] or review_context["moc_count"]:
        summary_bullets.append(
            f"Coverage currently includes {review_context['source_note_count']} source/deep-dive notes and {review_context['moc_count']} atlas pages."
        )
    if review_context["open_contradiction_count"] or review_context["stale_summary_count"]:
        summary_bullets.append(
            f"Review pressure: {review_context['open_contradiction_count']} open contradictions and {review_context['stale_summary_count']} stale summaries in this cluster scope."
        )
    structural_label = _derive_cluster_structural_label(
        center_title=str(cluster["center_title"]),
        edge_summary_items=edge_summary_items,
    )
    relation_pattern_items = _build_relation_pattern_items(edge_summary_items)
    relation_pattern_preview = _relation_pattern_preview(relation_pattern_items)
    related_clusters = _build_related_cluster_items(
        vault_dir,
        cluster_id=str(cluster["cluster_id"]),
        requested_pack=requested_pack,
        current_source_note_items=source_note_items,
        current_moc_items=moc_items,
    )

    return {
        "screen": "graph/cluster-detail",
        "requested_pack": requested_pack,
        "cluster": enriched_cluster,
        "browser_path": f"/clusters?pack={quote(requested_pack, safe='')}",
        "display_title": structural_label["title"],
        "edges": enriched_edges,
        "edge_kind_counts": dict(edge_kind_counts),
        "edge_summary_items": edge_summary_items,
        "relation_pattern_items": relation_pattern_items,
        "relation_pattern_preview": relation_pattern_preview,
        "object_kind_counts": dict(object_kind_counts),
        "structural_label": structural_label,
        "review_context": review_context,
        "open_contradictions": open_contradictions,
        "stale_summaries": stale_summaries,
        "related_clusters": related_clusters,
        "top_source_notes": _top_counter_items(source_note_counts, source_note_items),
        "top_mocs": _top_counter_items(moc_counts, moc_items),
        "summary_bullets": summary_bullets,
        "model_notes": [
            "Cluster detail currently reflects pack-owned graph seed structure, not a final semantic subgraph model.",
            "Edges are filtered to the cluster's own member set inside the requested pack projection.",
        ],
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
        "scope_summary": {
            "item_count": len(items),
            "object_count": len({object_id for item in items for object_id in item["object_ids"]}),
            "source_note_count": len(
                {
                    note["slug"]
                    for item in items
                    for note in item["provenance"]["source_notes"]
                }
            ),
        },
        "detection_contract": {
            "model": "page_summary_polarity",
            "confidence": "heuristic",
            "status_buckets": {
                "open": status_counts.get("open", 0),
                "reviewed": sum(count for row_status, count in status_counts.items() if row_status != "open"),
            },
            "status_explanations": CONTRADICTION_STATUS_EXPLANATIONS,
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


def _cluster_timeline_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (str(event["event_date"]), str(event["object_id"]))
        cluster = clusters.setdefault(
            key,
            {
                "event_date": event["event_date"],
                "object_id": event["object_id"],
                "title": event["title"],
                "object_path": event["object_path"],
                "summary_text": event["summary_text"],
                "review_links": event["review_links"],
                "provenance": event["provenance"],
                "row_count": 0,
                "row_types": [],
                "event_labels": [],
                "semantic_roles": [],
                "timeline_anchor_labels": [],
            },
        )
        cluster["row_count"] += 1
        for field, value in (
            ("row_types", event["row_type"]),
            ("event_labels", event["event_label"]),
            ("semantic_roles", event["semantic_role"]),
            ("timeline_anchor_labels", event["timeline_anchor_label"]),
        ):
            if value not in cluster[field]:
                cluster[field].append(value)
    for cluster in clusters.values():
        cluster["row_types"] = sorted(cluster["row_types"])
        cluster["semantic_roles"] = sorted(cluster["semantic_roles"])
    return sorted(clusters.values(), key=lambda item: (str(item["event_date"]), str(item["object_id"])))


def build_truth_dashboard_payload(vault_dir: Path | str) -> dict[str, Any]:
    objects = build_objects_index_payload(vault_dir, limit=12, offset=0)
    contradictions = build_contradiction_browser_payload(vault_dir)
    events = build_event_dossier_payload(vault_dir, limit=8)
    stale_summaries = build_stale_summary_browser_payload(vault_dir)
    evolution = build_evolution_browser_payload(vault_dir, status="all")
    signals = build_signal_browser_payload(vault_dir)
    production_weak_points = _build_production_weak_points(vault_dir)
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
    for item in production_weak_points[:4]:
        priorities.append(
            {
                "kind": "production_gap",
                "label": item["title"],
                "path": f"/note?path={quote(item['note_path'], safe='')}",
                "detail": item["detail"],
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
        "evolution": {
            "candidate_count": evolution["candidate_count"],
            "accepted_count": evolution["accepted_count"],
            "items": evolution["candidate_items"][:6],
        },
        "production": {
            "weak_points": production_weak_points,
            "weak_point_count": len(production_weak_points),
        },
        "signals": {
            "count": signals["count"],
            "items": signals["items"][:8],
            "type_counts": signals["type_counts"],
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
    items = list_atlas_memberships(vault_dir, query=query, limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT)
    derivations = list_deep_dive_derivations(vault_dir, limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT)
    object_to_deep_dives: dict[str, dict[str, dict[str, str]]] = {}
    object_to_source_notes: dict[str, dict[str, dict[str, str]]] = {}
    for item in derivations:
        source_notes = get_note_traceability(vault_dir, note_path=item["path"])["source_notes"]
        for derived in item["derived_objects"]:
            object_to_deep_dives.setdefault(derived["object_id"], {})[item["slug"]] = {
                "slug": item["slug"],
                "title": item["title"],
                "note_type": "deep_dive",
                "path": item["path"],
            }
            object_to_source_notes.setdefault(derived["object_id"], {})
            for source in source_notes:
                object_to_source_notes[derived["object_id"]][source["path"]] = source
    enriched_items = []
    for item in items:
        preview_titles = [member["title"] for member in item["members"][:5]]
        member_object_ids = [member["object_id"] for member in item["members"]]
        source_note_map: dict[str, dict[str, str]] = {}
        deep_dive_map: dict[str, dict[str, str]] = {}
        for member_object_id in member_object_ids:
            for source in object_to_source_notes.get(member_object_id, {}).values():
                source_note_map.setdefault(source["path"], source)
            for deep_dive in object_to_deep_dives.get(member_object_id, {}).values():
                deep_dive_map.setdefault(deep_dive["slug"], deep_dive)
        enriched_items.append(
            {
                **item,
                "member_count": len(item["members"]),
                "preview_titles": preview_titles,
                "source_notes": list(source_note_map.values()),
                "deep_dives": list(deep_dive_map.values()),
            }
        )
    return {
        "screen": "atlas/browser",
        "items": enriched_items,
        "count": len(enriched_items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
    }


def build_derivation_browser_payload(vault_dir: Path | str, *, query: str | None = None) -> dict[str, Any]:
    items = list_deep_dive_derivations(vault_dir, query=query, limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT)
    enriched_items = []
    for item in items:
        existing_object_rows = _existing_object_rows(
            vault_dir,
            [member["object_id"] for member in item["derived_objects"]],
        )
        derived_objects = [
            {
                "object_id": member["object_id"],
                "title": existing_object_rows.get(member["object_id"], member["title"]),
            }
            for member in item["derived_objects"]
            if member["object_id"] in existing_object_rows
        ]
        preview_titles = [member["title"] for member in derived_objects[:5]]
        provenance_map = get_object_provenance_map(vault_dir, [member["object_id"] for member in derived_objects])
        atlas_page_map: dict[str, dict[str, str]] = {}
        for provenance in provenance_map.values():
            for atlas_page in provenance["mocs"]:
                atlas_page_map.setdefault(atlas_page["slug"], atlas_page)
        source_notes = get_note_traceability(vault_dir, note_path=item["path"])["source_notes"]
        enriched_items.append(
            {
                **item,
                "derived_objects": derived_objects,
                "derived_object_count": len(derived_objects),
                "preview_titles": preview_titles,
                "atlas_pages": list(atlas_page_map.values()),
                "source_notes": source_notes,
            }
        )
    return {
        "screen": "derivations/browser",
        "items": enriched_items,
        "count": len(enriched_items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
    }


def build_production_browser_payload(vault_dir: Path | str, *, query: str | None = None) -> dict[str, Any]:
    items = list_production_chains(vault_dir, query=query, limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT)
    source_items = [item for item in items if item["stage_label"] == "source_note"]
    deep_dive_items = [item for item in items if item["stage_label"] == "deep_dive"]
    weak_points = _build_production_weak_points(vault_dir, query=query)
    return {
        "screen": "production/browser",
        "items": items,
        "source_items": source_items,
        "deep_dive_items": deep_dive_items,
        "weak_points": weak_points,
        "count": len(items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
        "counts": {
            "source_notes": len(source_items),
            "deep_dives": len(deep_dive_items),
        },
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
        "production_chain": get_note_traceability(vault_dir, note_path=note_path),
    }
