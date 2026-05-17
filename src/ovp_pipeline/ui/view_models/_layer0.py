# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *




def _access_projection_label(
    *,
    surface: str,
    pack_name: str | None,
    generated_by: str,
    derived_from: tuple[str, ...] = ("knowledge.db",),
    rebuild_policy: str = "read_time",
) -> dict[str, object]:
    return projection_label(
        surface=surface,
        projection_kind="access_surface",
        layer="Layer 3",
        owner_pack=pack_name or PRIMARY_PACK_NAME,
        generated_by=generated_by,
        derived_from=derived_from,
        rebuild_policy=rebuild_policy,
    )



def _assembly_contract(recipe_name: str, *, pack_name: str | None = None) -> dict[str, str]:
    return describe_assembly_recipe_contract(pack_name=pack_name, recipe_name=recipe_name)



def _audit_row_pack(payload: dict[str, Any]) -> str | None:
    """Pack recorded in the audit payload, or None for legacy rows
    that predate pack stamping."""
    pack = payload.get("pack")
    return str(pack) if pack else None



def _bridge_kind_display_name(bridge_kind: str) -> str:
    if bridge_kind == "source_and_atlas_overlap":
        return "Source + Atlas Overlap"
    if bridge_kind == "source_overlap":
        return "Source Overlap"
    if bridge_kind == "atlas_overlap":
        return "Atlas Overlap"
    return bridge_kind.replace("_", " ").title()



def _briefing_value_actionability(item: dict[str, Any]) -> str:
    recommended_action = item.get("recommended_action")
    if not isinstance(recommended_action, dict):
        return "review"
    queue_status = str(recommended_action.get("queue_status") or "").strip().lower()
    if queue_status in {"queued", "running", "pending", "scheduled", "in_progress"}:
        return "queued"
    if bool(recommended_action.get("executable")):
        return "executable"
    return "review"



def _briefing_value_evidence_count(item: dict[str, Any]) -> int:
    evidence: set[str] = set()
    for key in ("source_paths", "note_paths", "object_ids"):
        value = item.get(key)
        if isinstance(value, list):
            evidence.update(str(entry) for entry in value if str(entry or "").strip())
    for key in ("signal_id", "path"):
        value = str(item.get(key) or "").strip()
        if value:
            evidence.add(value)
    return len(evidence)



def _build_production_summary(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    object_traceability = [
        get_object_traceability(vault_dir, object_id, pack_name=pack_name)
        for object_id in normalized_object_ids
    ]
    source_note_counts: Counter[str] = Counter()
    atlas_page_counts: Counter[str] = Counter()
    source_note_items: dict[str, dict[str, str]] = {}
    atlas_page_items: dict[str, dict[str, str]] = {}
    missing_source_object_ids: list[str] = []
    missing_atlas_object_ids: list[str] = []

    for traceability in object_traceability:
        object_id = traceability["object"]["object_id"]
        if not traceability["source_notes"]:
            missing_source_object_ids.append(object_id)
        if not traceability["atlas_pages"]:
            missing_atlas_object_ids.append(object_id)
        for item in traceability["source_notes"]:
            source_note_items.setdefault(item["path"], item)
            source_note_counts[item["path"]] += 1
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
            "atlas_pages": len(atlas_page_items),
        },
        "top_source_notes": _top_items(source_note_counts, source_note_items),
        "top_atlas_pages": _top_items(atlas_page_counts, atlas_page_items),
        "signals": signals,
    }



