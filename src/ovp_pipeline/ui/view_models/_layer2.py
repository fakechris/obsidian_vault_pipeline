# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *




def _build_m25_hybrid_cards(
    db_path: Path,
    *,
    date_key: str,
    requested_pack: str,
    effective_pack: str,
) -> list[dict[str, Any]]:
    """Build the five M25 hybrid cards.

    See ``build_today_digest_payload`` docstring for the shape /
    contract.  Single sqlite connection so we don't reopen per
    card.
    """
    cards: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        has_ops_state = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='ops_state'"
        ).fetchone() is not None

        for card_def in M25_LIFECYCLE_CARD_DEFS:
            state = str(card_def["id"])
            event_types = _event_types_for_card(card_def)

            # ── Primary number + samples ──────────────────────
            primary_count = 0
            samples: list[dict[str, str]] = []
            if has_ops_state:
                primary_row = conn.execute(
                    "SELECT COUNT(*) FROM ops_state "
                    " WHERE pack = ? AND state = ?",
                    (effective_pack, state),
                ).fetchone()
                primary_count = int(primary_row[0] or 0) if primary_row else 0

                # Samples: 3 newest items per card, sourced from
                # ``ops_state`` (M25 plan §M25.3 lock — samples
                # come from items, not events).  NeedsAction is
                # the one exception: oldest first so the operator
                # sees the most-aged blockers.
                order_dir = (
                    "ASC" if state == "NeedsAction" else "DESC"
                )
                sample_rows = conn.execute(
                    f"""
                    SELECT item_kind, item_id, last_evidence_at
                      FROM ops_state
                     WHERE pack = ? AND state = ?
                     ORDER BY last_evidence_at {order_dir}
                     LIMIT ?
                    """,
                    (effective_pack, state, TODAY_CARD_SAMPLE_SIZE),
                ).fetchall()

                # Resolve source slugs to vault paths so samples link
                # to real routes (mirrors the M25.2 lookup).
                source_slugs = [
                    str(r[1]) for r in sample_rows
                    if r and r[0] == "source" and r[1]
                ]
                slug_to_path: dict[str, str] = {}
                if source_slugs:
                    has_pages = conn.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pages_index'"
                    ).fetchone()
                    if has_pages is not None:
                        placeholders = ",".join("?" * len(source_slugs))
                        for slug, path in conn.execute(
                            f"SELECT slug, path FROM pages_index "
                            f" WHERE slug IN ({placeholders})",
                            source_slugs,
                        ).fetchall():
                            if slug and path:
                                slug_to_path[str(slug)] = str(path)

                for kind, item_id, last_ts in sample_rows:
                    kind_str = str(kind or "")
                    item_id_str = str(item_id or "")
                    source_path = (
                        slug_to_path.get(item_id_str)
                        if kind_str == "source"
                        else ""
                    )
                    href = _items_primary_href(
                        kind_str, item_id_str, effective_pack,
                        source_path=source_path or "",
                    )
                    samples.append({
                        "item_kind": kind_str,
                        "item_id": item_id_str,
                        "last_evidence_at": str(last_ts or ""),
                        "path": href,
                    })

            # ── Secondary number (distinct items on this day) ──
            # BL-101/BL-102: count DISTINCT items (not raw event
            # rows), bucketed by operator-local day with pack
            # scoping.  ``by_type`` stays a raw-row breakdown so the
            # drilldown evidence still reconciles per event_type.
            event_count = 0
            by_type: dict[str, int] = {}
            if event_types:
                rows = _fetch_activity_rows(
                    conn, event_types, date_key, effective_pack
                )
                identities: set[str] = set()
                for _ts, et, slug, payload in rows:
                    by_type[et] = by_type.get(et, 0) + 1
                    ident = _activity_item_identity(state, slug, payload)
                    if ident is not None:
                        identities.add(ident)
                by_type = dict(
                    sorted(
                        by_type.items(),
                        key=lambda kv: kv[1],
                        reverse=True,
                    )
                )
                event_count = len(identities)

            # ── Hrefs ─────────────────────────────────────────
            # Primary CTA → /ops/items.  Critically NO date param:
            # the primary number is "all current items in this
            # state", not date-windowed (M25 plan §M25.2/3 lock).
            primary_href_parts = [f"state={quote(state, safe='')}"]
            if requested_pack:
                primary_href_parts.append(
                    f"pack={quote(requested_pack, safe='')}"
                )
            primary_href = (
                f"/ops/items?{'&'.join(primary_href_parts)}"
            )

            # Secondary CTA → /ops/events/audit (M25.4).  This is
            # the raw-audit-evidence view that reads the same SQL
            # the card secondary count used, so card N === page N
            # by construction.  The legacy /ops/events (timeline
            # projection) remains accessible from the audit page's
            # role banner.
            #
            # M25.4 (codex review on PR #239): set the URL limit to
            # at LEAST the raw evidence-row volume (NOT the distinct
            # item count) so the drilldown isn't silently truncated
            # — one item can carry many rows.  Clamp to the audit
            # view's hard MAX so the URL stays bounded.
            secondary_href = ""
            if event_types:
                raw_row_total = sum(by_type.values())
                target_limit = max(EVENTS_AUDIT_DEFAULT_LIMIT, raw_row_total)
                target_limit = min(target_limit, EVENTS_AUDIT_MAX_LIMIT)
                see_all_qs_parts = [
                    f"date={quote(date_key, safe='')}",
                    f"limit={target_limit}",
                    "event_types=" + quote(",".join(event_types), safe=""),
                ]
                secondary_href = _scoped_path(
                    f"/ops/events/audit?{'&'.join(see_all_qs_parts)}",
                    pack_name=requested_pack,
                )

            # Per-state secondary label.  Fall back to the
            # conservative "N evidence events today" when the
            # default verb would be misleading.
            secondary_verb = str(card_def.get("secondary_verb", ""))
            secondary_label = (
                f"{event_count} {secondary_verb}"
                if secondary_verb
                else f"{event_count} items today"
            )

            cards.append({
                "id": state,
                "label": str(card_def["label"]),
                "explainer": str(card_def.get("explainer", "")),
                "primary_count": primary_count,
                "primary_href": primary_href,
                "event_count": event_count,
                "event_label": secondary_label,
                "event_href": secondary_href,
                "event_by_type": by_type,
                "event_types": list(event_types),
                "samples": samples,
            })
    return cards



