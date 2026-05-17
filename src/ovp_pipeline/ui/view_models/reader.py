# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *




def _object_reader_profile(detail: dict[str, Any], *, relation_count: int) -> dict[str, object]:
    summary_text = str((detail.get("summary") or {}).get("summary_text") or "").strip()
    source_note_count = len(detail["provenance"]["source_notes"])
    atlas_count = len(detail["provenance"]["mocs"])
    return {
        "kind_label": _object_kind_label(str(detail["object"].get("object_kind") or "")),
        "headline": detail["object"]["title"],
        "dek": summary_text or "No compiled summary yet.",
        "supporting_line": (
            f"{len(detail['claims'])} claims · {relation_count} relations · "
            f"{source_note_count} source notes · {atlas_count} atlas pages"
        ),
        "empty_summary": not bool(summary_text),
    }



def _reader_summary(
    reader_groups: list[dict[str, Any]],
    source_groups: list[dict[str, Any]],
) -> str:
    object_parts = [
        f"{group['result_count']} {str(group['kind']).replace('_', ' ')}"
        + ("" if int(group["result_count"]) == 1 else "s")
        for group in reader_groups
    ]
    note_count = sum(int(group["result_count"]) for group in source_groups)
    if note_count:
        object_parts.append(f"{note_count} note" + ("" if note_count == 1 else "s"))
    return ", ".join(object_parts) if object_parts else "No reader results"



def _build_reader_search_projection(
    vault_dir: Path | str,
    *,
    query: str,
    objects: list[dict[str, Any]],
    notes: list[dict[str, Any]],
) -> dict[str, Any]:
    object_pack_pairs = sorted(
        {
            (str(item["object_id"]), str(item.get("row_pack") or item.get("pack") or ""))
            for item in objects
        }
    )
    summary_by_object: dict[tuple[str, str], str] = {}
    evidence_count_by_object: dict[tuple[str, str], int] = {}
    if object_pack_pairs:
        db_path = _db_path(vault_dir)
        pair_clause = " OR ".join("(object_id = ? AND pack = ?)" for _ in object_pack_pairs)
        claim_pair_clause = " OR ".join(
            "(claims.object_id = ? AND claims.pack = ?)" for _ in object_pack_pairs
        )
        pair_params = [value for pair in object_pack_pairs for value in pair]
        with sqlite3.connect(db_path) as conn:
            summary_by_object = {
                (str(object_id), str(pack)): str(summary_text or "")
                for object_id, pack, summary_text in conn.execute(
                    f"""
                    SELECT object_id, pack, summary_text
                    FROM compiled_summaries
                    WHERE {pair_clause}
                    """,
                    tuple(pair_params),
                ).fetchall()
            }
            evidence_count_by_object = {
                (str(object_id), str(pack)): int(count)
                for object_id, pack, count in conn.execute(
                    f"""
                    SELECT claims.object_id, claims.pack, COUNT(claim_evidence.claim_id)
                    FROM claims
                    LEFT JOIN claim_evidence
                      ON claim_evidence.pack = claims.pack
                     AND claim_evidence.claim_id = claims.claim_id
                    WHERE {claim_pair_clause}
                    GROUP BY claims.object_id, claims.pack
                    """,
                    tuple(pair_params),
                ).fetchall()
            }

    grouped_objects: dict[str, dict[str, Any]] = {}
    for item in objects:
        object_kind = str(item.get("object_kind") or "object").strip().lower() or "object"
        group = grouped_objects.setdefault(
            object_kind,
            {
                "kind": object_kind,
                "label": _plural_reader_label(_object_kind_label(object_kind)),
                "items": [],
                "result_count": 0,
            },
        )
        object_id = str(item["object_id"])
        row_pack = str(item.get("row_pack") or item.get("pack") or "")
        summary = summary_by_object.get((object_id, row_pack), "")
        evidence_count = evidence_count_by_object.get((object_id, row_pack), 0)
        group["items"].append(
            {
                **item,
                "summary": summary or "No compiled summary yet.",
                "evidence_count": evidence_count,
                "reason": _search_match_reason(
                    query=query,
                    title=str(item.get("title") or ""),
                    summary=summary,
                    evidence_count=evidence_count,
                ),
            }
        )
        group["result_count"] += 1

    source_groups_by_type: dict[str, dict[str, Any]] = {}
    for item in notes:
        note_type = str(item.get("note_type") or "note").strip().lower() or "note"
        group = source_groups_by_type.setdefault(
            note_type,
            {
                "kind": note_type,
                "label": _search_note_type_label(note_type),
                "items": [],
                "result_count": 0,
            },
        )
        group["items"].append(
            {
                **item,
                "reason": "Matched note title or body.",
            }
        )
        group["result_count"] += 1

    reader_groups = list(grouped_objects.values())
    source_groups = list(source_groups_by_type.values())
    return {
        "reader_groups": reader_groups,
        "source_groups": source_groups,
        "reader_summary": _reader_summary(reader_groups, source_groups),
    }



