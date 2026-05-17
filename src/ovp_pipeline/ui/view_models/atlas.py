# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *




def _atlas_community_slug(name: str) -> str:
    """Lower-kebab slug, used for legend stable ids in the kit."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "community"



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



def _build_cluster_provenance_index(
    vault_dir: Path | str,
    cluster_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(row["cluster_id"]): _collect_cluster_provenance(
            vault_dir,
            [str(member["object_id"]) for member in row["members"]],
        )
        for row in cluster_rows
    }



def _build_related_cluster_items(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    requested_pack: str,
    current_source_note_items: dict[str, dict[str, Any]],
    current_moc_items: dict[str, dict[str, Any]],
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    current_source_slugs = set(current_source_note_items)
    current_moc_slugs = set(current_moc_items)
    if not current_source_slugs and not current_moc_slugs:
        return []
    related_items: list[dict[str, Any]] = []
    rows = cluster_rows if cluster_rows is not None else list_graph_clusters(vault_dir, pack_name=requested_pack, limit=200)
    for row in rows:
        if str(row["cluster_id"]) == cluster_id:
            continue
        provenance = None
        if cluster_provenance_index is not None:
            provenance = cluster_provenance_index.get(str(row["cluster_id"]))
        if provenance is None:
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
        if shared_source_slugs and shared_moc_slugs:
            bridge_kind = "source_and_atlas_overlap"
        elif shared_source_slugs:
            bridge_kind = "source_overlap"
        else:
            bridge_kind = "atlas_overlap"
        if len(shared_source_slugs) >= 1 and len(shared_moc_slugs) >= 1:
            bridge_band = "strong"
        elif len(shared_source_slugs) >= 1 or len(shared_moc_slugs) >= 2:
            bridge_band = "medium"
        else:
            bridge_band = "light"
        related_items.append(
            {
                "cluster_id": str(row["cluster_id"]),
                "pack": requested_pack,
                "label": str(row["label"]),
                "display_title": f"Cluster around {row['center_title']}",
                "detail_path": (
                    f"/ops/cluster?id={quote(str(row['cluster_id']), safe='')}"
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
                "bridge_kind": bridge_kind,
                "bridge_band": bridge_band,
                "reason": ", ".join(reason_parts),
                "score": score,
            }
        )
    related_items.sort(key=lambda item: (-item["score"], item["label"].lower(), item["cluster_id"]))
    return related_items[:5]



def _build_related_cluster_groups(related_clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in related_clusters:
        group = grouped.setdefault(
            str(item["bridge_kind"]),
            {
                "bridge_kind": str(item["bridge_kind"]),
                "display_name": _bridge_kind_display_name(str(item["bridge_kind"])),
                "count": 0,
                "cluster_titles": [],
            },
        )
        group["count"] += 1
        if item["display_title"] not in group["cluster_titles"]:
            group["cluster_titles"].append(item["display_title"])
    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["count"]), str(item["bridge_kind"])),
    )



def _build_cluster_surface_sections(
    vault_dir: Path | str,
    *,
    cluster: dict[str, Any],
    edges: list[dict[str, Any]],
    requested_pack: str,
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    member_object_ids = [str(member["object_id"]) for member in cluster["members"]]
    edge_kind_counts = Counter(edge["edge_kind"] for edge in edges)
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
    review_context = get_review_context(vault_dir, member_object_ids, pack_name=requested_pack)
    open_contradictions = [
        {
            "contradiction_id": item["contradiction_id"],
            "subject_key": item["subject_key"],
            "object_ids": _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"]),
            "path": f"/ops/contradictions?q={quote(str(item['subject_key']), safe='')}",
        }
        for item in list_contradictions(
            vault_dir,
            pack_name=requested_pack,
            status="open",
            limit=MAX_PAGE_SIZE,
        )
        if set(_object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])) & set(member_object_ids)
    ][:5]
    stale_summaries = list_stale_summaries(
        vault_dir,
        pack_name=requested_pack,
        object_ids=member_object_ids,
        limit=5,
    )
    provenance = (
        cluster_provenance_index.get(str(cluster["cluster_id"]))
        if cluster_provenance_index is not None and str(cluster["cluster_id"]) in cluster_provenance_index
        else _collect_cluster_provenance(vault_dir, member_object_ids)
    )
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
            f"{len(edges)} internal edges across {len(edge_kind_counts)} edge kinds; dominant edge kind is {top_edge_kind[0]} ({top_edge_kind[1]})."
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
        cluster_rows=cluster_rows,
        cluster_provenance_index=cluster_provenance_index,
    )
    related_cluster_groups = _build_related_cluster_groups(related_clusters)
    reading_routes = _build_reading_routes(related_clusters)
    next_read_cluster = related_clusters[0] if related_clusters else None

    return {
        "display_title": structural_label["title"],
        "edge_count": len(edges),
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
        "related_cluster_groups": related_cluster_groups,
        "reading_routes": reading_routes,
        "next_read_cluster": next_read_cluster,
        "top_source_notes": _top_counter_items(source_note_counts, source_note_items),
        "top_mocs": _top_counter_items(moc_counts, moc_items),
        "summary_bullets": summary_bullets,
    }



def build_cluster_summary_payload(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    pack_name: str | None = None,
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    detail = get_graph_cluster_detail(vault_dir, cluster_id, pack_name=pack_name)
    cluster = detail["cluster"]
    requested_pack = pack_name or str(cluster["pack"])
    member_index = {str(member["object_id"]): member for member in cluster["members"]}
    detail_path = (
        f"/ops/cluster?id={quote(str(cluster['cluster_id']), safe='')}"
        f"&pack={quote(requested_pack, safe='')}"
    )
    enriched_cluster = {
        **cluster,
        "detail_path": detail_path,
        "center_object_path": _scoped_path(
            f"/object?id={quote(str(cluster['center_object_id']), safe='')}",
            pack_name=requested_pack,
        ),
        "member_links": [
            {
                **member,
                "path": _scoped_path(
                    f"/object?id={quote(str(member['object_id']), safe='')}",
                    pack_name=requested_pack,
                ),
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
            "source_path": _scoped_path(
                f"/object?id={quote(str(edge['source_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "target_path": _scoped_path(
                f"/object?id={quote(str(edge['target_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for edge in detail["edges"]
    ]
    sections = _build_cluster_surface_sections(
        vault_dir,
        cluster=enriched_cluster,
        edges=enriched_edges,
        requested_pack=requested_pack,
        cluster_rows=cluster_rows,
        cluster_provenance_index=cluster_provenance_index,
    )
    return {
        "requested_pack": requested_pack,
        "cluster": enriched_cluster,
        "edges": enriched_edges,
        **sections,
    }



def build_topic_overview_payload(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    research_shell_enabled = _supports_research_shell(pack_name)
    neighborhood = get_topic_neighborhood(vault_dir, object_id, pack_name=pack_name)
    detail = get_object_detail(vault_dir, object_id, pack_name=pack_name)
    scoped_object_ids = [object_id, *[item["object_id"] for item in neighborhood["neighbors"]]]
    review_context = (
        get_review_context(
            vault_dir,
            scoped_object_ids,
            pack_name=pack_name,
        )
        if research_shell_enabled
        else {}
    )
    scoped_stale_summaries = (
        list_stale_summaries(
            vault_dir,
            pack_name=pack_name,
            object_ids=scoped_object_ids,
            limit=50,
        )
        if research_shell_enabled
        else []
    )
    scoped_contradictions = (
        [
            item
            for item in list_contradictions(vault_dir, pack_name=pack_name, limit=100)
            if set(item["positive_claim_ids"] + item["negative_claim_ids"])
            and any(claim_id.split("::", 1)[0] in set(scoped_object_ids) for claim_id in item["positive_claim_ids"] + item["negative_claim_ids"])
            and item["status"] == "open"
        ]
        if research_shell_enabled
        else []
    )
    neighbors = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in neighborhood["neighbors"]
    ]
    production_summary = _build_production_summary(
        vault_dir,
        scoped_object_ids,
        pack_name=pack_name,
    )
    research_links = {
        "events_path": _scoped_path(f"/ops/events?q={quote(object_id, safe='')}", pack_name=requested_pack),
        "contradictions_path": _scoped_path(
            f"/ops/contradictions?q={quote(object_id, safe='')}",
            pack_name=requested_pack,
        ),
        "summaries_path": _scoped_path(
            f"/ops/summaries?q={quote(object_id, safe='')}",
            pack_name=requested_pack,
        ),
        "atlas_path": _scoped_path(f"/atlas?q={quote(object_id, safe='')}", pack_name=requested_pack),
    } if research_shell_enabled else {
        "events_path": "",
        "contradictions_path": "",
        "summaries_path": "",
        "atlas_path": "",
    }
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=f"{len(neighbors)} neighbors and {len(neighborhood['edges'])} edges define this topic view.",
            items=[
                {
                    "kind": "center_object",
                    "label": detail["object"]["title"],
                    "path": _scoped_path(f"/object?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": detail["summary"]["summary_text"] if detail["summary"] else "No compiled summary yet.",
                },
                *[
                    {
                        "kind": "neighbor",
                        "label": item["title"],
                        "path": item["object_path"],
                        "detail": "Neighbor in current topic scope",
                    }
                    for item in neighbors[:3]
                ],
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=f"{review_context.get('open_contradiction_count', 0)} contradictions and {review_context.get('stale_summary_count', 0)} stale summaries currently shape this topic.",
            items=[
                *(
                    [
                        {
                            "kind": "events",
                            "label": "Event dossier",
                            "path": research_links["events_path"],
                            "detail": "See time-bounded activity around this topic.",
                        },
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary=f"{len(detail['provenance']['source_notes'])} source notes and {len(detail['provenance']['mocs'])} atlas pages anchor this topic.",
            items=[
                *[
                    {
                        "kind": "source_note",
                        "label": item["title"],
                        "path": _scoped_path(f"/note?path={quote(item['path'], safe='')}", pack_name=requested_pack),
                        "detail": item["note_type"],
                    }
                    for item in detail["provenance"]["source_notes"][:3]
                ],
                *[
                    {
                        "kind": "atlas_page",
                        "label": item["title"],
                        "path": _scoped_path(f"/note?path={quote(item['path'], safe='')}", pack_name=requested_pack),
                        "detail": "Atlas / MOC",
                    }
                    for item in detail["provenance"]["mocs"][:3]
                ],
            ],
        ),
        _compiled_section(
            "production_chain",
            "Production Chain",
            summary=(
                f"{production_summary['object_count']} objects in scope currently resolve to "
                f"{production_summary['counts']['source_notes']} source notes and "
                f"{production_summary['counts']['atlas_pages']} atlas pages."
            ),
            items=[
                *[
                    {
                        "kind": "top_atlas_page",
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(item['path'], safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"Reaches {item['object_count']} objects in this topic scope.",
                    }
                    for item in production_summary["top_atlas_pages"][:2]
                ],
                *[
                    {
                        "kind": "gap_signal",
                        "label": item["label"],
                        "path": _scoped_path(
                            f"/ops/production?q={quote(object_id, safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"{item['count']} objects in this topic scope.",
                    }
                    for item in production_summary["signals"][:3]
                ],
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{len(scoped_contradictions)} open contradictions and {len(scoped_stale_summaries)} stale summaries remain in this topic scope.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": research_links["contradictions_path"],
                        "detail": item["status"],
                    }
                    for item in scoped_contradictions[:3]
                ],
                *[
                    {
                        "kind": "stale_summary",
                        "label": item["title"],
                        "path": research_links["summaries_path"],
                        "detail": ", ".join(item["reason_texts"]),
                    }
                    for item in scoped_stale_summaries[:2]
                ],
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Jump from the topic hub into the most useful next compiled products.",
            items=[
                {
                    "kind": "center_object",
                    "label": "Center object",
                    "path": _scoped_path(f"/object?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": "Open the canonical object page.",
                },
                *(
                    [
                        {
                            "kind": "contradictions",
                            "label": "Contradictions",
                            "path": research_links["contradictions_path"],
                            "detail": "Review open tensions.",
                        },
                        {
                            "kind": "atlas",
                            "label": "Atlas / MOC",
                            "path": research_links["atlas_path"],
                            "detail": "Open atlas reach.",
                        },
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Center object",
            _scoped_path(f"/object?id={quote(object_id, safe='')}", pack_name=requested_pack),
            "Open the canonical object page.",
        ),
        _operator_action(
            "Event dossier" if research_shell_enabled else "Signals",
            research_links["events_path"] if research_shell_enabled else _scoped_path("/ops/signals", pack_name=requested_pack),
            (
                "See time-bounded activity around this topic."
                if research_shell_enabled
                else "Review active shell signals."
            ),
        ),
        _operator_action(
            "Contradictions" if research_shell_enabled else "Search",
            research_links["contradictions_path"] if research_shell_enabled else _scoped_path("/search", pack_name=requested_pack),
            (
                "Review open tensions in topic scope."
                if research_shell_enabled
                else "Search laterally from this topic."
            ),
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect production-chain weak points in the current shell.",
        ),
    ]
    payload: dict[str, Any] = {
        "screen": "overview/topic",
        "requested_pack": requested_pack,
        "assembly_contract": _assembly_contract("topic_overview", pack_name=pack_name),
        "research_shell_enabled": research_shell_enabled,
        **neighborhood,
        "neighbors": neighbors,
        "edge_count": len(neighborhood["edges"]),
        "neighbor_count": len(neighbors),
        "center_summary": detail["summary"]["summary_text"] if detail["summary"] else "",
        "provenance": detail["provenance"],
        "production_summary": production_summary,
        "review_context": review_context,
        "review_history": (
            list_review_actions(
                vault_dir,
                object_ids=scoped_object_ids,
                limit=8,
            )
            if research_shell_enabled
            else []
        ),
        "evolution": (
            _build_evolution_section(
                vault_dir,
                pack_name=pack_name,
                status="all",
                scoped_object_ids=scoped_object_ids,
            )
            if research_shell_enabled
            else {
                "accepted_links": [],
                "rejected_links": [],
                "candidate_items": [],
                "candidate_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "link_types": [],
                "status": "all",
            }
        ),
        "scoped_object_ids": scoped_object_ids,
        "scoped_stale_summary_ids": [item["object_id"] for item in scoped_stale_summaries],
        "scoped_open_contradiction_ids": [item["contradiction_id"] for item in scoped_contradictions],
        "links": {
            "center_object_path": _scoped_path(
                f"/object?id={quote(object_id, safe='')}",
                pack_name=requested_pack,
            ),
            **research_links,
        },
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }
    _emit_briefing_reuse(
        vault_dir,
        payload,
        pack=str((neighborhood.get("center") or {}).get("row_pack") or requested_pack),
        consumer_ref=f"view:topic_overview:{object_id}",
    )
    return payload



def build_cluster_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_TRACEABILITY_BROWSER_LIMIT,
    show_all: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    # ``show_all`` lifts the display cap so the operator can audit the
    # full cluster set when they need to.  We still cap at MAX_PAGE_SIZE
    # to protect renderer cost; a vault with 10k+ clusters would still
    # be unworkable, so warn at the call site rather than render forever.
    #
    # ``show_all`` and ``offset`` are mutually exclusive — Show all
    # always starts from cluster #0; an explicit offset only paginates
    # within the per-page limit.
    effective_limit = MAX_PAGE_SIZE if show_all else limit
    effective_offset = 0 if show_all else max(0, int(offset or 0))
    total_count = count_graph_clusters(vault_dir, pack_name=pack_name, query=query)
    items = list_graph_clusters(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=effective_limit,
        offset=effective_offset,
    )
    cluster_provenance_index = _build_cluster_provenance_index(vault_dir, items)
    cluster_kind_counts = Counter(item["cluster_kind"] for item in items)
    largest_cluster_size = max((int(item["member_count"]) for item in items), default=0)
    enriched_items = []
    for item in items:
        requested_pack = pack_name or str(item["pack"])
        summary = build_cluster_summary_payload(
            vault_dir,
            cluster_id=str(item["cluster_id"]),
            pack_name=requested_pack,
            cluster_rows=items,
            cluster_provenance_index=cluster_provenance_index,
        )
        review_context = summary["review_context"]
        dominant_edge_kind = next(
            iter(
                sorted(
                    summary["edge_kind_counts"].items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ),
            None,
        )
        priority_score = (
            review_context["open_contradiction_count"] * 100
            + review_context["stale_summary_count"] * 40
            + int(item["member_count"]) * 10
            + int(summary["edge_count"]) * 3
            + review_context["source_note_count"]
            + review_context["moc_count"]
        )
        if summary["reading_routes"]:
            priority_score += 15
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
        strongest_related = summary["related_clusters"][0] if summary["related_clusters"] else None
        top_reading_route = summary["reading_routes"][0] if summary["reading_routes"] else None
        enriched_items.append(
            {
                **item,
                "row_pack": str(item.get("row_pack") or item["pack"]),
                "pack": requested_pack,
                "detail_path": summary["cluster"]["detail_path"],
                "center_object_path": summary["cluster"]["center_object_path"],
                "member_links": summary["cluster"]["member_links"],
                "display_title": summary["display_title"],
                "relation_pattern_preview": summary["relation_pattern_preview"],
                "related_cluster_count": len(summary["related_clusters"]),
                "related_cluster_preview": ", ".join(
                    related["display_title"] for related in summary["related_clusters"][:2]
                ),
                "neighborhood_score": strongest_related["score"] if strongest_related else 0,
                "neighborhood_reason": strongest_related["reason"] if strongest_related else "",
                "neighborhood_band": strongest_related["bridge_band"] if strongest_related else "",
                "neighborhood_bridge_kind": strongest_related["bridge_kind"] if strongest_related else "",
                "next_read_title": strongest_related["display_title"] if strongest_related else "",
                "next_read_path": strongest_related["detail_path"] if strongest_related else "",
                "next_read_reason": strongest_related["reason"] if strongest_related else "",
                "top_reading_route_kind": top_reading_route["route_kind"] if top_reading_route else "",
                "top_reading_route_title": top_reading_route["display_title"] if top_reading_route else "",
                "top_reading_route_reason": top_reading_route["route_reason"] if top_reading_route else "",
                "has_reading_route": bool(top_reading_route),
                "reading_intent_count": len(summary["reading_routes"]),
                "reading_intent_preview": ", ".join(
                    route["display_name"] for route in summary["reading_routes"]
                ),
                "summary_bullets": summary["summary_bullets"],
                "structural_label": summary["structural_label"],
                "edge_kind_counts": summary["edge_kind_counts"],
                "edge_summary_items": summary["edge_summary_items"],
                "edge_count": summary["edge_count"],
                "relation_pattern_items": summary["relation_pattern_items"],
                "review_context": summary["review_context"],
                "open_contradictions": summary["open_contradictions"],
                "stale_summaries": summary["stale_summaries"],
                "related_clusters": summary["related_clusters"],
                "related_cluster_groups": summary["related_cluster_groups"],
                "reading_routes": summary["reading_routes"],
                "next_read_cluster": summary["next_read_cluster"],
                "top_source_notes": summary["top_source_notes"],
                "top_mocs": summary["top_mocs"],
                "object_kind_counts": summary["object_kind_counts"],
                "priority_score": priority_score,
                "priority_band": priority_band,
                "priority_reason": priority_reason,
                "top_summary_bullet": summary["summary_bullets"][0] if summary["summary_bullets"] else "",
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
        "projection_label": _access_projection_label(
            surface="graph_clusters",
            pack_name=pack_name,
            generated_by="build_cluster_browser_payload",
            derived_from=("knowledge.db.graph_clusters", "knowledge.db.graph_edges"),
        ),
        "query": query or "",
        "limit": effective_limit,
        "default_limit": limit,
        "offset": effective_offset,
        "show_all": bool(show_all),
        "total_count": total_count,
        # Compute truncation from actual counts so show_all=True
        # doesn't silently report "complete" while still capped at
        # MAX_PAGE_SIZE on a vault with > MAX_PAGE_SIZE clusters.
        "is_limited": total_count > len(enriched_items),
        "items": enriched_items,
        "count": len(enriched_items),
        "cluster_kind_counts": dict(cluster_kind_counts),
        "largest_cluster_size": largest_cluster_size,
        "model_notes": [
            "Graph clusters currently come from pack-owned graph seed projections, not from a final semantic clustering model.",
            "Current research-tech clusters are relation/contradiction connected components over pack-scoped truth rows.",
        ],
    }



def _clamp_graph_coordinate(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))



def build_graph_map_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_GRAPH_MAP_LIMIT,
    show_all: bool = False,
    member_cap: int = DEFAULT_GRAPH_MAP_MEMBER_CAP,
) -> dict[str, Any]:
    cluster_payload = build_cluster_browser_payload(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=limit,
    )
    clusters = cluster_payload["items"]
    # BL-051: cap each cluster's members in the visual map (full
    # list still reachable via ``/ops/clusters`` and ``/ops/cluster``).
    # ``show_all`` lifts the cap.
    if not show_all and member_cap > 0:
        for cluster in clusters:
            members = cluster.get("members") or []
            if len(members) > member_cap:
                cluster["members"] = members[:member_cap]
                cluster["truncated_member_count"] = len(members) - member_cap
    requested_pack = pack_name or cluster_payload.get("requested_pack", "")
    center_x = GRAPH_MAP_WIDTH / 2
    center_y = GRAPH_MAP_HEIGHT / 2
    cluster_orbit_x = GRAPH_MAP_WIDTH * GRAPH_MAP_CLUSTER_ORBIT_X_FACTOR
    cluster_orbit_y = GRAPH_MAP_HEIGHT * GRAPH_MAP_CLUSTER_ORBIT_Y_FACTOR
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    all_member_ids = sorted(
        {
            str(member["object_id"])
            for cluster in clusters
            for member in cluster.get("members", [])
            if member.get("object_id")
        }
    )
    cluster_packs = sorted(
        {
            str(cluster.get("row_pack") or cluster.get("pack") or requested_pack)
            for cluster in clusters
            if cluster.get("row_pack") or cluster.get("pack") or requested_pack
        }
    )
    scoped_edges = list_graph_edges_for_object_scope(
        vault_dir,
        object_ids=all_member_ids,
        pack_names=cluster_packs,
        pack_name=pack_name,
    )
    edges_by_pack: dict[str, list[dict[str, Any]]] = {}
    for edge in scoped_edges:
        edges_by_pack.setdefault(str(edge.get("pack") or ""), []).append(edge)

    for cluster_index, cluster in enumerate(clusters):
        display_pack = str(cluster.get("pack") or requested_pack)
        cluster_count = max(1, len(clusters))
        cluster_angle = (2 * math.pi * cluster_index / cluster_count) if cluster_count > 1 else 0
        cluster_x = center_x + (math.cos(cluster_angle) * cluster_orbit_x if cluster_count > 1 else 0)
        cluster_y = center_y + (math.sin(cluster_angle) * cluster_orbit_y if cluster_count > 1 else 0)
        members = cluster.get("members", [])
        member_count = max(1, len(members))
        local_radius = min(
            GRAPH_MAP_LOCAL_RADIUS_MAX,
            GRAPH_MAP_LOCAL_RADIUS_BASE + member_count * GRAPH_MAP_LOCAL_RADIUS_PER_MEMBER,
        )
        for member_index, member in enumerate(members):
            object_id = str(member["object_id"])
            member_angle = (2 * math.pi * member_index / member_count) - (math.pi / 2)
            x = _clamp_graph_coordinate(
                cluster_x + math.cos(member_angle) * local_radius,
                GRAPH_MAP_MARGIN,
                GRAPH_MAP_WIDTH - GRAPH_MAP_MARGIN,
            )
            y = _clamp_graph_coordinate(
                cluster_y + math.sin(member_angle) * local_radius,
                GRAPH_MAP_MARGIN,
                GRAPH_MAP_HEIGHT - GRAPH_MAP_MARGIN,
            )
            node = nodes.setdefault(
                object_id,
                {
                    "object_id": object_id,
                    "title": str(member.get("title") or object_id),
                    "object_kind": str(member.get("object_kind") or "object"),
                    "kind_label": _object_kind_label(str(member.get("object_kind") or "")),
                    "path": _scoped_path(
                        f"/object?id={quote(object_id, safe='')}",
                        pack_name=display_pack,
                    ),
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "degree": 0,
                    "cluster_ids": [],
                    "cluster_titles": [],
                },
            )
            if cluster["cluster_id"] not in node["cluster_ids"]:
                node["cluster_ids"].append(cluster["cluster_id"])
                node["cluster_titles"].append(cluster.get("display_title") or cluster["label"])

    # Edge collection runs once after every cluster has populated
    # ``nodes`` so cross-community edges (source in cluster A, target
    # in cluster B) survive — the previous per-cluster filter dropped
    # any edge whose endpoints didn't both sit inside the same
    # cluster's member list, leaving the atlas almost edge-less.
    for edges_list in edges_by_pack.values():
        for edge in edges_list:
            source_id = str(edge["source_object_id"])
            target_id = str(edge["target_object_id"])
            if source_id not in nodes or target_id not in nodes:
                continue
            key = (source_id, target_id, str(edge["edge_kind"]))
            edge_weight = float(edge.get("weight") or 0.0)
            if key in edges:
                edges[key]["weight"] += edge_weight
            else:
                edges[key] = {
                    "source_object_id": source_id,
                    "target_object_id": target_id,
                    "edge_kind": str(edge["edge_kind"]),
                    "weight": edge_weight,
                    "source_title": nodes[source_id]["title"],
                    "target_title": nodes[target_id]["title"],
                }

    for edge in edges.values():
        nodes[edge["source_object_id"]]["degree"] += 1
        nodes[edge["target_object_id"]]["degree"] += 1

    node_items = sorted(nodes.values(), key=lambda item: (-int(item["degree"]), str(item["title"]).lower()))
    for node in node_items:
        node["radius"] = GRAPH_MAP_NODE_BASE_RADIUS + min(
            GRAPH_MAP_NODE_RADIUS_BONUS_MAX,
            int(node["degree"]) * GRAPH_MAP_NODE_RADIUS_PER_DEGREE,
        )
    edge_items = sorted(
        edges.values(),
        key=lambda item: (
            str(item["source_title"]).lower(),
            str(item["target_title"]).lower(),
            str(item["edge_kind"]),
        ),
    )

    # AtlasGraph kit-shape projection — the dark 3D view at /map
    # consumes ``atlas`` directly via ``window.OVP_GRAPH``. The
    # existing ``nodes``/``edges``/``clusters`` keys above are kept
    # for backward-compat with payload consumers (tests, /api, the
    # 2D inspector still on /ops/cluster).  See
    # docs/design-system/ui_kits/ovp/graph-data.js for the
    # canonical kit shape this mirrors.
    backlinks_in: dict[str, int] = {}
    for edge in edge_items:
        target = str(edge["target_object_id"])
        backlinks_in[target] = backlinks_in.get(target, 0) + 1
    atlas_communities = [
        {
            "id": str(cluster["cluster_id"]),
            "name": str(cluster.get("display_title") or cluster["label"]),
            "slug": _atlas_community_slug(
                str(cluster.get("display_title") or cluster["label"])
            ),
            # The kit reads the trailing digit off ``var(--c-N)`` and
            # uses the runtime-computed token, so the swatch follows
            # the active theme.  Cycle through 1..8 by index.
            "color": f"var(--c-{(idx % 8) + 1})",
            "count": int(cluster.get("member_count") or 0),
        }
        for idx, cluster in enumerate(clusters)
    ]
    # ``absorbedAt`` is required by the kit's timeline scrubber.
    # We don't yet surface a per-object absorbed-at on the cluster
    # member dict; for v1 every node carries today's date so the
    # timeline degenerates to a single bucket and the "Play history"
    # affordance is harmless.  Stage 4 will surface real timestamps.
    # Use UTC so day boundaries match the rest of this module — local
    # timezone would shift fallback nodes between timeline buckets
    # around UTC midnight.
    absorbed_default = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    atlas_nodes = [
        {
            "id": str(node["object_id"]),
            "label": str(node["title"]),
            "type": _ATLAS_TYPE_BY_OBJECT_KIND.get(
                str(node.get("object_kind") or ""),
                "evergreen",
            ),
            "community": (
                str(node["cluster_ids"][0])
                if node.get("cluster_ids")
                else ""
            ),
            "quality": None,
            "backlinks": int(backlinks_in.get(str(node["object_id"]), 0)),
            "openQuestion": str(node.get("object_kind") or "")
                == "contradiction_crystal",
            "source": "manual",
            "absorbedAt": absorbed_default,
            "path": str(node.get("path") or ""),
        }
        for node in node_items
    ]
    atlas_links = [
        {
            "source": str(edge["source_object_id"]),
            "target": str(edge["target_object_id"]),
            "kind": _ATLAS_LINK_KINDS.get(
                str(edge["edge_kind"]).lower(),
                "ref",
            ),
        }
        for edge in edge_items
    ]
    atlas_payload = {
        "communities": atlas_communities,
        "nodes": atlas_nodes,
        "links": atlas_links,
    }
    return {
        "screen": "graph/map",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="graph_map",
            pack_name=pack_name,
            generated_by="build_graph_map_payload",
            derived_from=("knowledge.db.graph_clusters", "knowledge.db.graph_edges"),
        ),
        "query": query or "",
        "limit": limit,
        # The graph map intentionally caps how many neighborhoods it
        # paints; treat that cap as "limited" even when the underlying
        # cluster set is small — the banner explains the display intent
        # rather than reporting on pagination.
        "is_limited": True,
        "layout": {"width": GRAPH_MAP_WIDTH, "height": GRAPH_MAP_HEIGHT},
        "nodes": node_items,
        "edges": edge_items,
        "clusters": [
            {
                "cluster_id": str(cluster["cluster_id"]),
                "title": str(cluster.get("display_title") or cluster["label"]),
                "detail_path": str(cluster["detail_path"]),
                "member_count": int(cluster["member_count"]),
                "priority_band": str(cluster["priority_band"]),
                "summary": str(cluster.get("top_summary_bullet") or cluster["priority_reason"]),
            }
            for cluster in clusters
        ],
        "map_summary": {
            "node_count": len(node_items),
            "edge_count": len(edge_items),
            "cluster_count": len(clusters),
            "largest_cluster_size": cluster_payload["largest_cluster_size"],
            # The graph map intentionally caps how many neighborhoods it
        # paints; treat that cap as "limited" even when the underlying
        # cluster set is small — the banner explains the display intent
        # rather than reporting on pagination.
        "is_limited": True,
            "limit": limit,
            # BL-051 visibility caps — surface to the renderer so the
            # page can show the right banner + ``Show all`` toggle.
            "show_all": show_all,
            "member_cap": member_cap if not show_all else 0,
            "truncated_clusters": sum(
                1 for c in clusters if c.get("truncated_member_count")
            ),
        },
        "model_notes": [
            "This spatial map is a reader projection over graph clusters and edges.",
            "Use it to see nearby ideas first; use the cluster browser for analytical/debug detail.",
        ],
        "atlas": atlas_payload,
    }



def build_cluster_detail_payload(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    pack_name: str | None = None,
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    detail = get_graph_cluster_detail(vault_dir, cluster_id, pack_name=pack_name)
    cluster = detail["cluster"]
    requested_pack = pack_name or str(cluster["pack"])
    member_index = {str(member["object_id"]): member for member in cluster["members"]}
    detail_path = (
        f"/ops/cluster?id={quote(str(cluster['cluster_id']), safe='')}"
        f"&pack={quote(requested_pack, safe='')}"
    )
    enriched_cluster = {
        **cluster,
        "detail_path": detail_path,
        "center_object_path": _scoped_path(
            f"/object?id={quote(str(cluster['center_object_id']), safe='')}",
            pack_name=requested_pack,
        ),
        "member_links": [
            {
                **member,
                "path": _scoped_path(
                    f"/object?id={quote(str(member['object_id']), safe='')}",
                    pack_name=requested_pack,
                ),
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
            "source_path": _scoped_path(
                f"/object?id={quote(str(edge['source_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "target_path": _scoped_path(
                f"/object?id={quote(str(edge['target_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for edge in detail["edges"]
    ]
    sections = _build_cluster_surface_sections(
        vault_dir,
        cluster=enriched_cluster,
        edges=enriched_edges,
        requested_pack=requested_pack,
        cluster_rows=cluster_rows,
        cluster_provenance_index=cluster_provenance_index,
    )

    return {
        "screen": "graph/cluster-detail",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="graph_cluster_detail",
            pack_name=pack_name,
            generated_by="build_cluster_detail_payload",
            derived_from=("knowledge.db.graph_clusters", "knowledge.db.graph_edges"),
        ),
        "cluster": enriched_cluster,
        "browser_path": f"/ops/clusters?pack={quote(requested_pack, safe='')}",
        "edges": enriched_edges,
        **sections,
        "model_notes": [
            "Cluster detail currently reflects pack-owned graph seed structure, not a final semantic subgraph model.",
            "Edges are filtered to the cluster's own member set inside the requested pack projection.",
        ],
    }



def build_atlas_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    items = list_atlas_memberships(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT,
    )
    # Atlas membership browser: source-note coverage comes from
    # ``get_note_traceability`` per atlas page.  Pre-BL-029 a parallel
    # query joined the deep-dive index too — the chain has no
    # deep-dive intermediate stage now, so just gather source notes.
    object_to_source_notes: dict[str, dict[str, dict[str, str]]] = {}
    for atlas_item in items:
        for member in atlas_item["members"]:
            member_id = str(member.get("object_id") or "")
            if not member_id:
                continue
            traceability = get_object_traceability(
                vault_dir, member_id, pack_name=pack_name,
            )
            object_to_source_notes.setdefault(member_id, {})
            for source in traceability["source_notes"]:
                object_to_source_notes[member_id][source["path"]] = source
    enriched_items = []
    for item in items:
        enriched_members = [
            {
                **member,
                "object_path": _scoped_path(
                    f"/object?id={quote(str(member['object_id']), safe='')}",
                    pack_name=requested_pack,
                ),
            }
            for member in item["members"]
        ]
        preview_titles = [member["title"] for member in enriched_members[:5]]
        member_object_ids = [member["object_id"] for member in enriched_members]
        source_note_map: dict[str, dict[str, str]] = {}
        for member_object_id in member_object_ids:
            for source in object_to_source_notes.get(member_object_id, {}).values():
                source_note_map.setdefault(source["path"], source)
        enriched_items.append(
            {
                **item,
                "members": enriched_members,
                "member_count": len(enriched_members),
                "preview_titles": preview_titles,
                "source_notes": list(source_note_map.values()),
            }
        )
    return {
        "screen": "atlas/browser",
        "requested_pack": requested_pack,
        "items": enriched_items,
        "count": len(enriched_items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
    }



def build_curated_atlas_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    from ovp_pipeline.synthesis.curated_atlas import build_curated_atlas
    from ovp_pipeline.synthesis._shared import CRYSTAL_DIR_REL

    requested_pack = pack_name or ""
    pack = pack_name or PRIMARY_PACK_NAME
    requested_top_n = top_n if top_n is not None else CURATED_ATLAS_DEFAULT_TOP_N
    requested_top_n = max(1, min(requested_top_n, CURATED_ATLAS_MAX_TOP_N))

    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
        atlas = build_curated_atlas(conn, pack=pack, top_n=requested_top_n)

    # Lazy import — same pattern as the adjacent
    # ``build_reader_home_payload`` helper.
    from ovp_pipeline.synthesis._shared import crystal_safe_id

    entries: list[dict[str, Any]] = []
    for entry in atlas.entries:
        safe_id = crystal_safe_id(entry.crystal_kind, entry.crystal_id)
        note_rel = str(CRYSTAL_DIR_REL / f"{safe_id}.md")
        entries.append(
            {
                "rank": entry.rank,
                "crystal_kind": entry.crystal_kind,
                "crystal_id": entry.crystal_id,
                "safe_id": safe_id,
                "label": entry.label,
                "score": round(entry.score, 4),
                "size_norm": round(entry.size_norm, 3),
                "credibility_norm": round(entry.credibility_norm, 3),
                "source_diversity_norm": round(entry.source_diversity_norm, 3),
                "contradiction_norm": round(entry.contradiction_norm, 3),
                "reuse_recency_norm": round(entry.reuse_recency_norm, 3),
                "evergreen_recency_norm": round(entry.evergreen_recency_norm, 3),
                "teaser": entry.teaser,
                "source_slugs": list(entry.source_slugs),
                "note_path": note_rel,
                "note_href": _scoped_path(
                    f"/note?path={quote(note_rel, safe='')}",
                    pack_name=requested_pack,
                ),
            }
        )

    # Emit one reuse event per displayed crystal so the
    # ``reuse_recency_norm`` signal in ``crystal_scoring`` actually
    # has a producer.  Pre-fix the signal stayed cold-zero because no
    # surface ever wrote ``reuse_events`` rows with
    # ``object_kind in ('community_crystal', 'contradiction_crystal')``.
    # Best-effort — a JSONL-append failure must not block the
    # /topics page from rendering.
    if entries:
        try:
            from ovp_pipeline.reuse_emitter import emit_crystal_reuse_events
            emit_crystal_reuse_events(
                vault_dir,
                pack=pack,
                crystals=[
                    (
                        f"{entry['crystal_kind']}_crystal",
                        str(entry["crystal_id"]),
                    )
                    for entry in entries
                ],
                surface="atlas",
                consumer_ref=f"top_n={atlas.top_n}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort instrumentation
            logger.warning(
                "crystal reuse-event emission failed for /topics: %s", exc,
            )

    return {
        "screen": "atlas/curated",
        "requested_pack": requested_pack,
        "pack": atlas.pack,
        "top_n": atlas.top_n,
        "total_chains": atlas.total_chains,
        "entries": entries,
        "count": len(entries),
        "generated_at": atlas.generated_at,
        "default_top_n": CURATED_ATLAS_DEFAULT_TOP_N,
        "max_top_n": CURATED_ATLAS_MAX_TOP_N,
    }


__all__ = [
    '_atlas_community_slug',
    '_derive_cluster_structural_label',
    '_collect_cluster_provenance',
    '_build_cluster_provenance_index',
    '_build_related_cluster_items',
    '_build_related_cluster_groups',
    '_build_cluster_surface_sections',
    'build_cluster_summary_payload',
    'build_topic_overview_payload',
    'build_cluster_browser_payload',
    '_clamp_graph_coordinate',
    'build_graph_map_payload',
    'build_cluster_detail_payload',
    'build_atlas_browser_payload',
    'build_curated_atlas_payload'
]
