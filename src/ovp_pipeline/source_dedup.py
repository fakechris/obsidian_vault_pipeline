"""URL-based source-dedup index — global across the active staging chain.

Scans every ``.md`` file living in the **active staging set** —
``Clippings/`` (incl. subdirs), ``50-Inbox/02-Pinboard/``,
``50-Inbox/01-Raw/``, ``50-Inbox/02-Processing/`` and
``50-Inbox/03-Processed/`` — and returns a ``url → path`` map.
Intake processors consult this before introducing a new raw, so
the same URL can never be clipped twice from different surfaces
(Pinboard + Reader-Clip, two re-clips, etc.) without an explicit
re-process intent.

Why this exists
---------------
The pre-fix pipeline had no URL-level dedup.  ``unique_child`` only
defends against filename collisions; ``source_fingerprint`` is a
scoring signal, not a gate.  Real-world fallout: 8 confirmed
duplicate URLs in 03-Processed at 2026-05-06 census, plus 12 more
created on 2026-05-07 when the v0.12.0 incremental run pulled the
same articles in via Clippings while older copies still sat in
03-Processed under different basenames.  Each dup triggered a
fresh absorb run, producing redundant evergreens with identical
bodies but different ``absorbed_at`` timestamps.

Why "active staging" not just 03-Processed
------------------------------------------
A URL on its way through the pipeline lives in different dirs at
different times: ``Clippings/X.md`` → ``01-Raw/X.md`` →
``02-Processing/X.md`` → ``03-Processed/<YYYY-MM>/X.md``.  Pinboard
items live in ``02-Pinboard/`` until the ``pinboard_process`` step
routes them to a per-type processor.  A check that only sees
03-Processed misses URLs that are still in flight (the BL-058
v0.12.0 ``incremental`` run is the canonical bug — Clippings sat
unchecked while the same URLs already lived in 03-Processed under
prior-run filenames).

Re-adding a URL the user explicitly wants:
  * The dedup check **excludes** ``70-Archive/`` by design — the
    user's "I cleared this and want a fresh take" workflow needs
    to keep working.  An archived URL doesn't claim its slot.

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

# Active staging dirs — anywhere a URL counts as "claimed" and a
# second arrival of the same URL should be skipped.  Order is
# **downstream-first**: when the same URL appears in multiple
# stages (which can happen during partially-completed runs), the
# more-finished copy claims the index slot.  This makes the
# self-match check in :meth:`AutoArticleProcessor._check_url_dedup`
# resolve correctly — a 01-Raw file looking itself up sees the
# 03-Processed sibling first and gets flagged as a dup.
ACTIVE_INTAKE_DIRS: tuple[str, ...] = (
    "50-Inbox/03-Processed",
    "50-Inbox/02-Processing",
    "50-Inbox/01-Raw",
    "50-Inbox/02-Pinboard",
    "Clippings",
)

# Subset used by the cleanup CLI (``ovp-dedup-cleanup``) — that CLI
# specifically targets dups that have already reached final intake
# and should NOT touch in-flight stagings.
PROCESSED_INTAKE_DIR = "50-Inbox/03-Processed"

# Frontmatter keys we recognize as the canonical source URL.  Order
# matters: matches the precedence in
# ``backfill_provenance.py:_canonical_source_url``.
_SOURCE_URL_KEYS = ("source_url", "source", "url", "github", "twitter", "arxiv")

# Frontmatter rarely exceeds ~1 KB; 3 KB is a safe upper bound that
# still lets us reject bodies without slurping multi-MB raws.
FRONTMATTER_SCAN_LIMIT = 3000


def read_file_head(path: Path, limit: int = FRONTMATTER_SCAN_LIMIT) -> str:
    """Return the first ``limit`` chars of ``path`` (or fewer if the
    file is shorter).  Used by the dedup index and intake URL gate to
    avoid loading multi-MB clipped articles when only the frontmatter
    needs inspection.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return fh.read(limit)


def extract_source_url(text: str) -> str | None:
    """Pull a ``source: <url>`` value from the frontmatter.  Returns
    ``None`` for files with no http/https URL in any recognized key.
    """
    head = text[:FRONTMATTER_SCAN_LIMIT]
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


