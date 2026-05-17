# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *




def build_timeline_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Daily digest of ``audit_events`` for the maintainer dashboard.

    Pre-fix the maintainer side had ``/ops/pulse`` (live tail) and
    ``/ops/events`` (object-keyed dossier) but no "what got created
    today / yesterday / last week" view.  This payload groups the
    last ``days`` days of audit events by date, surfaces the highest-
    signal event types per day (new evergreens, github intake,
    absorb errors, crystal synthesis), and samples a handful of
    affected slugs so the user can click straight through to a
    specific note from the dashboard.

    Returns ``{"days": [{date, total, by_type: {...},
    samples: [{slug, title, path}], errors: [{type, slug, snippet}]}]}``
    in reverse-chronological order.
    """
    from datetime import datetime, timedelta, timezone
    requested_pack = pack_name or ""
    window = max(1, days if days is not None else DEFAULT_TIMELINE_DAYS)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).strftime("%Y-%m-%d")

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/timeline",
            "requested_pack": requested_pack,
            "window_days": window,
            "days": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    days_map: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as conn:
        # Per-day, per-type counts.  ``date()`` rolls a UTC ISO
        # timestamp into ``YYYY-MM-DD`` — the same key the renderer
        # uses to header each section.
        rows = conn.execute(
            """
            SELECT date(timestamp) AS day, event_type, COUNT(*) AS n
              FROM audit_events
             WHERE date(timestamp) >= ?
             GROUP BY day, event_type
            """,
            (cutoff,),
        ).fetchall()
        for day, event_type, count in rows:
            if not day:
                continue
            bucket = days_map.setdefault(day, {
                "date": day, "total": 0, "by_type": {},
                "samples": [], "errors": [],
            })
            bucket["by_type"][event_type] = int(count)
            bucket["total"] += int(count)

        # Sample evergreens promoted today/yesterday/etc — give the
        # user a clickable list rather than a bare count.
        sample_rows = conn.execute(
            """
            SELECT date(timestamp) AS day,
                   json_extract(payload_json, '$.slug') AS slug,
                   json_extract(payload_json, '$.title') AS title
              FROM audit_events
             WHERE event_type = 'evergreen_auto_promoted'
               AND date(timestamp) >= ?
             ORDER BY timestamp DESC
            """,
            (cutoff,),
        ).fetchall()
        for day, slug, title in sample_rows:
            if not day or not slug:
                continue
            bucket = days_map.get(day)
            if bucket is None:
                continue
            if len(bucket["samples"]) >= DEFAULT_TIMELINE_SAMPLE_SIZE:
                continue
            note_path = f"10-Knowledge/Evergreen/{slug}.md"
            bucket["samples"].append({
                "slug": str(slug),
                "title": str(title or slug),
                "note_href": _scoped_path(
                    f"/note?path={quote(note_path, safe='')}",
                    pack_name=requested_pack,
                ),
            })

        # Error / skip events get their own short list — these are
        # the things the maintainer most often opens the dashboard
        # to chase down.  Types live in ``TIMELINE_ERROR_EVENT_TYPES``
        # so the SQL ``IN`` clause stays in sync with downstream
        # consumers (e.g. the renderer's "error" pill colouring).
        placeholders = ",".join("?" for _ in TIMELINE_ERROR_EVENT_TYPES)
        error_rows = conn.execute(
            f"""
            SELECT date(timestamp) AS day, event_type,
                   COALESCE(json_extract(payload_json, '$.source'),
                            json_extract(payload_json, '$.slug'),
                            slug) AS subject,
                   substr(payload_json, 1, {TIMELINE_SNIPPET_CHARS}) AS snippet
              FROM audit_events
             WHERE event_type IN ({placeholders})
               AND date(timestamp) >= ?
             ORDER BY timestamp DESC
            """,
            (*TIMELINE_ERROR_EVENT_TYPES, cutoff),
        ).fetchall()
        for day, event_type, subject, snippet in error_rows:
            if not day:
                continue
            bucket = days_map.get(day)
            if bucket is None:
                continue
            if len(bucket["errors"]) >= DEFAULT_TIMELINE_SAMPLE_SIZE:
                continue
            bucket["errors"].append({
                "event_type": str(event_type),
                "subject": str(subject or "(unspecified)"),
                "snippet": str(snippet or ""),
            })

    days_sorted = sorted(
        days_map.values(), key=lambda d: d["date"], reverse=True,
    )
    return {
        "screen": "ops/timeline",
        "requested_pack": requested_pack,
        "window_days": window,
        "days": days_sorted,
        "available": True,
        "highlighted_types": list(TIMELINE_HIGHLIGHTED_TYPES),
    }



def build_events_audit_payload(
    vault_dir: Path | str,
    *,
    event_types: tuple[str, ...] | list[str] | None = None,
    date_key: str = "",
    pack_name: str | None = None,
    limit: int = EVENTS_AUDIT_DEFAULT_LIMIT,
    state: str = "",
) -> dict[str, Any]:
    """M25.4: ``/ops/events/audit`` raw-audit-evidence view.

    The M25 cards' SECONDARY count comes from a query against the
    raw ``audit_events`` table.  ``/ops/events`` today renders
    **timeline projections** (``list_timeline_events`` over dated
    notes + contradictions) — a different ledger.  Pointing the
    card's secondary CTA at that page resurrects the M24.0
    two-ledger problem (card N != page N).

    This view fixes the contract: it reads ``audit_events``
    directly using the same SQL the card uses, so card N === page
    N by construction.  Flat table, no timeline grouping.

    Plan contract (locked in M25 §M25.4, tightened by M26 BL-102):
    * ``event_types`` is the card's event_types list — required
      so the page rows match what the card counted.
    * ``date_key`` filters to that day, bucketed by operator-local
      day via the shared ``audit_time`` parser (NOT SQLite
      ``date(timestamp)``) so it matches the card exactly.
    * Pack scoping is applied to rows (BL-102): matching payload
      pack included, different excluded, legacy pack-less rows only
      under the default pack — identical to the card.
    * ``state`` (optional) lets the page report the distinct-item
      count; inferred from ``event_types`` when omitted.  The card
      count equals ``distinct_item_count`` here by construction —
      both go through ``_fetch_activity_rows`` +
      ``_activity_item_identity``.
    """
    requested_pack = pack_name or ""
    event_types_tup = tuple(event_types or ())

    safe_limit = max(1, min(int(limit or EVENTS_AUDIT_DEFAULT_LIMIT), EVENTS_AUDIT_MAX_LIMIT))

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/events/audit",
            "available": False,
            "reason": "knowledge_index has not been built yet",
            "state": state or _state_for_event_types(event_types_tup),
            "distinct_item_count": 0,
            "event_types": list(event_types_tup),
            "date": date_key,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "limit": safe_limit,
        }

    resolved_state = state or _state_for_event_types(event_types_tup)
    effective_pack = requested_pack or PRIMARY_PACK_NAME

    audit_rows: list[dict[str, Any]] = []
    distinct_item_count = 0

    def _row(ts: str, et: str, slug: str, payload_str: str, src: str) -> dict[str, Any]:
        snippet = (
            payload_str[:117] + "…" if len(payload_str) > 120 else payload_str
        )
        return {
            "timestamp": ts,
            "event_type": et,
            "slug": slug,
            "payload_snippet": snippet,
            "payload_full": payload_str,
            "source_log": src,
        }

    if event_types_tup and date_key:
        # Card-drilldown path: identical scoping to the card
        # (operator-local day + pack) so card N === page N by
        # construction.  source_log isn't returned by the shared
        # fetch; re-read it here keyed on the same scoped rows.
        with sqlite3.connect(db_path) as conn:
            scoped = _fetch_activity_rows(
                conn, event_types_tup, date_key, effective_pack
            )
        identities: set[str] = set()
        for ts, et, slug, payload in scoped:
            if resolved_state:
                ident = _activity_item_identity(resolved_state, slug, payload)
                if ident is not None:
                    identities.add(ident)
            audit_rows.append(
                _row(
                    ts,
                    et,
                    slug,
                    json.dumps(payload, ensure_ascii=False)
                    if payload
                    else "",
                    "",
                )
            )
        distinct_item_count = len(identities)
        total = len(audit_rows)
        audit_rows.sort(key=lambda r: r["timestamp"], reverse=True)
        audit_rows = audit_rows[:safe_limit]
    else:
        # Legacy landing (no scope): N most-recent rows across all
        # event_types so the page isn't empty when the operator
        # arrives from the timeline-projection role banner.
        where: list[str] = []
        params: list[object] = []
        if event_types_tup:
            placeholders = ",".join("?" for _ in event_types_tup)
            where.append(f"event_type IN ({placeholders})")
            params.extend(event_types_tup)
        if date_key:
            where.append("date(timestamp) = ?")
            params.append(date_key)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with sqlite3.connect(db_path) as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) FROM audit_events {where_sql}",
                params,
            ).fetchone()
            total = int(total_row[0] or 0) if total_row else 0
            rows = conn.execute(
                f"""
                SELECT timestamp, event_type, slug, payload_json, source_log
                  FROM audit_events
                 {where_sql}
                 ORDER BY timestamp DESC
                 LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
        for ts, event_type, slug, payload_json, source_log in rows:
            audit_rows.append(
                _row(
                    str(ts or ""),
                    str(event_type or ""),
                    str(slug or ""),
                    str(payload_json or ""),
                    str(source_log or ""),
                )
            )

    return {
        "screen": "ops/events/audit",
        "available": True,
        "reason": "",
        "state": resolved_state,
        "distinct_item_count": distinct_item_count,
        "event_types": list(event_types_tup),
        "date": date_key,
        "requested_pack": requested_pack,
        "rows": audit_rows,
        "total": total,
        "limit": safe_limit,
    }



