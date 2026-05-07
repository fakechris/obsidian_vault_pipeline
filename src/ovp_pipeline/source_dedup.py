"""URL-based source-dedup index.

Scans ``50-Inbox/03-Processed/**/*.md`` for frontmatter ``source:``
URLs and returns a ``url → path`` map.  Intake processors (article,
clippings) consult this before writing a new raw to skip duplicate
URLs that are already on disk.

Why this exists
---------------
The pre-fix pipeline had no URL-level dedup.  ``unique_child`` only
defends against filename collisions; ``source_fingerprint`` is a
scoring signal, not a gate.  Real-world fallout: 8 confirmed
duplicate URLs in 03-Processed at 2026-05-06 census time —
the same Twitter thread re-clipped from Reader 2-3 times.

Each duplicate triggered a fresh absorb run, producing redundant
evergreens with identical bodies but different ``absorbed_at``
timestamps and slightly different concept slugs.  This module
shuts the door at intake, before any LLM cost is spent.

Re-adding a URL the user explicitly wants:
  * The dedup check looks ONLY at the active ``03-Processed`` tree,
    NOT ``70-Archive``.  Archived raws are out of scope by design —
    the user's "I cleared this and want a fresh take" workflow needs
    to keep working.

Out of scope:
  * Body-content fingerprinting (two URLs that resolve to the same
    article).  The ~1% overlap doesn't justify the SHA cost yet;
    URL-level dedup catches the noisy case.
  * Cross-domain canonicalization (``mp.weixin.qq.com/s/X`` vs the
    same article re-shared on Substack).  Out of scope — the URLs
    differ, treat as distinct sources.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Frontmatter keys we recognize as the canonical source URL.  Order
# matters: matches the precedence in
# ``backfill_provenance.py:_canonical_source_url``.
_SOURCE_URL_KEYS = ("source_url", "source", "url", "github", "twitter", "arxiv")


def _extract_source_url(text: str) -> str | None:
    """Pull a ``source: <url>`` value from the frontmatter.  Returns
    ``None`` for files with no http/https URL in any recognized key.
    """
    head = text[:3000]
    if not head.startswith("---"):
        return None
    fm_end = head.find("\n---", 3)
    if fm_end == -1:
        return None
    fm = head[3:fm_end]
    for key in _SOURCE_URL_KEYS:
        m = re.search(rf"^{key}:\s*(.+)$", fm, re.MULTILINE)
        if not m:
            continue
        v = m.group(1).strip().strip('"').strip("'")
        # Strip trailing punctuation that some YAML emitters add.
        v = v.rstrip(",").rstrip(")").rstrip("]")
        if v.startswith(("http://", "https://")):
            return v
    return None


def build_url_index(vault_dir: Path | str) -> dict[str, Path]:
    """One-shot scan of ``50-Inbox/03-Processed/**/*.md`` returning
    ``{source_url: raw_path}``.

    The map is **first-write-wins** when two raws have the same URL —
    the alphabetically first by relative path.  Callers don't rely
    on which one wins because the dedup check is "any of them
    exists" not "the canonical one is X".
    """
    vault = Path(vault_dir).resolve()
    processed = vault / "50-Inbox" / "03-Processed"
    if not processed.is_dir():
        return {}

    out: dict[str, Path] = {}
    for f in sorted(processed.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        url = _extract_source_url(text)
        if url and url not in out:
            out[url] = f
    return out


def find_existing_by_url(
    vault_dir: Path | str,
    url: str,
    *,
    index: dict[str, Path] | None = None,
) -> Path | None:
    """Return the ``03-Processed`` raw file whose ``source:``
    matches ``url``, or ``None``.

    Pass a pre-built ``index`` (from :func:`build_url_index`) for
    batch operations.  A fresh ``index`` is built per call when
    ``index`` is ``None`` — fine for one-off intake checks but
    wasteful in a tight loop.
    """
    if index is None:
        index = build_url_index(vault_dir)
    return index.get(url)


def find_duplicate_groups(
    vault_dir: Path | str,
) -> dict[str, list[Path]]:
    """Return groups of paths that share a ``source:`` URL — i.e.
    the duplicates already on disk.  Used by the cleanup CLI to
    flag and archive everything but the canonical copy.

    Excludes singletons; only groups with len ≥ 2 are returned.
    """
    vault = Path(vault_dir).resolve()
    processed = vault / "50-Inbox" / "03-Processed"
    groups: dict[str, list[Path]] = {}
    if not processed.is_dir():
        return groups
    for f in sorted(processed.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        url = _extract_source_url(text)
        if url:
            groups.setdefault(url, []).append(f)
    return {u: paths for u, paths in groups.items() if len(paths) > 1}
