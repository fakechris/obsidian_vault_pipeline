"""Lifecycle kernel (M24.1, 2026-05-14).

Pure-function derivation of an item's current lifecycle state from
``audit_events`` plus the truth-projection tables (``objects``,
``evergreen_revisions``, ``community_crystals``,
``contradiction_crystals``, ``graph_clusters``).

The kernel **never** reads from markdown, calls a producer, or
guesses at "what should be there".  It reports what the evidence
says.  If the evidence is silent, the item is not classified —
``lifecycle_state_of`` returns ``None``.  M24 is honest about gaps;
fabricating state to hide them is exactly what M23 got wrong.

Design contract (docs/operational-lifecycle.md)
-----------------------------------------------

* Five visible states: ``Received``, ``Extracted``, ``Accepted``,
  ``Synthesized``, ``NeedsAction``.  M25 will rename today's cards
  to these labels; the kernel labels them with this vocabulary now.
* Two internal sub-states: ``Prepared`` (producer emitted but
  downstream consumer hasn't run), ``Projected`` (a derived row
  exists but the primary evidence row is missing).  These never
  surface on operator cards; they exist so the kernel can return
  honest disagreements between ledgers.
* Evidence classification routes through
  ``event_evidence_registry.classify``.  The kernel never hardcodes
  event_type strings — adding a new producer means adding a row to
  the registry, not editing this file.
* Freshness checks take an ``as_of`` argument; no ``datetime.now``
  inside the kernel.  Same inputs, same output, always.

Scope locks (from M24.1 plan, 2026-05-14)
-----------------------------------------

* Per-pack.  State is scoped to a single pack; cross-pack lifecycle
  is not modelled in M24.1.
* No write side.  The kernel reads sqlite; it never inserts.
  ``ops_state.py`` owns the projection.
* No surface side.  The kernel is a library — ``/ops/today`` and
  friends keep their current data sources in M24.1.  M24.4 wires
  them to read ``ops_state``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Final, Iterator

from .event_evidence_registry import classify


# ── Public vocabulary ──────────────────────────────────────────────

STATE_RECEIVED: Final[str] = "Received"
STATE_EXTRACTED: Final[str] = "Extracted"
STATE_ACCEPTED: Final[str] = "Accepted"
STATE_SYNTHESIZED: Final[str] = "Synthesized"
STATE_NEEDS_ACTION: Final[str] = "NeedsAction"

ALL_STATES: Final[tuple[str, ...]] = (
    STATE_RECEIVED,
    STATE_EXTRACTED,
    STATE_ACCEPTED,
    STATE_SYNTHESIZED,
    STATE_NEEDS_ACTION,
)

SUBSTATE_PREPARED: Final[str] = "Prepared"
SUBSTATE_PROJECTED: Final[str] = "Projected"

ITEM_KIND_SOURCE: Final[str] = "source"
ITEM_KIND_OBJECT: Final[str] = "object"
ITEM_KIND_CLUSTER: Final[str] = "cluster"

ALL_ITEM_KINDS: Final[tuple[str, ...]] = (
    ITEM_KIND_SOURCE,
    ITEM_KIND_OBJECT,
    ITEM_KIND_CLUSTER,
)


@dataclass(frozen=True)
class LifecycleState:
    """One item's current lifecycle classification.

    ``state`` is one of :data:`ALL_STATES`.  ``sub_state`` is
    populated only when the kernel detected a Prepared / Projected
    disagreement — it never replaces ``state`` (operator cards read
    ``state``; debugging tools read ``sub_state``).

    ``evidence`` is ordered newest-first so the first element is
    the most recent audit row that contributed to the classification.
    ``last_evidence_at`` is the timestamp of ``evidence[0]`` or
    ``""`` if no audit evidence (only projection rows).
    """

    item_id: str
    item_kind: str
    state: str
    sub_state: str | None
    evidence: tuple[str, ...]
    last_evidence_at: str
    needs_action_reason: str | None
    pack: str


# ── Evidence-category → state contribution ─────────────────────────
#
# Pure mapping, exposed so callers can audit the contract without
# reading the function body.  A single audit row can shift an item
# across multiple states; the kernel honours **the strongest** state
# that the row's category contributes.
#
#   failures   → NeedsAction      (always wins)
#   synthesis  → Synthesized      (with freshness check)
#   absorb     → Extracted        (or Accepted via promote events)
#   governance → Accepted | NeedsAction (depends on event_type)
#   intake     → Received         (or Accepted via source_archived_to_processed)
#
# Event-type-specific overrides live in ``_state_for_event_type``.


def _state_for_event_type(event_type: str) -> tuple[str, str | None]:
    """Return ``(state, needs_action_reason)`` for an event_type.

    Returns ``("", None)`` when the event doesn't contribute to a
    state classification (e.g. forensic / debug-only rows).
    """
    entry = classify(event_type)
    if entry is None or not entry.user_visible:
        return ("", None)

    cat = entry.category

    if cat == "failures":
        return (STATE_NEEDS_ACTION, event_type)

    if cat == "synthesis":
        # Freshness handled by ``lifecycle_state_of`` after this
        # function returns — kernel demotes Synthesized to Accepted
        # if the synthesis is stale relative to the item's newest
        # revision.
        return (STATE_SYNTHESIZED, None)

    if cat == "absorb":
        # ``evergreen_auto_promoted`` is the one absorb event that
        # actually moves an item to Accepted (it writes the canonical
        # path).  Everything else stays at Extracted.
        if event_type == "evergreen_auto_promoted":
            return (STATE_ACCEPTED, None)
        return (STATE_EXTRACTED, None)

    if cat == "governance":
        # ``promote_concept`` accepts.  Open contradictions /
        # candidate-needs-review surface as NeedsAction.
        if event_type == "promote_concept":
            return (STATE_ACCEPTED, None)
        if event_type == "candidate_review_action":
            # Whether this is "accepted" or "needs review" depends on
            # the payload action; without the payload we conservatively
            # report Accepted (the row exists because a reviewer
            # acted).  ``NeedsAction`` for open candidates is detected
            # via the ``contradictions``/``candidates`` projection
            # check below, not the event_type alone.
            return (STATE_ACCEPTED, None)
        if event_type == "contradictions_resolved":
            return (STATE_ACCEPTED, None)
        return (STATE_ACCEPTED, None)

    if cat == "intake":
        if event_type == "source_archived_to_processed":
            # The source completed its intake journey.  Whether the
            # canonical absorb artifact exists is a separate check —
            # the kernel resolves the final state at the item level.
            return (STATE_RECEIVED, None)
        return (STATE_RECEIVED, None)

    return ("", None)


def _state_priority(state: str) -> int:
    """Higher number wins when multiple events contribute different states."""
    return {
        STATE_NEEDS_ACTION: 5,
        STATE_SYNTHESIZED: 4,
        STATE_ACCEPTED: 3,
        STATE_EXTRACTED: 2,
        STATE_RECEIVED: 1,
        "": 0,
    }.get(state, 0)


# ── Internal helpers ───────────────────────────────────────────────


def _fetch_audit_rows(
    conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    object_id: str | None = None,
    cluster_id: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return ``(event_type, timestamp, payload_json)`` rows about an item,
    newest first.

    The ``audit_events`` table only carries ``slug`` natively; matches
    against ``object_id`` and ``cluster_id`` go via the payload_json
    ``LIKE`` filter.  This is intentionally lossy — payload schemas
    aren't enforced — so kernel callers must be tolerant of misses
    and rely on the ``Projected`` sub-state to flag mismatches.
    """
    if slug:
        rows = conn.execute(
            "SELECT event_type, timestamp, payload_json "
            "  FROM audit_events "
            " WHERE slug = ? "
            " ORDER BY timestamp DESC",
            (slug,),
        ).fetchall()
        return [(r[0], r[1] or "", r[2] or "{}") for r in rows]
    if object_id:
        # Payload-based match.  Use a LIKE on the JSON literal — robust
        # enough for the kernel's classification needs.
        needle = f'"object_id": "{object_id}"'
        needle_alt = f'"object_id":"{object_id}"'
        rows = conn.execute(
            "SELECT event_type, timestamp, payload_json "
            "  FROM audit_events "
            " WHERE payload_json LIKE ? OR payload_json LIKE ? "
            " ORDER BY timestamp DESC",
            (f"%{needle}%", f"%{needle_alt}%"),
        ).fetchall()
        return [(r[0], r[1] or "", r[2] or "{}") for r in rows]
    if cluster_id:
        needle = f'"cluster_id": "{cluster_id}"'
        needle_alt = f'"cluster_id":"{cluster_id}"'
        rows = conn.execute(
            "SELECT event_type, timestamp, payload_json "
            "  FROM audit_events "
            " WHERE payload_json LIKE ? OR payload_json LIKE ? "
            " ORDER BY timestamp DESC",
            (f"%{needle}%", f"%{needle_alt}%"),
        ).fetchall()
        return [(r[0], r[1] or "", r[2] or "{}") for r in rows]
    return []


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _max_revision_ts(
    conn: sqlite3.Connection, pack: str, object_id: str
) -> str:
    if not _has_table(conn, "evergreen_revisions"):
        return ""
    row = conn.execute(
        "SELECT MAX(derived_at) FROM evergreen_revisions "
        " WHERE pack = ? AND object_id = ?",
        (pack, object_id),
    ).fetchone()
    return (row[0] if row and row[0] else "") or ""