def build_object_page_payload(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    research_shell_enabled = _supports_research_shell(pack_name)
    detail = get_object_detail(vault_dir, object_id, pack_name=pack_name)
    neighborhood = get_topic_neighborhood(vault_dir, object_id, pack_name=pack_name)
    review_context = get_review_context(vault_dir, [object_id], pack_name=pack_name) if research_shell_enabled else {}
    neighbor_titles = {item["object_id"]: item["title"] for item in neighborhood["neighbors"]}
    relations = [
        {
            **item,
            "target_title": neighbor_titles.get(item["target_object_id"], item["target_object_id"]),
            "target_path": _scoped_path(
                f"/object?id={quote(str(item['target_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in detail["relations"]
    ]
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
    evolution_section = (
        _build_evolution_section(
            vault_dir,
            pack_name=pack_name,
            status="all",
            scoped_object_ids=[object_id],
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
    )
    summary_text = detail["summary"]["summary_text"] if detail["summary"] else ""
    reader_profile = _object_reader_profile(detail, relation_count=len(relations))
    kind_profile = _object_kind_profile(detail, relation_count=len(relations))
    section_labels = kind_profile["section_labels"]
    source_backlink_rail = _build_source_backlink_rail(
        vault_dir,
        detail=detail,
        relations=relations,
        requested_pack=requested_pack,
    )
    stale_summary_details = (
        list_stale_summaries(
            vault_dir,
            pack_name=pack_name,
            object_ids=[object_id],
            limit=10,
        )
        if research_shell_enabled
        else []
    )
    production_chain = get_object_traceability(vault_dir, object_id, pack_name=pack_name)
    source_chain = get_object_source_chain(vault_dir, object_id, pack_name=pack_name)
    compiled_sections = [
        _compiled_section(
            "current_state",
            str(section_labels.get("current_state") or "Current State"),
            summary=summary_text or "No compiled summary yet.",
            items=[
                {
                    "kind": "summary",
                    "label": detail["object"]["title"],
                    "path": "",
                    "detail": summary_text or "No compiled summary yet.",
                },
                {"kind": "claims", "label": "Claims", "path": "", "detail": f"{len(detail['claims'])} claims"},
                {"kind": "relations", "label": "Relations", "path": "", "detail": f"{len(relations)} relations"},
            ],
        ),
        _compiled_section(
            "why_it_matters",
            str(section_labels.get("why_it_matters") or "Why It Matters"),
            summary=f"{len(detail['contradictions']) if research_shell_enabled else 0} contradictions and {review_context.get('stale_summary_count', 0)} stale summaries shape current maintenance urgency.",
            items=[
                {
                    "kind": "topic",
                    "label": "Explore topic",
                    "path": _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": "Open the surrounding topic neighborhood.",
                },
                *(
                    [
                        {
                            "kind": "events",
                            "label": "Related events",
                            "path": research_links["events_path"],
                            "detail": "See timeline context for this object.",
                        }
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            str(section_labels.get("evidence_traceability") or "Evidence Traceability"),
            summary=f"{len(detail['evidence'])} evidence rows, {len(detail['provenance']['source_notes'])} source notes, {len(detail['provenance']['mocs'])} atlas pages.",
            items=[
                {
                    "kind": "evergreen",
                    "label": "Evergreen note",
                    "path": _scoped_path(
                        f"/note?path={quote(detail['provenance']['evergreen_path'], safe='')}",
                        pack_name=requested_pack,
                    )
                    if detail["provenance"]["evergreen_path"]
                    else "",
                    "detail": detail["provenance"]["evergreen_path"] or "No evergreen markdown path",
                },
                *[
                    {
                        "kind": "source_note",
                        "label": item["title"],
                        "path": _scoped_path(f"/note?path={quote(item['path'], safe='')}", pack_name=requested_pack),
                        "detail": item["note_type"],
                    }
                    for item in detail["provenance"]["source_notes"][:3]
                ],
            ],
        ),
        _compiled_section(
            "production_chain",
            "Production Chain",
            summary=str(production_chain.get("chain_summary") or ""),
            items=[
                {
                    "kind": "chain_status",
                    "label": "Chain status",
                    "path": "",
                    "detail": str(production_chain.get("chain_status") or ""),
                },
                {
                    "kind": "missing_stages",
                    "label": "Missing stages",
                    "path": "",
                    "detail": ", ".join(
                        str(item).replace("_", " ")
                        for item in production_chain.get("missing_stages", [])
                    )
                    or "None",
                },
                *[
                    {
                        "kind": "atlas_page",
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(item['path'], safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": "Atlas / MOC reach",
                    }
                    for item in production_chain["atlas_pages"][:2]
                ],
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{len(detail['contradictions']) if research_shell_enabled else 0} contradictions and {len(stale_summary_details) if research_shell_enabled else 0} stale-summary signals remain.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": research_links["contradictions_path"],
                        "detail": item["status"],
                    }
                    for item in detail["contradictions"][:3]
                ],
                *[
                    {
                        "kind": "stale_summary",
                        "label": item["title"],
                        "path": research_links["summaries_path"],
                        "detail": ", ".join(item["reason_texts"]),
                    }
                    for item in stale_summary_details[:2]
                ],
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Use the surrounding compiled products to continue reading or review.",
            items=[
                {
                    "kind": "topic",
                    "label": "Topic overview",
                    "path": _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": "Open the surrounding topic page.",
                },
                *(
                    [
                        {
                            "kind": "events",
                            "label": "Event dossier",
                            "path": research_links["events_path"],
                            "detail": "See event and time context.",
                        },
                        {
                            "kind": "contradictions",
                            "label": "Contradiction review",
                            "path": research_links["contradictions_path"],
                            "detail": "Inspect open conflicts.",
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
            "Topic overview",
            _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
            "Open the surrounding topic page.",
        ),
        _operator_action(
            "Event dossier" if research_shell_enabled else "Signals",
            research_links["events_path"] if research_shell_enabled else _scoped_path("/ops/signals", pack_name=requested_pack),
            (
                "See timeline context for this object."
                if research_shell_enabled
                else "Open active signal review."
            ),
        ),
        _operator_action(
            "Contradiction review" if research_shell_enabled else "Search",
            research_links["contradictions_path"] if research_shell_enabled else _scoped_path("/search", pack_name=requested_pack),
            (
                "Inspect open contradictions for this object."
                if research_shell_enabled
                else "Search laterally from this object."
            ),
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect downstream production chain state.",
        ),
    ]
    payload: dict[str, Any] = {
        "screen": "object/page",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="object_page",
            pack_name=pack_name,
            generated_by="build_object_page_payload",
            derived_from=("knowledge.db", "review audit"),
        ),
        "assembly_contract": _assembly_contract("object_brief", pack_name=pack_name),
        "research_shell_enabled": research_shell_enabled,
        **detail,
        "reader_profile": reader_profile,
        "kind_profile": kind_profile,
        "source_backlink_rail": source_backlink_rail,
        "source_chain": source_chain,
        "production_chain": production_chain,
        "relations": relations,
        "claim_count": len(detail["claims"]),
        "relation_count": len(relations),
        "contradiction_count": len(detail["contradictions"]) if research_shell_enabled else 0,
        "evidence_count": len(detail["evidence"]),
        "context": {
            "object_kind": detail["object"]["object_kind"],
            "source_slug": detail["object"]["source_slug"],
            "canonical_path": detail["object"]["canonical_path"],
        },
        "provenance": detail["provenance"],
        "mention_kind_stats": list_mention_kind_stats(vault_dir, object_id, pack_name=pack_name),
        "relation_kind_stats": list_relation_kind_stats(vault_dir, object_id, pack_name=pack_name),
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=[object_id], limit=8) if research_shell_enabled else [],
        "evolution": evolution_section,
        "stale_summary_details": stale_summary_details,
        "open_contradiction_ids": (
            [item["contradiction_id"] for item in detail["contradictions"] if item["status"] == "open"]
            if research_shell_enabled
            else []
        ),
        "links": {
            "topic_path": _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
            **research_links,
        },
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": [
            {"href": "#summary", "label": "Summary"},
            {"href": "#sources", "label": "Sources"},
            *_section_nav_from_compiled_sections(compiled_sections),
            {"href": "#claims", "label": "Claims"},
            {"href": "#relations", "label": "Relations"},
            *(
                [{"href": "#contradictions", "label": "Contradictions"}]
                if research_shell_enabled
                else []
            ),
        ],
    }
    _emit_briefing_reuse(
        vault_dir,
        payload,
        pack=str((detail.get("object") or {}).get("pack") or requested_pack),
        consumer_ref=f"view:object_page:{object_id}",
    )
    return payload



def build_runtime_home_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    runtime = get_runtime_status(vault_dir)
    operational_runtime_state = get_operational_runtime_state(vault_dir)
    research_overview_supported = _supports_research_shell(pack_name)
    try:
        objects = build_objects_index_payload(vault_dir, limit=8, offset=0, pack_name=pack_name)
    except (OSError, sqlite3.Error):
        objects = {
            "total_count": 0,
            "items": [],
            "error": "object_index_unavailable",
        }
    # BL-053 Phase 2 foyer block: three-section "what's the state of
    # the world" header rendered at the top of /ops.  Each block re-
    # uses an existing builder so the foyer never gets out of sync
    # with the source-of-truth pages it links to.
    foyer: dict[str, Any] = {
        "today_summary": "",
        "today_path": _scoped_path("/ops/today", pack_name=requested_pack),
        "queue_summary": "",
        "queue_path": _scoped_path("/ops/queue", pack_name=requested_pack),
        "last_run": None,
        "runs_path": _scoped_path("/ops/runs", pack_name=requested_pack),
    }
    try:
        today = build_today_digest_payload(vault_dir, pack_name=pack_name)
        if today.get("available"):
            cards = today.get("cards") or []
            ingested = sum(
                int(card.get("total") or 0)
                for card in cards
                if card.get("id") in ("intake", "absorb")
            )
            failures = sum(
                int(card.get("total") or 0)
                for card in cards
                if card.get("id") == "failures"
            )
            foyer["today_summary"] = (
                f"{ingested} ingested · {failures} failure"
                f"{'s' if failures != 1 else ''} · {today.get('date', '')}"
            )
    except (OSError, sqlite3.Error):
        pass
    try:
        queue = build_queue_overview_payload(vault_dir, pack_name=pack_name)
        pending_chunks = [
            f"{int(q.get('count') or 0)} {q.get('label')}"
            for q in queue.get("queues", [])
            if int(q.get("count") or 0) > 0
        ]
        if pending_chunks:
            foyer["queue_summary"] = " · ".join(pending_chunks)
        else:
            foyer["queue_summary"] = "no pending review items"
    except (OSError, sqlite3.Error):
        pass
    try:
        runs = build_runs_index_payload(vault_dir, pack_name=pack_name, limit=1)
        if runs.get("runs"):
            last = runs["runs"][0]
            foyer["last_run"] = {
                "txn_id": str(last.get("txn_id", "")),
                "workflow_type": str(last.get("workflow_type", "")),
                "status": str(last.get("status", "")),
                "started_at": str(last.get("started_at", "")),
                "detail_href": str(last.get("detail_href", "")),
            }
    except (OSError, sqlite3.Error):
        pass

    entry_sections: list[dict[str, Any]] = []
    return {
        "screen": "truth/runtime-home",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="reader_home",
            pack_name=pack_name,
            generated_by="build_runtime_home_payload",
            derived_from=("knowledge.db", "runtime ledgers"),
        ),
        "foyer": foyer,
        "runtime": runtime,
        "runtime_state": operational_runtime_state,
        "research_overview": {
            "status": "supported" if research_overview_supported else "shared_shell_only",
            "reason": (
                "Research-specific overview surfaces are available because this pack resolves through research-tech."
                if research_overview_supported
                else "This pack currently gets the shared home shell only; research-specific overview panels stay hidden until the pack defines its own equivalents."
            ),
        },
        "workflow_groups": _build_dashboard_workflow_groups(
            requested_pack=requested_pack,
            research_overview_supported=research_overview_supported,
        ),
        "entry_sections": entry_sections,
        "objects": {
            "count": objects["total_count"],
            "total_count": objects["total_count"],
            "items": objects["items"],
            **({"error": objects["error"]} if objects.get("error") else {}),
        },
        "orientation": {
            "assembly_contract": _assembly_contract("orientation_brief", pack_name=pack_name),
            "governance_contract": describe_governance_contract(pack_name=pack_name),
        },
        "signals": {
            "count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/signals", pack_name=requested_pack),
            "surface_contract": describe_observation_surface_contract(pack_name=pack_name, surface_kind="signals"),
        },
        "production": {
            "weak_points": [],
            "weak_point_count": 0,
            "browser_path": _scoped_path("/ops/production", pack_name=requested_pack),
            "surface_contract": describe_observation_surface_contract(
                pack_name=pack_name,
                surface_kind="production_chains",
            ),
        },
        "contradictions": {
            "count": 0,
            "open_count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/contradictions", pack_name=requested_pack),
        },
        "events": {
            "count": 0,
            "items": [],
            "dates": [],
            "browser_path": _scoped_path("/ops/events", pack_name=requested_pack),
        },
        "stale_summaries": {
            "count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/summaries", pack_name=requested_pack),
        },
        "evolution": {
            "candidate_count": 0,
            "accepted_count": 0,
            "items": [],
        },
        "recent_review_actions": [],
        "priorities": [],
        "mode": "runtime_first",
    }



def build_reader_home_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Reader-shell home payload.  No DB stat counts, no pipeline
    state — just reading entry points sourced from the synthesis
    substrate.

    Sections:

    * **top_topics** — top-N rows from ``crystal_scores`` for the
      pack, joined with body + label so the home can render a teaser
      without re-fetching markdown.
    * **curated_atlas** — total chain count + top-N constant so the
      home can headline "30 most reusable ideas in your vault".
    * **recent_crystals** — community crystals synthesized in the
      last ``READER_HOME_RECENT_DAYS`` days, capped at
      ``READER_HOME_RECENT_CRYSTALS_LIMIT``.
    * **map_supported** — whether the active pack supports a
      research-style graph nav (drives the Map card visibility).
    """
    from ovp_pipeline.synthesis.curated_atlas import build_curated_atlas, _extract_teaser
    from ovp_pipeline.synthesis._shared import CRYSTAL_DIR_REL
    from datetime import datetime, timedelta, timezone

    requested_pack = pack_name or ""
    pack = pack_name or PRIMARY_PACK_NAME

    db_path = _db_path(vault_dir)
    # Reader home must not crash on a fresh vault that hasn't run
    # ``ovp-knowledge-index`` yet — show the empty-state hint instead.
    atlas = None
    recent_rows: list[tuple] = []
    recent_total_active = 0
    recent_newest_at = ""
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                atlas = build_curated_atlas(
                    conn, pack=pack, top_n=READER_HOME_TOP_TOPICS_LIMIT,
                )
                cutoff = (datetime.now(timezone.utc)
                          - timedelta(days=READER_HOME_RECENT_DAYS)).isoformat(timespec="seconds")
                recent_rows = conn.execute(
                    """
                    SELECT cc.cluster_id, cc.synthesized_at, cc.body_md, gc.label
                      FROM community_crystals cc
                      JOIN graph_clusters gc
                        ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
                     WHERE cc.pack = ?
                       AND cc.superseded_by_synthesized_at = ''
                       AND cc.synthesized_at > ?
                     ORDER BY cc.synthesized_at DESC
                     LIMIT ?
                    """,
                    (pack, cutoff, READER_HOME_RECENT_CRYSTALS_LIMIT),
                ).fetchall()
                # M25.7 honest-zero: when the 7-day window is empty,
                # the operator needs to know WHY — a bare "no topics"
                # reads as broken when in fact there are hundreds of
                # crystals that are simply older than the window.
                # Pull the active-crystal total + newest synthesized
                # date so the renderer can explain instead of going
                # silent (M25.6 dogfood finding: home looked broken
                # on a vault whose newest crystal was 10 days old).
                ctx_row = conn.execute(
                    """
                    SELECT COUNT(*), MAX(synthesized_at)
                      FROM community_crystals
                     WHERE pack = ?
                       AND superseded_by_synthesized_at = ''
                    """,
                    (pack,),
                ).fetchone()
                recent_total_active = int(ctx_row[0] or 0) if ctx_row else 0
                recent_newest_at = (
                    str(ctx_row[1]) if ctx_row and ctx_row[1] else ""
                )
        except sqlite3.DatabaseError:
            atlas = None
            recent_rows = []
            recent_total_active = 0
            recent_newest_at = ""
    if atlas is None:
        # Empty placeholder so downstream rendering shows the
        # ``run ovp-knowledge-index`` hint without special-casing.
        from ovp_pipeline.synthesis.curated_atlas import CuratedAtlas
        atlas = CuratedAtlas(
            pack=pack, top_n=READER_HOME_TOP_TOPICS_LIMIT,
            total_chains=0, entries=(),
            generated_at="",
        )

    # Single source of truth for crystal_id → on-disk safe-id is
    # ``synthesis._shared.crystal_safe_id``.  Imported lazily here to
    # avoid pulling synthesis dependencies into ``view_models``'s
    # module-load path.
    from ovp_pipeline.synthesis._shared import crystal_safe_id

    top_topics = []
    for entry in atlas.entries:
        safe_id = crystal_safe_id(entry.crystal_kind, entry.crystal_id)
        note_rel = str(CRYSTAL_DIR_REL / f"{safe_id}.md")
        top_topics.append({
            "rank": entry.rank,
            "label": entry.label,
            "teaser": entry.teaser,
            "score": round(entry.score, 3),
            "note_href": _scoped_path(
                f"/note?path={quote(note_rel, safe='')}",
                pack_name=requested_pack,
            ),
        })

    recent_crystals = []
    for cluster_id, synthesized_at, body_md, label in recent_rows:
        # Recent-crystals query is community-only by design (it joins
        # community_crystals + graph_clusters), so the kind is fixed.
        safe_id = crystal_safe_id("community", str(cluster_id))
        note_rel = str(CRYSTAL_DIR_REL / f"{safe_id}.md")
        recent_crystals.append({
            "label": str(label or "(untitled)"),
            "synthesized_at": str(synthesized_at or ""),
            "teaser": _extract_teaser(str(body_md or ""), max_chars=140),
            "note_href": _scoped_path(
                f"/note?path={quote(note_rel, safe='')}",
                pack_name=requested_pack,
            ),
        })

    return {
        "screen": "reader/home",
        "requested_pack": requested_pack,
        "pack": atlas.pack,
        "top_topics": top_topics,
        # ``curated_atlas`` payload: ``available`` flips off when the
        # corpus is empty so the renderer can suppress the card
        # instead of headlining "30 ideas... ranked from 0 chains".
        # ``effective_top_n`` is ``min(default, total)`` so the body
        # copy never claims more than actually shipped.
        "curated_atlas": {
            "available": atlas.total_chains > 0,
            "total_chains": atlas.total_chains,
            "top_n": CURATED_ATLAS_DEFAULT_TOP_N,
            "effective_top_n": min(CURATED_ATLAS_DEFAULT_TOP_N, atlas.total_chains),
            "atlas_href": _scoped_path("/topics", pack_name=requested_pack),
        },
        "recent_crystals": recent_crystals,
        "recent_days": READER_HOME_RECENT_DAYS,
        # M25.7 honest-zero context for the empty Recent Topics
        # state.  ``total_active`` = crystals that exist regardless
        # of age; ``newest_at`` = when the most recent one was
        # synthesized.  The renderer uses these to explain an
        # empty 7-day window instead of going silent.
        "recent_context": {
            "total_active": recent_total_active,
            "newest_at": recent_newest_at,
            "topics_href": _scoped_path(
                "/topics", pack_name=requested_pack
            ),
        },
        "map_supported": _supports_research_shell(pack_name),
        "search_href": _scoped_path("/search", pack_name=requested_pack),
        "map_href": _scoped_path("/map", pack_name=requested_pack),
        # M20 / BL-077: latest digest summary for the Reader home
        # banner card.  Empty dict when no digest has been generated.
        "digest": _build_latest_digest_info(
            Path(vault_dir), requested_pack=requested_pack,
        ),
    }



def build_search_payload(
    vault_dir: Path | str,
    *,
    query: str,
    pack_name: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    offset = (page - 1) * page_size
    results = search_vault_surface(
        vault_dir,
        query=query,
        pack_name=pack_name,
        object_limit=page_size,
        note_limit=page_size,
        object_offset=offset,
        note_offset=offset,
    )
    objects = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in results["objects"]
    ]
    notes = [
        {
            **item,
            "note_path": _scoped_path(
                f"/note?path={quote(str(item['path']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in results["notes"]
    ]
    reader_projection = _build_reader_search_projection(
        vault_dir,
        query=query,
        objects=objects,
        notes=notes,
    )
    return {
        "screen": "search/results",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="search_results",
            pack_name=pack_name,
            generated_by="build_search_payload",
            derived_from=("knowledge.db.objects", "knowledge.db.pages_index"),
        ),
        **results,
        "objects": objects,
        "notes": notes,
        **reader_projection,
        "object_count": len(results["objects"]),
        "note_count": len(results["notes"]),
        "page": page,
        "page_size": page_size,
    }



def build_note_page_payload(
    vault_dir: Path | str,
    *,
    note_path: str,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    provenance = get_note_provenance(vault_dir, note_path=note_path)
    production_chain = get_note_traceability(vault_dir, note_path=note_path, pack_name=pack_name)
    inbound_capture = get_note_inbound_capture_summary(vault_dir, note_path=note_path)
    production_chain["source_notes"] = [
        {
            **item,
            "note_path": _scoped_path(
                f"/note?path={quote(str(item['path']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in production_chain["source_notes"]
    ]
    production_chain["objects"] = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in production_chain["objects"]
    ]
    production_chain["atlas_pages"] = [
        {
            **item,
            "note_path": _scoped_path(
                f"/note?path={quote(str(item['path']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in production_chain["atlas_pages"]
    ]
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=(
                f"{production_chain['note']['title']} currently resolves as a "
                f"{production_chain.get('stage_label', '').replace('_', ' ')} with "
                f"{production_chain['counts']['objects']} objects and "
                f"{production_chain['counts']['atlas_pages']} atlas pages downstream."
            ),
            items=[
                {
                    "kind": "stage",
                    "label": str(production_chain.get("stage_label") or "").replace("_", " "),
                    "path": "",
                    "detail": str(production_chain.get("chain_status") or ""),
                }
            ],
        ),
        _compiled_section(
            "inbound_capture",
            "Inbound Capture",
            summary=str(inbound_capture.get("summary") or ""),
            items=[
                {
                    "kind": str(item.get("kind") or ""),
                    "label": str(item.get("label") or ""),
                    "path": (
                        _scoped_path(f"/note?path={quote(str(item['path']), safe='')}", pack_name=requested_pack)
                        if item.get("path")
                        else ""
                    ),
                    "detail": str(item.get("detail") or ""),
                }
                for item in inbound_capture.get("items", [])
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary="The note traceability chain shows which objects and atlas pages this note currently anchors.",
            items=[
                {
                    "kind": "object",
                    "label": item["title"],
                    "path": item["object_path"],
                    "detail": "Derived evergreen object",
                }
                for item in production_chain["objects"][:3]
            ],
        ),
        _compiled_section(
            "production_chain",
            "Production Chain",
            summary=str(production_chain.get("chain_summary") or ""),
            items=[
                {
                    "kind": "chain_status",
                    "label": "Chain status",
                    "path": "",
                    "detail": str(production_chain.get("chain_status") or ""),
                },
                {
                    "kind": "missing_stages",
                    "label": "Missing stages",
                    "path": "",
                    "detail": ", ".join(str(item).replace("_", " ") for item in production_chain.get("missing_stages", [])) or "None",
                },
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Continue into derived objects or atlas reach from this note.",
            items=[
                {
                    "kind": "object",
                    "label": item["title"],
                    "path": item["object_path"],
                    "detail": "Open derived object page.",
                }
                for item in production_chain["objects"][:2]
            ],
        ),
    ]
    fallback_object_path = (
        production_chain["objects"][0]["object_path"]
        if production_chain["objects"]
        else _scoped_path("/ops/objects", pack_name=requested_pack)
    )
    fallback_object_label = "Open derived object" if production_chain["objects"] else "Objects"
    return {
        "screen": "note/page",
        "requested_pack": requested_pack,
        "note_path": note_path,
        "provenance": provenance,
        "inbound_capture": inbound_capture,
        "production_chain": production_chain,
        # BL-058 follow-up — raw source ↔ evergreens ↔ clusters ↔ crystals
        # chain.  ``None`` for notes that aren't an evergreen or
        # 03-Processed source so the renderer can suppress the card.
        "lineage": _compute_v2_lineage(
            vault_dir, note_path, requested_pack,
        ),
        "operator_rail": [
            _operator_action(
                "Production Browser",
                _scoped_path("/ops/production", pack_name=requested_pack),
                "Inspect broader production-chain weak points.",
            ),
            _operator_action(
                "Signals",
                _scoped_path("/ops/signals", pack_name=requested_pack),
                "Open active signals for this shell scope.",
            ),
            _operator_action(
                fallback_object_label,
                fallback_object_path,
                "Jump into the most relevant derived object surface.",
            ),
        ],
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }


__all__ = [
    '_object_reader_profile',
    '_reader_summary',
    '_build_reader_search_projection',
    'build_object_page_payload',
    'build_runtime_home_payload',
    'build_reader_home_payload',
    'build_search_payload',
    'build_note_page_payload'
]
