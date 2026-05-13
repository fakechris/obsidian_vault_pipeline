"""Tests for M21b / BL-087 — "Ask about this" entry buttons."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ovp_pipeline.commands._ui_renderers import (
    _anchor_title_for_note,
    _ask_about_this_href,
    _render_ask_about_this_button,
    _render_note_page,
)

# ── _ask_about_this_href ───────────────────────────────────────


def test_ask_about_this_href_carries_kind_ref():
    href = _ask_about_this_href("note", "20-Areas/x.md")
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    assert parsed.path == "/chat"
    assert qs["anchor"] == ["note:20-Areas/x.md"]
    assert "title" not in qs


def test_ask_about_this_href_includes_title_when_given():
    href = _ask_about_this_href("note", "x.md", title="My Note")
    qs = parse_qs(urlparse(href).query)
    assert qs["title"] == ["My Note"]


def test_ask_about_this_href_url_encodes_special_chars():
    """Slashes, spaces, and ampersands in path/title must survive
    the round-trip so the GET handler can parse them back."""
    href = _ask_about_this_href("note", "20-Areas/x y.md", title="A & B")
    qs = parse_qs(urlparse(href).query)
    assert qs["anchor"] == ["note:20-Areas/x y.md"]
    assert qs["title"] == ["A & B"]


# ── _render_ask_about_this_button ──────────────────────────────


def test_render_button_returns_anchor_tag():
    html = _render_ask_about_this_button("note", "x.md", title="A note")
    assert html.startswith('<a class="btn ghost ask-about-this"')
    assert "Ask about this" in html
    assert "/chat?anchor=" in html


def test_render_button_empty_ref_returns_empty_string():
    """No-anchor case (e.g. a note that can't be located) renders
    no button rather than a broken link."""
    html = _render_ask_about_this_button("note", "")
    assert html == ""


def test_render_button_escapes_anchor_title():
    """Operator-supplied title with HTML metacharacters must be
    rendered safely."""
    html = _render_ask_about_this_button("note", "x.md", title="<script>alert(1)</script>")
    assert "<script>" not in html
    # Title doesn't actually land in the visible HTML — it rides
    # through the href as a URL parameter — but the URL must be
    # safely encoded.
    assert "%3Cscript%3E" in html


# ── _anchor_title_for_note ─────────────────────────────────────


def test_anchor_title_prefers_h1():
    md = "# My Note Title\n\nbody\n"
    assert _anchor_title_for_note("20-Areas/x.md", md) == "My Note Title"


def test_anchor_title_falls_back_to_frontmatter_title():
    md = "---\ntitle: From Frontmatter\n---\n\nbody\n"
    assert _anchor_title_for_note("20-Areas/x.md", md) == "From Frontmatter"


def test_anchor_title_falls_back_to_path_basename():
    md = "Some prose with no headings.\n"
    assert _anchor_title_for_note("20-Areas/my-note.md", md) == "my-note"


def test_anchor_title_handles_empty_h1():
    """An empty ``# `` line shouldn't shadow a later frontmatter
    title."""
    md = "---\ntitle: Fallback\n---\n\n# \nbody\n"
    title = _anchor_title_for_note("x.md", md)
    # Frontmatter ``title`` is the established fallback — assert it
    # explicitly so a future regression that flipped to the path
    # basename ("x") would fail (CodeRabbit M).
    assert title == "Fallback"


def test_anchor_title_ignores_body_title_lines(tmp_path: Path):
    """``title: foo`` outside the frontmatter block (e.g. inside
    a code fence example) must NOT shadow the path basename when
    the real frontmatter has no title (CodeRabbit M)."""
    md = "```\ntitle: From Code Fence\n```\n\nbody\n"
    assert _anchor_title_for_note("20-Areas/my-note.md", md) == "my-note"


# ── full-page integration ──────────────────────────────────────


def test_render_note_page_includes_button(tmp_path: Path):
    """The full /note rendering for a regular note carries an
    'Ask about this' button bound to the vault-relative path."""
    body = "# Topic Page\n\nbody\n"
    html = _render_note_page(tmp_path, "20-Areas/topic.md", body)
    assert "Ask about this" in html
    assert "/chat?anchor=note%3A20-Areas%2Ftopic.md" in html
    assert "title=Topic%20Page" in html


def test_render_note_page_thin_shell_also_has_button(tmp_path: Path):
    """Digest / live-concept thin notes also surface a button so
    operators can interrogate a generated artifact."""
    body = (
        "---\ntype: digest\ngenerated_at: 2026-05-12T06:00:00Z\n"
        "pack: research-tech\n---\n\n# Digest 2026-05-12\n\nbody\n"
    )
    html = _render_note_page(
        tmp_path,
        "40-Resources/Generated/digests/2026-05-12.md",
        body,
    )
    assert "Ask about this" in html
    assert "/chat?anchor=note%3A40-Resources%2FGenerated%2Fdigests%2F2026-05-12.md" in html


# ── Codex P2 — object/topic buttons bind paths, not object_ids ──


def test_object_anchor_button_uses_vault_path():
    """Codex P2 — the binder reads ``anchor.path`` as a vault-
    relative file path.  The button on ``/object`` must bind the
    canonical / evergreen path, not the bare ``object_id``, so the
    new chat session can actually load the artifact body."""
    html = _render_ask_about_this_button(
        "object",
        "10-Knowledge/Evergreen/test-object.md",
        title="Test Object",
    )
    assert "/chat?anchor=object%3A10-Knowledge%2FEvergreen%2Ftest-object.md" in html
    # Sanity check: bare object_id form would have failed the
    # binder's path resolution.
    assert "object%3Aobj-" not in html


def test_object_button_skipped_when_no_canonical_path():
    """When neither evergreen_path nor canonical_path exist, we
    cannot give the binder a valid anchor.  The button helper
    short-circuits to an empty string rather than render a link
    that would 404 the inquiry session."""
    assert _render_ask_about_this_button("object", "") == ""
