"""Tests for the M24.0 stop-gap event-evidence registry.

The registry's job is single source of truth for "which event_types
belong to which Maintainer card."  Lock this in so the three
surfaces (``/ops/today``, ``/digests`` calendar, ``/ops/events``)
cannot drift again before M24 lifecycle work lands.
"""

from __future__ import annotations

import pytest

from ovp_pipeline.event_evidence_registry import (
    CATEGORIES,
    EventEvidence,
    all_event_types,
    classify,
    event_types_for_category,
    is_user_visible,
)


# ── Schema ─────────────────────────────────────────────────────


def test_categories_are_the_canonical_five():
    """The visible Maintainer card vocabulary is locked.  Adding a
    sixth category requires a code change here and a renderer change
    — make it deliberate, not accidental."""
    assert CATEGORIES == (
        "intake", "absorb", "synthesis", "governance", "failures",
    )


def test_every_registered_event_has_a_known_category():
    for et in all_event_types():
        entry = classify(et)
        assert entry is not None
        assert entry.category in CATEGORIES


# ── Intake list anchors the three surfaces ─────────────────────


def test_intake_includes_real_producer_events():
    """The events real producers emit in the operator vault must be
    classified as intake.  This is the test that would have caught
    the M23 default allowlist drift (``clippings_batch_processed``
    etc. — names no producer ever emits)."""
    intake = event_types_for_category("intake")
    assert "article_processed" in intake
    assert "source_archived_to_processed" in intake
    assert "source_staged_for_processing" in intake
    assert "github_intake_completed" in intake
    assert "clippings_processed" in intake


def test_intake_excludes_legacy_or_debug_only_by_default():
    """High-volume forensic events like ``images_downloaded`` exist
    but mustn't inflate the primary intake count."""
    intake = event_types_for_category("intake")
    assert "images_downloaded" not in intake
    assert "pinboard_process_file_started" not in intake


def test_intake_includes_legacy_when_asked():
    primary = event_types_for_category("intake")
    with_legacy = event_types_for_category("intake", include_legacy=True)
    # ``include_legacy`` shouldn't drop any primary entries.
    assert set(primary) <= set(with_legacy)
    # And it MUST actually add the legacy / non-user-visible rows
    # the default excludes — otherwise the flag is a no-op
    # (CodeRabbit Major caught this with the prior implementation).
    # ``images_downloaded`` and ``pinboard_process_file_started``
    # are intake-category, user_visible=False; ``include_legacy=True``
    # must surface them.
    assert "images_downloaded" not in primary
    assert "images_downloaded" in with_legacy
    assert "pinboard_process_file_started" in with_legacy


# ── Failures stay separate ─────────────────────────────────────


def test_failures_isolated_from_other_categories():
    """A failure-class event must not also count as intake/absorb/
    etc — otherwise the same row inflates two cards."""
    fail = set(event_types_for_category("failures"))
    for cat in ("intake", "absorb", "synthesis", "governance"):
        other = set(event_types_for_category(cat))
        overlap = fail & other
        assert not overlap, f"failures overlap with {cat}: {overlap}"


# ── Visibility flag ────────────────────────────────────────────


@pytest.mark.parametrize("user_visible_et", [
    "article_processed",
    "absorb_route_decision",
    "promote_concept",
    "absorb_parse_error",
])
def test_user_visible_events_count_for_primary_cards(user_visible_et):
    assert is_user_visible(user_visible_et)


@pytest.mark.parametrize("debug_et", [
    "atlas_updated_from_registry",
    "quality_checked",
    "transaction_started",
    "task_dispatched",
])
def test_debug_only_events_do_not_count_for_primary_cards(debug_et):
    assert not is_user_visible(debug_et)


def test_unregistered_event_type_returns_none():
    assert classify("totally_made_up_event_2027") is None
    assert is_user_visible("totally_made_up_event_2027") is False


# ── Contract enforcement: all three downstream surfaces agree ──


def test_today_card_event_types_come_from_registry():
    """``TODAY_DIGEST_CARDS`` must read from the registry, not a
    hardcoded list.  Defends against a future contributor pasting
    a fourth allowlist into view_models.py."""
    from ovp_pipeline.ui.view_models import TODAY_DIGEST_CARDS

    for card_id, _label, event_types in TODAY_DIGEST_CARDS:
        registry_set = set(event_types_for_category(card_id))
        card_set = set(event_types)
        assert card_set == registry_set, (
            f"/ops/today {card_id} drifted from registry: "
            f"missing={registry_set - card_set}, "
            f"extra={card_set - registry_set}"
        )


def test_digests_calendar_intake_list_matches_registry():
    """The ``/digests`` calendar's intake counter must use the same
    registry list — this is the bug the user originally reported
    (27 vs 7 on the same day)."""
    from ovp_pipeline.commands._digests_list_page import _INTAKE_EVENT_TYPES

    assert set(_INTAKE_EVENT_TYPES) == set(event_types_for_category("intake"))


def test_digest_config_default_intake_matches_registry():
    """M23 digest's Layer 0 default must match too."""
    from ovp_pipeline.digest_config import _DEFAULT_INTAKE_EVENT_TYPES

    assert set(_DEFAULT_INTAKE_EVENT_TYPES) == set(
        event_types_for_category("intake")
    )
