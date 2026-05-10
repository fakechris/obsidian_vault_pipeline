"""BL-063 PR#2: pure trigger evaluators.

Three trigger kinds, one parser.  Each test pins a single
behaviour so a regression points at the exact field.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ovp_pipeline.live_concept import LiveConceptFrontmatter, LiveConceptHandle
from ovp_pipeline.live_concept_triggers import (
    WeeklySchedule,
    evaluate_contradiction_matches,
    evaluate_ingest_matches,
    parse_weekly_schedule,
    weekly_resynthesis_due,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _handle(
    *,
    objective: str = "Track LLM evals.",
    active: bool = True,
    triggers: dict | None = None,
    scope_evergreens: tuple[str, ...] = (),
    last_run_at: str = "",
    last_attempt_at: str = "",
) -> LiveConceptHandle:
    fm = LiveConceptFrontmatter(
        objective=objective,
        active=active,
        triggers=triggers or {},
        scope_evergreens=scope_evergreens,
        last_run_at=last_run_at,
        last_attempt_at=last_attempt_at,
    )
    return LiveConceptHandle(
        path=Path("/tmp/test/30-Projects/Tracking/test.md"),
        relative_path="30-Projects/Tracking/test.md",
        slug="test",
        frontmatter=fm,
    )


# ---------------------------------------------------------------------------
# parse_weekly_schedule
# ---------------------------------------------------------------------------


def test_parse_schedule_three_letter_day():
    s = parse_weekly_schedule("Mon 09:00")
    assert s == WeeklySchedule(day_of_week=0, hour=9, minute=0)


def test_parse_schedule_full_day_name_lowercase():
    s = parse_weekly_schedule("monday 9:00")
    assert s == WeeklySchedule(day_of_week=0, hour=9, minute=0)


def test_parse_schedule_sunday_index_six():
    s = parse_weekly_schedule("Sun 23:59")
    assert s == WeeklySchedule(day_of_week=6, hour=23, minute=59)


def test_parse_schedule_rejects_unknown_day():
    assert parse_weekly_schedule("Jan 09:00") is None
    assert parse_weekly_schedule("Funday 09:00") is None


def test_parse_schedule_rejects_malformed_time():
    assert parse_weekly_schedule("Mon 9") is None  # no colon
    assert parse_weekly_schedule("Mon 25:00") is None  # invalid hour
    assert parse_weekly_schedule("Mon 09:60") is None  # invalid minute
    assert parse_weekly_schedule("Mon abc:def") is None  # non-numeric


def test_parse_schedule_rejects_non_string():
    assert parse_weekly_schedule(None) is None
    assert parse_weekly_schedule(42) is None
    assert parse_weekly_schedule({"day": "Mon"}) is None


def test_parse_schedule_tolerates_extra_whitespace():
    s = parse_weekly_schedule("  Tue   14:30  ")
    assert s == WeeklySchedule(day_of_week=1, hour=14, minute=30)


# ---------------------------------------------------------------------------
# weekly_resynthesis_due
# ---------------------------------------------------------------------------


def test_weekly_due_never_run_after_first_scheduled_instant():
    """Brand-new concept (last_run_at='') fires on the first scan
    after the scheduled time has passed."""
    handle = _handle(triggers={"weekly_resynthesis": "Mon 09:00"})
    # Tuesday 10:00 UTC — past Monday 09:00, never run before
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is True


def test_weekly_due_already_ran_after_most_recent_fire():
    """Last run was Monday 09:30; now is Monday 09:31 (the same
    week's scheduled fire) — already ran, don't fire again."""
    handle = _handle(
        triggers={"weekly_resynthesis": "Mon 09:00"},
        last_run_at="2026-05-11T09:30:00Z",
    )
    now = datetime(2026, 5, 11, 9, 31, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is False


def test_weekly_due_new_week_after_previous_run():
    """Last run was Monday 09:30 last week; now it's the next Tuesday
    — past Monday 09:00, last run is older — fire."""
    handle = _handle(
        triggers={"weekly_resynthesis": "Mon 09:00"},
        last_run_at="2026-05-04T09:30:00Z",
    )
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is True


def test_weekly_due_exactly_at_scheduled_time_fires():
    """Now equals the scheduled time exactly.  ``most_recent_past``
    is the *previous* week's instant (the helper returns < before),
    so a never-run concept fires."""
    handle = _handle(triggers={"weekly_resynthesis": "Mon 09:00"})
    now = datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc)  # Mon 09:00 sharp
    assert weekly_resynthesis_due(handle, now=now) is True


def test_weekly_due_before_scheduled_time_today_uses_last_week():
    """Now is Monday 08:00 — today's scheduled instant hasn't
    arrived; the most recent past fire was last Monday."""
    handle = _handle(
        triggers={"weekly_resynthesis": "Mon 09:00"},
        last_run_at="2026-05-04T09:30:00Z",  # last Monday post-fire
    )
    now = datetime(2026, 5, 11, 8, 0, tzinfo=timezone.utc)
    # Last run (last Mon 09:30) > most-recent-past (last Mon 09:00) —
    # we already ran since the last scheduled fire.
    assert weekly_resynthesis_due(handle, now=now) is False


def test_weekly_due_inactive_concept_never_fires():
    handle = _handle(
        active=False,
        triggers={"weekly_resynthesis": "Mon 09:00"},
    )
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is False


def test_weekly_due_no_trigger_config_does_not_fire():
    handle = _handle(triggers={})
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is False


def test_weekly_due_malformed_schedule_does_not_fire():
    handle = _handle(triggers={"weekly_resynthesis": "Maybeday 9:00"})
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is False


def test_weekly_due_malformed_last_run_at_treats_as_never_run():
    handle = _handle(
        triggers={"weekly_resynthesis": "Mon 09:00"},
        last_run_at="garbage",
    )
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert weekly_resynthesis_due(handle, now=now) is True


# ---------------------------------------------------------------------------
# evaluate_ingest_matches
# ---------------------------------------------------------------------------


def _route_event(*, source: str, update_slugs: list[str], timestamp: str = "2026-05-10T12:00:00Z"):
    return {
        "event_type": "absorb_route_decision",
        "timestamp": timestamp,
        "payload": {
            "source": source,
            "update_slugs": update_slugs,
            "create_titles": [],
            "status": "ok",
        },
    }


def test_ingest_match_fires_on_concept_similarity_to_slug():
    handle = _handle(
        triggers={"on_ingest_match": {
            "concept_similarity_to": "llm-eval-leakage",
            "threshold": 0.65,
        }},
    )
    rows = [
        _route_event(source="50-Inbox/foo.md", update_slugs=["llm-eval-leakage", "other"]),
    ]
    matches = evaluate_ingest_matches(handle, recent_route_decisions=rows)
    assert len(matches) == 1
    assert matches[0].matched_slug == "llm-eval-leakage"
    assert matches[0].matched_via == "concept_similarity_to"
    assert matches[0].source_path == "50-Inbox/foo.md"


def test_ingest_match_fires_on_scope_evergreens_slug():
    handle = _handle(
        triggers={"on_ingest_match": {"threshold": 0.65}},
        scope_evergreens=("eval-cost-vs-quality",),
    )
    rows = [
        _route_event(source="50-Inbox/bar.md", update_slugs=["eval-cost-vs-quality"]),
    ]
    matches = evaluate_ingest_matches(handle, recent_route_decisions=rows)
    assert len(matches) == 1
    assert matches[0].matched_slug == "eval-cost-vs-quality"
    assert matches[0].matched_via == "scope_evergreens"


def test_ingest_match_explicit_trigger_slug_takes_precedence_over_scope():
    """When the same slug appears in both ``concept_similarity_to``
    and ``scope_evergreens``, the explicit trigger config wins as
    the ``matched_via`` reason."""
    handle = _handle(
        triggers={"on_ingest_match": {"concept_similarity_to": "llm-eval"}},
        scope_evergreens=("llm-eval",),
    )
    rows = [_route_event(source="x.md", update_slugs=["llm-eval"])]
    matches = evaluate_ingest_matches(handle, recent_route_decisions=rows)
    assert len(matches) == 1
    assert matches[0].matched_via == "concept_similarity_to"


def test_ingest_match_skips_non_tracked_slugs():
    handle = _handle(
        triggers={"on_ingest_match": {"concept_similarity_to": "llm-eval"}},
    )
    rows = [_route_event(source="x.md", update_slugs=["totally-unrelated"])]
    assert evaluate_ingest_matches(handle, recent_route_decisions=rows) == []


def test_ingest_match_inactive_concept_returns_empty():
    handle = _handle(
        active=False,
        triggers={"on_ingest_match": {"concept_similarity_to": "llm-eval"}},
    )
    rows = [_route_event(source="x.md", update_slugs=["llm-eval"])]
    assert evaluate_ingest_matches(handle, recent_route_decisions=rows) == []


def test_ingest_match_no_trigger_config_returns_empty():
    handle = _handle(triggers={})
    rows = [_route_event(source="x.md", update_slugs=["whatever"])]
    assert evaluate_ingest_matches(handle, recent_route_decisions=rows) == []


def test_ingest_match_dedupes_repeated_payload_slugs():
    """A single audit row that lists the same slug twice in
    update_slugs (shouldn't happen but defence) yields one match."""
    handle = _handle(
        triggers={"on_ingest_match": {"concept_similarity_to": "llm-eval"}},
    )
    rows = [_route_event(source="x.md", update_slugs=["llm-eval", "llm-eval"])]
    assert len(evaluate_ingest_matches(handle, recent_route_decisions=rows)) == 1


def test_ingest_match_handles_malformed_payload_gracefully():
    """Row with no payload / non-list update_slugs is silently
    dropped — never crashes the scheduler."""
    handle = _handle(
        triggers={"on_ingest_match": {"concept_similarity_to": "llm-eval"}},
    )
    rows = [
        {"event_type": "absorb_route_decision", "timestamp": "2026-05-10T12:00:00Z"},
        {"event_type": "absorb_route_decision", "payload": {"update_slugs": "not-a-list"}},
        _route_event(source="ok.md", update_slugs=["llm-eval"]),
    ]
    matches = evaluate_ingest_matches(handle, recent_route_decisions=rows)
    assert len(matches) == 1
    assert matches[0].source_path == "ok.md"


# ---------------------------------------------------------------------------
# evaluate_contradiction_matches
# ---------------------------------------------------------------------------


def _contradiction(*, cid: str, subject: str, status: str = "open"):
    return {
        "contradiction_id": cid,
        "subject_key": subject,
        "status": status,
        "positive_claim_ids": [],
        "negative_claim_ids": [],
    }


def test_contradiction_match_fires_on_in_scope_subject():
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("llm-eval-leakage",),
    )
    rows = [_contradiction(cid="c1", subject="research-tech::llm-eval-leakage")]
    matches = evaluate_contradiction_matches(handle, open_contradictions=rows)
    assert len(matches) == 1
    assert matches[0].matched_slug == "llm-eval-leakage"
    assert matches[0].subject_key == "research-tech::llm-eval-leakage"


def test_contradiction_match_skips_out_of_scope_subject():
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("llm-eval-leakage",),
    )
    rows = [_contradiction(cid="c1", subject="something::else-entirely")]
    assert evaluate_contradiction_matches(handle, open_contradictions=rows) == []


