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

from .audit_identity import (
    audit_cluster_ids,
    audit_object_ids,
    collect_string_values,
)
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


@dataclass(frozen=True)
class _AuditIndex:
    """In-memory inverted index over ``audit_events``.

    Built once at bulk-classification time; reused for every
    per-item lookup.  Without this, the kernel does a full-table
    LIKE scan per item — O(items × rows), which on the operator
    vault was 9.5k items × 36k rows and timed out at 5 minutes
    during the M25.6 dogfood run.

    Each map is ``key -> [(event_type, timestamp, payload_json), …]``
    sorted newest-first.
    """

    by_slug: dict[str, list[tuple[str, str, str]]]
    by_object_id: dict[str, list[tuple[str, str, str]]]
    by_cluster_id: dict[str, list[tuple[str, str, str]]]


# M24 PR-B: identity extraction moved to the shared
# ``audit_identity`` module so this index and
# ``knowledge_index._infer_audit_slug`` use ONE normalization and
# cannot drift.  ``_collect_string_values`` kept as a thin
# backward-compatible alias for any external importer / test.
_collect_string_values = collect_string_values


def _build_audit_index(conn: sqlite3.Connection) -> _AuditIndex:
    """Single-pass index build.  Parses payload_json once per row
    so per-item lookups are O(1) hash lookups instead of full-table
    LIKE scans.

    M24 PR-B audit-identity normalization: ``by_object_id`` now
    keys on ``audit_identity.audit_object_ids`` (object_id +
    concept + mutation.slug + mutation.target_slug, verbatim AND
    canonicalized), recovering the ~10k historical
    ``evergreen_auto_promoted`` events that carried ``concept`` /
    ``mutation.*`` instead of ``object_id``.  ``by_slug`` keys on
    the ``audit_events.slug`` column, which ``_infer_audit_slug``
    now fills from ``source`` / ``file`` / ``path`` for
    source-class events.  Source and object identities stay in
    separate maps — the shared helper never mixes them (see
    ``audit_identity`` module docstring).
    """
    by_slug: dict[str, list[tuple[str, str, str]]] = {}
    by_object_id: dict[str, list[tuple[str, str, str]]] = {}
    by_cluster_id: dict[str, list[tuple[str, str, str]]] = {}

    rows = conn.execute(
        "SELECT event_type, timestamp, slug, payload_json "
        "  FROM audit_events "
        " ORDER BY timestamp DESC"
    ).fetchall()

    for event_type, ts, slug, payload_json in rows:
        et = event_type or ""
        ts = ts or ""
        pj = payload_json or "{}"
        record = (et, ts, pj)
        if slug:
            by_slug.setdefault(str(slug), []).append(record)
        try:
            payload = json.loads(pj)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for obj_id in audit_object_ids(payload):
            by_object_id.setdefault(obj_id, []).append(record)
        for cluster_id in audit_cluster_ids(payload):
            by_cluster_id.setdefault(cluster_id, []).append(record)
    return _AuditIndex(
        by_slug=by_slug,
        by_object_id=by_object_id,
        by_cluster_id=by_cluster_id,
    )