def _build_production_weak_points(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    return list_production_gaps(vault_dir, pack_name=pack_name, query=query, limit=limit)



def _build_reading_routes(related_clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route_specs = [
        (
            "full_context_route",
            "Full Context Route",
            {"source_and_atlas_overlap"},
        ),
        (
            "source_continuity_route",
            "Source Continuity Route",
            {"source_and_atlas_overlap", "source_overlap"},
        ),
        (
            "atlas_continuity_route",
            "Atlas Continuity Route",
            {"source_and_atlas_overlap", "atlas_overlap"},
        ),
    ]
    routes: list[dict[str, Any]] = []
    for index, (route_kind, display_name, allowed_bridge_kinds) in enumerate(route_specs, start=1):
        candidate = next(
            (item for item in related_clusters if str(item["bridge_kind"]) in allowed_bridge_kinds),
            None,
        )
        if candidate is None:
            continue
        if route_kind == "full_context_route":
            route_reason = (
                "Best first if you want both evidence continuity and atlas continuity across clusters."
            )
            route_score = int(candidate["score"]) + 30
        elif route_kind == "source_continuity_route":
            route_reason = "Best if you want to keep reading along shared source-note coverage."
            route_score = int(candidate["score"]) + 20
        else:
            route_reason = "Best if you want to keep reading along shared atlas-page coverage."
            route_score = int(candidate["score"]) + 10
        routes.append(
            {
                "route_kind": route_kind,
                "route_rank": index,
                "route_score": route_score,
                "display_name": display_name,
                "cluster_id": candidate["cluster_id"],
                "display_title": candidate["display_title"],
                "detail_path": candidate["detail_path"],
                "bridge_kind": candidate["bridge_kind"],
                "bridge_band": candidate["bridge_band"],
                "reason": candidate["reason"],
                "route_reason": route_reason,
            }
        )
    return routes



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



def _build_timeline_event_item(row: tuple[Any, ...] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        payload = json.loads(str(row.get("payload_json") or "{}"))
        event_date = str(row.get("event_date") or "")
        event_type = str(row.get("event_type") or "")
        heading = str(row.get("heading") or "").strip()
        object_id = str(row.get("object_id") or "")
        title = str(row.get("title") or object_id)
        summary_text = str(row.get("summary_text") or "")
        row_pack = str(row.get("row_pack") or "")
    else:
        payload = json.loads(row[3] or "{}")
        event_date = str(row[0] or "")
        event_type = str(row[1])
        heading = str(row[2] or "").strip()
        object_id = str(row[4])
        title = str(row[5])
        summary_text = str(row[6] or "")
        row_pack = ""
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
        "event_date": event_date,
        "event_type": event_type,
        "row_type": event_type,
        "event_kind": event_kind,
        "event_label": event_label,
        "semantic_role": semantic_role,
        "timeline_anchor_kind": timeline_anchor_kind,
        "timeline_anchor_label": timeline_anchor_label,
        "object_id": object_id,
        "title": title,
        "summary_text": summary_text,
        "row_pack": row_pack,
    }



def _capture_status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(
            str((item.get("capture_summary") or {}).get("status") or "missing")
            for item in items
        )
    )



def _clean_excerpt_line(line: str) -> str:
    return _LIST_MARKER_RE.sub("", line.strip()).strip()



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
                "grouping_kind": "object_date_rollup",
                "event_vs_note_explanation": (
                    "This cluster groups timeline rows for the same object and date; "
                    "it is a dossier rollup, not a canonical event entity."
                ),
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



def _compiled_section(
    section_id: str,
    label: str,
    *,
    summary: str,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_items = list(items or [])
    return {
        "id": section_id,
        "label": label,
        "anchor": section_id.replace("_", "-"),
        "summary": summary,
        "item_count": len(normalized_items),
        "items": normalized_items,
    }



def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db



def _edge_kind_parts(edge_kind: str) -> tuple[str, str]:
    family, sep, subtype = str(edge_kind).partition(":")
    if not sep:
        return (family, "")
    return (family, subtype)



def _emit_briefing_reuse(
    vault_dir: Path | str,
    payload: dict[str, Any],
    *,
    pack: str,
    consumer_ref: str,
) -> None:
    """Emit one ``briefing``-surface reuse event per canonical object in payload.

    Reuse-event emission is best-effort instrumentation — a JSONL append
    failure must never block the view-builder from returning a payload.
    """
    if not pack:
        return
    try:
        object_ids = collect_object_ids(payload)
        if not object_ids:
            return
        emit_reuse_events_for_object_ids(
            vault_dir,
            pack=pack,
            object_ids=object_ids,
            surface="briefing",
            consumer_ref=consumer_ref,
        )
    except Exception:  # noqa: BLE001 — best-effort instrumentation
        return



def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")



def _event_types_for_card(card: dict[str, Any]) -> tuple[str, ...]:
    """Compose the event_type list for a hybrid card's secondary
    count.  Starts from the card's declared categories, adds any
    ``include_event_types``, removes any ``exclude_event_types``.
    """
    result: list[str] = []
    seen: set[str] = set()
    for cat in card.get("categories", ()):
        for et in _evt_for_category(cat):
            if et not in seen:
                seen.add(et)
                result.append(et)
    for et in card.get("include_event_types", ()):
        if et not in seen:
            seen.add(et)
            result.append(et)
    excluded = set(card.get("exclude_event_types", ()))
    return tuple(et for et in result if et not in excluded)



def _impact_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(
            str((item.get("impact_summary") or {}).get("impact_status") or "unknown")
            for item in items
        )
    )



def _items_primary_href(
    item_kind: str | None,
    item_id: str | None,
    pack: str,
    *,
    source_path: str = "",
) -> str:
    """Map (kind, id) → the canonical drilldown URL.

    * ``source``  → ``/note?path=<vault-relative-path>`` when the
      caller resolved the path via ``pages_index``; otherwise
      empty string (renderer falls back to a plain non-link cell).
      We DON'T link to the future M25.4 ``/ops/events/audit``
      route because that doesn't exist yet — clicking would 404
      (codex review on PR #236 flagged this).
    * ``object``  → ``/object?id=…``  (existing route).
    * ``cluster`` → ``/ops/cluster?id=…`` (existing route).
    """
    if not item_id:
        return ""
    kind = (item_kind or "").lower()
    pack_qs = f"&pack={quote(pack, safe='')}" if pack else ""
    if kind == "object":
        return f"/object?id={quote(str(item_id), safe='')}{pack_qs}"
    if kind == "cluster":
        return f"/ops/cluster?id={quote(str(item_id), safe='')}{pack_qs}"
    if kind == "source" and source_path:
        return f"/note?path={quote(str(source_path), safe='')}"
    # No known drilldown — return empty so the renderer surfaces
    # the item as plain text rather than a broken link.  M25.4
    # adds the raw-audit-evidence view that will pick this up.
    return ""



def _jsonl_latest_ts(jsonl_path: Path):
    """Newest audit timestamp in ``pipeline.jsonl`` WITHOUT a full
    read (BL-108 debt): the log is append-only so the last non-empty
    line is the newest event.  Tail ~64KB, walk lines from the end,
    return the first that parses.  None if unreadable/unparseable."""
    try:
        size = jsonl_path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", "ignore")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp") or row.get("ts")
        parsed = _parse_audit_ts(str(ts or ""))
        if parsed is not None:
            return parsed
    return None



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



