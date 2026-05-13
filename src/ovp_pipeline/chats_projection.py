"""Chats projection rebuild (M21c / BL-085).

Sweeps ``40-Resources/Chats/**/*.md`` and writes:

* one row per transcript to ``knowledge.db.chats`` (display / metadata)
* for ``visibility = 'indexed'`` sessions only, a shadow row to
  ``pages_index`` + a row to ``page_fts`` so the existing
  ``/search`` (which is ``page_fts JOIN pages_index``) finds
  session bodies.

Unindexed sessions get the ``chats`` row only — never reach search,
never reach the BL-083 retrieval layer.  This is the privacy
boundary the M21 plan locked in.

The projection is fully rebuildable: ``ovp-knowledge-index`` calls
:func:`rebuild_chats_projection` after walking the directory,
clearing prior rows first.  Like every other projection in
OVP, no mutable state lives in the DB — everything derives from
the markdown corpus.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Iterable

from ovp_pipeline.chat_fileops import CHATS_DIR, ChatFrontmatter, parse_chat

logger = logging.getLogger(__name__)


# Synthetic slug shape for ``pages_index`` shadow rows.  ``:`` would
# be stripped by ``identity.canonicalize_note_id`` (the helper that
# normalises every ``/note`` / ``/object`` route input), so a slug
# like ``chat:chat-a7b3`` becomes ``chatchat-a7b3`` on lookup and the
# session disappears from search-driven retrieval.  Codex review P2:
# use the bare ``chat_id`` (which already starts with ``chat-``) so
# the slug round-trips cleanly through canonicalisation.
#
# ``slug LIKE 'chat-%'`` is the indexed-chat corpus.  In practice
# ``chat_id`` is ``chat-<8 hex>`` (BL-082 mint), so the collision
# risk against operator-created notes is negligible; the projection
# rebuild also walks the chats corpus exclusively, so a real note
# happening to be named ``chat-abcd1234.md`` would be left alone.
_CHAT_SLUG_PREFIX = "chat-"


def chat_slug(chat_id: str) -> str:
    """Return the ``pages_index`` shadow-slug for a chat session.

    The chat_id minted by :func:`chat_fileops.new_chat_id` already
    starts with ``chat-``, so we just return it.  Survives
    :func:`identity.canonicalize_note_id` so search-driven
    retrieval round-trips cleanly (codex review P2).
    """
    return chat_id


def iter_chat_transcripts(vault_dir: Path) -> Iterable[Path]:
    """Yield every chat transcript under ``40-Resources/Chats/``.

    Skips ``.lock`` sentinel files (BL-082 fileops uses them for
    per-chat concurrency) and ``.tmp`` partials.
    """
    chats_dir = vault_dir / CHATS_DIR
    if not chats_dir.is_dir():
        return
    for path in chats_dir.rglob("*.md"):
        if path.name.startswith("."):
            continue
        yield path


def _chat_body_text(path: Path) -> str:
    """Return just the prose under the YAML frontmatter.

    Strips manifest comments and the H1 so what feeds FTS is the
    operator's questions + the assistant's prose — which is what
    operators search for.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            text = text[end + 5 :]
    # Strip multi-line HTML comments (manifest snapshots).
    parts: list[str] = []
    in_block = False
    for line in text.splitlines():
        if in_block:
            if "-->" in line:
                in_block = False
            continue
        if "<!--" in line and "-->" not in line:
            in_block = True
            continue
        if "<!--" in line and "-->" in line:
            # Single-line comment — drop in place.
            continue
        parts.append(line)
    return "\n".join(parts).strip()


def rebuild_chats_projection(
    conn: sqlite3.Connection,
    vault_dir: Path,
) -> dict[str, int]:
    """Rebuild every chats-related projection row.

    Clears ``chats``, removes prior ``pages_index`` /  ``page_fts``
    rows whose slug starts with ``chat:``, then walks the
    transcript corpus.

    Caller (``ovp-knowledge-index``) owns the transaction +
    commit.  Returns a counts dict for the run report.
    """
    counts = {
        "total": 0,
        "indexed": 0,
        "unindexed": 0,
        "skipped": 0,
    }
    # Codex P1: FTS5 declares ``slug`` as ``UNINDEXED``, which
    # means ``LIKE`` on that column doesn't actually match — a
    # naive ``DELETE FROM page_fts WHERE slug LIKE 'chat-%'``
    # leaves every prior chat row in place.  Read the explicit
    # chat slugs from the projection table first, then delete
    # each one by exact equality.  Visit ``page_fts`` BEFORE
    # clearing ``chats`` so the slug list is still available.
    prior_chat_slugs = [
        chat_slug(row[0]) for row in conn.execute("SELECT chat_id FROM chats").fetchall()
    ]
    if prior_chat_slugs:
        placeholders = ",".join("?" * len(prior_chat_slugs))
        conn.execute(
            f"DELETE FROM page_fts WHERE slug IN ({placeholders})",
            prior_chat_slugs,
        )
        conn.execute(
            f"DELETE FROM pages_index WHERE slug IN ({placeholders})",
            prior_chat_slugs,
        )
    conn.execute("DELETE FROM chats")

    for path in iter_chat_transcripts(vault_dir):
        fm = parse_chat(path)
        if fm is None:
            counts["skipped"] += 1
            logger.debug(
                "chats_projection: skipping non-chat file %s",
                path,
            )
            continue
        try:
            rel_path = path.relative_to(vault_dir).as_posix()
        except ValueError:
            rel_path = str(path)
        _insert_chat_row(conn, fm, rel_path)
        counts["total"] += 1
        if fm.visibility == "indexed":
            _insert_indexed_shadow(conn, fm, rel_path, _chat_body_text(path))
            counts["indexed"] += 1
        else:
            counts["unindexed"] += 1

    return counts


