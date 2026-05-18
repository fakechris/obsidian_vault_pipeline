# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

import copy as _copy
from collections import OrderedDict as _OrderedDict

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *


# /ops/today day-switch performance: build_today_digest_payload runs
# 6 heavy builders (each its own sqlite connect on a ~350MB db) with
# no caching, so flipping back and forth between dates re-pays the
# full cost every click.  The payload is a pure projection of
# knowledge.db, so an atomic db rebuild (knowledge_index) or any
# ops_state write changes the file's mtime — keying the cache on
# (db_path, db_mtime_ns, date, pack) means a real data change busts
# it (no staleness, the very failure mode this whole effort fought)
# while repeated day navigation between rebuilds is served instantly.
_TODAY_PAYLOAD_CACHE: "_OrderedDict[tuple, dict]" = _OrderedDict()
_TODAY_PAYLOAD_CACHE_MAX = 48


def build_today_digest_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    """mtime-keyed cache wrapper around the real builder.

    The cache key embeds ``knowledge.db``'s ``st_mtime_ns``, so any
    projection rebuild (atomic replace) or ops_state write
    invalidates every cached date for that vault — repeated
    day-switching between rebuilds is O(1), correctness is unchanged.
    """
    from datetime import datetime

    try:
        db_path = _db_path(vault_dir)
        if not db_path.exists():
            # Cheap "not built" path — nothing to cache.
            return _build_today_digest_payload_uncached(
                vault_dir, pack_name=pack_name, target_date=target_date
            )
        if target_date:
            date_key = target_date.strip()
        else:
            date_key = datetime.now().astimezone().strftime("%Y-%m-%d")
        effective_pack = (pack_name or "") or PRIMARY_PACK_NAME
        mtime_ns = db_path.stat().st_mtime_ns
        key = (str(db_path), mtime_ns, date_key, effective_pack)
    except OSError:
        # stat()/path race → bypass the cache, never fail the page.
        return _build_today_digest_payload_uncached(
            vault_dir, pack_name=pack_name, target_date=target_date
        )

    cached = _TODAY_PAYLOAD_CACHE.get(key)
    if cached is not None:
        _TODAY_PAYLOAD_CACHE.move_to_end(key)
        return _copy.deepcopy(cached)

    payload = _build_today_digest_payload_uncached(
        vault_dir, pack_name=pack_name, target_date=target_date
    )
    _TODAY_PAYLOAD_CACHE[key] = payload
    while len(_TODAY_PAYLOAD_CACHE) > _TODAY_PAYLOAD_CACHE_MAX:
        _TODAY_PAYLOAD_CACHE.popitem(last=False)
    return _copy.deepcopy(payload)


