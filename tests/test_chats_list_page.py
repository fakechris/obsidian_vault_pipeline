"""Tests for M21c / BL-088 — Reader ``/chats`` list view."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ovp_pipeline.commands._chats_list_page import (
    ChatRow,
    list_indexed_chats,
    render_chats_list_body,
)


def _make_db_with_chats(db_path: Path, rows: list[dict]) -> None:
    """Bootstrap a knowledge.db with just the ``chats`` table."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE chats (
              chat_id TEXT PRIMARY KEY,
              pack TEXT NOT NULL DEFAULT '',
              file_path TEXT NOT NULL,
              status TEXT NOT NULL,
              visibility TEXT NOT NULL,
              anchor_kind TEXT NOT NULL,
              anchor_ref TEXT NOT NULL DEFAULT '',
              anchor_title TEXT NOT NULL DEFAULT '',
              profile TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL DEFAULT '',
              temperature REAL NOT NULL DEFAULT 0.7,
              started_at TEXT NOT NULL DEFAULT '',
              last_message_at TEXT NOT NULL DEFAULT '',
              turn_count INTEGER NOT NULL DEFAULT 0,
              input_tokens INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0
            )
            """)
        conn.executemany(
            """
            INSERT INTO chats (
              chat_id, file_path, status, visibility, anchor_kind,
              anchor_ref, anchor_title, profile, last_message_at,
              turn_count
            ) VALUES (
              :chat_id, :file_path, :status, :visibility, :anchor_kind,
              :anchor_ref, :anchor_title, :profile, :last_message_at,
              :turn_count
            )
            """,
            rows,
        )
        conn.commit()


def _make_vault_with_db(tmp_path: Path, rows: list[dict]) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault / "20-Areas").mkdir()
    (vault / "50-Inbox").mkdir()
    (vault / "60-Logs").mkdir()
    # ``VaultLayout.knowledge_db`` resolves to ``60-Logs/knowledge.db``.
    db_path = vault / "60-Logs" / "knowledge.db"
    if rows is not None:
        _make_db_with_chats(db_path, rows)
    return vault


# ── list_indexed_chats ────────────────────────────────────────


def test_list_indexed_chats_filters_to_indexed_only(tmp_path: Path):
    """Unindexed sessions are hidden from the projection read so
    /chats can't accidentally surface them."""
    rows = [
        {
            "chat_id": "chat-a",
            "file_path": "40-Resources/Chats/2026-05/a.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "note",
            "anchor_ref": "x.md",
            "anchor_title": "Note A",
            "profile": "balanced",
            "last_message_at": "2026-05-12T11:00:00Z",
            "turn_count": 4,
        },
        {
            "chat_id": "chat-private",
            "file_path": "40-Resources/Chats/2026-05/p.md",
            "status": "active",
            "visibility": "unindexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "deep",
            "last_message_at": "2026-05-12T12:00:00Z",
            "turn_count": 2,
        },
    ]
    vault = _make_vault_with_db(tmp_path, rows)
    indexed = list_indexed_chats(vault / "60-Logs" / "knowledge.db")
    chat_ids = [r.chat_id for r in indexed]
    assert "chat-a" in chat_ids
    assert "chat-private" not in chat_ids


def test_list_indexed_chats_ordered_newest_first(tmp_path: Path):
    rows = [
        {
            "chat_id": "chat-old",
            "file_path": "x.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "balanced",
            "last_message_at": "2026-05-01T11:00:00Z",
            "turn_count": 1,
        },
        {
            "chat_id": "chat-new",
            "file_path": "y.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "balanced",
            "last_message_at": "2026-05-12T11:00:00Z",
            "turn_count": 1,
        },
    ]
    vault = _make_vault_with_db(tmp_path, rows)
    indexed = list_indexed_chats(vault / "60-Logs" / "knowledge.db")
    assert [r.chat_id for r in indexed] == ["chat-new", "chat-old"]


def test_list_indexed_chats_missing_db_returns_empty(tmp_path: Path):
    """No knowledge.db → no crash, just empty list."""
    assert list_indexed_chats(tmp_path / "nope.db") == []


def test_list_indexed_chats_schema_mismatch_returns_empty(tmp_path: Path):
    """Pre-BL-085 vault doesn't have the chats table — degrade
    gracefully so /chats shows the empty-state instead of 500ing."""
    db = tmp_path / "knowledge.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE other_table (x INTEGER)")
    assert list_indexed_chats(db) == []


# ── render_chats_list_body ────────────────────────────────────


def test_render_chats_list_body_empty_state(tmp_path: Path):
    vault = _make_vault_with_db(tmp_path, [])
    html = render_chats_list_body(vault)
    assert "No indexed inquiry sessions yet" in html
    # Mentions the entry points so operators know what to do next.
    assert "Ask about this" in html
    assert "/chat" in html


