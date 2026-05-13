"""Tests for M21c / BL-085 — chats projection + visibility-aware FTS."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.chat_fileops import (
    ChatAnchor,
    append_turn,
    create_chat_file,
)
from ovp_pipeline.chats_projection import (
    _CHAT_SLUG_PREFIX,
    chat_slug,
    iter_chat_transcripts,
    rebuild_chats_projection,
)


def _make_schema(conn: sqlite3.Connection) -> None:
    """Build the minimal pages_index + page_fts + chats schema the
    projection rebuild touches.  Faster than spinning up the full
    knowledge.db build for a focused test."""
    conn.executescript("""
        CREATE TABLE pages_index (
          slug TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          note_type TEXT NOT NULL,
          path TEXT NOT NULL,
          day_id TEXT NOT NULL,
          frontmatter_json TEXT NOT NULL,
          body TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE page_fts USING fts5(
          slug UNINDEXED,
          title,
          body,
          tokenize='trigram'
        );
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
        );
        """)


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _make_schema(conn)
    return conn


# ── chat_slug ──────────────────────────────────────────────────


def test_chat_slug_prefix_is_stable():
    assert chat_slug("chat-a7b3").startswith(_CHAT_SLUG_PREFIX)
    # Codex P2: slug must survive canonicalize_note_id so search-
    # driven retrieval lookups round-trip cleanly.  Using the raw
    # chat_id avoids the colon-stripping issue with the previous
    # ``chat:<id>`` shape.
    assert chat_slug("chat-a7b3") == "chat-a7b3"


def test_chat_slug_survives_canonicalize_note_id():
    """The chosen slug shape must round-trip through the same
    canonicaliser that ``/note`` and ``/object`` use; otherwise
    search-driven retrieval can't find the indexed chat."""
    from ovp_pipeline.identity import canonicalize_note_id

    slug = chat_slug("chat-a7b3c2d1")
    assert canonicalize_note_id(slug) == slug


# ── iter_chat_transcripts ──────────────────────────────────────


def test_iter_chat_transcripts_skips_non_chat_dirs(tmp_path: Path):
    """Only files under ``40-Resources/Chats/`` are yielded."""
    other = tmp_path / "20-Areas" / "note.md"
    other.parent.mkdir(parents=True)
    other.write_text("not a chat", encoding="utf-8")
    create_chat_file(tmp_path, anchor=ChatAnchor(kind="standalone"))
    paths = list(iter_chat_transcripts(tmp_path))
    assert all("40-Resources/Chats/" in p.as_posix() for p in paths)
    assert all(p.suffix == ".md" for p in paths)
    assert other not in paths


def test_iter_chat_transcripts_empty_vault_returns_nothing(tmp_path: Path):
    assert list(iter_chat_transcripts(tmp_path)) == []


# ── rebuild_chats_projection ───────────────────────────────────


def test_rebuild_indexed_session_writes_three_rows(tmp_path: Path, db_conn: sqlite3.Connection):
    """An indexed session lands in chats AND in pages_index +
    page_fts so /search finds it."""
    path, fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="note", path="20-Areas/x.md", title="X"),
        visibility="indexed",
    )
    # Add a turn so there's content for FTS to index.
    append_turn(path, role="user", body="searchable user question")

    counts = rebuild_chats_projection(db_conn, tmp_path)
    assert counts == {"total": 1, "indexed": 1, "unindexed": 0, "skipped": 0}

    chats_rows = db_conn.execute(
        "SELECT chat_id, status, visibility, anchor_kind FROM chats"
    ).fetchall()
    assert chats_rows == [(fm.chat_id, "active", "indexed", "note")]

    pages_rows = db_conn.execute(
        "SELECT slug, note_type, title FROM pages_index WHERE note_type = 'chat'"
    ).fetchall()
    assert pages_rows == [(fm.chat_id, "chat", "X")]

    fts_match = db_conn.execute(
        "SELECT slug FROM page_fts WHERE page_fts MATCH 'searchable'"
    ).fetchall()
    assert fts_match == [(fm.chat_id,)]