# Backwards-compatible private alias kept so a partially-updated tree
# doesn't ImportError; new callers should use ``extract_source_url``.
_extract_source_url = extract_source_url


def _scan_dirs(
    vault_dir: Path | str,
    relative_dirs: Iterable[str],
) -> dict[str, Path]:
    """Walk every ``.md`` in each relative dir and return
    ``{source_url: first_seen_path}``.

    First-write-wins on URL collision; iteration order is the order
    of ``relative_dirs`` then ``rglob`` lexicographic.  Callers
    don't depend on which path wins — only on "URL is claimed
    somewhere" semantics.
    """
    vault = Path(vault_dir).resolve()
    out: dict[str, Path] = {}
    for rel in relative_dirs:
        d = vault / rel
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            try:
                text = read_file_head(f)
            except OSError:
                continue
            url = extract_source_url(text)
            if url and url not in out:
                out[url] = f
    return out


def build_active_url_index(vault_dir: Path | str) -> dict[str, Path]:
    """Scan every active-staging dir and return ``{url → path}``.

    Active staging = ``Clippings/`` + the four ``50-Inbox/`` stages
    (not ``70-Archive``, not ``10-Knowledge``).  This is the
    correct gate for **intake** — anywhere a URL appears in the
    staging chain, it has already claimed its slot.
    """
    return _scan_dirs(vault_dir, ACTIVE_INTAKE_DIRS)


def build_url_index(vault_dir: Path | str) -> dict[str, Path]:
    """Scan ``50-Inbox/03-Processed/**/*.md`` only and return
    ``{url → path}``.

    Used by the cleanup CLI (``ovp-dedup-cleanup``) which targets
    dups that have already reached final intake.  Intake gates
    should call :func:`build_active_url_index` instead — the
    narrow scope was the BL-058 v0.12.0 dedup-bypass bug.
    """
    return _scan_dirs(vault_dir, (PROCESSED_INTAKE_DIR,))


def find_existing_by_url(
    vault_dir: Path | str,
    url: str,
    *,
    index: dict[str, Path] | None = None,
    scope: str = "active",
) -> Path | None:
    """Return the staging raw whose ``source:`` matches ``url``,
    or ``None``.

    ``scope='active'`` (default) walks the full active-staging set
    — the right choice for intake guards.  ``scope='processed'``
    walks ``03-Processed`` only — kept for the cleanup CLI and
    older callers that were paired with ``build_url_index``.

    Pass a pre-built ``index`` for batch operations.  A fresh
    ``index`` is built per call when ``index`` is ``None`` —
    fine for one-off intake checks but wasteful in a tight loop.
    """
    if index is None:
        if scope == "active":
            index = build_active_url_index(vault_dir)
        elif scope == "processed":
            index = build_url_index(vault_dir)
        else:
            raise ValueError(f"Unknown scope {scope!r}; expected 'active' or 'processed'")
    return index.get(url)


def find_duplicate_groups(
    vault_dir: Path | str,
) -> dict[str, list[Path]]:
    """Return groups of paths that share a ``source:`` URL within
    ``50-Inbox/03-Processed/``.  Used by the cleanup CLI to flag
    and archive everything but the canonical copy.

    Scope is intentionally narrow (final intake only) — in-flight
    stagings legitimately contain a URL that is also in
    03-Processed when a previous run partially completed; the
    fix for those is to **finish** the run, not archive the
    in-flight raw.

    Excludes singletons; only groups with len ≥ 2 are returned.
    """
    vault = Path(vault_dir).resolve()
    processed = vault / PROCESSED_INTAKE_DIR
    groups: dict[str, list[Path]] = {}
    if not processed.is_dir():
        return groups
    for f in sorted(processed.rglob("*.md")):
        try:
            text = read_file_head(f)
        except OSError:
            continue
        url = extract_source_url(text)
        if url:
            groups.setdefault(url, []).append(f)
    return {u: paths for u, paths in groups.items() if len(paths) > 1}
