"""Tests for M21b / BL-086 — Reader ``/chat`` page renderer."""

from __future__ import annotations

from pathlib import Path

from ovp_pipeline.chat_fileops import ChatAnchor, create_chat_file
from ovp_pipeline.commands._chat_page import (
    _profile_options,
    render_chat_page_body,
)

# ── renderer — standalone (new session) ────────────────────────


def test_render_new_standalone_chat_has_composer(tmp_path: Path):
    html = render_chat_page_body(tmp_path)
    assert 'method="POST" action="/chat/message"' in html
    assert "Standalone (no anchor)" in html
    assert "<textarea" in html
    # Visibility toggle copy from the plan must appear verbatim.
    assert "Don't index or reuse this inquiry." in html
    assert "selected LLM provider" in html


def test_render_new_anchored_chat_carries_anchor(tmp_path: Path):
    html = render_chat_page_body(
        tmp_path,
        anchor_kind="note",
        anchor_ref="20-Areas/topic.md",
        anchor_title="A topic",
    )
    assert 'value="note:20-Areas/topic.md"' in html
    assert "A topic" in html


def test_render_new_chat_does_not_include_manifest_card(tmp_path: Path):
    """The manifest card only shows on existing sessions (it's the
    audit summary of the prior turn's context, not a directive for
    the next one)."""
    html = render_chat_page_body(tmp_path)
    assert "Context anchored to" not in html


# ── renderer — existing session ────────────────────────────────


def test_render_existing_chat_shows_manifest_and_turns(tmp_path: Path):
    path, _ = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="note", path="20-Areas/x.md", title="X"),
        topic="memory architecture",
    )
    # Append fake turns directly to the markdown so the renderer
    # has something to display.  Tests of run_turn cover the
    # round-trip.
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## User · 2026-05-12T11:00:01Z\n\nHello vault.\n\n"
        "## Assistant · 2026-05-12T11:00:02Z · turn-2\n\n"
        "<!-- context-manifest\n  token_estimate: 10\n-->\n\n"
        "Hi there.\n\n",
        encoding="utf-8",
    )
    html = render_chat_page_body(tmp_path, chat_path=path)
    assert "Context anchored to" in html
    assert "Hello vault." in html
    assert "Hi there." in html
    # The literal manifest HTML comment block must not leak into
    # the rendered transcript body (the explanatory <code> snippet
    # in the manifest card is fine — it's escaped pedagogy, not
    # raw audit data).
    assert "token_estimate: 10" not in html
    assert "<!-- context-manifest" not in html
    # User vs assistant turn classes are distinct so CSS can style.
    assert "chat-turn-user" in html
    assert "chat-turn-assistant" in html


def test_render_missing_chat_renders_error_block(tmp_path: Path):
    html = render_chat_page_body(tmp_path, chat_id="chat-nope")
    assert "not found" in html.lower()


# ── profile dropdown ───────────────────────────────────────────


def test_profile_options_orders_canonical_first(tmp_path: Path):
    cfg = tmp_path / ".ovp" / "llm_profiles.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        """
profiles:
  my-custom:
    provider: anthropic
    model: claude-x
  balanced:
    provider: anthropic
    model: claude-sonnet-4-6
  deep:
    provider: anthropic
    model: claude-opus-4-7
  fast:
    provider: anthropic
    model: MiniMax-M2.7-highspeed
""",
        encoding="utf-8",
    )
    options = _profile_options(tmp_path, current="balanced")
    # Canonical three first, then custom.
    assert options.index('value="fast"') < options.index('value="balanced"')
    assert options.index('value="balanced"') < options.index('value="deep"')
    assert options.index('value="deep"') < options.index('value="my-custom"')
    # Selection persists.
    assert 'value="balanced" selected' in options


def test_profile_options_with_only_fallback_book(tmp_path: Path):
    """A vault without ``.ovp/llm_profiles.yaml`` falls back to a
    single 'balanced' profile from env vars.  The dropdown still
    renders something."""
    options = _profile_options(tmp_path, current="balanced")
    assert 'value="balanced"' in options


# ── visibility toggle ──────────────────────────────────────────


def test_visibility_toggle_defaults_to_indexed_for_new_chats(tmp_path: Path):
    html = render_chat_page_body(tmp_path)
    # Indexed pre-selected; unindexed unselected.
    assert 'value="indexed" checked' in html
    assert 'value="unindexed" checked' not in html


def test_visibility_toggle_preserves_unindexed_session(tmp_path: Path):
    path, _ = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="standalone"),
        visibility="unindexed",
    )
    html = render_chat_page_body(tmp_path, chat_path=path)
    assert 'value="unindexed" checked' in html
    assert 'value="indexed" checked' not in html
