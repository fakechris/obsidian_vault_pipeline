from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ... import truth_api as core
from ...runtime import resolve_vault_dir


def list_production_chains(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = core._validate_page_args(limit=limit, offset=0)
    db_path = core._db_path(vault_dir)
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
        relative_path = core._vault_relative_path(resolved_vault, path)
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
            frontmatter = core._parse_frontmatter(candidate.read_text(encoding="utf-8"))
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
        chain = core.get_note_traceability(vault_dir, note_path=relative_path)
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


def build_signal_entries(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_pack = str(pack_name or core.DEFAULT_WORKFLOW_PACK_NAME)
    timestamp = core._utc_now_text()
    signals: list[dict[str, Any]] = []

    for item in core.list_contradictions(resolved_vault, status="open", limit=core.MAX_PAGE_SIZE):
        object_ids = list(
            dict.fromkeys(
                claim_id.split("::", 1)[0]
                for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
            )
        )
        signals.append(
            {
                "signal_id": core._signal_id("contradiction_open", item["contradiction_id"]),
                "signal_type": "contradiction_open",
                "detected_at": timestamp,
                "status": "active",
                "title": item["subject_key"],
                "detail": (
                    f"{item['scope_summary']['object_count']} objects, "
                    f"{len(item['ranked_evidence'])} ranked evidence rows"
                ),
                "explanation": core.SIGNAL_TYPE_EXPLANATIONS["contradiction_open"],
                "source_path": "/contradictions",
                "source_label": "Contradictions",
                "object_ids": object_ids,
                "note_paths": [],
                "downstream_effects": [
                    {"label": "Review contradiction", "path": f"/contradictions?q={quote(item['subject_key'], safe='')}"},
                    *[
                        {"label": f"Object: {claim['object_title']}", "path": f"/object?id={claim['object_id']}"}
                        for claim in item["positive_claims"][:1] + item["negative_claims"][:1]
                    ],
                ],
                "recommended_action": core._recommended_action(
                    kind="review_contradiction",
                    label="Review contradiction",
                    path=f"/contradictions?q={quote(item['subject_key'], safe='')}",
                    executable=True,
                ),
                "payload": {
                    "contradiction_id": item["contradiction_id"],
                    "scope_summary": item["scope_summary"],
                    "status_bucket": item["status_bucket"],
                },
            }
        )

    for item in core.list_stale_summaries(resolved_vault, limit=core.MAX_PAGE_SIZE):
        signals.append(
            {
                "signal_id": core._signal_id("stale_summary", item["object_id"]),
                "signal_type": "stale_summary",
                "detected_at": timestamp,
                "status": "active",
                "title": item["title"],
                "detail": ", ".join(item["reason_texts"]),
                "explanation": core.SIGNAL_TYPE_EXPLANATIONS["stale_summary"],
                "source_path": f"/summaries?q={quote(item['object_id'], safe='')}",
                "source_label": "Stale Summaries",
                "object_ids": [item["object_id"]],
                "note_paths": [],
                "downstream_effects": [
                    {"label": "Open object", "path": item["object_path"]},
                    {"label": "Review stale summary", "path": f"/summaries?q={quote(item['object_id'], safe='')}"},
                ],
                "recommended_action": core._recommended_action(
                    kind="rebuild_summary",
                    label="Rebuild summary",
                    path=f"/summaries?q={quote(item['object_id'], safe='')}",
                    executable=True,
                ),
                "payload": {
                    "reason_codes": item["reason_codes"],
                    "latest_event_date": item["latest_event_date"],
                },
            }
        )

    production_chains = core.list_production_chains(
        resolved_vault,
        pack_name=normalized_pack,
        limit=core.MAX_PAGE_SIZE,
    )
    for item in core._production_gap_items_from_chains(production_chains, limit=core.MAX_PAGE_SIZE):
        object_ids = [entry["object_id"] for entry in item["traceability"]["objects"]]
        signals.append(
            {
                "signal_id": item["signal_id"],
                "signal_type": "production_gap",
                "detected_at": timestamp,
                "status": "active",
                "title": item["title"],
                "detail": item["detail"],
                "explanation": core.SIGNAL_TYPE_EXPLANATIONS["production_gap"],
                "source_path": f"/note?path={quote(item['note_path'], safe='')}",
                "source_label": "Production",
                "object_ids": object_ids,
                "note_paths": [item["note_path"]],
                "downstream_effects": [
                    {"label": "Open note", "path": f"/note?path={quote(item['note_path'], safe='')}"},
                    {"label": "Inspect production chain", "path": f"/production?q={quote(item['title'], safe='')}"},
                ],
                "recommended_action": core._recommended_action(
                    kind="inspect_production_gap",
                    label="Inspect production gap",
                    path=f"/production?q={quote(item['title'], safe='')}",
                    executable=False,
                ),
                "payload": {
                    "stage_label": item["stage_label"],
                    "missing": item["missing"],
                    "traceability_counts": item["traceability"]["counts"],
                },
            }
        )

    for item in production_chains:
        traceability = item["traceability"]
        if item["stage_label"] == "source_note" and not traceability["deep_dives"]:
            signals.append(
                {
                    "signal_id": core._signal_id("source_needs_deep_dive", item["path"]),
                    "signal_type": "source_needs_deep_dive",
                    "detected_at": timestamp,
                    "status": "active",
                    "title": item["title"],
                    "detail": "Processed source note has no derived deep dive yet.",
                    "explanation": core.SIGNAL_TYPE_EXPLANATIONS["source_needs_deep_dive"],
                    "source_path": f"/note?path={quote(item['path'], safe='')}",
                    "source_label": "Production",
                    "object_ids": [],
                    "note_paths": [item["path"]],
                    "downstream_effects": [
                        {"label": "Open source note", "path": f"/note?path={quote(item['path'], safe='')}"},
                        {"label": "Inspect production chain", "path": f"/production?q={quote(item['title'], safe='')}"},
                    ],
                    "recommended_action": core._recommended_action(
                        kind="deep_dive_workflow",
                        label="Create deep dive",
                        path=f"/note?path={quote(item['path'], safe='')}",
                        executable=False,
                    ),
                    "payload": {
                        "stage_label": item["stage_label"],
                        "traceability_counts": traceability["counts"],
                    },
                }
            )
        if item["stage_label"] == "deep_dive" and not traceability["objects"]:
            signals.append(
                {
                    "signal_id": core._signal_id("deep_dive_needs_objects", item["path"]),
                    "signal_type": "deep_dive_needs_objects",
                    "detected_at": timestamp,
                    "status": "active",
                    "title": item["title"],
                    "detail": "Deep dive has not produced any evergreen objects yet.",
                    "explanation": core.SIGNAL_TYPE_EXPLANATIONS["deep_dive_needs_objects"],
                    "source_path": f"/note?path={quote(item['path'], safe='')}",
                    "source_label": "Production",
                    "object_ids": [],
                    "note_paths": [item["path"]],
                    "downstream_effects": [
                        {"label": "Open deep dive", "path": f"/note?path={quote(item['path'], safe='')}"},
                        {"label": "Inspect production chain", "path": f"/production?q={quote(item['title'], safe='')}"},
                    ],
                    "recommended_action": core._recommended_action(
                        kind="object_extraction_workflow",
                        label="Extract evergreen objects",
                        path=f"/note?path={quote(item['path'], safe='')}",
                        executable=False,
                    ),
                    "payload": {
                        "stage_label": item["stage_label"],
                        "traceability_counts": traceability["counts"],
                    },
                }
            )

    for item in core.list_review_actions(resolved_vault, limit=core.MAX_PAGE_SIZE):
        if item["event_type"] == "ui_contradictions_resolved":
            status = item["status"] or "reviewed"
            signals.append(
                {
                    "signal_id": core._signal_id(
                        "contradiction_reviewed",
                        f"{item['timestamp']}::{','.join(item['contradiction_ids'])}",
                    ),
                    "signal_type": "contradiction_reviewed",
                    "detected_at": item["timestamp"],
                    "status": "active",
                    "title": "Contradiction reviewed",
                    "detail": f"{len(item['contradiction_ids'])} contradictions moved to {status}.",
                    "explanation": core.SIGNAL_TYPE_EXPLANATIONS["contradiction_reviewed"],
                    "source_path": "/contradictions?status=resolved",
                    "source_label": "Review Actions",
                    "object_ids": item["object_ids"],
                    "note_paths": [],
                    "downstream_effects": [
                        {"label": "Open resolved contradictions", "path": "/contradictions?status=resolved"},
                        *[
                            {"label": f"Object: {object_id}", "path": f"/object?id={object_id}"}
                            for object_id in item["object_ids"][:2]
                        ],
                    ],
                    "recommended_action": core._recommended_action(
                        kind="review_resolution",
                        label="Inspect resolved contradictions",
                        path="/contradictions?status=resolved",
                        executable=False,
                    ),
                    "payload": {
                        "event_type": item["event_type"],
                        "contradiction_ids": item["contradiction_ids"],
                        "status": status,
                        "rebuilt_object_ids": item["rebuilt_object_ids"],
                    },
                }
            )
        elif item["event_type"] == "ui_summaries_rebuilt":
            rebuilt_count = item["objects_rebuilt"] or len(item["rebuilt_object_ids"])
            signals.append(
                {
                    "signal_id": core._signal_id(
                        "summary_rebuilt",
                        f"{item['timestamp']}::{','.join(item['rebuilt_object_ids'])}",
                    ),
                    "signal_type": "summary_rebuilt",
                    "detected_at": item["timestamp"],
                    "status": "active",
                    "title": "Summary rebuilt",
                    "detail": f"{rebuilt_count} summaries rebuilt.",
                    "explanation": core.SIGNAL_TYPE_EXPLANATIONS["summary_rebuilt"],
                    "source_path": "/summaries",
                    "source_label": "Review Actions",
                    "object_ids": item["object_ids"],
                    "note_paths": [],
                    "downstream_effects": [
                        {"label": "Open stale summaries", "path": "/summaries"},
                        *[
                            {"label": f"Object: {object_id}", "path": f"/object?id={object_id}"}
                            for object_id in item["rebuilt_object_ids"][:2]
                        ],
                    ],
                    "recommended_action": core._recommended_action(
                        kind="review_rebuilt_summary",
                        label="Inspect rebuilt summaries",
                        path="/summaries",
                        executable=False,
                    ),
                    "payload": {
                        "event_type": item["event_type"],
                        "objects_rebuilt": rebuilt_count,
                        "rebuilt_object_ids": item["rebuilt_object_ids"],
                    },
                }
            )

    signals.sort(key=lambda item: (item["signal_type"], item["title"].lower(), item["signal_id"]))
    return signals


def build_briefing_snapshot(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    limit, _ = core._validate_page_args(limit=limit, offset=0)
    resolved_vault = resolve_vault_dir(vault_dir)
    recent_signals = core._list_signals_from_ledger(
        resolved_vault,
        ledger_path=core._signal_ledger_path(
            resolved_vault,
            pack_name=str(pack_name or core.DEFAULT_WORKFLOW_PACK_NAME),
        ),
        limit=limit,
    )
    unresolved_signal_types = {
        "contradiction_open",
        "stale_summary",
        "production_gap",
        "source_needs_deep_dive",
        "deep_dive_needs_objects",
    }
    unresolved_issues = [item for item in recent_signals if item["signal_type"] in unresolved_signal_types]
    unresolved_issues.sort(
        key=lambda item: (
            core._briefing_priority_score(item),
            str(item.get("title") or "").lower(),
            str(item.get("signal_id") or ""),
        ),
        reverse=True,
    )
    unresolved_issues = unresolved_issues[:limit]
    changed_signals = [
        item
        for item in recent_signals
        if item["signal_type"] in {"contradiction_reviewed", "summary_rebuilt"}
    ]
    changed_object_ids = list(
        dict.fromkeys(
            object_id
            for item in changed_signals
            for object_id in item.get("object_ids", [])
            if object_id
        )
    )
    topic_counts: Counter[str] = Counter()
    for item in recent_signals:
        for object_id in item.get("object_ids", []):
            topic_counts[object_id] += 1
    active_topic_ids = [object_id for object_id, _ in topic_counts.most_common(limit)]

    object_rows = core._batch_object_rows(resolved_vault, [*changed_object_ids, *active_topic_ids])
    changed_objects = [
        {
            "object_id": object_id,
            "title": object_rows.get(object_id, {}).get("title") or object_id,
            "path": f"/object?id={object_id}",
        }
        for object_id in changed_object_ids[:limit]
    ]
    active_topics = [
        {
            "object_id": object_id,
            "title": object_rows.get(object_id, {}).get("title") or object_id,
            "signal_count": count,
            "path": f"/topic?id={object_id}",
        }
        for object_id, count in topic_counts.most_common(limit)
    ]
    evolution_candidates = core.list_evolution_candidates(vault_dir, limit=min(core.MAX_PAGE_SIZE, limit * 3))
    evolution_object_ids = list(
        dict.fromkeys(
            object_id
            for item in evolution_candidates
            for object_id in item.get("object_ids", [])
            if object_id
        )
    )
    evolution_rows = core._batch_object_rows(vault_dir, evolution_object_ids)
    merged_insights: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in evolution_candidates:
        primary_object_id = next((object_id for object_id in item.get("object_ids", []) if object_id), "")
        primary_title = (
            evolution_rows.get(primary_object_id, {}).get("title")
            or object_rows.get(primary_object_id, {}).get("title")
            or str(item.get("subject_id") or primary_object_id)
        )
        path = (
            "/evolution?link_type="
            + quote(str(item["link_type"]), safe="")
            + "&q="
            + quote(str(primary_title), safe="")
        )
        insight = {
            "kind": f"evolution_{item['link_type']}",
            "link_type": item["link_type"],
            "title": str(primary_title),
            "detail": core.EVOLUTION_LINK_EXPLANATIONS.get(
                item["link_type"], "Knowledge evolution was detected."
            ),
            "path": path,
            "source_paths": [path for path in item.get("source_paths", []) if path][:3],
            "object_ids": list(item.get("object_ids", [])),
            "recommended_action": core._recommended_action(
                kind="review_evolution",
                label="Review evolution",
                path=path,
                executable=True,
            ),
        }
        key = (str(insight["kind"]), str(insight["title"]), str(insight["path"]))
        existing = merged_insights.get(key)
        if existing is None:
            merged_insights[key] = insight
        else:
            existing["source_paths"] = list(
                dict.fromkeys([*existing.get("source_paths", []), *insight.get("source_paths", [])])
            )[:3]
            existing["object_ids"] = list(
                dict.fromkeys([*existing.get("object_ids", []), *insight.get("object_ids", [])])
            )
    insights = list(merged_insights.values())
    insights.sort(
        key=lambda item: (
            core._briefing_evolution_score(item),
            str(item.get("title") or "").lower(),
            str(item.get("path") or ""),
        ),
        reverse=True,
    )
    insights = insights[:limit]

    priority_items: list[dict[str, Any]] = []
    for item in unresolved_issues:
        priority_items.append(
            {
                "signal_id": item["signal_id"],
                "kind": item["signal_type"],
                "title": item["title"],
                "detail": item["detail"],
                "path": item["source_path"],
                "source_paths": list(item.get("note_paths", [])),
                "object_ids": list(item.get("object_ids", [])),
                "recommended_action": item.get("recommended_action"),
            }
        )
        if len(priority_items) >= limit:
            break
    seen_priority_keys = {(item["kind"], item["title"], item["path"]) for item in priority_items}
    for item in insights:
        key = (item["kind"], item["title"], item["path"])
        if key in seen_priority_keys:
            continue
        priority_items.append(item)
        seen_priority_keys.add(key)
        if len(priority_items) >= limit:
            break
    first_useful_sign = insights[0] if insights else (priority_items[0] if priority_items else None)
    action_items = core.list_action_queue(vault_dir, limit=core.MAX_PAGE_SIZE)
    queue_summary = {
        "queued_count": sum(1 for item in action_items if item.get("status") == "queued"),
        "safe_queued_count": sum(
            1 for item in action_items if item.get("status") == "queued" and bool(item.get("safe_to_run"))
        ),
        "running_count": sum(1 for item in action_items if item.get("status") == "running"),
        "failed_count": sum(1 for item in action_items if item.get("status") == "failed"),
        "failure_buckets": dict(
            Counter(
                str(item.get("failure_bucket") or "")
                for item in action_items
                if item.get("status") == "failed" and str(item.get("failure_bucket") or "")
            )
        ),
    }

    return {
        "generated_at": core._utc_now_text(),
        "recent_signal_count": len(recent_signals),
        "unresolved_issue_count": len(unresolved_issues),
        "changed_object_count": len(changed_objects),
        "active_topic_count": len(active_topics),
        "recent_signals": recent_signals,
        "unresolved_issues": unresolved_issues,
        "changed_objects": changed_objects,
        "active_topics": active_topics,
        "insight_count": len(insights),
        "priority_item_count": len(priority_items),
        "insights": insights,
        "priority_items": priority_items,
        "first_useful_sign": first_useful_sign,
        "queue_summary": queue_summary,
    }