def _build_source_backlink_rail(
    vault_dir: Path | str,
    *,
    detail: dict[str, Any],
    relations: list[dict[str, Any]],
    requested_pack: str,
) -> dict[str, object]:
    object_id = str(detail["object"]["object_id"])
    title = str(detail["object"]["title"])
    evergreen_path = str(detail["provenance"].get("evergreen_path") or "")
    source_notes = [
        {
            **item,
            "excerpt": _source_excerpt_for_object(
                vault_dir,
                note_path=str(item.get("path") or ""),
                object_id=object_id,
                title=title,
            ),
            "jump_path": _build_note_jump_path(item.get("path"), pack_name=requested_pack),
        }
        for item in detail["provenance"]["source_notes"]
    ]
    atlas_pages = [
        {
            **item,
            "jump_path": _build_note_jump_path(item.get("path"), pack_name=requested_pack),
        }
        for item in detail["provenance"]["mocs"]
    ]
    related_objects = []
    for item in relations:
        if len(related_objects) >= OBJECT_SOURCE_RAIL_RELATED_LIMIT:
            break
        related_objects.append(
            {
                "object_id": item["target_object_id"],
                "title": item.get("target_title", item["target_object_id"]),
                "relation_type": item["relation_type"],
                "path": item.get("target_path", ""),
            }
        )
    return {
        "summary": (
            f"{len(source_notes)} source notes, {len(atlas_pages)} atlas pages, "
            f"{len(related_objects)} related objects"
        ),
        "evergreen": {
            "title": title,
            "path": evergreen_path,
            "jump_path": _build_note_jump_path(evergreen_path, pack_name=requested_pack),
        },
        "source_notes": source_notes,
        "atlas_pages": atlas_pages,
        "related_objects": related_objects,
    }



