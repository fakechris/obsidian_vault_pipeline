"""Crystal full-text search (BL-047 first slice, M14).

Extends the existing ``page_fts`` virtual table to index every
current crystal body so the existing ``/search`` Access Surface
returns crystals alongside evergreen pages without a new route.

Architecture role: **Access Surface support** — the FTS row is a
Projection over the synthesis Projections.  No Canonical State
read or write.

Slug convention so existing wikilink behaviour stays sane:

* community crystal:  slug = ``crystal:<safe-id>``,
  title = ``[crystal] <label>``
* contradiction crystal: slug = ``contradiction:<safe-id>``,
  title = ``[contradiction] <subject_key>``

The prefix lets the search UI distinguish the rows visually; the
``<safe-id>`` after the colon matches the on-disk filename (
``40-Resources/Crystals/<safe-id>.md`` and
``40-Resources/Crystals/contradiction-<safe-id>.md``) so callers
can construct a click-through URL deterministically.

This module is invoked from ``rebuild_knowledge_index`` AFTER the
existing ``page_fts`` population block, so crystal rows are
appended to the same FTS table the regular search already
consults.  ``page_fts`` is rebuilt on every index rebuild, so
this run is naturally idempotent.

Out of BL-047 scope (deferred):

* **Tag facet** — every crystal currently shares the same
  ``tags: [crystal, ...]``; until LLM-generated per-crystal tags
  emerge there's nothing to filter on.
* **Entity facet** — joining crystal mentions through
  ``entity_aliases`` to enable "show me crystals about Karpathy".
  Worthwhile but ~3× the implementation cost of FTS alone.

Both follow up under a separate BL once the core FTS surface is
in production use and the actual user query patterns surface.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


# Slug prefixes — these are the discriminator the UI keys off when
# rendering a search result so an evergreen page and a crystal
# don't collide visually.  Match the filesystem layout in
# ``40-Resources/Crystals/<safe-id>.md`` for the community case
# and ``40-Resources/Crystals/contradiction-<safe-id>.md`` for the
# contradiction case.
_COMMUNITY_PREFIX = "crystal:"
_CONTRADICTION_PREFIX = "contradiction:"


def _community_safe_id(cluster_id: str) -> str:
    if cluster_id.startswith("cluster::"):
        return cluster_id[len("cluster::"):]
    return cluster_id


def _contradiction_safe_id(contradiction_id: str) -> str:
    if contradiction_id.startswith("contradiction::"):
        return contradiction_id[len("contradiction::"):]
    return contradiction_id


def index_crystals_into_page_fts(
    conn: sqlite3.Connection,
    *,
    pack: str,
) -> int:
    """Append every current crystal row to ``page_fts``.  Returns
    the number of FTS rows inserted.  No-op when ``page_fts``
    doesn't exist yet (first ``ovp-knowledge-index`` run on a
    fresh DB creates it before this is called).
    """
    # Defensive: page_fts is a virtual FTS5 table; a tiny query
    # tells us whether it exists without scanning anything.
    try:
        conn.execute("SELECT 1 FROM page_fts LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return 0

    inserted = 0

    # Community crystals — current rows only.
    cur = conn.execute(
        """
        SELECT cc.cluster_id, gc.label, cc.body_md
          FROM community_crystals cc
          JOIN graph_clusters gc
            ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
         WHERE cc.pack = ?
           AND cc.superseded_by_synthesized_at = ''
        """,
        (pack,),
    )
    rows = []
    for cluster_id, label, body_md in cur:
        slug = _COMMUNITY_PREFIX + _community_safe_id(cluster_id)
        # BL-051: user-facing label says "topic", not "crystal".
        # Slug prefix (``crystal:``) stays as-is — it's a stable
        # identifier downstream code keys off.
        title = f"[topic] {label or '(untitled)'}"
        rows.append((slug, title, body_md or ""))
    if rows:
        conn.executemany(
            "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
            rows,
        )
        inserted += len(rows)

    # Contradiction crystals — current rows only.
    cur = conn.execute(
        """
        SELECT contradiction_id, subject_key, body_md
          FROM contradiction_crystals
         WHERE pack = ?
           AND superseded_by_synthesized_at = ''
        """,
        (pack,),
    )
    rows = []
    for contradiction_id, subject_key, body_md in cur:
        slug = (_CONTRADICTION_PREFIX
                + _contradiction_safe_id(contradiction_id))
        # BL-051: contradiction crystals surface as "open question" —
        # they're unresolved tensions, not settled topics.
        title = f"[open question] {subject_key or '(untitled)'}"
        rows.append((slug, title, body_md or ""))
    if rows:
        conn.executemany(
            "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
            rows,
        )
        inserted += len(rows)

    return inserted