def _object_kind_label(object_kind: str) -> str:
    from ovp_pipeline.object_kinds import display_label, normalize_kind

    raw = (object_kind or "").strip().lower()
    if not raw:
        return "Object"
    return display_label(normalize_kind(raw))



def _object_scope_paths(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, str]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {}
    rows = _batch_object_rows(vault_dir, normalized_object_ids, pack_name=pack_name)
    return {
        str(object_id): str(rows.get(object_id, {}).get("canonical_path") or "")
        for object_id in normalized_object_ids
    }



def _operator_action(label: str, path: str, detail: str) -> dict[str, str]:
    return {
        "label": label,
        "path": path,
        "detail": detail,
    }



def _plural_reader_label(label: str) -> str:
    if len(label) >= 2 and label.endswith("y") and label[-2].lower() not in "aeiou":
        return f"{label[:-1]}ies"
    if label.endswith("s"):
        return label
    return f"{label}s"



def _relation_pattern_preview(relation_pattern_items: list[dict[str, Any]]) -> str:
    if not relation_pattern_items:
        return ""
    preview_items = relation_pattern_items[:2]
    preview = ", ".join(f"{item['display_name']} ({item['count']})" for item in preview_items)
    if len(relation_pattern_items) > 2:
        return f"{preview}, +{len(relation_pattern_items) - 2} more"
    return preview



def _scoped_path(path: str, *, pack_name: str | None = None) -> str:
    if not pack_name:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}pack={quote(pack_name, safe='')}"



def _search_match_reason(
    *,
    query: str,
    title: str,
    summary: str,
    evidence_count: int,
) -> str:
    normalized_query = query.strip().lower()
    title_match = bool(normalized_query and normalized_query in title.lower())
    summary_match = bool(normalized_query and normalized_query in summary.lower())
    if title_match and summary_match and evidence_count > 0:
        return "Matched title, summary, and evidence-backed claims."
    if title_match and evidence_count > 0:
        return "Matched title and evidence-backed claims."
    if summary_match and evidence_count > 0:
        return "Matched summary and evidence-backed claims."
    if title_match:
        return "Matched title."
    if summary_match:
        return "Matched summary."
    if evidence_count > 0:
        return "Matched evidence-backed claims."
    return "Matched object text."



def _section_nav_from_compiled_sections(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "href": f"#{str(section.get('anchor') or str(section.get('id') or '').replace('_', '-'))}",
            "label": str(section.get("label") or section.get("id") or ""),
        }
        for section in sections
    ]