def _build_today_digest_payload_uncached(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    """M25.3 hybrid cards for ``/ops/today``.

    Five cards keyed on the lifecycle vocabulary
    (Received / Extracted / Accepted / Synthesized / NeedsAction).
    Each card carries two parallel numbers per the M25 plan
    §M25.3:

    * **Primary** — items currently in this state, read from
      ``ops_state``.  Primary CTA targets ``/ops/items?state=…``
      with NO date param (cards count "current items", not
      date-windowed; adding date would break card-N === page-N).
    * **Secondary** — evidence events for this state in the
      operator's date window, read from ``audit_events``.
      Secondary CTA targets ``/ops/events?event_types=…&date=…``
      (M25.4 will move this to ``/ops/events/audit`` to honor
      raw-audit semantics).

    Samples come from ``ops_state`` rows, not event rows — the
    plan locks this so the visible items match what the primary
    number counted.

    ``target_date`` accepts ``YYYY-MM-DD`` for back-dated views
    (defaults to the operator-local day).  The date affects the
    SECONDARY number only; the primary number is "right now", not
    historic.
    """
    from datetime import datetime

    requested_pack = pack_name or ""
    if target_date:
        date_key = target_date.strip()
    else:
        # BL-102 consistency: Activity buckets by operator-LOCAL
        # day, so the default "today" must be the local day too —
        # a UTC default is off-by-one near midnight for every
        # operator and made now()-seeded tests wall-clock flaky.
        date_key = datetime.now().astimezone().strftime("%Y-%m-%d")

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/today",
            "requested_pack": requested_pack,
            "date": date_key,
            "cards": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    effective_pack = requested_pack or PRIMARY_PACK_NAME
    cards: list[dict[str, Any]] = _build_m25_hybrid_cards(
        db_path,
        date_key=date_key,
        requested_pack=requested_pack,
        effective_pack=effective_pack,
    )

    # Prev/next date pivots so the operator can step through history
    # without crafting query strings.  Always populated (the dossier
    # may be empty for the target date — that is itself useful info).
    from datetime import datetime, timedelta
    try:
        anchor = datetime.strptime(date_key, "%Y-%m-%d")
        prev_date = (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (anchor + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        prev_date = ""
        next_date = ""

    def _date_path(d: str) -> str:
        if not d:
            return ""
        return _scoped_path(f"/ops/today?date={quote(d, safe='')}", pack_name=requested_pack)

    # M25.3: the M24.4 standalone lifecycle backlog strip is now
    # collapsed INTO the cards above (primary number per card).
    # We keep the ``lifecycle_summary`` payload field so the
    # renderer can detect "projection not built yet" and surface
    # an explicit reason banner — same honest-zero rule that
    # already governs every other M24/M25 surface.
    lifecycle_summary = _read_lifecycle_summary(
        vault_dir, pack=requested_pack
    )
    staleness = compute_today_staleness(
        vault_dir, pack=effective_pack
    )
    intake_cohort = build_intake_cohort_payload(
        vault_dir, date_key=date_key, pack=effective_pack
    )
    workflow_progress = build_workflow_progress_payload(
        vault_dir, date_key=date_key, pack=effective_pack
    )

    # BL-103b: attach a zero-reason to every card showing 0 so the
    # operator can tell "did not run" from "ran, no output" from
    # "stale" — a bare 0 is otherwise undiagnosable.
    zero_cards = [c for c in cards if int(c.get("event_count") or 0) == 0]
    if zero_cards:
        try:
            with sqlite3.connect(db_path) as _conn:
                _runs = _stage_runs_for_day(
                    _conn, date_key, effective_pack
                )
        except sqlite3.Error:
            _runs = {}
        for c in zero_cards:
            reason, detail = _zero_reason_for_card(
                str(c.get("id") or ""), _runs, staleness
            )
            c["zero_reason"] = reason
            c["zero_detail"] = detail

    return {
        "screen": "ops/today",
        "requested_pack": requested_pack,
        "date": date_key,
        "prev_date": prev_date,
        "next_date": next_date,
        "prev_date_path": _date_path(prev_date),
        "next_date_path": _date_path(next_date),
        "cards": cards,
        "lifecycle_summary": lifecycle_summary,
        "staleness": staleness,
        "intake_cohort": intake_cohort,
        "workflow_progress": workflow_progress,
        "available": True,
    }



def build_truth_dashboard_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    runtime = get_runtime_status(vault_dir)
    operational_runtime_state = get_operational_runtime_state(vault_dir)
    objects = build_objects_index_payload(vault_dir, limit=12, offset=0, pack_name=pack_name)
    signals = build_signal_browser_payload(vault_dir, pack_name=pack_name)
    production = build_production_browser_payload(vault_dir, pack_name=pack_name)
    production_weak_points = production["weak_points"]
    research_overview_supported = _supports_research_shell(pack_name)
    if research_overview_supported:
        contradictions = build_contradiction_browser_payload(vault_dir, pack_name=pack_name)
        events = build_event_dossier_payload(vault_dir, pack_name=pack_name, limit=8)
        stale_summaries = build_stale_summary_browser_payload(vault_dir, pack_name=pack_name)
        evolution = build_evolution_browser_payload(vault_dir, pack_name=pack_name, status="all")
    else:
        contradictions = {
            "count": 0,
            "open_count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/contradictions", pack_name=requested_pack),
        }
        events = {
            "count": 0,
            "items": [],
            "dates": [],
            "browser_path": _scoped_path("/ops/events", pack_name=requested_pack),
        }
        stale_summaries = {
            "count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/summaries", pack_name=requested_pack),
        }
        evolution = {
            "candidate_count": 0,
            "accepted_count": 0,
            "items": [],
        }
    priorities: list[dict[str, Any]] = []
    if research_overview_supported:
        for item in contradictions["items"][:4]:
            priorities.append(
                {
                    "kind": "contradiction",
                    "label": item["subject_key"],
                    "path": _scoped_path(
                        f"/ops/contradictions?q={quote(str(item['subject_key']), safe='')}",
                        pack_name=requested_pack,
                    ),
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
    else:
        for item in signals["items"][:4]:
            priorities.append(
                {
                    "kind": item["signal_type"],
                    "label": item["title"],
                    "path": item["source_path"],
                    "detail": item["detail"],
                }
            )
    for item in production_weak_points[:4]:
        priorities.append(
            {
                "kind": "production_gap",
                "label": item["title"],
                "path": _scoped_path(
                    f"/note?path={quote(item['note_path'], safe='')}",
                    pack_name=requested_pack,
                ),
                "detail": item["detail"],
            }
        )
    orientation = build_briefing_payload(vault_dir, pack_name=pack_name)
    entry_sections = [
        _compiled_section(
            "what_changed_recently",
            "What Changed Recently",
            summary=f"{orientation.get('changed_object_count', 0)} changed objects and {orientation.get('recent_signal_count', 0)} recent signals surfaced.",
            items=[
                *[
                    {
                        "kind": "changed_object",
                        "label": item["title"],
                        "path": item["path"],
                        "detail": f"Changed object · {item['object_id']}",
                    }
                    for item in orientation.get("changed_objects", [])[:4]
                ]
            ],
        ),
        _compiled_section(
            "important_right_now",
            "Important Right Now",
            summary=f"{len(orientation.get('priority_items', []))} priority items are currently surfaced.",
            items=[
                *[
                    {
                        "kind": str(item["kind"]),
                        "label": str(item["title"]),
                        "path": str(item["path"]),
                        "detail": str(item["detail"]),
                    }
                    for item in orientation.get("priority_items", [])[:4]
                ]
            ],
        ),
        _compiled_section(
            "deserves_review",
            "Deserves Review",
            summary=f"{contradictions['open_count'] if research_overview_supported else signals['count']} review-oriented items are currently in scope.",
            items=(
                [
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(
                            f"/ops/contradictions?q={quote(str(item['subject_key']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"{len(item['object_ids'])} objects in scope",
                    }
                    for item in contradictions["items"][:4]
                ]
                if research_overview_supported
                else [
                    {
                        "kind": str(item["signal_type"]),
                        "label": str(item["title"]),
                        "path": str(item["source_path"]),
                        "detail": str(item["detail"]),
                    }
                    for item in signals["items"][:4]
                ]
            ),
        ),
        _compiled_section(
            "recommended_next_steps",
            "Recommended Next Steps",
            summary="Start with the orientation brief, then move into the highest-signal compiled surfaces.",
            items=[
                {
                    "kind": "orientation",
                    "label": "Orientation Brief",
                    "path": _scoped_path("/ops/briefing", pack_name=requested_pack),
                    "detail": "Open the current knowledge entry product.",
                },
                {
                    "kind": "signals",
                    "label": "Signals",
                    "path": _scoped_path("/ops/signals", pack_name=requested_pack),
                    "detail": "Review current active signals.",
                },
                {
                    "kind": "production",
                    "label": "Production",
                    "path": _scoped_path("/ops/production", pack_name=requested_pack),
                    "detail": "Inspect production weak points.",
                },
                *(
                    [
                        {
                            "kind": "graph",
                            "label": "Clusters",
                            "path": _scoped_path("/ops/clusters", pack_name=requested_pack),
                            "detail": "Explore graph clusters.",
                        }
                    ]
                    if research_overview_supported
                    else []
                ),
            ],
        ),
    ]
    workflow_groups = _build_dashboard_workflow_groups(
        requested_pack=requested_pack,
        research_overview_supported=research_overview_supported,
    )
    return {
        "screen": "truth/dashboard",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="ops_dashboard",
            pack_name=pack_name,
            generated_by="build_truth_dashboard_payload",
            derived_from=("knowledge.db", "runtime ledgers", "review audit"),
        ),
        "research_overview": {
            "status": "supported" if research_overview_supported else "shared_shell_only",
            "reason": (
                "Research-specific overview surfaces are available because this pack resolves through research-tech."
                if research_overview_supported
                else "This pack currently gets the shared home shell only; research-specific overview panels stay hidden until the pack defines its own equivalents."
            ),
        },
        "objects": {
            "count": objects["total_count"],
            "items": objects["items"],
        },
        "contradictions": {
            "count": contradictions["count"],
            "open_count": contradictions["open_count"],
            "items": contradictions["items"][:8],
            "browser_path": _scoped_path("/ops/contradictions", pack_name=requested_pack),
        },
        "events": {
            "count": events["event_count"],
            "items": events["events"][:8],
            "dates": events["dates"],
            "browser_path": _scoped_path("/ops/events", pack_name=requested_pack),
        },
        "stale_summaries": {
            "count": stale_summaries["count"],
            "items": stale_summaries["items"][:8],
            "browser_path": _scoped_path("/ops/summaries", pack_name=requested_pack),
        },
        "evolution": {
            "candidate_count": evolution["candidate_count"],
            "accepted_count": evolution["accepted_count"],
            "items": evolution["candidate_items"][:6],
        },
        "production": {
            **production,
            "browser_path": _scoped_path("/ops/production", pack_name=requested_pack),
            "weak_point_count": len(production_weak_points),
        },
        "signals": {
            **signals,
            "items": signals["items"][:8],
            "browser_path": _scoped_path("/ops/signals", pack_name=requested_pack),
        },
        "runtime": runtime,
        "runtime_state": operational_runtime_state,
        "orientation": orientation,
        "workflow_groups": workflow_groups,
        "entry_sections": entry_sections,
        "recent_review_actions": list_review_actions(vault_dir, limit=8),
        "priorities": priorities[:8],
    }


__all__ = [
    'build_today_digest_payload',
    'build_truth_dashboard_payload'
]
