"""Phase 33 durability — re-apply ``ovp-evidence verify``/``backfill`` results.

``rebuild_knowledge_index`` recreates ``claim_evidence`` and ``relations`` from
the pack projection, which would clobber the per-row verification fields
(``locator``, ``content_hash``, ``retrieval_context``, ``status``,
``verified_at``) that :mod:`commands.evidence_verify` writes. Each verify pass
appends one ``evidence_verified`` event per row to
``60-Logs/evidence-verifications.jsonl``; this module replays them after the
projection inserts so verification survives rebuild.

Last-event-wins per ``(table, key tuple)``: a verify-then-reverify sequence
collapses to the latest values during replay.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .event_emitter import collect_for_index, emit
from .runtime import VaultLayout


EVIDENCE_VERIFICATIONS_LOG = "evidence-verifications.jsonl"

_TABLE_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "claim_evidence": ("pack", "claim_id", "source_slug", "evidence_kind"),
    "relations": (
        "pack",
        "source_object_id",
        "target_object_id",
        "relation_type",
        "evidence_source_slug",
    ),
}


def emit_evidence_verified(
    vault_dir: Path | str,
    *,
    table: str,
    key: dict[str, Any],
    locator: str,
    content_hash: str,
    retrieval_context: str,
    status: str,
    verified_at: str,
    pack: str,
) -> None:
    """Append one ``evidence_verified`` event for replay on next rebuild."""
    if table not in _TABLE_KEY_COLUMNS:
        raise ValueError(f"Unsupported evidence table: {table}")
    emit(
        vault_dir,
        EVIDENCE_VERIFICATIONS_LOG,
        "evidence_verified",
        {
            "table": table,
            "key": {col: str(key.get(col, "") or "") for col in _TABLE_KEY_COLUMNS[table]},
            "locator": locator,
            "content_hash": content_hash,
            "retrieval_context": retrieval_context,
            "status": status,
            "verified_at": verified_at,
        },
        pack=pack,
    )


def _latest_per_key(events: Iterable[dict[str, Any]]) -> dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]]:
    """Collapse N events per (table, key) into the most recent one.

    Iteration order is JSONL order (chronological, append-only), so the last
    event for any (table, key) wins. Skips malformed events silently — they
    are just lines in a log, not an authoritative store.
    """
    latest: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "evidence_verified":
            continue
        table = str(event.get("table") or "")
        if table not in _TABLE_KEY_COLUMNS:
            continue
        key = event.get("key") or {}
        if not isinstance(key, dict):
            continue
        try:
            key_tuple = tuple(
                (col, str(key[col])) for col in _TABLE_KEY_COLUMNS[table]
            )
        except KeyError:
            continue
        latest[(table, key_tuple)] = event
    return latest


def replay_evidence_verifications(
    conn: sqlite3.Connection,
    layout: VaultLayout,
    *,
    pack_name: str,
) -> int:
    """Apply the latest verification fields to each (table, key) for ``pack_name``.

    Returns the number of UPDATE statements that affected at least one row;
    events whose target row no longer exists in the projection (e.g. the
    underlying source markdown was deleted) are silently dropped — that row
    will simply remain in the ``unverified`` state on the next verify pass.
    """
    events = collect_for_index(layout, EVIDENCE_VERIFICATIONS_LOG)
    if not events:
        return 0
    pack_events = [
        ev for ev in events if str(ev.get("pack") or "") == pack_name
    ]
    if not pack_events:
        return 0

    applied = 0
    for (table, key_tuple), event in _latest_per_key(pack_events).items():
        key_columns = _TABLE_KEY_COLUMNS[table]
        where_sql = " AND ".join(f"{col} = ?" for col in key_columns)
        key_values = tuple(value for _, value in key_tuple)
        cursor = conn.execute(
            f"""
            UPDATE {table}
               SET locator = ?,
                   content_hash = ?,
                   retrieval_context = ?,
                   status = ?,
                   verified_at = ?
             WHERE {where_sql}
            """,
            (
                str(event.get("locator") or ""),
                str(event.get("content_hash") or ""),
                str(event.get("retrieval_context") or ""),
                str(event.get("status") or ""),
                str(event.get("verified_at") or ""),
                *key_values,
            ),
        )
        if cursor.rowcount:
            applied += 1
    return applied
