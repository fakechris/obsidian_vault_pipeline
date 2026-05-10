"""BL-063 PR#2 — pure trigger evaluators for Live Concepts.

The three trigger kinds documented in
:mod:`ovp_pipeline.live_concept`:

* ``weekly_resynthesis`` — cron-lite weekly schedule
  (``"Mon 09:00"``).  Fires when the most recent scheduled instant
  has passed since ``last_run_at``.
* ``on_ingest_match`` — fires when a recent
  ``absorb_route_decision`` audit event touched a slug the concept
  cares about (either the explicit ``concept_similarity_to`` slug
  or any slug in ``scope_evergreens``).
* ``on_contradiction_against_view`` — fires when an open
  contradiction's ``subject_key`` overlaps with one of the
  concept's ``scope_evergreens``.

This module is **pure**: every function takes pre-fetched data and
the current time, and returns a result.  No DB access, no I/O,
no clock reads — that's the scheduler module's job.  Pure
evaluators make trigger semantics straightforward to unit-test
without spinning up a fake DB.

PR#2 stops at evaluation + listing.  The actual agent fire +
``patch_live`` calls (to bump ``lastAttemptAt`` / ``lastRunAt``)
land in PR#3 alongside the section-aware body editor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .live_concept import LiveConceptHandle


# ---------------------------------------------------------------------------
# weekly_resynthesis
# ---------------------------------------------------------------------------


# Day-of-week aliases.  ``datetime.weekday()`` returns 0=Monday … 6=Sunday;
# we accept both 3-letter abbreviations and full names, case-insensitive.
_DAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1, "tues": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3, "thur": 3, "thurs": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


@dataclass(frozen=True)
class WeeklySchedule:
    """Parsed ``"Mon 09:00"`` schedule.

    ``day_of_week`` matches :meth:`datetime.weekday` (0=Mon … 6=Sun);
    ``hour`` / ``minute`` are 24-hour clock values in the timezone
    the caller compares against (the scheduler module passes UTC,
    so ``"Mon 09:00"`` means 09:00 UTC every Monday).
    """

    day_of_week: int
    hour: int
    minute: int


def parse_weekly_schedule(text: Any) -> WeeklySchedule | None:
    """Parse strings like ``"Mon 09:00"`` / ``"Friday 14:30"`` /
    ``"sun 0:00"``.

    Returns ``None`` on any parse failure (unknown day, malformed
    time, non-string input, etc.) — caller treats the trigger as
    "misconfigured, do not fire".  Liberal on whitespace and case;
    conservative on shape.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    day_raw, time_raw = parts
    day_key = day_raw.strip().lower()
    if day_key not in _DAYS:
        return None
    if ":" not in time_raw:
        return None
    hour_str, minute_str = time_raw.split(":", 1)
    try:
        hour = int(hour_str)
        minute = int(minute_str)
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return WeeklySchedule(
        day_of_week=_DAYS[day_key], hour=hour, minute=minute,
    )


def _most_recent_past_occurrence(
    schedule: WeeklySchedule, before: datetime,
) -> datetime:
    """Return the latest scheduled datetime ``< before``.

    Independent helper so tests can pin the corner case at the
    exact day-of-week / time boundary (e.g. now is Mon 09:00:00
    sharp — the most recent past occurrence is the *previous*
    Monday, not "right now").
    """
    today_at_schedule_time = before.replace(
        hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0,
    )
    days_back = (before.weekday() - schedule.day_of_week) % 7
    candidate = today_at_schedule_time - timedelta(days=days_back)
    if candidate >= before:
        # Scheduled time today hasn't elapsed yet → most recent fire was a
        # week ago.
        candidate -= timedelta(days=7)
    return candidate


