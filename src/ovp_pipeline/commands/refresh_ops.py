"""``ovp-refresh-ops`` — the codified post-absorb lifecycle refresh.

Operator-flagged problem: after an absorb run, deciding how to
refresh the lifecycle dashboard was a manual judgement call that
too easily mis-ran the heavy full ``ovp-knowledge-index`` rebuild
(~482MB RSS, 20+ min on the operator vault).  For the common case
— absorb produced only candidates, no new canonical objects — the
full rebuild re-derives nothing; the lightweight audit-sync +
ops_state rebuild gives the exact correct state movement.

This command hard-codes the rule so the judgement is no longer
manual:

1. Snapshot the pre-refresh per-state ``ops_state`` counts.
2. ``sync_audit_events_from_jsonl`` — re-ingest ``audit_events``
   from ``pipeline.jsonl`` ONLY (no embeddings, no truth /
   graph projection rebuild, low memory).
3. ``ops_state.rebuild`` — re-derive the lifecycle projection.
4. Print the state diff (before → after, conserved-total check).
5. Inspect the just-synced audit window for canonical-object
   evidence (``evergreen_auto_promoted`` / ``promote_concept``).
   * none → print "full knowledge rebuild NOT needed".
   * present → print an explicit WARNING that canonical objects
     changed and a projection / full rebuild may be warranted,
     and exit non-zero so a wrapper script can branch on it.

Read-only over markdown; the only writes are to the derived
``knowledge.db`` (audit_events + ops_state), exactly what the
lightweight path already does.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..audit_time import parse_audit_ts as _parse_audit_ts
from ..knowledge_index import sync_audit_events_from_jsonl
from ..ops_lifecycle import ALL_STATES
from ..ops_state import rebuild as rebuild_ops_state
from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from ..runtime import resolve_vault_dir

KNOWLEDGE_DB_REL = "60-Logs/knowledge.db"

# Audit event types that mean a CANONICAL object materialized or
# changed — the only case where the lightweight path is
# insufficient and a heavier projection / full rebuild is
# warranted.  Candidate-only evidence
# (candidates_upserted / absorb_pending_upsert /
# absorb_route_decision) does NOT require a full rebuild.
_CANONICAL_OBJECT_EVENTS = (
    "evergreen_auto_promoted",
    "promote_concept",
    "evergreen_created",
)


def _db_path(vault_dir: Path) -> Path:
    return vault_dir / KNOWLEDGE_DB_REL


def _state_counts(conn: sqlite3.Connection, pack: str) -> dict[str, int]:
    counts = {s: 0 for s in ALL_STATES}
    # The ``ops_state`` table may not exist yet on a vault that has
    # never run the projection — the pre-refresh snapshot must
    # degrade to all-zeros, not crash (it gets created by the
    # rebuild that follows).
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master " "WHERE type='table' AND name='ops_state'"
    ).fetchone()
    if not has_table:
        return counts
    rows = conn.execute(
        "SELECT state, COUNT(*) FROM ops_state " " WHERE pack = ? GROUP BY state",
        (pack,),
    ).fetchall()
    for state, n in rows:
        if state in counts:
            counts[state] = int(n)
    return counts


def _row_pack(payload_json: str) -> str | None:
    """Pack recorded inside the audit payload, or None if absent.

    ``event_emitter`` stores ``pack`` in ``payload_json``; legacy
    rows predate that field.  None means "pack unknown".
    """
    try:
        payload = json.loads(payload_json or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    pack = payload.get("pack")
    return str(pack) if pack else None


def _canonical_evidence_since(
    conn: sqlite3.Connection, window_minutes: int, pack: str
) -> dict[str, int]:
    """Count canonical-object audit events for ``pack`` in the window.

    Timestamps are parsed in Python rather than compared
    lexicographically in SQL — audit rows mix ISO ``T`` and
    space-separated formats, and a string compare across that
    boundary misclassifies rows (false positives that wrongly
    trigger the heavy rebuild path).  We only need "did any land",
    so precision beyond the parse is unnecessary.

    Evidence is scoped to ``pack``: a recent ``promote_concept`` for a
    DIFFERENT pack must not make this command tell wrappers the
    selected pack needs a heavier rebuild.  Legacy rows with no
    recorded pack are kept (conservative — never suppress a real
    warning just because an old row lacks the field).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=int(window_minutes))
    placeholders = ",".join("?" * len(_CANONICAL_OBJECT_EVENTS))
    rows = conn.execute(
        f"""
        SELECT event_type, timestamp, payload_json
          FROM audit_events
         WHERE event_type IN ({placeholders})
        """,
        (*_CANONICAL_OBJECT_EVENTS,),
    ).fetchall()
    found: dict[str, int] = {}
    for et, ts, payload_json in rows:
        row_pack = _row_pack(str(payload_json or ""))
        if row_pack is not None and row_pack != pack:
            continue
        parsed = _parse_audit_ts(str(ts or ""))
        if parsed is None or parsed < cutoff:
            continue
        key = str(et)
        found[key] = found.get(key, 0) + 1
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Codified post-absorb lifecycle refresh: audit-sync + "
            "ops_state rebuild + state diff.  Avoids the heavy "
            "full ovp-knowledge-index rebuild for the common "
            "candidates-only case."
        )
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument(
        "--pack",
        default=DEFAULT_WORKFLOW_PACK_NAME,
        help="Pack to refresh",
    )
    parser.add_argument(
        "--canonical-window-minutes",
        type=int,
        default=180,
        help=(
            "Look-back window for detecting canonical-object "
            "evidence that would warrant a heavier rebuild "
            "(default: 180)."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    db_path = _db_path(vault_dir)
    if not db_path.is_file():
        print(
            f"refresh-ops: no knowledge.db at {db_path} — run " "`ovp-knowledge-index` once first.",
            file=sys.stderr,
        )
        return 1

    # 1. pre-state
    with sqlite3.connect(str(db_path)) as conn:
        before = _state_counts(conn, args.pack)

    # 2. lightweight audit re-ingest (NO embeddings / projection)
    sync_payload = sync_audit_events_from_jsonl(vault_dir)
    sync_status = sync_payload.get("status", "?")
    if sync_status != "synced":
        # Deciding "is a heavier rebuild needed?" on a stale or
        # un-synced audit table is worse than not deciding — the
        # entire value of this command is a TRUSTWORTHY state
        # movement.  Abort with a distinct code so a wrapper can
        # tell "stale, can't decide" apart from "heavier needed".
        reason = sync_payload.get("reason", "unknown reason")
        print(
            f"refresh-ops: audit sync did not complete "
            f"(status={sync_status}: {reason}). Refusing to decide "
            "on a stale audit table — run `ovp-knowledge-index` "
            "once first.",
            file=sys.stderr,
        )
        return 3

    # 3. rebuild ops_state projection
    with sqlite3.connect(str(db_path)) as conn:
        after_counts = rebuild_ops_state(conn, pack=args.pack)
        # 5. canonical-object evidence detection
        canonical = _canonical_evidence_since(conn, args.canonical_window_minutes, args.pack)

    after = {s: int(after_counts.get(s, 0)) for s in ALL_STATES}
    deltas = {s: after[s] - before[s] for s in ALL_STATES}
    before_total = sum(before.values())
    after_total = sum(after.values())

    heavier_needed = bool(canonical)
    payload = {
        "vault_dir": str(vault_dir),
        "pack": args.pack,
        "audit_sync": sync_status,
        "before": before,
        "after": after,
        "deltas": deltas,
        "before_total": before_total,
        "after_total": after_total,
        "canonical_object_evidence": canonical,
        "heavier_rebuild_needed": heavier_needed,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"refresh-ops  pack={args.pack}")
        print(f"  audit sync: {payload['audit_sync']}")
        print(f"  {'state':<14}{'before':>9}{'after':>8}{'delta':>8}")
        for s in ALL_STATES:
            d = deltas[s]
            print(f"  {s:<14}{before[s]:>9}{after[s]:>8}" f"{('+' if d >= 0 else '') + str(d):>8}")
        print(
            f"  {'TOTAL':<14}{before_total:>9}{after_total:>8}"
            f"{('+' if after_total - before_total >= 0 else '') + str(after_total - before_total):>8}"
        )
        print()
        if heavier_needed:
            ev = ", ".join(f"{k}×{v}" for k, v in sorted(canonical.items()))
            print(
                "  ⚠️  Canonical-object evidence detected in the "
                f"last {args.canonical_window_minutes}m: {ev}.\n"
                "      New / changed canonical objects are NOT fully "
                "reflected by audit-sync alone — a projection or "
                "full `ovp-knowledge-index` rebuild may be "
                "warranted before trusting Accepted / Synthesized."
            )
        else:
            print(
                "  ✓ No canonical-object evidence in the window "
                "(candidates / source evidence only).\n"
                "    Full `ovp-knowledge-index` rebuild NOT needed "
                "— the lightweight path fully reflects this change."
            )

    # Exit non-zero when a heavier rebuild may be needed so a
    # wrapper / CI can branch on it deterministically.
    return 2 if heavier_needed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