def _source_identity(slug: str, payload: dict[str, Any]) -> str | None:
    """Source-class distinct identity: the populated ``slug`` column
    if present, else derived from the payload exactly as ingest's
    ``_infer_audit_slug`` would (``file`` / ``source`` / ``path``
    basename).  The ``slug`` column is only ~60% backfilled on the
    live vault (M24 PR-B), so relying on it alone would silently
    drop ~40% of source rows from the count."""
    s = (slug or "").strip()
    if s:
        return s
    derived = audit_slug_for_column(payload)
    return derived or None



def _supports_research_shell(pack_name: str | None = None) -> bool:
    try:
        return any(pack.name == PRIMARY_PACK_NAME for pack in iter_compatible_packs(pack_name))
    except ValueError:
        return False



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



def _workflow_group(
    group_id: str,
    title: str,
    summary: str,
    items: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": group_id,
        "title": title,
        "summary": summary,
        "items": items,
    }



def _zero_reason_for_card(
    state: str,
    stage_runs: dict[str, dict[str, Any]],
    staleness: dict[str, Any],
) -> tuple[str, str]:
    """BL-103b: why is this card 0?  Returns (reason, detail).

    A zero is only meaningful with a reason — staleness first (the
    numbers may simply be behind), then the stage-run ledger.
    """
    if state == "NeedsAction":
        return ("healthy", "No blockers recorded on this day.")
    if staleness.get("audit_sync_stale"):
        return (
            "audit_sync_stale",
            "Audit sync is behind pipeline.jsonl — run "
            "`ovp-refresh-ops`; the real count may be non-zero.",
        )
    if staleness.get("projection_stale"):
        return (
            "projection_stale",
            "Lifecycle projection is older than synced audit — run "
            "`ovp-ops-state --rebuild`.",
        )
    feeding = _STATE_FEEDING_STAGES.get(state, frozenset())
    relevant = {s: stage_runs[s] for s in feeding if s in stage_runs}
    if not relevant:
        joined = ", ".join(sorted(feeding)) or "the feeding stage"
        return (
            "not_run",
            f"No run recorded for {joined} on this day.",
        )
    statuses = {r["status"] for r in relevant.values()}
    if "failed" in statuses:
        return (
            "failed",
            "A feeding stage failed before completing on this day.",
        )
    completed = [
        r for r in relevant.values() if r["status"] == "completed"
    ]
    if completed:
        in_known = [
            r["input"] for r in completed if isinstance(r["input"], int)
        ]
        out_known = [
            r["output"] for r in completed if isinstance(r["output"], int)
        ]
        if in_known and max(in_known) == 0:
            return (
                "ran_no_input",
                "The feeding stage ran but found zero eligible "
                "inputs on this day.",
            )
        if in_known and max(in_known) > 0 and out_known and max(out_known) == 0:
            return (
                "ran_no_output",
                "The feeding stage ran inputs but produced zero "
                "outputs on this day.",
            )
        if out_known and max(out_known) > 0:
            return (
                "telemetry_missing",
                "A feeding stage reported output but no evidence "
                "projected here — projection may be stale.",
            )
    if "skipped" in statuses:
        return (
            "not_run",
            "The feeding stage was skipped on this day.",
        )
    return ("unknown", "Run status unknown for this day.")


__all__ = [
    '_access_projection_label',
    '_assembly_contract',
    '_audit_row_pack',
    '_bridge_kind_display_name',
    '_briefing_value_actionability',
    '_briefing_value_evidence_count',
    '_build_production_summary',
    '_build_production_weak_points',
    '_build_reading_routes',
    '_build_relation_pattern_items',
    '_build_timeline_event_item',
    '_capture_status_counts',
    '_clean_excerpt_line',
    '_cluster_timeline_events',
    '_compiled_section',
    '_db_path',
    '_edge_kind_parts',
    '_emit_briefing_reuse',
    '_escape_like',
    '_event_types_for_card',
    '_impact_counts',
    '_items_primary_href',
    '_jsonl_latest_ts',
    '_object_ids_from_claim_ids',
    '_object_kind_label',
    '_object_scope_paths',
    '_operator_action',
    '_plural_reader_label',
    '_relation_pattern_preview',
    '_scoped_path',
    '_search_match_reason',
    '_section_nav_from_compiled_sections',
    '_source_identity',
    '_supports_research_shell',
    '_top_counter_items',
    '_workflow_group',
    '_zero_reason_for_card'
]