def _insert_chat_row(
    conn: sqlite3.Connection,
    fm: ChatFrontmatter,
    rel_path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO chats (
            chat_id, pack, file_path, status, visibility,
            anchor_kind, anchor_ref, anchor_title,
            profile, model, temperature,
            started_at, last_message_at, turn_count,
            input_tokens, output_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fm.chat_id,
            "",  # pack column reserved for future BLs
            rel_path,
            fm.status,
            fm.visibility,
            fm.anchor.kind,
            fm.anchor.path,
            fm.anchor.title,
            fm.profile,
            fm.model,
            fm.temperature,
            fm.started_at,
            fm.last_message_at,
            fm.turn_count,
            0,  # input_tokens — lifetime totals filled by BL-085-2
            0,  # output_tokens
        ),
    )


def _insert_indexed_shadow(
    conn: sqlite3.Connection,
    fm: ChatFrontmatter,
    rel_path: str,
    body: str,
) -> None:
    """Insert ``pages_index`` + ``page_fts`` rows so /search finds
    this indexed session.

    The synthetic slug ``chat:<chat_id>`` keeps the indexed-chat
    corpus trivially separable from regular notes.
    """
    slug = chat_slug(fm.chat_id)
    title = (
        fm.anchor.title or f"Inquiry — {fm.anchor.kind}: {fm.anchor.path}".rstrip(": ")
        if fm.anchor.path
        else fm.anchor.title or f"Inquiry {fm.chat_id}"
    )
    day_id = fm.last_message_at[:10] if fm.last_message_at else ""
    conn.execute(
        """
        INSERT OR REPLACE INTO pages_index (
            slug, title, note_type, path, day_id, frontmatter_json, body
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (slug, title, "chat", rel_path, day_id, "{}", body),
    )
    conn.execute(
        "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
        (slug, title, body),
    )


def upsert_chat_projection(
    conn: sqlite3.Connection,
    vault_dir: Path,
    chat_path: Path,
) -> bool:
    """Refresh a single chat's projection rows in place (M22 codex P2).

    The drawer's Save / Absorb path flips a session from
    ``unindexed`` → ``indexed`` on disk; without this helper the
    ``knowledge.db.chats`` row and the ``pages_index`` / ``page_fts``
    shadows still reflect the prior ``unindexed`` state, so the
    saved session would not appear in ``/chats`` or ``/search``
    until the next full ``ovp-knowledge-index`` rebuild.

    Drops every prior row for the chat's slug + chat_id, then
    re-inserts using the current frontmatter.  Returns True on a
    successful write; False when the file isn't a valid chat
    transcript (caller can log).

    Caller owns the transaction + commit — matches
    :func:`rebuild_chats_projection`.
    """
    fm = parse_chat(chat_path)
    if fm is None:
        return False
    try:
        rel_path = chat_path.relative_to(vault_dir).as_posix()
    except ValueError:
        rel_path = str(chat_path)

    slug = chat_slug(fm.chat_id)
    # Clear prior shadow rows first; FTS5 ``slug`` is ``UNINDEXED``
    # so exact-equality DELETE is the only path that matches
    # (same constraint that drives ``rebuild_chats_projection``).
    conn.execute("DELETE FROM page_fts WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM pages_index WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM chats WHERE chat_id = ?", (fm.chat_id,))

    _insert_chat_row(conn, fm, rel_path)
    if fm.visibility == "indexed":
        _insert_indexed_shadow(conn, fm, rel_path, _chat_body_text(chat_path))
    return True


def remove_chat_projection(conn: sqlite3.Connection, chat_id: str) -> None:
    """Remove every projection row for a chat (M22 codex P2 sibling).

    Drawer Discard deletes the markdown; mirror that here so a
    repeated rebuild doesn't re-resurrect the row, and any cached
    /chats render is consistent with disk.  Idempotent.
    """
    if not chat_id:
        return
    slug = chat_slug(chat_id)
    conn.execute("DELETE FROM page_fts WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM pages_index WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))


__all__ = [
    "_CHAT_SLUG_PREFIX",
    "chat_slug",
    "iter_chat_transcripts",
    "rebuild_chats_projection",
    "remove_chat_projection",
    "upsert_chat_projection",
]
