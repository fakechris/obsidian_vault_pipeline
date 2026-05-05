"""Shared data-loading helpers for the synthesis modules.

Both community and contradiction crystals need the same primitives:
read evergreen bodies, look up objects by ID, strip frontmatter
before sending content to the LLM, and refuse to follow paths that
escape the vault root.  Hosting them here keeps
``contradiction_crystal`` from reaching into ``community_crystal``'s
private namespace (PR-133 review feedback) and gives both modules a
single source of truth for the safety guards.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


# Output directory (relative to vault root) for crystal markdowns.
CRYSTAL_DIR_REL: Path = Path("40-Resources") / "Crystals"

# SQLite caps parameterised IN clauses at ~999 items by default.
# Subset loaders chunk below this floor so a vault with hundreds
# of communities doesn't trip the limit.
_OBJECTS_LOOKUP_CHUNK = 500


def crystal_safe_id(crystal_kind: str, crystal_id: str) -> str:
    """Translate a ``crystal_id`` into the on-disk filename stem.

    Pre-fix this prefix-stripping logic was duplicated across
    ``curated_atlas._crystal_safe_id``, ``community_crystal._safe_id``,
    ``crystal_fts._community_safe_id`` / ``_contradiction_safe_id``,
    and ``ui.view_models``'s inline ``_safe_id`` helper.  Single source
    of truth here so a future rename of the ID format only edits one
    file.

    Conventions:
      * ``community`` crystals: ``cluster::<digest>`` →
        ``<digest>`` (file lives at ``<safe-id>.md``).
      * ``contradiction`` crystals: ``contradiction::<digest>`` →
        ``contradiction-<digest>`` (the returned safe-id already
        carries the ``contradiction-`` prefix, so the on-disk
        filename is ``<safe-id>.md`` — single prefix, not double).
      * Anything else passes through unchanged — defensive default
        matches the historical behaviour of the duplicated helpers.
    """
    if crystal_kind == "community" and crystal_id.startswith("cluster::"):
        return crystal_id[len("cluster::"):]
    if crystal_kind == "contradiction" and crystal_id.startswith("contradiction::"):
        return f"contradiction-{crystal_id[len('contradiction::'):]}"
    return crystal_id


def related_notes_section(slugs: tuple[str, ...] | list[str]) -> str:
    """Render the ``## 相关笔记`` block both crystal modules append
    to their bodies.

    Pre-fix this was duplicated between
    ``community_crystal._related_notes_section`` and
    ``contradiction_crystal._related_notes_section`` — identical
    bodies, twice the surface area to keep in sync if the format
    ever changes.

    Source-note backlinks are deterministic by construction here
    (the LLM used to drop them ~30% of the time when asked to cite
    in prose), so the Obsidian backlink graph stays sane regardless
    of prompt drift.
    """
    lines = ["## 相关笔记", ""]
    for slug in slugs:
        lines.append(f"- [[{slug}]]")
    return "\n".join(lines)


def strip_frontmatter(text: str) -> str:
    """Remove the YAML frontmatter block from an evergreen markdown.

    Frontmatter is bounded by ``---`` on its own line at the very
    start of the file and a closing ``---`` on its own line.  When
    absent or malformed, return the text unchanged — better to
    pass through than drop content.

    Stripping saves ~10 lines × top_k notes of LLM tokens per
    crystal call on a vault where every evergreen carries the
    standard frontmatter block.
    """
    if not text.startswith("---"):
        return text
    closer = text.find("\n---", 3)
    if closer == -1:
        return text
    return text[closer + 4:].lstrip("\n")


def load_objects_subset(
    conn: sqlite3.Connection,
    pack: str,
    object_ids: set[str],
) -> dict[str, tuple[str, str]]:
    """Targeted lookup — only the object_ids the caller will consume.

    Avoids loading all 7000 objects into memory just to read the
    few hundred inside a top-K member slice.  Chunked at
    ``_OBJECTS_LOOKUP_CHUNK`` to stay below SQLite's parameter cap.
    """
    if not object_ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    ids_list = sorted(object_ids)
    for start in range(0, len(ids_list), _OBJECTS_LOOKUP_CHUNK):
        chunk = ids_list[start:start + _OBJECTS_LOOKUP_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT object_id, title, canonical_path FROM objects "
            f"WHERE pack = ? AND object_id IN ({placeholders})",
            (pack, *chunk),
        )
        for object_id, title, canonical_path in cur:
            out[object_id] = (title, canonical_path)
    return out


def load_evergreen_bodies(
    vault_dir: Path,
    *,
    member_object_ids: list[str],
    objects_by_id: dict[str, tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Read evergreen bodies for use in crystal synthesis prompts.

    Three safety properties on every read:

    * **Vault containment** — ``canonical_path`` comes from
      ``knowledge.db``, which is derived state.  A corrupted /
      stale row could carry a path that resolves outside the
      vault root.  The LLM only sees in-vault content, so we
      refuse to follow such paths.
    * **Frontmatter stripped** — see ``strip_frontmatter``.
    * **Read failures don't sink the batch** — ``OSError`` /
      ``UnicodeDecodeError`` log a structured warning and skip
      the file.
    """
    vault_root = vault_dir.resolve()
    out: list[tuple[str, str, str]] = []
    for object_id in member_object_ids:
        title_path = objects_by_id.get(object_id)
        if title_path is None:
            logger.warning(
                "object_id %r not found in objects table; skipping member",
                object_id,
            )
            continue
        title, canonical_path = title_path
        full_path = vault_dir / canonical_path
        try:
            full_path.resolve().relative_to(vault_root)
        except ValueError:
            logger.warning(
                "evergreen path %r escapes vault root %s; skipping",
                canonical_path, vault_root,
            )
            continue
        try:
            body = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "failed to read evergreen %s for crystal synthesis: %s",
                full_path, exc,
            )
            continue
        out.append((object_id, title, strip_frontmatter(body)))
    return out
