"""Tests for the PR #208 round-3 review fixes.

Covers:

* ``_safe_wikilink`` strips wikilink delimiters from
  operator-supplied labels / slugs (rev-bot 208 round-2 #6).
  Originally tested via ``_build_sources_section`` against the M20
  dict-shape inputs; M23 rewrote the sources block to consume
  ``DigestInputs`` (BL-095), so the delimiter-safety invariant now
  lives in ``_safe_wikilink`` directly.
* ``_render_frontmatter_details`` skips the disclosure block when
  the file has no frontmatter (rev-bot 208 round-2 #4).
* ``_evolution_candidate_lock`` returns distinct locks for
  distinct cache keys (rev-bot 208 round-2 #9 — per-key
  coordination).
* ``_read_body_sections`` splits frontmatter before
  scanning (rev-bot 208 round-2 #8).
"""

from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline._truth_helpers import _evolution_candidate_lock
from ovp_pipeline.commands._ui_renderers import _render_frontmatter_details
from ovp_pipeline.commands.digest_handler import _safe_wikilink


# ── wikilink delimiter sanitisation ─────────────────────────────


def test_safe_wikilink_strips_closing_brackets():
    """``]`` would close the wikilink prematurely; both ``[`` and
    ``]`` are stripped to whitespace."""
    assert "]" not in _safe_wikilink("trailing]]bad")
    assert "[" not in _safe_wikilink("[[wrapped]]")


def test_safe_wikilink_strips_pipe():
    """``|`` is the wikilink target/label separator — stripping it
    avoids accidentally splitting a single value into two parts."""
    assert "|" not in _safe_wikilink("a|b|c")


def test_safe_wikilink_strips_newlines():
    """Newlines would break out of the surrounding list item."""
    assert "\n" not in _safe_wikilink("line1\nline2")


def test_safe_wikilink_handles_falsy():
    """``None`` / empty / falsy inputs degrade to ``''`` via
    ``str(value or "")`` — the implementation collapses any falsy
    value (incl. ``0``) so the surrounding wikilink doesn't render
    a bogus ``[[0]]`` row from a missing-int field."""
    assert _safe_wikilink(None) == ""
    assert _safe_wikilink("") == ""
    assert _safe_wikilink(0) == ""


def test_safe_wikilink_preserves_safe_chars():
    """Hyphens, underscores, alphanumerics, colons survive."""
    assert _safe_wikilink("cluster::memory-systems_v2") == "cluster::memory-systems_v2"


# ── _render_frontmatter_details elision ─────────────────────────


def test_frontmatter_details_empty_renders_nothing():
    assert _render_frontmatter_details("") == ""
    assert _render_frontmatter_details("   ") == ""


def test_frontmatter_details_renders_when_present():
    html = _render_frontmatter_details("<table><tr><th>k</th><td>v</td></tr></table>")
    assert "<details" in html
    assert "<summary>Frontmatter</summary>" in html
    assert "<table>" in html


# ── per-key lock ────────────────────────────────────────────────


def test_evolution_candidate_lock_is_per_key():
    key_a = ("/vault-a", (), "pack-x", ())
    key_b = ("/vault-b", (), "pack-x", ())
    lock_a = _evolution_candidate_lock(key_a)
    lock_b = _evolution_candidate_lock(key_b)
    assert lock_a is not lock_b
    # Same key returns the same lock (registry idempotency).
    assert _evolution_candidate_lock(key_a) is lock_a


def test_evolution_candidate_lock_allows_unrelated_misses_to_progress():
    """A miss holding lock_a must not block a miss for lock_b."""
    key_a = ("/vault-a", (), "p", ())
    key_b = ("/vault-b", (), "p", ())
    lock_a = _evolution_candidate_lock(key_a)
    lock_b = _evolution_candidate_lock(key_b)
    # Acquire A and verify B is still acquirable without waiting.
    assert lock_a.acquire(blocking=False)
    try:
        assert lock_b.acquire(blocking=False)
        lock_b.release()
    finally:
        lock_a.release()


# ── LC agent splits frontmatter before reading sections ─────────


def test_lc_agent_reads_sections_after_frontmatter_split(tmp_path: Path):
    """Verify the agent's section reader doesn't accidentally pick
    up YAML keys that happen to look like H2 headings (e.g. inside
    a quoted scalar block).  Routes through the real public API
    so the regression target is the integration, not the helper."""
    from ovp_pipeline.live_concept_agent import _read_body_sections

    p = tmp_path / "concept.md"
    # YAML body intentionally contains a string that *could*
    # confuse a naive heading scanner.  After split it's
    # off-limits; the agent must only read the body sections.
    p.write_text(
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: |\n"
        "    Some objective\n"
        '    "## Current synthesis"\n'
        "  active: true\n"
        "  scope_evergreens: []\n"
        "  triggers: {}\n"
        "---\n\n"
        "## My take\n\nuser-only.\n\n"
        "## Current synthesis  <!-- agent-owned -->\n\n"
        "real body line.\n",
        encoding="utf-8",
    )
    sections = _read_body_sections(p)
    assert "real body line." in sections["Current synthesis"]
    # The YAML-quoted ``## Current synthesis`` must NOT leak in.
    assert "Some objective" not in sections["Current synthesis"]