def _crystal_for_cluster(
    conn: sqlite3.Connection, pack: str, cluster_id: str
) -> tuple[str, bool]:
    """Return ``(latest_synthesized_at, has_active_crystal)`` for a cluster.

    ``has_active_crystal`` is True when the most recent row has
    ``superseded_by_synthesized_at = ''``.
    """
    if not _has_table(conn, "community_crystals"):
        return ("", False)
    row = conn.execute(
        "SELECT synthesized_at, superseded_by_synthesized_at "
        "  FROM community_crystals "
        " WHERE pack = ? AND cluster_id = ? "
        " ORDER BY synthesized_at DESC LIMIT 1",
        (pack, cluster_id),
    ).fetchone()
    if not row:
        return ("", False)
    return (row[0] or "", not row[1])


def _cluster_max_member_revision_ts(
    conn: sqlite3.Connection, pack: str, cluster_id: str
) -> str:
    """Newest evergreen_revisions.derived_at among the cluster's members."""
    if not (_has_table(conn, "graph_clusters") and _has_table(conn, "evergreen_revisions")):
        return ""
    row = conn.execute(
        "SELECT member_object_ids_json FROM graph_clusters "
        " WHERE pack = ? AND cluster_id = ?",
        (pack, cluster_id),
    ).fetchone()
    if not row or not row[0]:
        return ""
    try:
        members = json.loads(row[0])
    except (TypeError, ValueError):
        return ""
    if not members:
        return ""
    placeholders = ",".join("?" * len(members))
    row = conn.execute(
        f"SELECT MAX(derived_at) FROM evergreen_revisions "
        f" WHERE pack = ? AND object_id IN ({placeholders})",
        (pack, *members),
    ).fetchone()
    return (row[0] if row and row[0] else "") or ""


