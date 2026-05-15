"""Tests for M25.5 vocabulary alignment.

M25 locked the five visible Maintainer states (Received,
Extracted, Accepted, Synthesized, Needs Action) as the card
vocabulary.  The underlying *evidence categories* (intake,
absorb, synthesis, governance, failures) stay in the registry —
those are classifications, not labels.

These tests prevent vocabulary drift between the cards (state
labels) and the supporting surfaces (digest body, /digests
calendar legend, /ops/items page headings, etc.).  A future PR
that renames a state label without updating the supporting
surfaces — or that misuses an evidence-category word as a
state-card label — fails CI loudly.
"""

from __future__ import annotations

import pytest


# ── State-label vocabulary lock ──────────────────────────────────


def test_m25_card_def_ids_use_the_five_locked_states():
    """The card definition list must enumerate exactly the five
    visible Maintainer states.  Adding a sixth (or renaming one)
    requires updating ``docs/operational-lifecycle.md`` and this
    test together."""
    from ovp_pipeline.ui.view_models import M25_LIFECYCLE_CARD_DEFS

    ids = [c["id"] for c in M25_LIFECYCLE_CARD_DEFS]
    assert ids == [
        "Received", "Extracted", "Accepted",
        "Synthesized", "NeedsAction",
    ]


def test_m25_card_def_labels_match_operational_lifecycle_doc():
    """Card labels are what the operator reads on the dashboard.
    Locked: ``Received / Extracted / Accepted / Synthesized /
    Needs Action`` (note the space in "Needs Action" — UI label,
    not the kernel's ``NeedsAction`` enum)."""
    from ovp_pipeline.ui.view_models import M25_LIFECYCLE_CARD_DEFS

    labels = {c["id"]: c["label"] for c in M25_LIFECYCLE_CARD_DEFS}
    assert labels["Received"] == "Received"
    assert labels["Extracted"] == "Extracted"
    assert labels["Accepted"] == "Accepted"
    assert labels["Synthesized"] == "Synthesized"
    # The kernel enum has no space; the display label does.
    assert labels["NeedsAction"] == "Needs Action"


# ── Per-state secondary verbs ────────────────────────────────────


def test_per_state_secondary_verbs_locked():
    """The per-state phrasing ("arrived today", "extracted today",
    etc.) is what differentiates the M25 hybrid card from the
    forbidden "+5 today" framing.  Lock the wording so future
    edits stay deliberate."""
    from ovp_pipeline.ui.view_models import M25_LIFECYCLE_CARD_DEFS

    verbs = {c["id"]: c.get("secondary_verb", "") for c in M25_LIFECYCLE_CARD_DEFS}
    assert verbs["Received"] == "arrived today"
    assert verbs["Extracted"] == "extracted today"
    assert verbs["Accepted"] == "accepted today"
    assert verbs["Synthesized"] == "synthesized today"
    assert verbs["NeedsAction"] == "new blockers today"


# ── Surface-level alignment ──────────────────────────────────────


def test_digests_calendar_legend_uses_audit_evidence_phrase():
    """M24.3 changed the calendar's em-dash gloss from the
    misleading "quiet day" to the honest "no audit evidence" with
    the three-cause ambiguity.  M25 keeps this — regression guard
    that the legend doesn't get reduced back to "quiet day"."""
    from pathlib import Path
    from ovp_pipeline.commands._digests_list_page import (
        _render_calendar_grid,
        CalendarCell,
    )

    cells = [
        CalendarCell(
            date="2026-05-13",
            has_digest=False,
            intake_count=0,
            digest_href="",
            explore_href="/ops/today?date=2026-05-13",
        ),
    ]
    html = _render_calendar_grid(cells)
    assert "no audit evidence" in html
    # The three causes the honest-zero principle names.
    for cause in ("not run", "no output", "missing instrumentation"):
        assert cause in html


def test_ops_items_state_explainers_cover_every_state():
    """The /ops/items renderer prints a state-specific explainer
    paragraph below the H1.  Every state in M25_LIFECYCLE_CARD_DEFS
    must have copy."""
    from ovp_pipeline.commands._ui_renderers import _render_items_list_page
    from ovp_pipeline.ui.view_models import M25_LIFECYCLE_CARD_DEFS

    for card in M25_LIFECYCLE_CARD_DEFS:
        state = card["id"]
        # Render the empty-state page for this state — explainer
        # appears regardless of whether there are rows.
        payload = {
            "screen": "ops/items",
            "available": True,
            "state": state,
            "pack": "research-tech",
            "requested_pack": "",
            "rows": [],
            "total": 0,
            "offset": 0,
            "limit": 50,
            "next_offset": None,
            "prev_offset": None,
        }
        html = _render_items_list_page(payload)
        assert state in html, (
            f"/ops/items renderer does not name the state {state!r}"
        )


# ── Anti-regression: no state label leakage as event category ────


def test_state_labels_are_not_used_as_event_categories():
    """The five state labels (Received / Extracted / Accepted /
    Synthesized / NeedsAction) must NOT appear as event_evidence
    categories.  Categories stay at intake / absorb / synthesis /
    governance / failures."""
    from ovp_pipeline.event_evidence_registry import CATEGORIES

    state_labels = {
        "Received", "Extracted", "Accepted",
        "Synthesized", "NeedsAction",
    }
    leaked = state_labels & set(CATEGORIES)
    assert not leaked, (
        f"State labels leaked into registry categories: {leaked}.  "
        "Categories are evidence classifications, not state names."
    )