def test_rebuild_unindexed_session_writes_only_chats_row(
    tmp_path: Path, db_conn: sqlite3.Connection
):
    """An unindexed session lands in ``chats`` only — never in
    ``pages_index`` or ``page_fts``."""
    path, fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="standalone"),
        visibility="unindexed",
    )
    append_turn(path, role="user", body="private question text")

    counts = rebuild_chats_projection(db_conn, tmp_path)
    assert counts == {"total": 1, "indexed": 0, "unindexed": 1, "skipped": 0}

    assert db_conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
    assert (
        db_conn.execute("SELECT COUNT(*) FROM pages_index WHERE note_type = 'chat'").fetchone()[0]
        == 0
    )
    assert (
        db_conn.execute("SELECT COUNT(*) FROM page_fts WHERE page_fts MATCH 'private'").fetchone()[
            0
        ]
        == 0
    )


def test_rebuild_is_idempotent(tmp_path: Path, db_conn: sqlite3.Connection):
    """Two rebuilds in a row produce the same row count — no
    duplicate ``chats`` / ``pages_index`` / ``page_fts`` entries."""
    create_chat_file(tmp_path, anchor=ChatAnchor(kind="standalone"))
    rebuild_chats_projection(db_conn, tmp_path)
    rebuild_chats_projection(db_conn, tmp_path)
    assert db_conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1


def test_rebuild_drops_orphan_pages_index_rows(tmp_path: Path, db_conn: sqlite3.Connection):
    """When a session is deleted on disk, its ``chats`` /
    ``pages_index`` / ``page_fts`` rows must disappear too —
    derived state mirrors the markdown corpus."""
    path, fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="note", path="x.md", title="X"),
    )
    append_turn(path, role="user", body="first question")
    rebuild_chats_projection(db_conn, tmp_path)
    assert (
        db_conn.execute("SELECT COUNT(*) FROM pages_index WHERE note_type = 'chat'").fetchone()[0]
        == 1
    )

    # Operator deletes the transcript via Obsidian.
    path.unlink()

    rebuild_chats_projection(db_conn, tmp_path)
    assert db_conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0
    assert (
        db_conn.execute("SELECT COUNT(*) FROM pages_index WHERE note_type = 'chat'").fetchone()[0]
        == 0
    )


def test_rebuild_clears_stale_page_fts_rows(tmp_path: Path, db_conn: sqlite3.Connection):
    """Codex P1 — FTS5 declares ``slug`` UNINDEXED, so a naive
    ``DELETE ... WHERE slug LIKE 'chat-%'`` doesn't match.  After
    deleting a chat on disk, its ``page_fts`` row must still
    disappear on the next rebuild; otherwise repeated rebuilds
    accumulate stale hits."""
    path, fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="note", path="x.md", title="X"),
    )
    append_turn(path, role="user", body="raretoken12345")
    rebuild_chats_projection(db_conn, tmp_path)
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM page_fts WHERE page_fts MATCH 'raretoken12345'"
        ).fetchone()[0]
        == 1
    )

    # Delete the transcript and rebuild — stale FTS rows must go.
    path.unlink()
    rebuild_chats_projection(db_conn, tmp_path)
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM page_fts WHERE page_fts MATCH 'raretoken12345'"
        ).fetchone()[0]
        == 0
    )


def test_rebuild_skips_non_chat_files(tmp_path: Path, db_conn: sqlite3.Connection):
    """A stray .md file under the chats directory that isn't a real
    transcript must skip rather than crash."""
    stray = tmp_path / "40-Resources" / "Chats" / "stray.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("---\ntype: note\n---\n\nstray\n", encoding="utf-8")
    create_chat_file(tmp_path, anchor=ChatAnchor(kind="standalone"))

    counts = rebuild_chats_projection(db_conn, tmp_path)
    assert counts["total"] == 1
    assert counts["skipped"] == 1


def test_rebuild_clears_prior_chat_shadow_only(tmp_path: Path, db_conn: sqlite3.Connection):
    """The rebuild must clear chat-prefix slugs only — regular
    page rows in ``pages_index`` survive."""
    db_conn.execute(
        "INSERT INTO pages_index (slug, title, note_type, path, day_id, frontmatter_json, body) "
        "VALUES ('regular-note', 'Regular', 'note', 'x.md', '', '{}', 'content')"
    )
    db_conn.execute(
        "INSERT INTO page_fts (slug, title, body) " "VALUES ('regular-note', 'Regular', 'content')"
    )
    rebuild_chats_projection(db_conn, tmp_path)
    assert (
        db_conn.execute("SELECT COUNT(*) FROM pages_index WHERE slug = 'regular-note'").fetchone()[
            0
        ]
        == 1
    )
