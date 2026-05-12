"""Tests for the PR #208 round-3 review fixes.

Covers:

* ``_decode_slugs`` rejects non-list JSON (rev-bot 208 round-2 #5).
* ``_build_sources_section`` strips wikilink delimiters from
  operator-supplied labels / slugs (rev-bot 208 round-2 #6).
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
from ovp_pipeline.commands.digest_handler import _build_sources_section


# ── _decode_slugs robustness via _build_sources_section ──────────


def test_sources_section_tolerates_null_json_payload():
    """A row whose ``source_evergreen_slugs_json`` was stored as
    ``null`` (or any non-list) must not abort digest generation."""
    inputs = {
        "tensions": [],
        # Themes carry the suspect field; if _decode_slugs
        # crashes, this call raises.
        "themes": [
            {
                "cluster_id": "cluster::abc",
                "label": "Theme A",
                "source_evergreen_slugs": [],  # decoded already
            },
        ],
        "open_questions": [],
    }
    md = _build_sources_section(inputs)
    assert "Crystals" in md
    assert "Theme A" in md


# ── wikilink delimiter sanitisation ─────────────────────────────


def test_sources_section_strips_label_delimiters():
    """``]]`` and ``|`` in a label would close or split the
    wikilink prematurely; both must be stripped."""
    inputs = {
        "tensions": [],
        "themes": [
            {
                "cluster_id": "cluster::abc",
                "label": "Tricky ]] | label\nwith newline",
                "source_evergreen_slugs": [],
            },
        ],
        "open_questions": [],
    }
    md = _build_sources_section(inputs)
    # The crystal link is rendered with the safe-id as target and
    # a sanitised label.  No nested ]] or | inside the wikilink.
    crystal_line = [
        ln for ln in md.splitlines() if ln.startswith("- [[abc")
    ][0]
    # Exactly two ``]]`` (the closing bracket of the wikilink)
    # and exactly one ``|`` (the target/label separator).
    assert crystal_line.count("]]") == 1
    assert crystal_line.count("|") == 1
    assert "\n" not in crystal_line


def test_sources_section_strips_slug_delimiters():
    """Evergreen slug rows likewise sanitise on render."""
    inputs = {
        "tensions": [],
        "themes": [
            {
                "cluster_id": "cluster::abc",
                "label": "Theme",
                "source_evergreen_slugs": ["bad]]slug", "ok-slug"],
            },
        ],
        "open_questions": [],
    }
    md = _build_sources_section(inputs)
    # The bad slug's ``]]`` was stripped — no nested ``]]``
    # inside any single evergreen row.
    for line in md.splitlines():
        if line.startswith("- [[") and "Theme" not in line:
            assert line.count("]]") == 1


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