def build_run_detail_payload(
    vault_dir: Path | str,
    txn_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Per-transaction event timeline for ``/ops/runs/<txn_id>``.

    Joins via ``session_id`` because most pipeline events don't
    carry ``txn_id`` directly — they're emitted by the same
    PipelineLogger process whose ``session_id`` is the column-
    level grouping key.  Approach:

    1. Find every ``transaction_started`` row matching ``txn_id`` —
       collect all ``session_id`` values that participated in the
       run.  Spawned subprocesses (``pinboard_process`` step etc.)
       have their own session_id but they all bracket themselves
       with ``transaction_started`` rows that share the parent
       ``txn_id`` chain — for the pipeline's current shape, the
       parent's session_id covers the whole run.
    2. SELECT every audit row whose ``session_id`` is in that set
       OR whose ``payload_json.txn_id`` matches.  This is the
       union of "events the bracketing logger wrote" + "events
       that explicitly tagged themselves with the txn".
    3. Order by timestamp, return with subject + snippet.
    """
    requested_pack = pack_name or ""
    cleaned_txn = (txn_id or "").strip()
    if not cleaned_txn:
        return {
            "screen": "ops/runs/detail",
            "requested_pack": requested_pack,
            "txn_id": "",
            "events": [],
            "available": False,
            "reason": "txn_id required",
        }

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/runs/detail",
            "requested_pack": requested_pack,
            "txn_id": cleaned_txn,
            "events": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    with sqlite3.connect(db_path) as conn:
        # Step 1: collect session_ids for this txn's bracketing rows.
        # ``$.type`` is pulled via SQLite's ``json_extract`` (proper
        # JSON parser) rather than substring-regex over the snippet —
        # the snippet was truncating long payloads and a JSON
        # formatter change could trivially break a regex match.
        bracket_rows = conn.execute(
            """
            SELECT session_id, timestamp, event_type,
                   json_extract(payload_json, '$.type') AS workflow_type
              FROM audit_events
             WHERE event_type IN ('transaction_started', 'transaction_completed')
               AND json_extract(payload_json, '$.txn_id') = ?
            """,
            (cleaned_txn,),
        ).fetchall()
        session_ids = {row[0] for row in bracket_rows if row[0]}

        if not session_ids and not bracket_rows:
            return {
                "screen": "ops/runs/detail",
                "requested_pack": requested_pack,
                "txn_id": cleaned_txn,
                "events": [],
                "available": False,
                "reason": f"no events found for txn_id {cleaned_txn}",
            }

        # Step 2: fetch every event in those sessions OR tagged
        # with the txn directly.  ``OR`` instead of ``UNION`` so
        # the SQLite optimiser can use both indexes.
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            rows = conn.execute(
                f"""
                SELECT timestamp, event_type, session_id,
                       COALESCE(json_extract(payload_json, '$.slug'),
                                json_extract(payload_json, '$.source'),
                                json_extract(payload_json, '$.file'),
                                json_extract(payload_json, '$.url'),
                                slug) AS subject,
                       substr(payload_json, 1, {TIMELINE_SNIPPET_CHARS}) AS snippet
                  FROM audit_events
                 WHERE session_id IN ({placeholders})
                    OR json_extract(payload_json, '$.txn_id') = ?
                 ORDER BY timestamp ASC
                """,
                (*session_ids, cleaned_txn),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT timestamp, event_type, session_id,
                       COALESCE(json_extract(payload_json, '$.slug'),
                                json_extract(payload_json, '$.source'),
                                json_extract(payload_json, '$.file'),
                                json_extract(payload_json, '$.url'),
                                slug) AS subject,
                       substr(payload_json, 1, {TIMELINE_SNIPPET_CHARS}) AS snippet
                  FROM audit_events
                 WHERE json_extract(payload_json, '$.txn_id') = ?
                 ORDER BY timestamp ASC
                """,
                (cleaned_txn,),
            ).fetchall()

    events = [
        {
            "timestamp": str(ts or ""),
            "event_type": str(event_type or ""),
            "session_id": str(session_id or ""),
            "subject": str(subject or ""),
            "snippet": str(snippet or ""),
        }
        for ts, event_type, session_id, subject, snippet in rows
    ]

    # Header data: pull from the bracketing rows we already fetched.
    # ``event_type`` is a column, not a payload field — keying off the
    # column lets us populate workflow_type from the payload's ``type``
    # without re-querying.
    started_at = ""
    completed_at = ""
    workflow_type = ""
    for _session_id, ts, event_type, payload_workflow_type in bracket_rows:
        if event_type == "transaction_started":
            started_at = str(ts or "")
            if payload_workflow_type:
                workflow_type = str(payload_workflow_type)
        elif event_type == "transaction_completed":
            completed_at = str(ts or "")

    return {
        "screen": "ops/runs/detail",
        "requested_pack": requested_pack,
        "txn_id": cleaned_txn,
        "workflow_type": workflow_type,
        "started_at": started_at,
        "completed_at": completed_at,
        "session_ids": sorted(session_ids),
        "events": events,
        "event_count": len(events),
        "available": True,
    }


__all__ = [
    'build_timeline_payload',
    'build_events_audit_payload',
    'build_run_detail_payload'
]