def build_briefing_payload(vault_dir: Path | str, *, pack_name: str | None = None) -> dict[str, Any]:
    requested_pack = pack_name or ""
    surface_contract = describe_observation_surface_contract(
        pack_name=pack_name,
        surface_kind="briefing",
    )
    assembly_contract = _assembly_contract("orientation_brief", pack_name=pack_name)
    governance_contract = describe_governance_contract(pack_name=pack_name)
    if surface_contract["status"] == "missing":
        return {
            "screen": "briefing/intelligence",
            "requested_pack": requested_pack,
            "projection_label": _access_projection_label(
                surface="briefing",
                pack_name=pack_name,
                generated_by="build_briefing_payload",
                derived_from=("knowledge.db", "signals ledger", "actions ledger"),
            ),
            "surface_contract": surface_contract,
            "assembly_contract": assembly_contract,
            "governance_contract": governance_contract,
            "surface_error": (
                f"Pack '{surface_contract['requested_pack']}' does not expose a shared shell "
                f"'briefing' surface."
            ),
            "generated_at": "",
            "recent_signal_count": 0,
            "unresolved_issue_count": 0,
            "changed_object_count": 0,
            "active_topic_count": 0,
            "recent_signals": [],
            "unresolved_issues": [],
            "changed_objects": [],
            "active_topics": [],
            "insight_count": 0,
            "priority_item_count": 0,
            "insights": [],
            "priority_items": [],
            "compiled_sections": [],
            "section_nav": [],
            "first_useful_sign": None,
            "first_useful_sign_check": {
                "status": "empty",
                "kind": "",
                "reason": "No briefing surface is available for this pack.",
                "evidence_count": 0,
                "actionability": "review",
            },
            "background_policy": {
                "governed_signal_types": [],
                "auto_queue_enabled_signal_types": [],
                "review_only_signal_types": [],
                "active_auto_queue_signal_count": 0,
                "active_review_only_signal_count": 0,
                "skipped_signal_count": 0,
                "skipped_reasons": {},
                "signal_type_decisions": {},
            },
            "loop_summary": {
                "productive_count": 0,
                "waiting_count": 0,
                "running_count": 0,
                "ready_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "stalled_count": 0,
                "review_only_count": 0,
            },
            "queue_summary": {
                "queued_count": 0,
                "safe_queued_count": 0,
                "running_count": 0,
                "failed_count": 0,
                "failure_buckets": {},
            },
        }
    snapshot = get_briefing_snapshot(vault_dir, pack_name=pack_name)
    changed_items = [
        {
            "kind": "changed_object",
            "label": str(item["title"]),
            "path": str(item["path"]),
            "detail": f"Changed object · {item['object_id']}",
        }
        for item in snapshot.get("changed_objects", [])[:5]
    ]
    what_matters_items = [
        {
            "kind": "active_topic",
            "label": str(item["title"]),
            "path": str(item["path"]),
            "detail": f"{int(item['signal_count'])} signals in scope",
        }
        for item in snapshot.get("active_topics", [])[:5]
    ]
    needs_review_items = [
        {
            "kind": str(item["signal_type"]),
            "label": str(item["title"]),
            "path": str(item["source_path"]),
            "detail": str(item["detail"]),
        }
        for item in snapshot.get("unresolved_issues", [])[:5]
    ]
    next_read_items = [
        {
            "kind": str(item["kind"]),
            "label": str(item["title"]),
            "path": str(item["path"]),
            "detail": str(item["detail"]),
        }
        for item in snapshot.get("insights", [])[:5]
    ]
    next_action_items = [
        {
            "kind": str(item["kind"]),
            "label": str(item["title"]),
            "path": str(((item.get("recommended_action") or {}).get("path")) or item.get("path") or ""),
            "detail": str(((item.get("recommended_action") or {}).get("label")) or item.get("detail") or ""),
        }
        for item in snapshot.get("priority_items", [])[:5]
    ]
    impact_counts = _impact_counts(snapshot.get("recent_signals", []))
    loop_summary = {
        "productive_count": impact_counts.get("productive", 0),
        "waiting_count": impact_counts.get("waiting", 0),
        "running_count": impact_counts.get("running", 0),
        "ready_count": impact_counts.get("ready", 0),
        "completed_count": impact_counts.get("completed", 0),
        "failed_count": impact_counts.get("failed", 0),
        "stalled_count": impact_counts.get("stalled", 0),
        "review_only_count": impact_counts.get("review_only", 0),
    }
    signal_loop_items = [
        {
            "kind": "productive",
            "label": "Productive",
            "path": _scoped_path("/ops/signals", pack_name=requested_pack),
            "detail": f"{loop_summary['productive_count']} signals produced visible downstream change.",
        },
        {
            "kind": "waiting",
            "label": "Waiting",
            "path": _scoped_path("/ops/actions", pack_name=requested_pack),
            "detail": f"{loop_summary['waiting_count']} signals currently have queued execution waiting.",
        },
        {
            "kind": "running",
            "label": "Running",
            "path": _scoped_path("/ops/actions", pack_name=requested_pack),
            "detail": f"{loop_summary['running_count']} signals are currently executing.",
        },
        {
            "kind": "blocked",
            "label": "Blocked",
            "path": _scoped_path("/ops/actions", pack_name=requested_pack),
            "detail": (
                f"{loop_summary['failed_count'] + loop_summary['stalled_count']} signals are failed or stalled."
            ),
        },
        {
            "kind": "review_only",
            "label": "Review Only",
            "path": _scoped_path("/ops/signals", pack_name=requested_pack),
            "detail": f"{loop_summary['review_only_count']} signals currently route to review rather than queued execution.",
        },
    ]
    productive_signal = next(
        (
            item
            for item in snapshot.get("recent_signals", [])
            if str((item.get("impact_summary") or {}).get("impact_status") or "") == "productive"
        ),
        None,
    )
    first_useful_sign = (
        {
            "signal_id": str(productive_signal.get("signal_id") or ""),
            "kind": str(productive_signal.get("signal_type") or ""),
            "title": str(productive_signal.get("title") or ""),
            "detail": str((productive_signal.get("impact_summary") or {}).get("impact_detail") or ""),
            "path": str(
                ((productive_signal.get("recommended_action") or {}).get("queue_path"))
                or ((productive_signal.get("recommended_action") or {}).get("path"))
                or productive_signal.get("source_path")
                or ""
            ),
            "source_paths": list(productive_signal.get("note_paths", [])),
            "object_ids": list(productive_signal.get("object_ids", [])),
            "recommended_action": productive_signal.get("recommended_action"),
        }
        if productive_signal is not None
        else snapshot.get("first_useful_sign")
    )
    first_useful_sign_check = _briefing_value_check(first_useful_sign)
    inbound_capture_items = [
        {
            "kind": "capture_signal",
            "label": str(item.get("title") or ""),
            "path": str(item.get("source_path") or ""),
            "detail": str((item.get("capture_summary") or {}).get("summary") or ""),
        }
        for item in snapshot.get("recent_signals", [])
        if str((item.get("capture_summary") or {}).get("status") or "") != "missing"
    ]
    compiled_sections = [
        _compiled_section(
            "signal_loop",
            "Signal Loop",
            summary=(
                f"{loop_summary['productive_count']} productive, "
                f"{loop_summary['waiting_count']} waiting, "
                f"{loop_summary['failed_count'] + loop_summary['stalled_count']} blocked/stalled."
            ),
            items=signal_loop_items,
        ),
        _compiled_section(
            "inbound_capture",
            "Inbound Capture",
            summary=(
                f"{len(inbound_capture_items)} recent signals currently expose deterministic inbound capture audit."
                if inbound_capture_items
                else "No recent signals currently carry inbound capture audit."
            ),
            items=inbound_capture_items,
        ),
        _compiled_section(
            "what_changed",
            "What Changed",
            summary=f"{len(changed_items)} changed objects surfaced recently.",
            items=changed_items,
        ),
        _compiled_section(
            "what_matters",
            "What Matters",
            summary=f"{len(what_matters_items)} active topics currently dominate the signal surface.",
            items=what_matters_items,
        ),
        _compiled_section(
            "needs_review",
            "Needs Review",
            summary=f"{len(needs_review_items)} unresolved issues currently deserve attention.",
            items=needs_review_items,
        ),
        _compiled_section(
            "next_reads",
            "Next Reads",
            summary=f"{len(next_read_items)} compiled next-read routes were surfaced from current evidence.",
            items=next_read_items,
        ),
        _compiled_section(
            "next_actions",
            "Next Actions",
            summary=f"{len(next_action_items)} next actions are currently available from the queue and briefing logic.",
            items=next_action_items,
        ),
    ]
    operator_rail = [
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Open active signal review from the current shell.",
        ),
        _operator_action(
            "Action Queue",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Run or inspect queued actions.",
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect production-chain weak points and reach.",
        ),
        _operator_action(
            "Search",
            _scoped_path("/search", pack_name=requested_pack),
            "Jump into freeform search from the current orientation pass.",
        ),
    ]
    payload: dict[str, Any] = {
        "screen": "briefing/intelligence",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="briefing",
            pack_name=pack_name,
            generated_by="build_briefing_payload",
            derived_from=("knowledge.db", "signals ledger", "actions ledger"),
        ),
        "surface_contract": surface_contract,
        "assembly_contract": assembly_contract,
        "governance_contract": governance_contract,
        **snapshot,
        "first_useful_sign": first_useful_sign,
        "first_useful_sign_check": first_useful_sign_check,
        "loop_summary": loop_summary,
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }
    _emit_briefing_reuse(
        vault_dir,
        payload,
        pack=requested_pack or PRIMARY_PACK_NAME,
        consumer_ref="view:briefing",
    )
    return payload



