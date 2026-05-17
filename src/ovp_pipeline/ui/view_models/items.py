# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *




def build_items_list_payload(
    vault_dir: Path | str,
    *,
    state: str,
    pack_name: str | None = None,
    offset: int = 0,
    limit: int = ITEMS_LIST_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """M25.2: ``/ops/items?state=<state>`` payload.

    Reads ``ops_state`` (built by M24.1's ``ovp-ops-state``) and
    returns the items currently in ``state``.  This is the route
    the M25 hybrid card primary CTA targets, so card N === page N
    is a hard contract: both numbers come from the same projection
    table with the same pack filter.

    No ``date=`` filter — the primary card number is "all current
    items in this state", not date-windowed.  The plan doc locks
    this in §M25.2 / M25.3 acceptance.
    """
    from ovp_pipeline.ops_lifecycle import ALL_STATES

    requested_pack = pack_name or ""
    state = state.strip()
    if state not in ALL_STATES:
        return {
            "screen": "ops/items",
            "available": False,
            "reason": (
                f"unknown state {state!r}; expected one of "
                f"{ALL_STATES}"
            ),
            "state": state,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
        }

    safe_limit = max(1, min(int(limit or ITEMS_LIST_DEFAULT_LIMIT), ITEMS_LIST_MAX_LIMIT))
    safe_offset = max(0, int(offset or 0))

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/items",
            "available": False,
            "reason": "knowledge_index has not been built yet",
            "state": state,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    effective_pack = requested_pack or PRIMARY_PACK_NAME
    try:
        with sqlite3.connect(db_path) as conn:
            # Guard: ops_state may not exist yet (M24.1 DAG step
            # hasn't run).  Surface explicitly rather than crash.
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='ops_state'"
            ).fetchone()
            if row is None:
                return {
                    "screen": "ops/items",
                    "available": False,
                    "reason": (
                        "ops_state projection not built yet — run "
                        "`ovp-ops-state --rebuild`"
                    ),
                    "state": state,
                    "requested_pack": requested_pack,
                    "rows": [],
                    "total": 0,
                    "offset": safe_offset,
                    "limit": safe_limit,
                }

            total_row = conn.execute(
                "SELECT COUNT(*) FROM ops_state "
                " WHERE pack = ? AND state = ?",
                (effective_pack, state),
            ).fetchone()
            total = int(total_row[0] or 0) if total_row else 0

            # M25.2: NeedsAction surfaces oldest-first so the
            # operator can attack the most-aged blockers first.
            # Every other state surfaces newest-first.
            order_dir = (
                "ASC" if state == "NeedsAction" else "DESC"
            )
            rows = conn.execute(
                f"""
                SELECT item_kind, item_id, sub_state,
                       last_evidence_at, evidence_event_types_json,
                       needs_action_reason
                  FROM ops_state
                 WHERE pack = ? AND state = ?
                 ORDER BY last_evidence_at {order_dir}
                 LIMIT ? OFFSET ?
                """,
                (effective_pack, state, safe_limit, safe_offset),
            ).fetchall()

            # M25.2 (codex review on PR #236): source-kind items
            # don't have a known canonical drilldown route yet
            # (the M25.4 ``/ops/events/audit`` view doesn't exist
            # until that PR lands).  Resolve source slugs to their
            # real file paths via ``pages_index`` so the primary
            # link points at ``/note?path=…`` — a route that
            # exists.  Sources we can't resolve fall through to an
            # unlinked cell.
            source_slugs = [
                str(r[1]) for r in rows
                if r and r[0] == "source" and r[1]
            ]
            slug_to_path: dict[str, str] = {}
            if source_slugs:
                page_row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='pages_index'"
                ).fetchone()
                if page_row is not None:
                    placeholders = ",".join("?" * len(source_slugs))
                    page_rows = conn.execute(
                        f"SELECT slug, path FROM pages_index "
                        f" WHERE slug IN ({placeholders})",
                        source_slugs,
                    ).fetchall()
                    slug_to_path = {
                        str(s): str(p) for s, p in page_rows if s and p
                    }
    except sqlite3.OperationalError as exc:
        return {
            "screen": "ops/items",
            "available": False,
            "reason": f"ops_state read failed: {exc}",
            "state": state,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    items: list[dict[str, Any]] = []
    for kind, item_id, sub_state, last_evidence_at, evt_json, na_reason in rows:
        try:
            evt_types = json.loads(evt_json) if evt_json else []
        except (TypeError, ValueError):
            evt_types = []
        # Top-3 evidence types for the row preview; rest are
        # available on the item's drilldown (out of scope for v1).
        evt_preview = list(evt_types)[:3] if isinstance(evt_types, list) else []
        kind_str = str(kind or "")
        item_id_str = str(item_id or "")
        resolved_source_path = (
            slug_to_path.get(item_id_str) if kind_str == "source" else ""
        )
        items.append({
            "item_kind": kind_str,
            "item_id": item_id_str,
            "sub_state": str(sub_state) if sub_state else "",
            "last_evidence_at": str(last_evidence_at or ""),
            "evidence_types": evt_preview,
            "needs_action_reason": str(na_reason) if na_reason else "",
            "primary_href": _items_primary_href(
                kind_str, item_id_str, effective_pack,
                source_path=resolved_source_path,
            ),
        })

    has_more = safe_offset + len(items) < total
    next_offset = safe_offset + safe_limit if has_more else None
    prev_offset = max(0, safe_offset - safe_limit) if safe_offset > 0 else None

    return {
        "screen": "ops/items",
        "available": True,
        "state": state,
        "pack": effective_pack,
        "requested_pack": requested_pack,
        "rows": items,
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "next_offset": next_offset,
        "prev_offset": prev_offset,
    }


__all__ = [
    'build_items_list_payload'
]
