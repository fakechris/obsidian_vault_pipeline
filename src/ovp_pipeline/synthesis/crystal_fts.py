"""Crystal full-text search (BL-047 first slice, M14).

Extends the existing ``page_fts`` virtual table to index every
current crystal body so the existing ``/search`` Access Surface
returns crystals alongside evergreen pages without a new route.

Architecture role: **Access Surface support** — the FTS row is a
Projection over the synthesis Projections.  No Canonical State
read or write.

Slug convention so existing wikilink behaviour stays sane:

* community crystal:  slug = ``crystal:<safe-id>``,
  title = ``[topic] <label>``
* contradiction crystal: slug = ``contradiction:<safe-id>``,
  title = ``[open question] <subject_key>``

The prefix lets the search UI distinguish the rows visually; the
``<safe-id>`` after the colon matches the on-disk filename (
``40-Resources/Crystals/<safe-id>.md`` and
``40-Resources/Crystals/contradiction-<safe-id>.md``) so callers
can construct a click-through URL deterministically.

This module is invoked from ``rebuild_knowledge_index`` AFTER the
existing ``page_fts`` and ``pages_index`` population block, so
crystal rows are appended to the same tables the regular search
already consults.  Both tables are rebuilt on every index
rebuild, so this run is naturally idempotent.

Why we also write ``pages_index`` (BL-047 follow-up review):
``/search`` joins ``page_fts`` to ``pages_index`` on slug — without
matching ``pages_index`` rows for the ``crystal:`` and
``contradiction:`` slugs, FTS hits are filtered out and never
surface.  Writing the parallel ``pages_index`` row is the
minimum-touch fix that keeps the FTS pipeline owning the slug
convention without changing the on-disk file scanner.

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

from ._shared import CRYSTAL_DIR_REL, crystal_safe_id

logger = logging.getLogger(__name__)


# Slug prefixes — these are the discriminator the UI keys off when
# rendering a search result so an evergreen page and a crystal
# don't collide visually.  Match the filesystem layout in
# ``40-Resources/Crystals/<safe-id>.md`` for the community case
# and ``40-Resources/Crystals/contradiction-<safe-id>.md`` for the
# contradiction case.
_COMMUNITY_PREFIX = "crystal:"
_CONTRADICTION_PREFIX = "contradiction:"

# ``note_type`` values stored in ``pages_index`` for crystal rows.
# Distinct from the file-scanner-emitted note types so the Reader
# can render badges (or a /search filter) without slug-prefix
# string-matching.
_COMMUNITY_NOTE_TYPE = "community_crystal"
_CONTRADICTION_NOTE_TYPE = "contradiction_crystal"


def _community_safe_id(cluster_id: str) -> str:
    return crystal_safe_id("community", cluster_id)


def _contradiction_safe_id(contradiction_id: str) -> str:
    # FTS slug uses the bare digest (no ``contradiction-`` filename
    # prefix) so callers get ``contradiction:<digest>``, matching the
    # historical surface that downstream UI code keys off.
    if contradiction_id.startswith("contradiction::"):
        return contradiction_id[len("contradiction::"):]
    return contradiction_id


def _community_path(safe_id: str) -> str:
    return str(CRYSTAL_DIR_REL / f"{safe_id}.md")


def _contradiction_path(safe_id: str) -> str:
    # Contradiction crystals on disk live under the same dir but with
    # a ``contradiction-`` filename prefix (see synthesis/_shared and
    # synthesis/contradiction_crystal.py).  Mirror that convention so
    # /note?path=... resolves to a real file.
    return str(CRYSTAL_DIR_REL / f"contradiction-{safe_id}.md")


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

    # Community crystals — current rows only.  BL-114: resolve the
    # label through the ledger so a concept whose current_cluster_id
    # shifted across a re-cluster still gets the label of its CURRENT
    # Louvain cluster.  Slug keys off ``concept_id`` (stable identity)
    # so FTS / pages_index entries survive re-clusters.
    cur = conn.execute(
        """
        SELECT cc.concept_id, gc.label, cc.body_md
          FROM community_crystals cc
          JOIN concept_identity_ledger cil
            ON cil.pack = cc.pack AND cil.concept_id = cc.concept_id
          JOIN graph_clusters gc
            ON gc.pack = cil.pack AND gc.cluster_id = cil.current_cluster_id
         WHERE cc.pack = ?
           AND cc.superseded_by_synthesized_at = ''
        """,
        (pack,),
    )
    fts_rows: list[tuple[str, str, str]] = []
    page_rows: list[tuple[str, str, str, str, str, str, str]] = []
    for concept_id, label, body_md in cur:
        safe_id = _community_safe_id(concept_id)
        slug = _COMMUNITY_PREFIX + safe_id
        # BL-051: user-facing label says "topic", not "crystal".
        # Slug prefix (``crystal:``) stays as-is — it's a stable
        # identifier downstream code keys off.
        title = f"[topic] {label or '(untitled)'}"
        body = body_md or ""
        fts_rows.append((slug, title, body))
        page_rows.append((
            slug, title, _COMMUNITY_NOTE_TYPE,
            _community_path(safe_id), "", "{}", body,
        ))
    if fts_rows:
        conn.executemany(
            "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
            fts_rows,
        )
        # ``INSERT OR REPLACE`` so a re-run inside the same transaction
        # (or a slug collision with a stray scanned page) is idempotent
        # rather than crashing.  Crystals own their slug namespace
        # (``crystal:`` / ``contradiction:``) so a real collision with
        # a scanned page is impossible by construction.
        conn.executemany(
            "INSERT OR REPLACE INTO pages_index "
            "(slug, title, note_type, path, day_id, frontmatter_json, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            page_rows,
        )
        inserted += len(fts_rows)

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
    fts_rows = []
    page_rows = []
    for contradiction_id, subject_key, body_md in cur:
        safe_id = _contradiction_safe_id(contradiction_id)
        slug = _CONTRADICTION_PREFIX + safe_id
        # BL-051: contradiction crystals surface as "open question" —
        # they're unresolved tensions, not settled topics.
        title = f"[open question] {subject_key or '(untitled)'}"
        body = body_md or ""
        fts_rows.append((slug, title, body))
        page_rows.append((
            slug, title, _CONTRADICTION_NOTE_TYPE,
            _contradiction_path(safe_id), "", "{}", body,
        ))
    if fts_rows:
        conn.executemany(
            "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
            fts_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO pages_index "
            "(slug, title, note_type, path, day_id, frontmatter_json, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            page_rows,
        )
        inserted += len(fts_rows)

    return inserted
