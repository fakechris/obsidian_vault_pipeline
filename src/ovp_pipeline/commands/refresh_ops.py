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
5. Inspect for canonical-object evidence
   (``evergreen_auto_promoted`` / ``promote_concept`` /
   ``evergreen_created``) NEWER than the last successful full
   rebuild (BL-107 / issue #250 — the ``truth_projections.built_at``
   watermark; falls back to a time window only when no rebuild has
   ever run).
   * none → print "full knowledge rebuild NOT needed".
   * present → print an explicit WARNING that canonical objects
     changed and a projection / full rebuild may be warranted,
     and exit non-zero so a wrapper script can branch on it.
   Idempotent: once the operator runs the full rebuild the
   watermark advances past the evidence, so a subsequent
   candidates-only refresh stops nagging (the issue #250 fix).

Read-only over markdown; the only writes are to the derived
``knowledge.db`` (audit_events + ops_state), exactly what the
lightweight path already does.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..audit_time import parse_audit_ts as _parse_audit_ts
from ..knowledge_index import (
    KNOWLEDGE_DB_PROJECTION_KIND,
    KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
    sync_audit_events_from_jsonl,
)
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


def _last_rebuild_watermark(conn: sqlite3.Connection, pack: str) -> datetime | None:
    """BL-107 / issue #250: timestamp of the last SUCCESSFUL full
    canonical (truth/object) projection rebuild for ``pack``.

    ``truth_projections.built_at`` is stamped only by the full
    ``ovp-knowledge-index`` truth-projection rebuild — NOT by
    ``ops_state.rebuild`` and NOT by ``sync_audit_events_from_jsonl``
    (verified: neither writes that table).  So it is exactly "the
    moment canonical objects were last re-derived", which is the
    correct idempotency watermark.  ``ops_state.refreshed_at`` was
    rejected for #250 precisely because it advances on a
    lifecycle-only refresh and would suppress a real warning before
    a canonical rebuild handled it.

    None when never rebuilt / table absent → caller falls back to
    the time-window heuristic (safe default for fresh vaults).
    """
    try:
        row = conn.execute(
            "SELECT MAX(built_at) FROM truth_projections " " WHERE pack = ?",
            (pack,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    return _parse_audit_ts(str(row[0]))


def _canonical_evidence_since(
    conn: sqlite3.Connection, window_minutes: int, pack: str
) -> dict[str, int]:
    """Count UNHANDLED canonical-object audit events for ``pack``.

    BL-107 / issue #250 — idempotency: the lower bound is the last
    successful full rebuild watermark, NOT merely "the last
    ``window_minutes``".  A promote that a prior
    ``ovp-knowledge-index`` rebuild already absorbed is older than
    the watermark and is NOT re-flagged, so a second candidates-only
    refresh after a handled promote stops nagging (exit 0).  When no
    rebuild has ever run (no watermark) we fall back to the
    ``window_minutes`` heuristic — the pre-BL-107 behaviour, still
    safe for fresh vaults.  The watermark path is also strictly more
    correct than the window for a stale unhandled promote: a
    5-day-old promote never rebuilt is still newer than the
    watermark and is correctly flagged, whereas a 180m window would
    have missed it.

    Timestamps are parsed in Python rather than compared
    lexicographically in SQL — audit rows mix ISO ``T`` and
    space-separated formats, and a string compare across that
    boundary misclassifies rows.

    Evidence is scoped to ``pack``: a recent ``promote_concept`` for a
    DIFFERENT pack must not make this command tell wrappers the
    selected pack needs a heavier rebuild.  Legacy rows with no
    recorded pack are kept (conservative — never suppress a real
    warning just because an old row lacks the field).
    """
    watermark = _last_rebuild_watermark(conn, pack)
    if watermark is not None:
        cutoff = watermark
    else:
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


@dataclass(frozen=True)
class RefreshDecision:
    """Outcome of :func:`decide_knowledge_refresh`.

    ``refresh_mode`` is the binary the pipeline / autopilot
    knowledge_index step branches on:

    * ``"audit_sync_only"`` — the lightweight path
      (``sync_audit_events_from_jsonl`` + ``ops_state`` rebuild) ALREADY
      RAN inside the decision and fully reflects this change; the
      caller must NOT run the heavy ``rebuild_knowledge_index``.
    * ``"full_rebuild"`` — the caller must run the full
      ``rebuild_knowledge_index``.  Either canonical-object evidence
      was detected, or the state was unknown/untrustworthy
      (DB/metadata/schema/sync) — conservative by design: *unknown
      ⇒ full rebuild*, never silently skip and let the projection go
      stale.
    """

    refresh_mode: str
    reason: str
    canonical_evidence_count: int = 0
    canonical_evidence: dict[str, int] = field(default_factory=dict)
    watermark: str = ""
    audit_sync_status: str = ""

    @property
    def is_full(self) -> bool:
        return self.refresh_mode == "full_rebuild"


def _projection_health(conn: sqlite3.Connection) -> str | None:
    """Return a failure reason if the projection metadata is missing
    or at a different schema version, else None.

    A missing ``projection_metadata`` row / ``truth_projections``
    table, or a schema-version mismatch, means audit-sync alone
    cannot be trusted to leave a coherent projection — escalate to a
    full rebuild (which is also what ``ensure_knowledge_db_current``
    would do).
    """
    has_tp = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='truth_projections'"
    ).fetchone()
    if not has_tp:
        return "truth_projections_table_missing"
    try:
        row = conn.execute(
            "SELECT projection_schema_version FROM projection_metadata "
            "WHERE projection_kind = ?",
            (KNOWLEDGE_DB_PROJECTION_KIND,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "projection_metadata_table_missing"
    if row is None or row[0] is None:
        return "projection_metadata_missing"
    if int(row[0]) != int(KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION):
        return (
            f"projection_schema_mismatch"
            f"(db={row[0]},expected={KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION})"
        )
    return None


def decide_knowledge_refresh(
    vault_dir: Path,
    pack: str,
    *,
    force_full: bool = False,
    canonical_window_minutes: int = 180,
) -> RefreshDecision:
    """Shared post-absorb refresh decision for the pipeline AND
    autopilot knowledge_index step (single source of truth — no
    pipeline/autopilot fork).

    Conservative escalation — *unknown ⇒ full rebuild*:

    1. ``force_full`` (operator ``--force-full-index``) → full.
    2. ``knowledge.db`` missing → full (first build).
    3. projection metadata missing / schema mismatch → full.
    4. audit-sync did not reach ``synced`` → full (never decide on a
       stale audit table).
    5. canonical-object evidence (``_canonical_evidence_since``,
       BL-107 watermark) present → full.
    6. otherwise → ``audit_sync_only``: the lightweight audit-sync +
       ``ops_state`` rebuild already ran here and fully reflects the
       change; the heavy rebuild is NOT needed.

    The lightweight work (audit-sync + ops_state rebuild) is executed
    *inside* this function for the cases that reach step 5/6, so the
    caller never double-runs it.
    """
    resolved = resolve_vault_dir(vault_dir)
    db_path = _db_path(resolved)

    if force_full:
        return RefreshDecision("full_rebuild", "force_full_index")
    if not db_path.is_file():
        return RefreshDecision("full_rebuild", "knowledge_db_missing")

    with sqlite3.connect(str(db_path)) as conn:
        health = _projection_health(conn)
    if health is not None:
        return RefreshDecision("full_rebuild", health)

    sync_payload = sync_audit_events_from_jsonl(resolved)
    sync_status = str(sync_payload.get("status", "?"))
    if sync_status != "synced":
        return RefreshDecision(
            "full_rebuild",
            f"audit_sync_{sync_status}",
            audit_sync_status=sync_status,
        )

    with sqlite3.connect(str(db_path)) as conn:
        rebuild_ops_state(conn, pack=pack)
        watermark = _last_rebuild_watermark(conn, pack)
        canonical = _canonical_evidence_since(conn, canonical_window_minutes, pack)

    wm = watermark.isoformat() if watermark is not None else ""
    evidence_count = sum(canonical.values())
    if canonical:
        return RefreshDecision(
            "full_rebuild",
            "canonical_object_evidence",
            canonical_evidence_count=evidence_count,
            canonical_evidence=canonical,
            watermark=wm,
            audit_sync_status=sync_status,
        )
    return RefreshDecision(
        "audit_sync_only",
        "no_canonical_evidence",
        canonical_evidence_count=0,
        canonical_evidence={},
        watermark=wm,
        audit_sync_status=sync_status,
    )


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
        # 5. canonical-object evidence detection (gated on the last
        #    successful full-rebuild watermark — BL-107 / #250)
        watermark = _last_rebuild_watermark(conn, args.pack)
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
        "rebuild_watermark": (watermark.isoformat() if watermark is not None else ""),
        "watermark_source": (
            "last_full_rebuild"
            if watermark is not None
            else f"window_{args.canonical_window_minutes}m"
        ),
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
        if watermark is not None:
            scope_msg = f"since the last full rebuild " f"({watermark.isoformat()})"
        else:
            scope_msg = (
                f"in the last {args.canonical_window_minutes}m " "(no prior full rebuild on record)"
            )
        if heavier_needed:
            ev = ", ".join(f"{k}×{v}" for k, v in sorted(canonical.items()))
            print(
                "  ⚠️  Canonical-object evidence detected "
                f"{scope_msg}: {ev}.\n"
                "      New / changed canonical objects are NOT fully "
                "reflected by audit-sync alone — a projection or "
                "full `ovp-knowledge-index` rebuild may be "
                "warranted before trusting Accepted / Synthesized.\n"
                "      (Once you run that rebuild this stops warning "
                "— the watermark advances past this evidence.)"
            )
        else:
            print(
                f"  ✓ No canonical-object evidence {scope_msg} "
                "(candidates / source evidence only).\n"
                "    Full `ovp-knowledge-index` rebuild NOT needed "
                "— the lightweight path fully reflects this change."
            )

    # Exit non-zero when a heavier rebuild may be needed so a
    # wrapper / CI can branch on it deterministically.
    return 2 if heavier_needed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
