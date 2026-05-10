"""BL-063 PR#2 — orchestrator: fetch DB inputs, evaluate triggers.

Pure-evaluation triggers live in :mod:`live_concept_triggers`.  This
module is the I/O layer: walks active live concepts, queries the
audit-events / contradictions tables for the per-trigger inputs,
and feeds them into the evaluators.

PR#2 stops at evaluation.  No ``patch_live`` calls, no agent
invocation, no audit emission of trigger-fire results — those are
PR#3.  The CLI in :mod:`ovp_pipeline.commands.live_concept_scan`
consumes the :class:`ConceptEvaluation` records produced here for
human-readable reporting and for the operator to dry-run the
trigger logic before PR#3 actually wires it to the agent.

What "read-only" means here
---------------------------

User-data wise, the scan never mutates objects / claims / live-
concept files.  But the underlying ``recent_audit_events`` /
``list_contradictions`` calls go through
:func:`knowledge_index._ensure_knowledge_db`, which is allowed to
run a schema migration on the projection DB (e.g. v6 → v7) when
the version sentinel is stale.  In practice this is benign — the
projection is rebuilt deterministically from the canonical state
— but a fresh-clone scan can take longer than expected on first
run if a schema bump is pending.

Pack scoping caveat
-------------------

``recent_audit_events`` doesn't accept a ``pack_name`` filter (the
``audit_events`` table doesn't carry a pack column), and the
``absorb_route_decision`` payload doesn't currently include the
pack either.  Result: a multi-pack vault where the same
``update_slug`` appears in two packs would fire ``on_ingest_match``
across both.  Mitigation today is operator hygiene (use
pack-prefixed slugs in ``concept_similarity_to`` /
``scope_evergreens``); a proper fix is out of scope for PR#2 but
tracked as a note for the future enhancement (`BL-064` candidate).

Why split orchestrator from evaluators
--------------------------------------

Triggers are pure functions on pre-fetched data — easy to
unit-test by handing them dataclasses.  The orchestrator deals
with SQLite, vault layout resolution, timezone-aware "now", and
the row-shape decoding from ``recent_audit_events`` /
``list_contradictions``.  Keeping these layers separate means a
PR#3 author can wire the agent against
:func:`evaluate_all_concepts` without re-mocking the trigger
evaluators, and a future trigger kind only needs a new evaluator
plus a one-line addition to the orchestrator's per-concept loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .knowledge_index import list_contradictions, recent_audit_events
from .live_concept import LiveConceptHandle, list_live_concepts
from .live_concept_triggers import (
    ContradictionMatch,
    IngestMatch,
    evaluate_contradiction_matches,
    evaluate_ingest_matches,
    weekly_resynthesis_due,
)


# Match BL-062's audit event type so we don't drift if the constant
# is renamed there — re-import from the source rather than redefining.
from .absorb_router import ABSORB_ROUTE_DECISION_EVENT


@dataclass(frozen=True)
class ConceptEvaluation:
    """Per-concept trigger-evaluation result.

    Carries the source handle for downstream consumers to reach the
    file path / objective / scope without re-walking the discovery
    function, plus the three trigger results.  All three are
    independent — a concept may fire 0, 1, 2, or 3 of them in a
    single scan.
    """

    handle: LiveConceptHandle
    weekly_due: bool
    ingest_matches: list[IngestMatch]
    contradiction_matches: list[ContradictionMatch]

    @property
    def has_any_trigger(self) -> bool:
        return (
            self.weekly_due
            or bool(self.ingest_matches)
            or bool(self.contradiction_matches)
        )


def _parse_audit_timestamp(text: str) -> datetime | None:
    """Parse the ``audit_events.timestamp`` column into a UTC-aware
    datetime.  Returns ``None`` on parse failure so the caller can
    drop the row silently instead of aborting the whole scan.

    Naive timestamps are treated as UTC.  This is correct for OVP
    today — every writer uses
    :func:`runtime.format_utc_timestamp` which emits Z-suffixed
    strings — but if a future writer ever drops the suffix, the
    cutoff comparison would silently shift by the operator's local
    offset.  Codex review flagged this as a hypothesis worth
    documenting.
    """
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _filter_recent_route_decisions(
    audit_events: list[dict[str, Any]],
    *,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Keep only ``absorb_route_decision`` rows newer than ``cutoff``.

    Audit rows whose timestamp can't be parsed are dropped — better
    to skip an unparseable row than to fire a trigger off a row we
    don't understand.
    """
    keep: list[dict[str, Any]] = []
    for row in audit_events:
        if row.get("event_type") != ABSORB_ROUTE_DECISION_EVENT:
            continue
        ts = _parse_audit_timestamp(str(row.get("timestamp", "")))
        if ts is None or ts < cutoff:
            continue
        keep.append(row)
    return keep


def _gather_contradictions_for_scope(
    vault_dir: Path,
    *,
    pack_name: str | None,
    scope_evergreens: tuple[str, ...],
    contradiction_limit: int,
) -> list[dict[str, Any]]:
    """Query ``list_contradictions`` per in-scope slug and dedupe.

    Why per-slug instead of one fetch-all: ``list_contradictions``
    accepts a ``subject`` LIKE filter that narrows the row count at
    the SQL layer.  For a concept with a tight scope (3–5
    evergreens), this is cheaper than pulling every contradiction in
    the pack and filtering in Python — and crucially scales with
    scope size, not vault size.
    """
    if not scope_evergreens:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for slug in scope_evergreens:
        if not slug:
            continue
        for row in list_contradictions(
            vault_dir,
            limit=contradiction_limit,
            subject=slug,
            pack_name=pack_name,
        ):
            cid = str(row.get("contradiction_id", ""))
            if not cid or cid in seen:
                continue
            seen.add(cid)
            out.append(row)
    return out


def evaluate_all_concepts(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    since_hours: int = 24,
    now: datetime | None = None,
    audit_event_limit: int = 500,
    contradiction_limit: int = 100,
) -> list[ConceptEvaluation]:
    """Walk every active live concept; return per-concept evaluation.

    Inputs:

    * ``since_hours`` — recency window for ``on_ingest_match``;
      route-decision rows older than this are not considered.
      Default 24h matches the typical operator's "I want to see
      what came in overnight" cadence.
    * ``now`` — clock anchor for ``weekly_resynthesis``.  Default
      ``datetime.now(timezone.utc)``.  Tests pin a fixed instant.
    * ``audit_event_limit`` — cap on ``recent_audit_events`` fetch
      per scan; routing decisions older than the cutoff are then
      dropped in Python.  Set to 500 because at typical absorb
      rates (≤50 sources/day) one day of routing events fits well
      under that, and overshooting the cap loses recency rather
      than completeness.
    * ``contradiction_limit`` — per-slug ``list_contradictions``
      cap.

    No mutation; safe to run concurrently with absorb / extract /
    other writers.
    """
    handles = list_live_concepts(vault_dir, active_only=True)
    if not handles:
        return []

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(hours=max(since_hours, 0))

    vault_path = Path(vault_dir) if not isinstance(vault_dir, Path) else vault_dir
    all_audit = recent_audit_events(vault_path, limit=audit_event_limit)
    route_events = _filter_recent_route_decisions(all_audit, cutoff=cutoff)

    evaluations: list[ConceptEvaluation] = []
    for handle in handles:
        contradictions = _gather_contradictions_for_scope(
            vault_path,
            pack_name=pack_name,
            scope_evergreens=handle.frontmatter.scope_evergreens,
            contradiction_limit=contradiction_limit,
        )
        evaluations.append(ConceptEvaluation(
            handle=handle,
            weekly_due=weekly_resynthesis_due(handle, now=current),
            ingest_matches=evaluate_ingest_matches(
                handle, recent_route_decisions=route_events,
            ),
            contradiction_matches=evaluate_contradiction_matches(
                handle, open_contradictions=contradictions,
            ),
        ))
    return evaluations