def _parse_iso_z(text: str) -> datetime | None:
    """Parse an ISO 8601 timestamp (Z-suffixed or with offset) into
    a UTC-aware datetime.  Returns ``None`` on any parse failure —
    caller treats malformed timestamps as "never run" and fires."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def weekly_resynthesis_due(
    handle: LiveConceptHandle,
    *,
    now: datetime | None = None,
) -> bool:
    """True when the concept's ``weekly_resynthesis`` schedule has
    passed since ``last_run_at``.

    Returns ``False`` on any of:

    * concept is ``active = false``
    * ``triggers.weekly_resynthesis`` missing or unparseable
    * the most recent scheduled instant is *after* ``last_run_at``
      → fire (returns True)
    * the most recent scheduled instant is *before or equal to*
      ``last_run_at`` → already ran since the last scheduled fire
      → don't fire (returns False)

    A never-run concept (``last_run_at == ""``) fires immediately
    once the first scheduled instant has passed, which gives the
    operator a fast feedback loop after they declare a new concept.
    """
    fm = handle.frontmatter
    if not fm.is_active:
        return False
    schedule = parse_weekly_schedule(fm.triggers.get("weekly_resynthesis"))
    if schedule is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    most_recent_past = _most_recent_past_occurrence(schedule, current)
    last_run = _parse_iso_z(fm.last_run_at)
    if last_run is None:
        return True
    return last_run < most_recent_past


# ---------------------------------------------------------------------------
# on_ingest_match
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestMatch:
    """One ``absorb_route_decision`` audit row that touched a slug
    the concept cares about.

    ``matched_slug`` is the slug from the concept's trigger config
    (``concept_similarity_to`` or one of ``scope_evergreens``) that
    appeared in the router's ``update_slugs``; ``source_path`` is
    the markdown source the router was deciding for; ``timestamp``
    is the audit row's ISO timestamp so the scheduler can rate-
    limit / dedupe across scans.

    ``matched_via`` says which side matched: ``"concept_similarity_to"``
    if the explicit trigger slug matched, ``"scope_evergreens"`` if
    one of the scope slugs matched.  Useful for the scan CLI's
    audit log ("which fence kept this trigger lit?").
    """

    source_path: str
    matched_slug: str
    matched_via: str
    timestamp: str


def evaluate_ingest_matches(
    handle: LiveConceptHandle,
    *,
    recent_route_decisions: list[dict[str, Any]],
) -> list[IngestMatch]:
    """For every recent ``absorb_route_decision`` audit row, return
    ``IngestMatch`` entries for any whose ``update_slugs`` overlaps
    a slug this concept tracks.

    A slug is "tracked" if it equals the concept's
    ``triggers.on_ingest_match.concept_similarity_to`` value OR
    appears in the concept's ``scope_evergreens``.  PR#2 treats the
    router's *decision to update* as the similarity signal — the
    router has already crossed its own internal gate by deciding to
    update.  PR#3 may layer a finer threshold on top via qmd
    rescoring, at which point the ``threshold`` config field
    documented in the schema starts to bite.

    Returns an empty list when:

    * concept is inactive
    * ``triggers.on_ingest_match`` is missing / falsy
    * no recent route-decision row matched a tracked slug

    Liberal on row shape: missing ``payload`` keys are treated as
    empty.  The caller (scheduler) filters by event type +
    timestamp before passing rows here, so this function trusts the
    inputs are routing decisions.
    """
    fm = handle.frontmatter
    if not fm.is_active:
        return []
    trigger_cfg = fm.triggers.get("on_ingest_match")
    if not trigger_cfg:
        return []

    tracked_slugs: dict[str, str] = {}
    if isinstance(trigger_cfg, dict):
        target = str(trigger_cfg.get("concept_similarity_to", "")).strip()
        if target:
            tracked_slugs[target] = "concept_similarity_to"
    for slug in fm.scope_evergreens:
        # If the slug was already registered via concept_similarity_to,
        # don't downgrade its source — keep the explicit trigger config
        # as the matched_via reason.
        tracked_slugs.setdefault(slug, "scope_evergreens")

    if not tracked_slugs:
        return []

    matches: list[IngestMatch] = []
    seen: set[tuple[str, str, str]] = set()
    for row in recent_route_decisions:
        payload = row.get("payload") or {}
        update_slugs = payload.get("update_slugs") or []
        if not isinstance(update_slugs, list):
            continue
        source_path = str(payload.get("source", "") or row.get("source_log", ""))
        timestamp = str(row.get("timestamp", ""))
        for slug in update_slugs:
            slug_str = str(slug).strip()
            if slug_str not in tracked_slugs:
                continue
            key = (source_path, slug_str, timestamp)
            if key in seen:
                continue
            seen.add(key)
            matches.append(IngestMatch(
                source_path=source_path,
                matched_slug=slug_str,
                matched_via=tracked_slugs[slug_str],
                timestamp=timestamp,
            ))
    return matches


# ---------------------------------------------------------------------------
# on_contradiction_against_view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContradictionMatch:
    """One open contradiction whose ``subject_key`` overlaps the
    concept's ``scope_evergreens``.

    The matched slug from ``scope_evergreens`` is recorded as
    ``matched_slug`` so the scan output / PR#3 agent prompt can
    point the operator at *which* in-scope evergreen the
    contradiction implicates.
    """

    contradiction_id: str
    subject_key: str
    matched_slug: str
    status: str


def evaluate_contradiction_matches(
    handle: LiveConceptHandle,
    *,
    open_contradictions: list[dict[str, Any]],
) -> list[ContradictionMatch]:
    """For every open contradiction, return matches when its
    ``subject_key`` mentions any of the concept's
    ``scope_evergreens`` slugs.

    Subject-key matching is substring-based: the subject keys
    ``list_contradictions`` returns can carry pack prefixes /
    qualifiers (e.g. ``"research-tech::llm-eval-leakage"``), so a
    plain ``"llm-eval-leakage" in subject_key`` is more robust than
    equality.

    Returns an empty list when:

    * concept is inactive
    * ``triggers.on_contradiction_against_view`` is falsy / missing
    * concept has no ``scope_evergreens`` (nothing to compare
      against — this trigger is meaningless without scope)
    * no open contradiction's subject mentions an in-scope slug

    Already-resolved contradictions are skipped — caller is
    expected to pre-filter to ``status='open'``, but we re-check
    here as a defence so a stale cache doesn't fire a resolved
    contradiction.
    """
    fm = handle.frontmatter
    if not fm.is_active:
        return []
    if not fm.triggers.get("on_contradiction_against_view"):
        return []
    if not fm.scope_evergreens:
        return []

    matches: list[ContradictionMatch] = []
    seen_ids: set[str] = set()
    for c in open_contradictions:
        if str(c.get("status", "")).lower() != "open":
            continue
        contradiction_id = str(c.get("contradiction_id", ""))
        if contradiction_id in seen_ids:
            continue
        subject = str(c.get("subject_key", ""))
        for slug in fm.scope_evergreens:
            if slug and slug in subject:
                seen_ids.add(contradiction_id)
                matches.append(ContradictionMatch(
                    contradiction_id=contradiction_id,
                    subject_key=subject,
                    matched_slug=slug,
                    status="open",
                ))
                break
    return matches