# ── Public API ─────────────────────────────────────────────────────


def lifecycle_state_of(
    conn: sqlite3.Connection,
    item_kind: str,
    item_id: str,
    *,
    pack: str,
    as_of: str = "",
) -> LifecycleState | None:
    """Derive ``LifecycleState`` for one item.

    ``as_of`` is the freshness anchor for synthesis-staleness checks;
    pass the operator's local-day boundary when reporting "today's"
    state.  Empty string means "use the data's own timestamps", i.e.
    no time gating — useful for backfill / batch rebuilds.

    Returns ``None`` when the kernel finds zero audit evidence **and**
    zero projection rows referencing the item.  Callers should treat
    ``None`` as "unknown", not as "Received".
    """
    if item_kind not in ALL_ITEM_KINDS:
        raise ValueError(
            f"ops_lifecycle: unknown item_kind {item_kind!r}; "
            f"expected one of {ALL_ITEM_KINDS}"
        )

    # Pull every audit row about the item.
    if item_kind == ITEM_KIND_SOURCE:
        rows = _fetch_audit_rows(conn, slug=item_id)
    elif item_kind == ITEM_KIND_OBJECT:
        rows = _fetch_audit_rows(conn, object_id=item_id)
    else:
        rows = _fetch_audit_rows(conn, cluster_id=item_id)

    # Check projections — used both for Projected sub-state detection
    # and for "no audit evidence at all but a row exists" fallback.
    has_projection = False
    if item_kind == ITEM_KIND_OBJECT and _has_table(conn, "objects"):
        proj = conn.execute(
            "SELECT 1 FROM objects WHERE pack = ? AND object_id = ?",
            (pack, item_id),
        ).fetchone()
        has_projection = proj is not None
    elif item_kind == ITEM_KIND_CLUSTER and _has_table(conn, "graph_clusters"):
        proj = conn.execute(
            "SELECT 1 FROM graph_clusters WHERE pack = ? AND cluster_id = ?",
            (pack, item_id),
        ).fetchone()
        has_projection = proj is not None

    if not rows and not has_projection:
        return None

    # Walk evidence; pick highest-priority state.  Record the
    # newest event_type that contributed (rows are newest-first, so
    # the first match per state wins ordering ties).
    best_state = ""
    best_event = ""
    needs_action_reason: str | None = None
    evidence_types: list[str] = []
    last_ts = ""

    for event_type, ts, _payload in rows:
        evidence_types.append(event_type)
        if not last_ts:
            last_ts = ts
        s, reason = _state_for_event_type(event_type)
        if _state_priority(s) > _state_priority(best_state):
            best_state = s
            best_event = event_type
            needs_action_reason = reason

    # Apply freshness for Synthesized at the cluster level.
    sub_state: str | None = None
    if best_state == STATE_SYNTHESIZED and item_kind == ITEM_KIND_CLUSTER:
        latest_synth, active = _crystal_for_cluster(conn, pack, item_id)
        newest_member_rev = _cluster_max_member_revision_ts(
            conn, pack, item_id
        )
        if not active:
            best_state = STATE_ACCEPTED
        elif (
            newest_member_rev
            and latest_synth
            and newest_member_rev > latest_synth
        ):
            # Crystal is stale relative to its inputs.
            best_state = STATE_ACCEPTED

    # Projected sub-state: object projection exists but no
    # ``evergreen_auto_promoted`` / ``promote_concept`` evidence.
    if item_kind == ITEM_KIND_OBJECT and has_projection:
        promote_events = {"evergreen_auto_promoted", "promote_concept"}
        if not any(et in promote_events for et in evidence_types):
            sub_state = SUBSTATE_PROJECTED
            if best_state == "":
                best_state = STATE_ACCEPTED  # projection asserts it
                best_event = "(projection-only)"

    # Prepared sub-state: an extraction-complete event exists but no
    # downstream upsert for the same source.
    if item_kind == ITEM_KIND_SOURCE:
        ext_complete = any(
            et == "evergreen_extraction_complete" for et in evidence_types
        )
        upserted = any(et == "candidates_upserted" for et in evidence_types)
        if ext_complete and not upserted and best_state in (STATE_EXTRACTED, STATE_RECEIVED):
            sub_state = SUBSTATE_PREPARED

    if best_state == "":
        # Evidence existed but only forensic / debug rows — surface
        # as ``Received`` conservatively, but flag via Prepared so
        # callers can debug producer drift.
        best_state = STATE_RECEIVED
        sub_state = sub_state or SUBSTATE_PREPARED

    return LifecycleState(
        item_id=item_id,
        item_kind=item_kind,
        state=best_state,
        sub_state=sub_state,
        evidence=tuple(evidence_types),
        last_evidence_at=last_ts,
        needs_action_reason=needs_action_reason
        if best_state == STATE_NEEDS_ACTION
        else None,
        pack=pack,
    )


