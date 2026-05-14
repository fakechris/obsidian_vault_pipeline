"""Tests for the honest-zero messaging helper (M24.3).

The whole point of centralising this module is so the wording stays
the same across every surface — a regression that drops the
ambiguity from one surface but not another would be hard to spot
manually.  These tests lock the phrase + lock the cross-surface
consistency.
"""

from __future__ import annotations

from ovp_pipeline.ops_honest_zero import (
    HONEST_ZERO_LONG,
    HONEST_ZERO_SHORT,
    honest_zero_html,
    honest_zero_markdown,
)


def test_short_phrase_names_the_three_causes():
    """The short form must mention all three upstream causes so the
    operator sees the ambiguity at a glance.  Adding a fourth cause
    requires a code change here — make it deliberate."""
    assert "not run" in HONEST_ZERO_SHORT
    assert "no output" in HONEST_ZERO_SHORT
    assert "missing instrumentation" in HONEST_ZERO_SHORT


def test_short_phrase_is_one_line():
    assert "\n" not in HONEST_ZERO_SHORT


def test_long_phrase_extends_the_short_one():
    """The long-form banner must contain every cause the short one
    surfaces — otherwise the empty-page banner says something less
    honest than the inline card footer."""
    for cause in ("didn't run", "emitted nothing", "audit row"):
        assert cause in HONEST_ZERO_LONG


def test_html_short_renders_a_paragraph():
    html = honest_zero_html(short=True)
    assert html.startswith("<p")
    assert HONEST_ZERO_SHORT in html
    assert "muted tiny" in html


def test_html_long_renders_a_card_banner():
    html = honest_zero_html(short=False)
    assert "<div" in html
    assert HONEST_ZERO_LONG in html


def test_html_respects_custom_css_class():
    html = honest_zero_html(short=True, css_class="custom-style")
    assert "custom-style" in html
    assert "muted tiny" not in html


def test_html_empty_css_class_falls_back():
    """Empty/whitespace css_class must not produce a class='' attr —
    fall back to the sensible default."""
    html = honest_zero_html(short=True, css_class="   ")
    assert "muted tiny" in html


def test_markdown_is_italic_wrapped():
    md = honest_zero_markdown()
    assert HONEST_ZERO_SHORT in md
    assert md.strip().startswith("_")
    assert md.strip().endswith("_")
