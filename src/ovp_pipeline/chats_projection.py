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


# Synthetic slug prefix for ``pages_index`` shadow rows.  Picked so
# the chat-vs-note distinction is obvious in any join: ``slug LIKE
# 'chat:%'`` is exactly the indexed-chat corpus.
_CHAT_SLUG_PREFIX = "chat:"


def chat_slug(chat_id: str) -> str:
    """Return the ``pages_index`` shadow-slug for a chat session.

    ``chat-a7b3`` → ``chat:chat-a7b3``.  Always prefixed so the
    indexed-chat corpus is trivially separable from regular pages.
    """
    return f"{_CHAT_SLUG_PREFIX}{chat_id}"


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
    conn.execute("DELETE FROM chats")
    conn.execute(
        "DELETE FROM pages_index WHERE slug LIKE ?",
        (f"{_CHAT_SLUG_PREFIX}%",),
    )
    conn.execute(
        "DELETE FROM page_fts WHERE slug LIKE ?",
        (f"{_CHAT_SLUG_PREFIX}%",),
    )

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


__all__ = [
    "_CHAT_SLUG_PREFIX",
    "chat_slug",
    "iter_chat_transcripts",
    "rebuild_chats_projection",
]
