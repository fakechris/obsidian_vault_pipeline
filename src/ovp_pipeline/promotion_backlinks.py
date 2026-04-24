"""Phase 38.C — write Evergreen-promotion backlinks into source markdown.

Today the source→evergreen edge in the graph is hydrated from
``audit_events`` (event_type=``evergreen_auto_promoted``) — see
graph_cli.py. That works but means the link is invisible to anyone reading
the source markdown file directly in Obsidian, and it can't be picked up by
plain wikilink scanners.

This module writes the same information back into the source MD as a
delimited block of plain wikilinks::

    <!-- ovp-promotions -->
    > 由 OVP Pipeline 自动提取的 Evergreen 概念
    - [[Evergreen-Slug-1]]
    - [[Evergreen-Slug-2]]
    <!-- /ovp-promotions -->

Properties:
- Idempotent: re-applying the same (source, slug) pair is a no-op.
- Block is the single source of truth — replacing the block replaces the
  whole list (no orphaned wikilinks).
- A backfill CLI (``ovp-promote-backfill``) replays existing
  ``evergreen_auto_promoted`` events from ``60-Logs/pipeline.jsonl`` so the
  block lands on every historical source in one pass.
- Once the backfill has covered all live sources, the audit_events
  hydration shim in graph_cli.py becomes redundant. Removing the shim is a
  follow-up PR (kept here for the transition window).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

MARKER_OPEN = "<!-- ovp-promotions -->"
MARKER_CLOSE = "<!-- /ovp-promotions -->"
MARKER_HEADER = "> 由 OVP Pipeline 自动提取的 Evergreen 概念"

_BLOCK_RE = re.compile(
    rf"{re.escape(MARKER_OPEN)}\n(.*?){re.escape(MARKER_CLOSE)}\n?",
    re.DOTALL,
)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\|#]+?)(?:#[^\[\]\|]*)?(?:\|[^\[\]]*)?\]\]")


def list_promotions(source_text: str) -> list[str]:
    """Return the Evergreen slugs declared in the source's promotion block."""
    match = _BLOCK_RE.search(source_text)
    if not match:
        return []
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(match.group(1))]


def _render_block(slugs: Iterable[str]) -> str:
    body_lines = "\n".join(f"- [[{s}]]" for s in slugs)
    return f"{MARKER_OPEN}\n{MARKER_HEADER}\n{body_lines}\n{MARKER_CLOSE}\n"


def upsert_promotions(source_text: str, slugs: Iterable[str]) -> tuple[str, bool]:
    """Add the given slugs to the promotion block (creating the block if absent).

    Idempotent: re-applying the same set is a no-op (returns ``(text, False)``).
    Slugs already present are kept; new ones are appended at the end of the
    list. Order within the block is stable.
    """
    incoming = [s for s in dict.fromkeys(slugs).keys() if s]
    if not incoming:
        return source_text, False

    existing = list_promotions(source_text)
    merged: list[str] = list(existing)
    for slug in incoming:
        if slug not in merged:
            merged.append(slug)
    if merged == existing:
        return source_text, False

    new_block = _render_block(merged)
    if _BLOCK_RE.search(source_text):
        new_text = _BLOCK_RE.sub(new_block, source_text, count=1)
    else:
        # Append the block at end-of-file with one separating blank line.
        sep = "" if source_text.endswith("\n\n") else ("\n" if source_text.endswith("\n") else "\n\n")
        new_text = source_text + sep + new_block
    return new_text, True


def upsert_promotions_in_file(source_path: Path, slugs: Iterable[str]) -> bool:
    """File-level wrapper around :func:`upsert_promotions`. Returns True if the file changed."""
    if not source_path.exists():
        return False
    text = source_path.read_text(encoding="utf-8")
    new_text, changed = upsert_promotions(text, slugs)
    if changed:
        source_path.write_text(new_text, encoding="utf-8")
    return changed