def test_contradiction_match_skips_resolved():
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("llm-eval-leakage",),
    )
    rows = [_contradiction(
        cid="c1", subject="llm-eval-leakage", status="resolved",
    )]
    assert evaluate_contradiction_matches(handle, open_contradictions=rows) == []


def test_contradiction_match_no_trigger_config_returns_empty():
    handle = _handle(
        triggers={},
        scope_evergreens=("llm-eval-leakage",),
    )
    rows = [_contradiction(cid="c1", subject="llm-eval-leakage")]
    assert evaluate_contradiction_matches(handle, open_contradictions=rows) == []


def test_contradiction_match_no_scope_returns_empty():
    """Trigger fires only against in-scope evergreens; no scope =
    nothing to match."""
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=(),
    )
    rows = [_contradiction(cid="c1", subject="llm-eval-leakage")]
    assert evaluate_contradiction_matches(handle, open_contradictions=rows) == []


def test_contradiction_match_inactive_concept_returns_empty():
    handle = _handle(
        active=False,
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("llm-eval-leakage",),
    )
    rows = [_contradiction(cid="c1", subject="llm-eval-leakage")]
    assert evaluate_contradiction_matches(handle, open_contradictions=rows) == []


def test_contradiction_match_segment_avoids_substring_collision():
    """Codex review fix: a short scope slug must not match a
    longer, unrelated word containing it.  E.g. scope ``"llm-eval"``
    must NOT fire on subject ``"large-llm-evals"``."""
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("llm-eval",),
    )
    rows = [_contradiction(cid="c1", subject="research::large-llm-evals")]
    assert evaluate_contradiction_matches(handle, open_contradictions=rows) == []