def _fetch_audit_rows(
    conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    object_id: str | None = None,
    cluster_id: str | None = None,
    audit_index: _AuditIndex | None = None,
) -> list[tuple[str, str, str]]:
    """Return ``(event_type, timestamp, payload_json)`` rows about an item,
    newest first.

    When ``audit_index`` is provided (the bulk-classification path),
    use the in-memory map for an O(1) lookup.  Otherwise (the
    single-item path used by ``ovp-lifecycle-show``), fall back to
    the SQL LIKE scan that was the original implementation.

    The single-item fallback (``audit_index is None``, e.g.
    ``ovp-lifecycle-show``) MUST agree with the bulk path.  PR-B
    codex review caught that the old SQL fallback only matched
    ``"object_id"`` literals, so an object whose evidence used
    ``concept`` / ``mutation.slug`` / ``mutation.target_slug``
    showed evidence in a bulk rebuild but "missing" in a one-off
    lookup.  The fallback now does a COARSE ``LIKE %value%`` to
    bound the candidate set, then confirms membership through the
    SAME shared ``audit_object_ids`` / ``audit_cluster_ids``
    helpers the index uses — zero drift, by construction.
    """
    if audit_index is not None:
        if slug:
            return list(audit_index.by_slug.get(slug, ()))
        if object_id:
            return list(audit_index.by_object_id.get(object_id, ()))
        if cluster_id:
            return list(audit_index.by_cluster_id.get(cluster_id, ()))
        return []

    # Single-item fallback (SQL).
    if slug:
        rows = conn.execute(
            "SELECT event_type, timestamp, payload_json "
            "  FROM audit_events "
            " WHERE slug = ? "
            " ORDER BY timestamp DESC",
            (slug,),
        ).fetchall()
        return [(r[0], r[1] or "", r[2] or "{}") for r in rows]

    if object_id or cluster_id:
        target = object_id or cluster_id
        # Coarse candidate fetch: any row mentioning the id string
        # anywhere in the payload.  The authoritative include/
        # exclude decision is delegated to the shared helper so
        # this can never diverge from ``_build_audit_index``.
        rows = conn.execute(
            "SELECT event_type, timestamp, payload_json "
            "  FROM audit_events "
            " WHERE payload_json LIKE ? "
            " ORDER BY timestamp DESC",
            (f"%{target}%",),
        ).fetchall()
        out: list[tuple[str, str, str]] = []
        for et, ts, pj in rows:
            pj = pj or "{}"
            try:
                payload = json.loads(pj)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if object_id and object_id in audit_object_ids(payload):
                out.append((et or "", ts or "", pj))
            elif cluster_id and cluster_id in audit_cluster_ids(payload):
                out.append((et or "", ts or "", pj))
        return out
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

    BL-115 review (codex P2): ``cluster_id`` here is the CURRENT
    graph_clusters id (what the lifecycle classifier iterates).  After
    an inherited re-cluster the active crystal still carries the
    synthesis-time ``cluster_id=old`` while the ledger maps
    ``concept_id`` → ``current_cluster_id=new``.  A direct
    ``WHERE cluster_id = new`` lookup therefore misses the fresh
    crystal and the concept mis-classifies as unsynthesized.  Resolve
    through the ledger first; fall back to the direct cluster_id query
    for pre-v10 DBs (no ledger) or clusters the ledger hasn't mapped.
    """
    if not _has_table(conn, "community_crystals"):
        return ("", False)
    if _has_table(conn, "concept_identity_ledger"):
        row = conn.execute(
            "SELECT cc.synthesized_at, cc.superseded_by_synthesized_at "
            "  FROM concept_identity_ledger cil "
            "  JOIN community_crystals cc "
            "    ON cc.pack = cil.pack AND cc.concept_id = cil.concept_id "
            " WHERE cil.pack = ? AND cil.current_cluster_id = ? "
            " ORDER BY cc.synthesized_at DESC LIMIT 1",
            (pack, cluster_id),
        ).fetchone()
        if row:
            return (row[0] or "", not row[1])
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
    audit_index: _AuditIndex | None = None,
) -> LifecycleState | None:
    """Derive ``LifecycleState`` for one item.

    ``as_of`` is the freshness anchor for synthesis-staleness checks;
    pass the operator's local-day boundary when reporting "today's"
    state.  Empty string means "use the data's own timestamps", i.e.
    no time gating — useful for backfill / batch rebuilds.

    ``audit_index`` is the in-memory inverted index built by
    ``_build_audit_index`` for bulk classification.  When ``None``
    (single-item path used by ``ovp-lifecycle-show``), the kernel
    falls back to SQL LIKE scans.  See ``_fetch_audit_rows`` for the
    rationale.

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
        rows = _fetch_audit_rows(conn, slug=item_id, audit_index=audit_index)
    elif item_kind == ITEM_KIND_OBJECT:
        rows = _fetch_audit_rows(conn, object_id=item_id, audit_index=audit_index)
    else:
        rows = _fetch_audit_rows(conn, cluster_id=item_id, audit_index=audit_index)

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

    # Cluster Synthesized resolution.
    sub_state: str | None = None
    if item_kind == ITEM_KIND_CLUSTER:
        latest_synth, active = _crystal_for_cluster(conn, pack, item_id)
        newest_member_rev = _cluster_max_member_revision_ts(
            conn, pack, item_id
        )
        # A cluster is freshly synthesized when its crystal is
        # active (not superseded) AND not stale relative to its
        # member revisions.
        crystal_fresh = bool(
            active
            and latest_synth
            and (
                not newest_member_rev
                or newest_member_rev <= latest_synth
            )
        )
        if best_state == STATE_SYNTHESIZED:
            # An audit event said synthesized — demote only if the
            # crystal is actually stale / superseded.
            if not crystal_fresh:
                best_state = STATE_ACCEPTED
        elif crystal_fresh:
            # M25.6 dogfood / codex #246 P1: projection-as-evidence.
            # An active, fresh ``community_crystals`` row IS a
            # synthesized cluster even when no
            # ``community_crystal_synthesized`` audit event exists
            # — e.g. crystals synthesized before the M24.2 emit was
            # wired, or the ``--skip-existing`` resume path that
            # never re-commits.  Without this, the operator vault's
            # 576 pre-existing crystals stay at Synthesized=0
            # forever unless every one is re-synthesized.  Mirrors
            # the object ``Projected`` pattern: the projection
            # asserts the state, the audit just didn't witness it.
            best_state = STATE_SYNTHESIZED
            if "community_crystal_synthesized" not in evidence_types:
                sub_state = SUBSTATE_PROJECTED

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
    audit_index: _AuditIndex | None = None,
) -> Iterator[LifecycleState]:
    """Yield ``LifecycleState`` for every item of ``item_kind`` in ``pack``.

    The kernel discovers items the way each kind makes available:

    * ``source`` — distinct ``slug`` in ``audit_events`` (the only
      place sources are tracked in ``knowledge.db``).
    * ``object`` — rows in ``objects``.
    * ``cluster`` — rows in ``graph_clusters``.

    M25.6 perf fix: build the audit index ONCE per call (or accept
    a pre-built one) so per-item lookups are O(1) hash hits.  The
    operator vault has ~9.5k objects × 36k audit rows; without
    this, the bulk classification timed out at 5 minutes.
    """
    if audit_index is None:
        audit_index = _build_audit_index(conn)

    if item_kind == ITEM_KIND_SOURCE:
        rows = conn.execute(
            "SELECT DISTINCT slug FROM audit_events "
            " WHERE slug <> '' "
            " ORDER BY slug"
        ).fetchall()
        for (slug,) in rows:
            state = lifecycle_state_of(
                conn, ITEM_KIND_SOURCE, slug,
                pack=pack, as_of=as_of, audit_index=audit_index,
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
                conn, ITEM_KIND_OBJECT, object_id,
                pack=pack, as_of=as_of, audit_index=audit_index,
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
                conn, ITEM_KIND_CLUSTER, cluster_id,
                pack=pack, as_of=as_of, audit_index=audit_index,
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
    # M25.6 perf fix: build the audit index ONCE for all three
    # item kinds rather than three times.
    audit_index = _build_audit_index(conn)
    counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    for kind in ALL_ITEM_KINDS:
        for state in lifecycle_states_for_kind(
            conn, kind, pack=pack, as_of=as_of,
            audit_index=audit_index,
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