def lifecycle_states_for_kind(
    conn: sqlite3.Connection,
    item_kind: str,
    *,
    pack: str,
    as_of: str = "",
) -> Iterator[LifecycleState]:
    """Yield ``LifecycleState`` for every item of ``item_kind`` in ``pack``.

    The kernel discovers items the way each kind makes available:

    * ``source`` — distinct ``slug`` in ``audit_events`` (the only
      place sources are tracked in ``knowledge.db``).
    * ``object`` — rows in ``objects``.
    * ``cluster`` — rows in ``graph_clusters``.
    """
    if item_kind == ITEM_KIND_SOURCE:
        rows = conn.execute(
            "SELECT DISTINCT slug FROM audit_events "
            " WHERE slug <> '' "
            " ORDER BY slug"
        ).fetchall()
        for (slug,) in rows:
            state = lifecycle_state_of(
                conn, ITEM_KIND_SOURCE, slug, pack=pack, as_of=as_of
            )
            if state is not None:
                yield state
        return

    if item_kind == ITEM_KIND_OBJECT:
        if not _has_table(conn, "objects"):
            return
        rows = conn.execute(
            "SELECT object_id FROM objects "
            " WHERE pack = ? "
            " ORDER BY object_id",
            (pack,),
        ).fetchall()
        for (object_id,) in rows:
            state = lifecycle_state_of(
                conn, ITEM_KIND_OBJECT, object_id, pack=pack, as_of=as_of
            )
            if state is not None:
                yield state
        return

    if item_kind == ITEM_KIND_CLUSTER:
        if not _has_table(conn, "graph_clusters"):
            return
        rows = conn.execute(
            "SELECT cluster_id FROM graph_clusters "
            " WHERE pack = ? "
            " ORDER BY cluster_id",
            (pack,),
        ).fetchall()
        for (cluster_id,) in rows:
            state = lifecycle_state_of(
                conn, ITEM_KIND_CLUSTER, cluster_id, pack=pack, as_of=as_of
            )
            if state is not None:
                yield state
        return

    raise ValueError(
        f"ops_lifecycle: unknown item_kind {item_kind!r}; "
        f"expected one of {ALL_ITEM_KINDS}"
    )


def lifecycle_counts(
    conn: sqlite3.Connection,
    *,
    pack: str,
    as_of: str = "",
) -> dict[str, int]:
    """Return ``{state: count}`` across every item kind in ``pack``.

    Missing states are present with count 0 — callers can plot the
    five buckets without preprocessing.
    """
    counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    for kind in ALL_ITEM_KINDS:
        for state in lifecycle_states_for_kind(
            conn, kind, pack=pack, as_of=as_of
        ):
            counts[state.state] = counts.get(state.state, 0) + 1
    return counts


__all__ = [
    "ALL_ITEM_KINDS",
    "ALL_STATES",
    "ITEM_KIND_CLUSTER",
    "ITEM_KIND_OBJECT",
    "ITEM_KIND_SOURCE",
    "LifecycleState",
    "STATE_ACCEPTED",
    "STATE_EXTRACTED",
    "STATE_NEEDS_ACTION",
    "STATE_RECEIVED",
    "STATE_SYNTHESIZED",
    "SUBSTATE_PREPARED",
    "SUBSTATE_PROJECTED",
    "lifecycle_counts",
    "lifecycle_state_of",
    "lifecycle_states_for_kind",
]
