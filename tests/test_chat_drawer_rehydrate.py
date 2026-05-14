"""Tests for the drawer-transcript-rehydrate endpoint (post-M22 fix).

The drawer's localStorage caches ``chat_id`` per anchor so a
page reload reopens the same session.  Before this endpoint, the
transcript itself was lost on reopen — operators saw an empty
drawer and thought their history had vanished.  This module pins
the rehydrate contract so a future refactor doesn't quietly
re-introduce the data-loss perception.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.chat_fileops import (
    ChatAnchor,
    append_turn,
    create_chat_file,
)
from ovp_pipeline.commands.ui_server import _render_drawer_turns_from_transcript


def test_renders_user_then_assistant_sections(tmp_path: Path):
    """Two turns → two ``<section>`` blocks in document order."""
    path, _fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor("note", "10-Knowledge/Evergreen/x.md", "X"),
    )
    append_turn(path, role="user", body="What's the gist of X?")
    append_turn(
        path,
        role="assistant",
        body="X argues that emergent memory beats designed.",
        turn_number=1,
        manifest_lines=["note: synthetic"],
    )
    text = path.read_text(encoding="utf-8")

    html = _render_drawer_turns_from_transcript(text)
    assert html.count("<section") == 2
    # User comes before assistant.
    assert html.index("chat-drawer-turn-user") < html.index(
        "chat-drawer-turn-assistant"
    )
    assert "What&#x27;s the gist" in html or "What's the gist" in html
    assert "emergent memory beats designed" in html


def test_strips_manifest_comment_from_assistant_body(tmp_path: Path):
    """The ``<!-- context-manifest -->`` audit snapshot must NOT
    leak into the drawer body — it's metadata, not prose."""
    path, _fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor("note", "x.md", "X"),
    )
    append_turn(path, role="user", body="ask")
    append_turn(
        path,
        role="assistant",
        body="real answer text",
        turn_number=1,
        manifest_lines=["anchor: note: x.md", "retrieval_hits: 0"],
    )
    text = path.read_text(encoding="utf-8")
    html = _render_drawer_turns_from_transcript(text)
    assert "context-manifest" not in html
    assert "anchor: note" not in html
    assert "real answer text" in html


def test_renders_assistant_markdown_as_html(tmp_path: Path):
    """Assistant turn body that contains markdown should produce
    real HTML (lists, bold) in the drawer."""
    path, _fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor("standalone"),
    )
    append_turn(path, role="user", body="give me a list")
    body_md = "- one\n- two\n- **three**\n"
    append_turn(
        path,
        role="assistant",
        body=body_md,
        turn_number=1,
        manifest_lines=["note: synthetic"],
    )
    text = path.read_text(encoding="utf-8")
    html = _render_drawer_turns_from_transcript(text)
    # markdown_it produces a <ul> + <li> for the bullets
    assert "<ul>" in html
    assert "<li>" in html
    assert "<strong>three</strong>" in html


def test_empty_transcript_returns_empty_string(tmp_path: Path):
    path, _fm = create_chat_file(tmp_path, anchor=ChatAnchor("standalone"))
    text = path.read_text(encoding="utf-8")
    html = _render_drawer_turns_from_transcript(text)
    assert html == ""


def test_renderer_tolerates_text_without_frontmatter():
    """A pasted assistant transcript without YAML frontmatter
    should still parse if it contains the ``## User`` / ``## Assistant``
    headers."""
    text = (
        "## User · 2026-05-13T00:00:00Z\n\nask\n\n"
        "## Assistant · 2026-05-13T00:00:01Z · turn-1\n\nbody\n"
    )
    html = _render_drawer_turns_from_transcript(text)
    assert "chat-drawer-turn-user" in html
    assert "chat-drawer-turn-assistant" in html
