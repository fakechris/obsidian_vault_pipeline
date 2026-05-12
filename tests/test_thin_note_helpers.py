"""Tests for the helpers extracted in PR #208 round 2.

* ``_resolve_effective_type`` consolidates the precedence chain
  that ``_is_thin_note`` and ``_render_thin_note_preamble`` both
  used to duplicate (rev-bot 208.2).
* ``_render_live_concept_preamble`` must escape every operator-
  supplied frontmatter value, including the ``threshold`` field
  on ``on_ingest_match`` (Codex P2).
"""

from __future__ import annotations

from pathlib import Path

from ovp_pipeline.commands._ui_renderers import (
    _resolve_effective_type,
    _render_thin_note_preamble,
)


# ── _resolve_effective_type precedence ───────────────────────────


def test_type_takes_precedence_over_original_note_type():
    fm = {"type": "digest", "original_note_type": "live-concept"}
    assert _resolve_effective_type(fm) == "digest"


def test_original_note_type_recovers_after_normalisation_drift():
    """A vault where ``note_type_normalize`` rewrote the type to
    ``article`` before PR #207 landed still routes through the
    thin shell via ``original_note_type``."""
    fm = {"type": "article", "original_note_type": "user-profile"}
    assert _resolve_effective_type(fm) == "user-profile"


def test_live_block_falls_back_structurally():
    """Even with no usable ``type:`` / ``original_note_type:``, a
    ``live:`` block keys the file as a live-concept."""
    fm = {"type": "article", "live": {"active": True}}
    assert _resolve_effective_type(fm) == "live-concept"


def test_non_thin_type_passes_through_lowercased():
    fm = {"type": "Evergreen"}
    assert _resolve_effective_type(fm) == "evergreen"


def test_empty_frontmatter_returns_empty():
    assert _resolve_effective_type({}) == ""


# ── threshold escape on Live Concept preamble ────────────────────


def test_live_concept_preamble_escapes_threshold(tmp_path: Path):
    """If a Live Concept's ``threshold`` is a string (e.g. an
    operator inserted ``"<script>alert(1)</script>"``), the
    preamble must escape it like every other frontmatter field."""
    md = (
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: test\n"
        "  active: true\n"
        "  triggers:\n"
        "    on_ingest_match:\n"
        "      concept_similarity_to: foo\n"
        "      threshold: \"<script>alert(1)</script>\"\n"
        "  scope_evergreens: []\n"
        "---\n\n# body\n"
    )
    html = _render_thin_note_preamble(
        "30-Projects/Tracking/x.md", md, requested_pack="",
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_live_concept_preamble_threshold_numeric_renders(tmp_path: Path):
    """The common case — a numeric threshold — still renders
    cleanly after the str/escape coercion."""
    md = (
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: test\n"
        "  active: true\n"
        "  triggers:\n"
        "    on_ingest_match:\n"
        "      concept_similarity_to: foo\n"
        "      threshold: 0.55\n"
        "  scope_evergreens: []\n"
        "---\n\n# body\n"
    )
    html = _render_thin_note_preamble(
        "30-Projects/Tracking/x.md", md, requested_pack="",
    )
    assert "cosine ≥ 0.55" in html