def build_evolution_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    status: str = "all",
    link_type: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    evolution = _build_evolution_section(
        vault_dir,
        pack_name=pack_name,
        query=query,
        link_type=link_type,
        status=status,
    )
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
        "requested_pack": requested_pack,
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



def build_workflow_progress_payload(
    vault_dir: Path | str, *, date_key: str, pack: str
) -> dict[str, Any]:
    """BL-104: which items MOVED into a lifecycle state on this day.

    Distinct from the other two date surfaces:

    * **Activity** counts evidence ROWS on the event day.
    * **Workflow Progress** (here) counts distinct ITEMS whose
      EARLIEST qualifying evidence for a state lands on this day —
      i.e. the day they *entered* that state ("16 sources moved
      into Extracted today"), the transition-time axis.
    * **Current Backlog** is the right-now snapshot.

    Earliest-qualifying-evidence-day is a sound, storage-free proxy
    for entry-into-state: the lifecycle kernel derives state from
    cumulative evidence, so the first time an item has evidence of a
    state's qualifying types is the day it reached that state.
    Reuses BL-101 identity + BL-102 local-day + the card
    event_type composition so it cannot drift from the cards.
    """
    db_path = _db_path(vault_dir)
    base: dict[str, Any] = {
        "screen": "ops/workflow-progress",
        "date": date_key,
        "requested_pack": pack,
        "available": False,
        "reason": "",
        "moved": {},
        "total": 0,
        "samples": {},
    }
    if not db_path.exists():
        base["reason"] = "knowledge_index has not been built yet"
        return base

    effective_pack = pack or PRIMARY_PACK_NAME
    moved: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    with sqlite3.connect(db_path) as conn:
        for card_def in M25_LIFECYCLE_CARD_DEFS:
            state = str(card_def["id"])
            ets = _event_types_for_card(card_def)
            moved[state] = 0
            samples[state] = []
            if not ets:
                continue
            et_ph = ",".join("?" for _ in ets)
            earliest: dict[str, Any] = {}
            for ts, slug, pj in conn.execute(
                f"SELECT timestamp, slug, payload_json FROM audit_events "
                f" WHERE event_type IN ({et_ph})",
                ets,
            ):
                parsed = _parse_audit_ts(str(ts or ""))
                if parsed is None:
                    continue
                try:
                    payload = json.loads(pj or "{}")
                except ValueError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                rp = _audit_row_pack(payload)
                if rp is None:
                    if effective_pack != PRIMARY_PACK_NAME:
                        continue
                elif rp != effective_pack:
                    continue
                ident = _activity_item_identity(
                    state, str(slug or ""), payload
                )
                if ident is None:
                    continue
                cur = earliest.get(ident)
                if cur is None or parsed < cur:
                    earliest[ident] = parsed
            entered = sorted(
                sid
                for sid, ts in earliest.items()
                if ts.astimezone().date().isoformat() == date_key
            )
            moved[state] = len(entered)
            samples[state] = entered[:TODAY_CARD_SAMPLE_SIZE]

    base.update(
        available=True,
        moved=moved,
        total=sum(moved.values()),
        samples=samples,
    )
    return base


__all__ = [
    '_build_m25_hybrid_cards',
    '_build_source_backlink_rail',
    'build_briefing_payload',
    'build_evolution_browser_payload',
    'build_workflow_progress_payload'
]