def test_contradiction_match_segment_handles_multi_level_subject():
    """Multi-level pack-prefixed subject like
    ``research-tech::llm-eval::leakage`` matches scope
    ``llm-eval`` because the second segment equals exactly."""
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("llm-eval",),
    )
    rows = [_contradiction(cid="c1", subject="research-tech::llm-eval::leakage")]
    matches = evaluate_contradiction_matches(handle, open_contradictions=rows)
    assert len(matches) == 1
    assert matches[0].matched_slug == "llm-eval"


def test_ingest_match_handles_non_dict_payload():
    """Codex review fix: a malformed audit row whose ``payload`` is
    a non-dict (legacy / corrupted JSON) must not crash the
    evaluator — silently dropped, scan continues."""
    handle = _handle(
        triggers={"on_ingest_match": {"concept_similarity_to": "llm-eval"}},
    )
    rows = [
        {"event_type": "absorb_route_decision", "payload": "not-a-dict"},
        {"event_type": "absorb_route_decision", "payload": ["also", "wrong"]},
        {"event_type": "absorb_route_decision", "payload": 42},
        _route_event(source="ok.md", update_slugs=["llm-eval"]),
    ]
    matches = evaluate_ingest_matches(handle, recent_route_decisions=rows)
    assert len(matches) == 1
    assert matches[0].source_path == "ok.md"


def test_contradiction_match_dedupes_by_contradiction_id():
    """Same contradiction id seen twice (e.g. emitted by two
    overlapping scope slugs) collapses to one match."""
    handle = _handle(
        triggers={"on_contradiction_against_view": True},
        scope_evergreens=("alpha", "beta"),
    )
    rows = [
        _contradiction(cid="c1", subject="research::alpha"),
        _contradiction(cid="c1", subject="research::alpha"),
    ]
    matches = evaluate_contradiction_matches(handle, open_contradictions=rows)
    assert len(matches) == 1
