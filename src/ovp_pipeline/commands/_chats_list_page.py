"""Reader ``/chats`` list view (M21c / BL-088).

Reads from ``knowledge.db.chats`` (BL-085 projection) and renders
a status-grouped list — Pinned / Active / Archived (the latter
collapsed by default).  Unindexed sessions are hidden by design:
the M21 plan promises ``Don't index or reuse`` actually means
"won't appear in the inquiry list".  Operators reach those
sessions via direct file path or ``ovp-ask show --id``.

Layout matches the plan-doc example:

    Pinned (3)
      • 2026-05-12 · Digest review · anchor: digest
      • 2026-05-09 · Memory architecture deep-dive · anchor: crystal
      • 2026-05-07 · M20 design conversation · anchor: standalone

    Active (12)
      • 2026-05-12 · ... (newest first)
      • ...

    Archived (45)
      ⌃ Show archived (collapsed by default)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote_plus

from ovp_pipeline.runtime import VaultLayout


@dataclass(frozen=True)
class ChatRow:
    """One row from ``knowledge.db.chats`` (BL-085 projection).

    Drives the ``/chats`` list view.  Field order matches the
    SQL SELECT for fast tuple-unpacking; downstream rendering
    code reads named attributes.
    """

    chat_id: str
    file_path: str
    status: str  # active | pinned | archived
    anchor_kind: str
    anchor_ref: str
    anchor_title: str
    profile: str
    last_message_at: str
    turn_count: int


# Status display order — Pinned first, then Active, then Archived.
# Mirrors the plan-doc example.
_STATUS_ORDER: tuple[str, ...] = ("pinned", "active", "archived")
_STATUS_LABELS = {
    "pinned": "Pinned",
    "active": "Active",
    "archived": "Archived",
}


def list_indexed_chats(db_path: Path) -> list[ChatRow]:
    """Read all indexed chats from ``knowledge.db.chats``.

    Returns an empty list when the database is missing or the
    table hasn't been built yet — the page renders a friendly
    empty-state instead of 500ing.
    """
    if not db_path.is_file():
        return []
    rows: list[ChatRow] = []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("""
                SELECT chat_id, file_path, status, anchor_kind,
                       anchor_ref, anchor_title, profile,
                       last_message_at, turn_count
                FROM chats
                WHERE visibility = 'indexed'
                ORDER BY last_message_at DESC
                """)
            for row in cursor.fetchall():
                rows.append(
                    ChatRow(
                        chat_id=str(row[0]),
                        file_path=str(row[1]),
                        # Coalesce BEFORE casting (CodeRabbit Minor) —
                        # ``str(None)`` is ``"None"`` (truthy), so the
                        # ``or "active"`` fallback never fired when the
                        # column was NULL.
                        status=str(row[2] or "active"),
                        anchor_kind=str(row[3] or "standalone"),
                        anchor_ref=str(row[4] or ""),
                        anchor_title=str(row[5] or ""),
                        profile=str(row[6] or ""),
                        last_message_at=str(row[7] or ""),
                        turn_count=int(row[8] or 0),
                    )
                )
    except sqlite3.DatabaseError:
        # Schema mismatch / table missing — operator hasn't run
        # ``ovp-knowledge-index`` since BL-085 landed.  Empty page
        # is better than 500.
        return []
    return rows


def _group_by_status(rows: list[ChatRow]) -> dict[str, list[ChatRow]]:
    grouped: dict[str, list[ChatRow]] = {s: [] for s in _STATUS_ORDER}
    for row in rows:
        status = row.status if row.status in grouped else "active"
        grouped[status].append(row)
    return grouped


def _row_title(row: ChatRow) -> str:
    """Pick a distinguishable row title.

    Codex P2: every standalone session previously rendered with the
    same bold "standalone" title because the anchor-label fallback
    chain ended on ``anchor_kind``.  Now we fall back to the file
    name (sans extension) and finally the chat_id when no friendlier
    label exists, so each session is identifiable in the list.
    """
    if row.anchor_title:
        return row.anchor_title
    if row.anchor_ref:
        return row.anchor_ref
    if row.file_path:
        stem = row.file_path.rsplit("/", 1)[-1]
        if stem.endswith(".md"):
            stem = stem[:-3]
        if stem:
            return stem
    return row.chat_id


def _render_chat_row(row: ChatRow) -> str:
    anchor_label = row.anchor_title or row.anchor_ref or row.anchor_kind
    anchor_pill = (
        f'<span class="pill ghost">{escape(row.anchor_kind)}: ' f"{escape(anchor_label)}</span>"
        if row.anchor_kind != "standalone"
        else '<span class="pill ghost">standalone</span>'
    )
    profile_pill = (
        f'<span class="pill">{escape(row.profile.capitalize())}</span>' if row.profile else ""
    )
    timestamp = escape(row.last_message_at[:10] or "—")
    title = _row_title(row)
    # CodeRabbit Major: HTML-escape isn't the right encoding for URL
    # parameters.  ``chat_id`` is validated against ``_CHAT_ID_RE``
    # at write time so it doesn't contain ``&`` / ``?`` today, but
    # treating the value as a URL fragment defends against any
    # future schema where the id permits richer characters.
    chat_id_url = quote_plus(row.chat_id)
    return (
        '<li class="chat-list-item">'
        f'<a class="chat-list-link" href="/chat?id={chat_id_url}">'
        f'<span class="chat-list-date">{timestamp}</span> '
        f"<strong>{escape(title)}</strong>"
        "</a> "
        f"{anchor_pill} {profile_pill} "
        f'<span class="muted small">turns={row.turn_count}</span>'
        "</li>"
    )


def render_chats_list_body(vault_dir: Path | str) -> str:
    """Render the ``/chats`` page body.

    Groups indexed chats by status; archived is collapsed inside
    a ``<details>`` block per the plan.  Empty state surfaces a
    friendly prompt to start an inquiry from any Reader page.
    """
    layout = VaultLayout.from_vault(vault_dir)
    rows = list_indexed_chats(layout.knowledge_db)
    if not rows:
        return (
            '<header class="chats-list-header">'
            "<h1>Inquiry history</h1>"
            "</header>"
            '<p class="muted">No indexed inquiry sessions yet. '
            "Start one with the <em>Ask about this</em> button on any "
            "note, object, or topic page — or open "
            '<a href="/chat">/chat</a> directly for a standalone session.'
            "</p>"
        )

    grouped = _group_by_status(rows)
    sections: list[str] = []
    for status in _STATUS_ORDER:
        items = grouped[status]
        if not items:
            continue
        label = _STATUS_LABELS[status]
        list_items = "".join(_render_chat_row(r) for r in items)
        header = f'<h2>{label} <span class="muted small">' f"({len(items)})</span></h2>"
        if status == "archived":
            sections.append(
                '<details class="card chats-list-section">'
                f"<summary>{header}</summary>"
                f'<ul class="chat-list">{list_items}</ul>'
                "</details>"
            )
        else:
            sections.append(
                '<section class="card chats-list-section">'
                f"{header}"
                f'<ul class="chat-list">{list_items}</ul>'
                "</section>"
            )

    return (
        '<header class="chats-list-header">'
        "<h1>Inquiry history</h1>"
        '<p class="muted">'
        f"{len(rows)} indexed sessions. Unindexed inquiries don't "
        "appear here by design — reach them via "
        "<code>ovp-ask show --id …</code>."
        "</p>"
        "</header>" + "\n".join(sections)
    )


__all__ = [
    "ChatRow",
    "list_indexed_chats",
    "render_chats_list_body",
]
