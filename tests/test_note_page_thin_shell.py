"""Tests for the thin /note shell + body-first ordering (PR #208).

User complaint on the live vault:

* Digest pages rendered the full evergreen scaffold (Production
  Chain, Inbound Capture, Evidence Traceability, Where To Go Next)
  even though those cards had no data — the actual digest body
  ended up buried below ~6 empty cards.
* Same problem on Live Concept pages.
* Even on real evergreens the body (the thing readers came for)
  sat at the bottom under all the metadata.

Fix:

* ``_is_thin_note`` detects ``type: digest`` / ``type: live-concept``
  / ``type: user-profile`` frontmatter, plus any file under
  ``40-Resources/Generated/``, and routes through a thin shell that
  shows only header + body + collapsed frontmatter.
* Full shell now leads with the body, scaffolding below.
"""

from __future__ import annotations

from pathlib import Path

from ovp_pipeline.commands._ui_renderers import (
    _THIN_NOTE_PATH_PREFIXES,
    _THIN_NOTE_TYPES,
    _is_thin_note,
    _render_note_page,
)


# ── _is_thin_note ────────────────────────────────────────────────


def test_digest_type_is_thin():
    md = "---\ntype: digest\n---\n\n# body\n"
    assert _is_thin_note("40-Resources/Generated/digests/x.md", md) is True


def test_live_concept_type_is_thin():
    md = "---\ntype: live-concept\nlive:\n  active: true\n---\n\n# body\n"
    assert _is_thin_note("30-Projects/Tracking/x.md", md) is True


def test_user_profile_type_is_thin():
    md = "---\ntype: user-profile\n---\n\n# body\n"
    assert _is_thin_note("00-Polaris/USER.md", md) is True


def test_generated_path_is_thin_without_explicit_type():
    """Files under 40-Resources/Generated/ are agent-produced —
    treat as thin even if the frontmatter is missing or unrecognised."""
    md = "# Some generated artifact\nbody\n"
    assert _is_thin_note("40-Resources/Generated/2026-05/research-x.md", md) is True


def test_evergreen_is_not_thin():
    md = "---\ntype: evergreen\n---\n\n# Concept X\nbody\n"
    assert _is_thin_note("10-Knowledge/Evergreen/concept-x.md", md) is False


def test_deep_dive_is_not_thin():
    md = "---\ntype: deep_dive\n---\n\n# Deep dive\n"
    assert _is_thin_note("20-Areas/AI-Research/Topics/2026-05/foo.md", md) is False


def test_no_frontmatter_outside_generated_is_not_thin():
    md = "# A note with no frontmatter\nbody\n"
    assert _is_thin_note("10-Knowledge/Evergreen/concept-x.md", md) is False


def test_thin_path_prefixes_present():
    """Sanity: at least the Generated/ prefix is registered."""
    assert any(
        p.startswith("40-Resources/Generated") for p in _THIN_NOTE_PATH_PREFIXES
    )


def test_thin_types_cover_m19_m20():
    """The three M19/M20 surface types stay in the thin set."""
    assert {"digest", "live-concept", "user-profile"} <= set(_THIN_NOTE_TYPES)


# ── thin renderer output ─────────────────────────────────────────


def test_thin_render_has_body_and_no_scaffold(tmp_path: Path):
    md = (
        "---\n"
        "type: digest\n"
        "schema_version: 1\n"
        "---\n\n"
        "# Digest — 2026-05-12\n\n"
        "## Tensions worth sitting with\n\n"
        "Some real digest content.\n"
    )
    html = _render_note_page(tmp_path, "40-Resources/Generated/digests/x.md", md)
    # Body is rendered.
    assert "Some real digest content" in html
    # The evergreen scaffold heading is NOT.
    assert "Production Chain" not in html
    assert "Inbound Capture" not in html
    assert "Evidence Traceability" not in html
    assert "Where To Go Next" not in html
    # Frontmatter is collapsed into <details>.
    assert "<details class='page-help'>" in html
    assert "<summary>Frontmatter</summary>" in html


def test_thin_render_skips_empty_subsection_h1(tmp_path: Path):
    """Verify the page H1 is the markdown-note header, not the
    digest body's H1.  Ensures the thin shell doesn't suppress the
    body's own headings."""
    md = (
        "---\ntype: live-concept\n---\n\n"
        "# Compounding Context vs Emergent Memory\n\n"
        "## My take\n\nI think X.\n"
    )
    html = _render_note_page(tmp_path, "30-Projects/Tracking/x.md", md)
    # Shell h1.
    assert "<h1>Markdown Note</h1>" in html
    # Body content.
    assert "Compounding Context vs Emergent Memory" in html
    assert "I think X" in html


# ── body-first ordering on full shell ────────────────────────────


def test_full_shell_leads_with_body(tmp_path: Path):
    """For ``type: evergreen`` (or anything outside the thin set),
    the note body must appear ABOVE the lineage / provenance /
    production-chain cards.  Frontmatter sits in a collapsed
    <details> at the bottom."""
    md = (
        "---\ntype: evergreen\n---\n\n"
        "# Concept X\n\nReal evergreen body.\n"
    )
    html = _render_note_page(tmp_path, "10-Knowledge/Evergreen/concept-x.md", md)
    body_idx = html.find("Real evergreen body")
    fm_idx = html.find("<summary>Frontmatter</summary>")
    assert body_idx > 0
    assert fm_idx > body_idx, (
        "Frontmatter must come AFTER the body on the full shell — "
        "user complaint was that metadata buried the content."
    )
