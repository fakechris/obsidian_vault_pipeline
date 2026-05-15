"""Event-type evidence registry (M24.0 stop-gap, 2026-05-14).

STOP-GAP STATUS — please read before extending
================================================

This module is an **evidence-classification** layer for the legacy
audit-event-driven surfaces (``/ops/today``, ``/digests`` calendar,
``/ops/events``).  It exists for one reason: as of M23 we had three
*different* event-type allowlists defining "intake" across three
surfaces, so the same day showed 27 / 7 / many different counts.
This registry collapses those three lists to one.

It is **not** the final pipeline-truth model.  Product-level
operational state (current lifecycle position of each source,
why a number is 0, what action repairs it) MUST come from the
``ops_lifecycle`` / ``ops_state`` modules that M24's Lifecycle
Contract Layer and M25's Maintainer Control Plane will introduce.

Hard rules for callers
----------------------

* This registry classifies *evidence*, not *truth*.  An event in
  category X means "we observed an X-shaped row land in
  ``audit_events``" — never "X happened" or "X is the current
  state".
* No card / page should fabricate a reason for a zero count from
  this data.  An empty bucket means "no audit-event evidence" —
  three different upstream causes (not run, ran no output, missing
  instrumentation) collapse into that one observation.  M24
  instrumentation repair plus ``ops_lifecycle`` will untangle the
  three.
* Legacy event types (``redo_deep_dive_archived``,
  ``entity_type_backfill_v2_passthrough``, etc.) are marked
  ``debug_only`` so they don't inflate primary cards but still
  show up in the forensic ``/ops/events`` log.

Categories (these mirror the 5-card maintainer surface today;
they will be re-thought into the 5 visible lifecycle states under
M25, but for the stop-gap we leave the surface labels alone).

* ``intake``      — material entered the vault (Source layer)
* ``absorb``      — Source → Candidate / Canonical (Absorb stage)
* ``synthesis``   — Canonical → Crystal / contradiction
* ``governance``  — human or governance-rule actions
* ``failures``    — error evidence from any stage

Naming note: the file is deliberately ``event_evidence_registry``,
not ``pipeline_events``, so future contributors don't mistake it
for the lifecycle model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class EventEvidence:
    """One row in the registry.

    ``category`` is one of the primary buckets; ``user_visible``
    controls whether the event surfaces as evidence on Maintainer
    primary cards.  Legacy events stay registered (so /ops/events
    can label them) but with ``user_visible=False`` so they don't
    drive the primary count.
    """

    event_type: str
    category: str
    user_visible: bool = True
    legacy: bool = False
    description: str = ""


# Authoritative categorisation.  Built from the operator vault's
# actual ``audit_events`` histogram on 2026-05-14 (47 distinct
# event types) plus the M14–M23 emitters in source.  Adding a new
# event_type producer requires adding a row here.
_REGISTRY: Final[tuple[EventEvidence, ...]] = (
    # ── intake ─────────────────────────────────────────────────
    EventEvidence("article_processed", "intake",
                  description="An article-shaped source finished its processor."),
    EventEvidence("article_intake_only", "intake",
                  description="A source was ingested without further processing."),
    EventEvidence("clippings_processed", "intake",
                  description="A web-clippings batch was parsed and split."),
    EventEvidence("pinboard_process_file_completed", "intake",
                  description="A pinboard archive entry was processed."),
    EventEvidence("source_staged_for_processing", "intake",
                  description="A raw source moved into the 02-Processing stage."),
    EventEvidence("source_archived_to_processed", "intake",
                  description="A processed source moved into 03-Processed (intake completed)."),
    EventEvidence("github_intake_completed", "intake",
                  description="A GitHub source-authority ingest finished."),
    EventEvidence("source_dedup_skipped", "intake",
                  description="A duplicate source was skipped at intake."),
    EventEvidence("redo_source_processed", "intake",
                  description="A previously processed source was reprocessed."),

    # ── absorb ─────────────────────────────────────────────────
    EventEvidence("absorb_completed", "absorb",
                  description="An absorb pass finished without raising."),
    EventEvidence("absorb_route_decision", "absorb",
                  description="The router picked an absorb target for a source."),
    EventEvidence("evergreen_auto_promoted", "absorb",
                  description="An evergreen candidate was auto-promoted to canonical."),
    # M24.2: legacy fallback branch in ``auto_evergreen_extractor``
    # that creates an evergreen directly (skipping the candidate
    # queue).  Registered so producer_audit can find it; the
    # registry classifies it as absorb evidence with user_visible
    # since it represents a canonical write the operator should see.
    EventEvidence("evergreen_created", "absorb",
                  description=(
                      "Legacy fallback: extractor wrote an evergreen "
                      "directly without going through the candidate "
                      "queue."
                  )),
    EventEvidence("evergreen_extraction_complete", "absorb",
                  description="Evergreen extraction finished for a source."),
    EventEvidence("candidates_upserted", "absorb",
                  description="Candidate rows were written to knowledge.db."),
    # M24.2 (2026-05-14): the kernel reads this to detect Prepared
    # sub-state precisely.  Producer fires it AFTER
    # ``evergreen_extraction_complete`` and BEFORE the candidate
    # upsert that follows; if the upsert fails or never happens,
    # this row remains and the kernel surfaces Prepared with a
    # real anchor.  ``user_visible=False`` so it never inflates
    # Maintainer card counts.
    EventEvidence("absorb_pending_upsert", "absorb",
                  user_visible=False,
                  description=(
                      "Extraction finished but the candidate upsert "
                      "hasn't been observed yet — kernel marker for "
                      "Prepared sub-state."
                  )),

    # ── synthesis ──────────────────────────────────────────────
    EventEvidence("community_crystal_synthesized", "synthesis",
                  description="A community crystal was generated or refreshed."),
    EventEvidence("contradiction_crystal_synthesized", "synthesis",
                  description="A contradiction crystal was generated."),
    EventEvidence("crystal_archived", "synthesis",
                  description="A crystal was archived (superseded)."),
    EventEvidence("moc_updated", "synthesis",
                  description="A Map-of-Content page was updated."),
    EventEvidence("moc_update_complete", "synthesis",
                  description="MOC update batch finished."),

    # ── governance ─────────────────────────────────────────────
    EventEvidence("promote_concept", "governance",
                  description="An operator (or auto-promote) promoted a concept."),
    # M24.2 (2026-05-14): ``promotion`` is the richer
    # state-transition row that ``promotion_audit.emit_promotion``
    # writes for the doctor's mtime check and the lint
    # ZONE_BOUNDARY_VIOLATION rule.  It is paired 1:1 with
    # ``promote_concept`` in the operator-promote path; both rows
    # are intentional (different consumers).  Registering it here
    # so ``/ops/events`` no longer labels it "uncategorised audit
    # event" and the producer audit can verify it lands when
    # expected.
    EventEvidence("promotion", "governance",
                  description=(
                      "Rich state-transition row (state-lifecycle "
                      "boundary crossing); pairs with promote_concept "
                      "on the operator-promote path."
                  )),
    # M24.2: ``zone_violation`` is emitted by ``promotion_audit.emit_zone_violation``
    # when something writes into the accepted-zone without going
    # through promotion.  The lint ZONE_BOUNDARY_VIOLATION rule
    # reads it; registering here so /ops/events labels it and the
    # producer audit doesn't flag it as drift.
    EventEvidence("zone_violation", "governance",
                  description=(
                      "Accepted-zone write that bypassed promotion "
                      "(read by lint ZONE_BOUNDARY_VIOLATION rule)."
                  )),
    EventEvidence("concept_archived", "governance",
                  description="A concept was archived by review."),
    EventEvidence("concept_merged", "governance",
                  description="Two concepts were merged by review."),
    EventEvidence("dedup_cleanup_archived", "governance",
                  description="Dedup cleanup archived a duplicate evergreen."),
    EventEvidence("candidate_review_action", "governance",
                  description="A reviewer accepted/rejected a candidate."),
    EventEvidence("evolution_review_action", "governance",
                  description="A reviewer acted on an evolution candidate."),
    EventEvidence("contradictions_resolved", "governance",
                  description="One or more contradictions were resolved."),
    EventEvidence("quality_checked", "governance", user_visible=False,
                  description="A quality check fired (high cardinality, debug-only)."),
    EventEvidence("quality_check_complete", "governance", user_visible=False,
                  description="A quality check batch finished."),
    EventEvidence("article_abstained", "governance",
                  description="The article processor declined to extract."),

    # ── failures ───────────────────────────────────────────────
    EventEvidence("absorb_parse_error", "failures",
                  description="A source failed to parse during absorb."),
    EventEvidence("absorb_schema_drift", "failures",
                  description="Absorb output drifted from the schema."),
    EventEvidence("absorb_skipped_source", "failures",
                  description="Absorb skipped a source because of a guard."),
    EventEvidence("broken_link", "failures",
                  description="A wikilink target was missing."),
    EventEvidence("github_intake_error", "failures",
                  description="A GitHub source-authority ingest raised."),
    EventEvidence("article_error", "failures",
                  description="An article processor raised."),
    EventEvidence("image_download_error", "failures",
                  description="Image rewrite couldn't fetch a URL."),
    EventEvidence("pipeline_partial_failure", "failures",
                  description="A pipeline run completed with at least one step failing."),
    EventEvidence("command_timeout", "failures",
                  description="A CLI step exceeded its time budget."),
    EventEvidence("evergreen_error", "failures",
                  description="Evergreen processing raised."),
    EventEvidence("evergreen_low_link", "failures", user_visible=False,
                  description="An evergreen lacks enough inbound links (lint signal, not an error)."),

    # ── legacy / debug-only ────────────────────────────────────
    # These rows stay registered so /ops/events can label them with
    # a real description instead of "Uncategorised audit event".
    # ``user_visible=False`` keeps them off the primary cards.
    EventEvidence("atlas_updated_from_registry", "governance",
                  user_visible=False,
                  description="Atlas index was rewritten from registry (high-volume, debug-only)."),
    EventEvidence("entity_type_backfill_v2_passthrough", "governance",
                  user_visible=False, legacy=True,
                  description="Legacy M11 entity-type backfill no-op row."),
    EventEvidence("entity_backfill_summary", "governance",
                  user_visible=False, legacy=True,
                  description="Legacy M11 entity-backfill summary."),
    EventEvidence("redo_deep_dive_archived", "absorb",
                  user_visible=False, legacy=True,
                  description="Legacy BL-029 Deep Dive archival rows; producer removed."),
    EventEvidence("file_moved", "governance", user_visible=False,
                  description="A file moved between stage folders (high-volume)."),
    EventEvidence("images_downloaded", "intake", user_visible=False,
                  description="Image fetcher succeeded (per-source detail, debug-only)."),
    EventEvidence("pinboard_process_file_started", "intake",
                  user_visible=False,
                  description="Pinboard processor entered a file (pairs with completed)."),
    EventEvidence("source_restored_to_raw", "intake",
                  user_visible=False,
                  description="A processed source was rolled back to raw."),
    EventEvidence("transaction_started", "governance", user_visible=False,
                  description="A workflow transaction opened (forensic only)."),
    EventEvidence("transaction_completed", "governance", user_visible=False,
                  description="A workflow transaction closed (forensic only)."),
    EventEvidence("command_started", "governance", user_visible=False,
                  description="A CLI step started (forensic only)."),
    EventEvidence("command_completed", "governance", user_visible=False,
                  description="A CLI step completed (forensic only)."),
    EventEvidence("pipeline_started", "governance", user_visible=False,
                  description="A pipeline run started."),
    EventEvidence("pipeline_completed", "governance", user_visible=False,
                  description="A pipeline run completed."),
    EventEvidence("task_dispatched", "governance", user_visible=False,
                  description="The task dispatcher ran a task (forensic only)."),
    # M25.6 dogfood pass on operator vault flagged this as drift —
    # ``ovp-live-concept-scan`` emits ``live_concept_agent_run``
    # but no registry row claimed it.  Register as governance /
    # forensic-only so it surfaces in /ops/events with a real
    # label and producer-audit stops flagging drift.
    EventEvidence("live_concept_agent_run", "governance",
                  user_visible=False,
                  description=(
                      "Live-concept synthesis agent ran "
                      "(``ovp-live-concept-scan``); forensic only."
                  )),
)


def _build_by_type_index(rows: tuple[EventEvidence, ...]) -> dict[str, EventEvidence]:
    """Reject duplicate ``event_type`` keys at module-load time.

    A duplicate would let ``classify`` silently return whichever
    row appeared last in the registry — a quiet way for two
    "single source of truth" rows to fight.  Fail loud (CodeRabbit
    Major) so the contributor adding the duplicate sees it
    immediately, not a year later when a count drifts.
    """
    index: dict[str, EventEvidence] = {}
    for row in rows:
        if row.event_type in index:
            raise ValueError(
                f"event_evidence_registry: duplicate event_type "
                f"{row.event_type!r} — registry must have one entry per type"
            )
        index[row.event_type] = row
    return index


_BY_TYPE: Final[dict[str, EventEvidence]] = _build_by_type_index(_REGISTRY)

CATEGORIES: Final[tuple[str, ...]] = (
    "intake", "absorb", "synthesis", "governance", "failures",
)


def classify(event_type: str) -> EventEvidence | None:
    """Return the registry entry for ``event_type``, or None if unregistered."""
    return _BY_TYPE.get(event_type)


def event_types_for_category(
    category: str, *, include_legacy: bool = False
) -> tuple[str, ...]:
    """All registered event_types in ``category``.

    Default (``include_legacy=False``) excludes ``user_visible=False``
    and ``legacy=True`` rows so primary surfaces don't inflate their
    counts with high-volume forensic / debug evidence
    (``atlas_updated_from_registry``, ``quality_checked``, etc.).

    ``include_legacy=True`` returns **every** row in ``category``,
    legacy + non-user-visible included.  Used by ``/ops/events``
    forensic view so it can classify rows that the primary cards
    skip (CodeRabbit Major: previously the flag was a no-op because
    the ``user_visible`` guard still ran, so debug-only legacy rows
    stayed hidden even when explicitly requested).
    """
    if include_legacy:
        return tuple(
            e.event_type
            for e in _REGISTRY
            if e.category == category
        )
    return tuple(
        e.event_type
        for e in _REGISTRY
        if e.category == category
        and e.user_visible
        and not e.legacy
    )


def all_event_types(*, include_legacy: bool = True) -> tuple[str, ...]:
    """Every registered event_type, primary + legacy.

    Used by ``/ops/events`` to know which rows have a registry
    label.  Anything not registered shows up as
    "uncategorised" in the forensic log.
    """
    return tuple(
        e.event_type
        for e in _REGISTRY
        if include_legacy or not e.legacy
    )


def is_user_visible(event_type: str) -> bool:
    """True when this event_type counts toward primary card totals."""
    entry = _BY_TYPE.get(event_type)
    return entry is not None and entry.user_visible


__all__ = [
    "CATEGORIES",
    "EventEvidence",
    "all_event_types",
    "classify",
    "event_types_for_category",
    "is_user_visible",
]
