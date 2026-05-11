"""Test the Reader-home Today's-digest banner card (M20 / BL-077)."""

from __future__ import annotations

from pathlib import Path

# Importing through the renderer aggregate avoids the circular-import
# path that ``from .reader_home import _render_reader_home`` would
# take at collection time.
from ovp_pipeline.commands._ui_renderers import _render_reader_home  # type: ignore[attr-defined]


def _base_payload(pack: str = "research-tech") -> dict:
    """Minimal valid payload — empty crystals, no map, no digest."""
    return {
        "requested_pack": pack,
        "pack": pack,
        "search_href": "/search",
        "map_href": "/map",
        "map_supported": False,
        "top_topics": [],
        "recent_crystals": [],
        "recent_days": 7,
        "curated_atlas": {
            "available": False,
            "total_chains": 0,
            "top_n": 0,
            "effective_top_n": 0,
            "atlas_href": "/topics",
        },
    }


def test_no_digest_card_when_payload_missing():
    payload = _base_payload()
    html = _render_reader_home(payload)
    assert "Today's digest" not in html


def test_no_digest_card_when_digest_is_empty_dict():
    payload = _base_payload()
    payload["digest"] = {}
    html = _render_reader_home(payload)
    assert "Today's digest" not in html


def test_digest_card_appears_with_link_and_date():
    payload = _base_payload()
    payload["digest"] = {
        "date": "2026-05-11",
        "href": "/note?path=40-Resources%2FGenerated%2Fdigests%2F2026-05-11.md",
        "teaser": "Two tensions worth sitting with today.",
    }
    html = _render_reader_home(payload)
    assert "Today's digest" in html
    assert "2026-05-11" in html
    assert "/note?path=40-Resources" in html
    assert "Two tensions worth sitting with today." in html
    # Card lives before the Top Topics heading so it actually leads.
    assert html.index("Today's digest") < html.index("Top Topics")


def test_digest_card_handles_missing_teaser():
    payload = _base_payload()
    payload["digest"] = {
        "date": "2026-05-11",
        "href": "/note?path=foo.md",
        "teaser": "",
    }
    html = _render_reader_home(payload)
    assert "Today's digest" in html
    assert "Open digest →" in html