def test_render_chats_list_body_groups_by_status(tmp_path: Path):
    rows = [
        {
            "chat_id": "chat-1",
            "file_path": "x.md",
            "status": "pinned",
            "visibility": "indexed",
            "anchor_kind": "note",
            "anchor_ref": "n.md",
            "anchor_title": "Pinned Note",
            "profile": "deep",
            "last_message_at": "2026-05-12T11:00:00Z",
            "turn_count": 6,
        },
        {
            "chat_id": "chat-2",
            "file_path": "y.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "balanced",
            "last_message_at": "2026-05-11T11:00:00Z",
            "turn_count": 3,
        },
        {
            "chat_id": "chat-3",
            "file_path": "z.md",
            "status": "archived",
            "visibility": "indexed",
            "anchor_kind": "object",
            "anchor_ref": "obj.md",
            "anchor_title": "Old Object",
            "profile": "fast",
            "last_message_at": "2026-04-01T11:00:00Z",
            "turn_count": 12,
        },
    ]
    vault = _make_vault_with_db(tmp_path, rows)
    html = render_chats_list_body(vault)
    # All three status sections present.
    assert "Pinned" in html
    assert "Active" in html
    assert "Archived" in html
    # Archived sits inside a collapsed <details>.
    assert "<details" in html
    # Each row links to the /chat page by id.
    assert "/chat?id=chat-1" in html
    assert "/chat?id=chat-3" in html


def test_render_chats_list_body_includes_anchor_and_profile_pills(
    tmp_path: Path,
):
    rows = [
        {
            "chat_id": "chat-a",
            "file_path": "x.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "note",
            "anchor_ref": "20-Areas/n.md",
            "anchor_title": "Friendly Title",
            "profile": "deep",
            "last_message_at": "2026-05-12T11:00:00Z",
            "turn_count": 4,
        }
    ]
    vault = _make_vault_with_db(tmp_path, rows)
    html = render_chats_list_body(vault)
    # Anchor pill shows the friendly title, not the raw path.
    assert "Friendly Title" in html
    # Profile pill is capitalised per the plan's UI vocabulary.
    assert "Deep" in html
    # Turn count surfaces in the row.
    assert "turns=4" in html


def test_render_chats_list_body_standalone_anchor_pill(tmp_path: Path):
    """A standalone session shows a ``standalone`` pill rather
    than an empty anchor pill."""
    rows = [
        {
            "chat_id": "chat-s",
            "file_path": "x.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "balanced",
            "last_message_at": "2026-05-12T11:00:00Z",
            "turn_count": 2,
        }
    ]
    vault = _make_vault_with_db(tmp_path, rows)
    html = render_chats_list_body(vault)
    assert "standalone" in html


# ── ChatRow dataclass ─────────────────────────────────────────


# ── Codex P2 — Reader nav exposes Chats ──────────────────────


def test_reader_nav_includes_chats_entry():
    """The Reader shell nav must surface a Chats link so operators
    can find the history list without typing the URL by hand."""
    from ovp_pipeline.commands._ui_renderers import _reader_nav_items

    items = _reader_nav_items()
    labels = [label for label, _path in items]
    paths = [path for _label, path in items]
    assert "Chats" in labels
    assert "/chats" in paths


# ── Codex P2 — standalone sessions get unique titles ─────────


def test_standalone_sessions_get_distinguishable_titles(tmp_path: Path):
    """Without an anchor, the row title must fall back to the file
    stem (or chat_id) rather than the literal word "standalone".
    Otherwise every /chat session renders with the same bold
    label and operators can't tell sessions apart."""
    rows = [
        {
            "chat_id": "chat-aaa1",
            "file_path": "40-Resources/Chats/2026-05/memory-question-aaa1.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "balanced",
            "last_message_at": "2026-05-12T11:00:00Z",
            "turn_count": 2,
        },
        {
            "chat_id": "chat-bbb2",
            "file_path": "40-Resources/Chats/2026-05/agents-overview-bbb2.md",
            "status": "active",
            "visibility": "indexed",
            "anchor_kind": "standalone",
            "anchor_ref": "",
            "anchor_title": "",
            "profile": "balanced",
            "last_message_at": "2026-05-11T11:00:00Z",
            "turn_count": 3,
        },
    ]
    vault = _make_vault_with_db(tmp_path, rows)
    html = render_chats_list_body(vault)
    # The two sessions get distinct bold titles drawn from the
    # filename stem — not just the literal anchor kind.
    assert "memory-question-aaa1" in html
    assert "agents-overview-bbb2" in html


def test_chat_row_is_frozen():
    """The dataclass must be immutable so list-view helpers can
    treat returned rows as values."""
    row = ChatRow(
        chat_id="x",
        file_path="x.md",
        status="active",
        anchor_kind="standalone",
        anchor_ref="",
        anchor_title="",
        profile="balanced",
        last_message_at="",
        turn_count=0,
    )
    try:
        row.status = "pinned"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised
