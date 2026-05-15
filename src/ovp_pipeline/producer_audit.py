"""Producer-audit registry (M24.2, 2026-05-14).

The M24.1 lifecycle kernel reads from ``audit_events`` and trusts
that producers emit the rows the kernel uses for state
classification.  If a producer silently skips an emit, the kernel
sees absence and either misclassifies the item (Prepared /
Projected sub-state on every source) or drops it from a card count
entirely.

This module declares — *per producer* — which event_types are
expected to land in ``audit_events`` and provides a verifier the
``ovp-producer-audit`` CLI runs against a real ``knowledge.db``.
It is intentionally scoped to the **hot-path producers** the M24
plan locks (~7 modules).  Extending the audit to the long-tail of
forensic-only producers is out of scope for M24.2.

Design contract
---------------

* This module is **read-only** over ``audit_events``.  It never
  inserts.
* Event-type classification still routes through
  ``event_evidence_registry`` — this module declares *who emits
  what*, the registry declares *what the row means*.  The
  contract test below enforces that every hot-path producer's
  declared events are registered.
* "Hot-path" means: every event_type the kernel reads from for
  state classification has at least one declared producer here.
  A test enforces that invariant.
* Declared events are split into ``must_emit`` (the producer
  cannot finish a normal happy-path run without emitting these)
  and ``may_emit`` (governed by config or branches — emitted
  sometimes, not always).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Final

from .event_evidence_registry import all_event_types, classify


@dataclass(frozen=True)
class ProducerContract:
    """One row in the producer-audit registry.

    ``module`` is the import-path-style identifier (so error
    messages can cite ``ovp_pipeline.auto_evergreen_extractor``
    rather than a relative file path).  ``must_emit`` is the set
    of event_types this producer is guaranteed to emit on a normal
    happy-path run; ``may_emit`` is best-effort or
    branch-governed.
    """

    producer: str
    module: str
    must_emit: tuple[str, ...]
    may_emit: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


# Authoritative hot-path producer registry.  Order is for
# readability only; lookups are by ``producer`` name.
CONTRACTS: Final[tuple[ProducerContract, ...]] = (
    ProducerContract(
        producer="auto_article_processor",
        module="ovp_pipeline.auto_article_processor",
        must_emit=("article_intake_only",),
        may_emit=("article_error", "article_abstained"),
        description=(
            "Generates deep-dive interpretations from raw articles "
            "in 50-Inbox/01-Raw and emits an intake row for each."
        ),
    ),
    ProducerContract(
        producer="clippings_processor",
        module="ovp_pipeline.clippings_processor",
        must_emit=("clippings_processed",),
        description=(
            "Splits clippings batches and emits one summary row per "
            "batch run."
        ),
    ),
    ProducerContract(
        producer="auto_github_processor",
        module="ovp_pipeline.auto_github_processor",
        must_emit=("github_intake_completed",),
        may_emit=("github_intake_error",),
        description=(
            "Pulls GitHub source-authority entries; one completion "
            "row per ingested source."
        ),
    ),
    ProducerContract(
        producer="absorb_router",
        module="ovp_pipeline.absorb_router",
        must_emit=("absorb_route_decision",),
        description=(
            "Picks an absorb target for each source; one decision "
            "row per source the router sees."
        ),
    ),
    ProducerContract(
        producer="auto_evergreen_extractor",
        module="ovp_pipeline.auto_evergreen_extractor",
        must_emit=(
            "evergreen_extraction_complete",
            # M24.2: the next three are what M24.2 wired.  Before
            # 2026-05-14 the extractor finished without telling the
            # kernel about the candidate writes that followed, so
            # every Extracted item showed up as Prepared on a real
            # vault.
            "absorb_pending_upsert",
            "candidates_upserted",
            "evergreen_auto_promoted",
        ),
        may_emit=("absorb_parse_error", "evergreen_error", "evergreen_created"),
        description=(
            "Extracts evergreen candidates from interpreted notes "
            "and writes them to knowledge.db; can auto-promote when "
            "source_count crosses the threshold."
        ),
    ),
    ProducerContract(
        producer="promote_command",
        module="ovp_pipeline.commands.promote",
        must_emit=("promote_concept", "promotion"),
        description=(
            "Operator-driven concept promotion via ``ovp-promote run "
            "--apply``.  Emits both ``promote_concept`` (kernel "
            "Accepted signal) and ``promotion`` (state-lifecycle "
            "boundary row read by the doctor mtime check)."
        ),
    ),
    ProducerContract(
        producer="community_crystal_synthesizer",
        module="ovp_pipeline.commands.synthesize_community_crystals",
        must_emit=("community_crystal_synthesized",),
        description=(
            "Synthesises one crystal per cluster from accumulated "
            "evergreens; emits a synthesis row per cluster touched."
        ),
    ),
)


_BY_PRODUCER: Final[dict[str, ProducerContract]] = {
    c.producer: c for c in CONTRACTS
}


def producer_for_event_type(event_type: str) -> ProducerContract | None:
    """Return the FIRST declared producer for ``event_type``.

    Most event_types are emitted by exactly one producer; when a
    row appears in multiple ``must_emit`` lists, this function
    returns the first match (registry order).  Callers who need
    every producer for an event should iterate ``CONTRACTS``
    directly.
    """
    for contract in CONTRACTS:
        if event_type in contract.must_emit or event_type in contract.may_emit:
            return contract
    return None


def all_declared_event_types(*, include_may: bool = True) -> set[str]:
    """Every event_type declared across every hot-path producer.

    ``include_may=False`` returns only the must-emit set — useful
    for "the producer audit reports any of these missing as a
    real instrumentation gap".
    """
    types: set[str] = set()
    for contract in CONTRACTS:
        types.update(contract.must_emit)
        if include_may:
            types.update(contract.may_emit)
    return types


# ── Audit verifier ────────────────────────────────────────────────


@dataclass(frozen=True)
class ProducerFinding:
    """One row in the audit report."""

    producer: str
    event_type: str
    severity: str  # "missing" | "ok" | "drift"
    last_seen: str  # ISO timestamp or "" if never
    count_in_window: int


@dataclass(frozen=True)
class ProducerAuditReport:
    """Result of running the audit against a knowledge.db."""

    window_start: str
    window_end: str
    findings: tuple[ProducerFinding, ...]
    unknown_event_types: tuple[str, ...]  # in log, not in any contract


def audit_against_log(
    conn: sqlite3.Connection,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    now: datetime | None = None,
    window_days: int = 7,
) -> ProducerAuditReport:
    """Compare the producer registry against ``audit_events`` rows.

    For each declared producer event:
      * ``severity="missing"`` if zero rows in the window (must_emit
        only — may_emit absence is not a finding).
      * ``severity="ok"`` if at least one row found.

    Plus a separate listing of event_types observed in the log that
    don't appear in any contract (``unknown_event_types``).  Those
    are either:
      * forensic/debug rows that intentionally aren't in the
        hot-path audit, or
      * drift — a producer that started emitting something we
        didn't declare.  Reviewer should check.

    Defaults to a 7-day window ending at ``now`` (UTC).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if window_end is None:
        window_end = now.isoformat()
    if window_start is None:
        window_start = (now - timedelta(days=window_days)).isoformat()

    findings: list[ProducerFinding] = []
    for contract in CONTRACTS:
        for et in contract.must_emit:
            row = conn.execute(
                "SELECT COUNT(*), MAX(timestamp) FROM audit_events "
                " WHERE event_type = ? AND timestamp >= ? AND timestamp <= ?",
                (et, window_start, window_end),
            ).fetchone()
            count = int(row[0] or 0)
            last_seen = row[1] or ""
            findings.append(
                ProducerFinding(
                    producer=contract.producer,
                    event_type=et,
                    severity="ok" if count > 0 else "missing",
                    last_seen=last_seen,
                    count_in_window=count,
                )
            )
        for et in contract.may_emit:
            row = conn.execute(
                "SELECT COUNT(*), MAX(timestamp) FROM audit_events "
                " WHERE event_type = ? AND timestamp >= ? AND timestamp <= ?",
                (et, window_start, window_end),
            ).fetchone()
            count = int(row[0] or 0)
            if count == 0:
                continue  # may_emit absence isn't a finding
            findings.append(
                ProducerFinding(
                    producer=contract.producer,
                    event_type=et,
                    severity="ok",
                    last_seen=row[1] or "",
                    count_in_window=count,
                )
            )

    # Drift = event_types in the log that are neither in a
    # producer contract NOR in the central event-evidence registry.
    # Registered-but-not-hot-path events (article_processed,
    # moc_updated, zone_violation, transaction_started, …) are
    # intentionally outside the contract scope and should NOT
    # surface as drift; that would make ovp-producer-audit exit 2
    # on every healthy vault.  Truly unknown rows — events neither
    # declared by a producer nor classified by the registry — are
    # the real signal the operator wants.  CodeRabbit review on
    # PR #234 caught this.
    declared = all_declared_event_types(include_may=True)
    registered = set(all_event_types(include_legacy=True))
    rows = conn.execute(
        "SELECT DISTINCT event_type FROM audit_events "
        " WHERE timestamp >= ? AND timestamp <= ?",
        (window_start, window_end),
    ).fetchall()
    observed = {r[0] for r in rows if r[0]}
    unknown = tuple(
        sorted(
            et for et in observed
            if et not in declared and et not in registered
        )
    )

    return ProducerAuditReport(
        window_start=window_start,
        window_end=window_end,
        findings=tuple(findings),
        unknown_event_types=unknown,
    )


__all__ = [
    "CONTRACTS",
    "ProducerAuditReport",
    "ProducerContract",
    "ProducerFinding",
    "all_declared_event_types",
    "audit_against_log",
    "producer_for_event_type",
]
