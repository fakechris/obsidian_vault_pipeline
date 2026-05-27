from __future__ import annotations

from array import array
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from io import TextIOBase
import json
import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

from .concept_registry import ConceptRegistry, ResolutionAction
from .event_emitter import iter_for_index
from .graph.frontmatter import FrontmatterParser, NoteMetadata
from .graph.link_parser import LinkParser
from .audit_identity import audit_slug_for_column
from .identity import canonicalize_note_id
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .projection_lifecycle import close_projection_repair_marker, write_projection_repair_marker
from .provenance import bulk_upsert_provenance_ingest
from .relation_writer import bulk_insert_relations
from .runtime import VaultLayout, knowledge_db_write_lock, resolve_vault_dir
from .truth_projection_registry import (
    execute_truth_projection_builder,
    resolve_truth_projection_builder,
)
from .truth_store import TRUTH_STORE_SCHEMA
from .truth_store_writers import insert_claims, insert_objects

logger = logging.getLogger(__name__)

SUMMARY_MAX_LEN = 320
SUMMARY_RELATED_LIMIT = 3
AUTHORITY_SCHEMA_VERSION = 1
KNOWLEDGE_DB_PROJECTION_KIND = "knowledge_db"
# ``projection_schema_version`` policy
# ===================================
#
# Every BL that changes the ``knowledge.db`` projection lands an
# entry in :data:`SCHEMA_MIGRATIONS` below.  Bumps without an entry
# fail ``tests/test_projection_schema_migrations.py`` — the registry
# is what stops "撞版本号 → 用户冷启动等几分钟" from being the default.
#
# Three buckets, mirrored in :class:`SchemaMigrationKind`:
#
# * **additive** — pure new table, or new ``nullable`` column on an
#   existing table.  Runner does ``CREATE TABLE IF NOT EXISTS …`` /
#   ``ALTER TABLE … ADD COLUMN …`` plus an optional local
#   re-projection.  Cost: seconds.
# * **recompute** — extraction/projection logic for an existing
#   table changed.  Runner does ``DELETE FROM <table>`` + the
#   rebuild routine for that single table.  Cost: bounded to the
#   touched table.
# * **breaking** — column dropped, type changed, primary key
#   restructured.  Runner falls through to a full
#   :func:`rebuild_knowledge_index`.  Cost: full vault rescan.
#   Only this bucket triggers the slow path.
#
# See ``ARCHITECTURE.md`` § Projection schema changes for the
# review-side rules + PR checklist.
KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION = 10


_FTS_QUERY_SCRUB = re.compile(r"[^\w\u4e00-\u9fff]+", flags=re.UNICODE)


def sanitize_fts_query(query_text: str) -> str:
    """Strip FTS5 syntax characters (`-`, `:`, `"`, etc.) from free-text input
    so prose like ``multi-step`` doesn't parse as ``multi NOT step`` and crash
    with ``no such column: step``. Keeps alphanumerics + CJK and collapses
    whitespace; returns ``""`` when nothing usable remains."""
    cleaned = _FTS_QUERY_SCRUB.sub(" ", query_text or "")
    return " ".join(cleaned.split())


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE pages_index (
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  note_type TEXT NOT NULL,
  path TEXT NOT NULL,
  day_id TEXT NOT NULL,
  frontmatter_json TEXT NOT NULL,
  body TEXT NOT NULL
);

-- Trigram tokenizer gives substring-style matching that works for English
-- and CJK alike (the default unicode61 tokenizer treats consecutive Chinese
-- characters as one opaque token, missing every mid-sentence query).
CREATE VIRTUAL TABLE page_fts USING fts5(
  slug UNINDEXED,
  title,
  body,
  tokenize='trigram'
);

CREATE TABLE page_links (
  source_slug TEXT NOT NULL,
  target_slug TEXT NOT NULL,
  target_raw TEXT NOT NULL DEFAULT '',
  link_type TEXT NOT NULL,
  line_number INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_page_links_source ON page_links(source_slug);
CREATE INDEX idx_page_links_target ON page_links(target_slug);

CREATE TABLE raw_data (
  slug TEXT NOT NULL,
  source_name TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_path TEXT NOT NULL,
  PRIMARY KEY (slug, source_name, source_path)
);

CREATE TABLE timeline_events (
  slug TEXT NOT NULL,
  event_date TEXT NOT NULL,
  event_type TEXT NOT NULL,
  heading TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_timeline_events_slug ON timeline_events(slug);
CREATE INDEX idx_timeline_events_date ON timeline_events(event_date);

CREATE TABLE audit_events (
  source_log TEXT NOT NULL,
  event_type TEXT NOT NULL,
  slug TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  timestamp TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL
);

CREATE INDEX idx_audit_events_log ON audit_events(source_log);
CREATE INDEX idx_audit_events_type ON audit_events(event_type);

CREATE TABLE page_embeddings (
  slug TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  section_title TEXT NOT NULL,
  chunk_text TEXT NOT NULL,
  embedding_blob BLOB NOT NULL,
  embedding_model TEXT NOT NULL,
  chunk_hash TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (slug, chunk_index)
);

CREATE INDEX idx_page_embeddings_slug ON page_embeddings(slug);

CREATE INDEX idx_page_embeddings_hash
  ON page_embeddings(chunk_hash, embedding_model);

CREATE TABLE page_metrics (
  slug TEXT PRIMARY KEY,
  last_seen_ts INTEGER NOT NULL DEFAULT 0,
  reuse_count INTEGER NOT NULL DEFAULT 0,
  citation_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_page_metrics_last_seen ON page_metrics(last_seen_ts);

CREATE TABLE projection_metadata (
  projection_kind TEXT PRIMARY KEY,
  authority_schema_version INTEGER NOT NULL,
  projection_schema_version INTEGER NOT NULL,
  built_at TEXT NOT NULL
);

CREATE TABLE entity_mentions (
  entity_slug TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  detection_method TEXT NOT NULL DEFAULT 'wikilink',
  mention_text TEXT NOT NULL DEFAULT '',
  snippet TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_entity_mentions_entity ON entity_mentions(entity_slug);
CREATE INDEX idx_entity_mentions_source ON entity_mentions(source_slug);
CREATE INDEX idx_entity_mentions_type ON entity_mentions(entity_type);

-- M21 BL-085: ``chats`` is a *display / metadata* projection over
-- the ``40-Resources/Chats/**/*.md`` corpus.  Lifetime token counts
-- here are display derivatives only — daily-cap math in BL-084
-- reads the append-only ``audit_events`` ledger, not this table.
CREATE TABLE chats (
  chat_id TEXT PRIMARY KEY,
  pack TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL,
  status TEXT NOT NULL,            -- active | pinned | archived
  visibility TEXT NOT NULL,        -- indexed | unindexed
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

CREATE INDEX idx_chats_pack_last ON chats(pack, last_message_at DESC);
CREATE INDEX idx_chats_visibility ON chats(visibility);
CREATE INDEX idx_chats_status ON chats(status);
"""

SCHEMA += "\n" + TRUTH_STORE_SCHEMA


# ---------------------------------------------------------------
# Schema migrations registry
# ---------------------------------------------------------------
#
# Every projection_schema_version bump MUST register a migration
# below or the test ``tests/test_projection_schema_migrations.py``
# fails CI.  See the policy block on
# :data:`KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION` above.


class SchemaMigrationKind(str, Enum):
    """Three buckets — only ``BREAKING`` triggers a full rebuild.

    See the policy block above ``KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION``
    for the review-side rules.
    """

    ADDITIVE = "additive"
    RECOMPUTE = "recompute"
    BREAKING = "breaking"


@dataclass(frozen=True)
class SchemaMigration:
    """One ``from_version`` → ``from_version + 1`` step.

    ``runner`` runs inside the same connection as the rest of the
    init path — DDL + DML in one transaction.  ``vault_dir`` is
    threaded through so additive migrations can re-project from
    the markdown corpus (e.g. BL-085's chats).
    """

    from_version: int
    kind: SchemaMigrationKind
    reason: str  # short BL reference, e.g. "BL-085 — chats table"
    runner: "Callable[[sqlite3.Connection, Path], None]"


def _migrate_6_to_7_evergreen_revisions(conn: sqlite3.Connection, vault_dir: Path) -> None:
    """BL-061 — ``evergreen_revisions`` append-only audit table.

    Retroactively classified as additive: the table is declared in
    ``TRUTH_STORE_SCHEMA``, so on a fresh DB build it's already
    created via the main ``SCHEMA`` script.  This runner exists so
    a vault that was somehow at v6 without the table (e.g. a hand-
    rolled migration) gets the table created without re-running
    the full vault rescan.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evergreen_revisions (
          pack TEXT NOT NULL,
          object_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          content_md TEXT NOT NULL,
          change_type TEXT NOT NULL,
          changed_by TEXT NOT NULL DEFAULT '',
          derived_at TEXT NOT NULL,
          change_note TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (pack, object_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_evergreen_revisions_object
          ON evergreen_revisions(pack, object_id);
        CREATE INDEX IF NOT EXISTS idx_evergreen_revisions_changed_at
          ON evergreen_revisions(derived_at);
        """)


def _migrate_7_to_8_chats(conn: sqlite3.Connection, vault_dir: Path) -> None:
    """BL-085 — ``chats`` projection.

    Pure additive: new table, no cross-table dependency, no
    schema change on any existing table.  Pre-fix this required
    a full ``rebuild_knowledge_index`` (minutes); now it's a
    ``CREATE TABLE`` + a local ``rebuild_chats_projection`` sweep
    of ``40-Resources/Chats/`` (seconds).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
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
        CREATE INDEX IF NOT EXISTS idx_chats_pack_last
          ON chats(pack, last_message_at DESC);
        CREATE INDEX IF NOT EXISTS idx_chats_visibility
          ON chats(visibility);
        CREATE INDEX IF NOT EXISTS idx_chats_status
          ON chats(status);
        """)
    # Populate from the markdown corpus.  CodeRabbit Major — do NOT
    # swallow exceptions: a failed seed must leave
    # ``projection_metadata`` at the old version so the next start
    # retries.  ``rebuild_chats_projection`` already degrades
    # gracefully on missing chats dir (it returns 0-count); a real
    # exception here means a hard error (disk full, malformed
    # transcript) that the operator should see.
    from .chats_projection import rebuild_chats_projection

    rebuild_chats_projection(conn, vault_dir=vault_dir)


def _migrate_8_to_9_embedding_hash(conn: sqlite3.Connection, vault_dir: Path) -> None:
    """PR3 — ``page_embeddings.chunk_hash`` for embedding reuse.

    Pure additive: one new ``NOT NULL DEFAULT ''`` column on an
    existing table plus a lookup index.  No re-projection — existing
    rows keep ``chunk_hash=''`` which simply never matches a freshly
    computed SHA-256, so the next full rebuild recomputes them once
    and from then on the hash is populated and reuse kicks in.  The
    ``IF NOT EXISTS`` / duplicate-column guard makes it idempotent
    against a DB already at the v9 shape (e.g. built fresh from
    ``SCHEMA``).
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='page_embeddings'"
    ).fetchone()
    if not table_exists:
        # No page_embeddings (minimal/partial DB).  A real DB gets
        # the v9-shaped table from the main SCHEMA on fresh build;
        # nothing to migrate here.
        return
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(page_embeddings)").fetchall()
    }
    if "chunk_hash" not in cols:
        conn.execute(
            "ALTER TABLE page_embeddings "
            "ADD COLUMN chunk_hash TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_embeddings_hash "
        "ON page_embeddings(chunk_hash, embedding_model)"
    )


def _migrate_9_to_10_concept_identity(conn: sqlite3.Connection, vault_dir: Path) -> None:
    """BL-114 — ``concept_identity_ledger`` + ``community_crystals.concept_id``.

    Pure additive: one new table, two new ``NOT NULL DEFAULT ''`` columns
    on the existing ``community_crystals`` table, and a one-shot seed
    where each existing ``cluster_id`` becomes its own ``concept_id``
    (so behaviour is byte-identical until BL-115's Jaccard matcher
    starts making the two diverge).

    Idempotent: ``IF NOT EXISTS`` on the table+indexes, duplicate-column
    guard on the ALTERs, and the UPDATE/INSERT both no-op when the
    seed already ran.  Safe to re-run against a DB already at the v10
    shape (e.g. built fresh from ``SCHEMA``).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS concept_identity_ledger (
          pack TEXT NOT NULL,
          concept_id TEXT NOT NULL,
          current_cluster_id TEXT NOT NULL DEFAULT '',
          last_matched_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT '',
          lineage_json TEXT NOT NULL DEFAULT '[]',
          PRIMARY KEY (pack, concept_id)
        );
        CREATE INDEX IF NOT EXISTS idx_concept_identity_ledger_current_cluster
          ON concept_identity_ledger(pack, current_cluster_id);
        """)

    # Guard against minimal vaults that don't have community_crystals
    # yet (e.g. v7 DBs in the migration-path tests).  The fresh-DB
    # build path defines this table via TRUTH_STORE_SCHEMA; this
    # migration only needs to do work if it already exists.
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='community_crystals'"
    ).fetchone()
    if not table_exists:
        return

    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(community_crystals)").fetchall()
    }
    if "concept_id" not in cols:
        conn.execute(
            "ALTER TABLE community_crystals "
            "ADD COLUMN concept_id TEXT NOT NULL DEFAULT ''"
        )
    if "supersede_reason" not in cols:
        conn.execute(
            "ALTER TABLE community_crystals "
            "ADD COLUMN supersede_reason TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_community_crystals_pack_concept "
        "ON community_crystals(pack, concept_id)"
    )

    # Seed: every existing crystal becomes its own concept_id.  Skips
    # rows already backfilled (re-runs become no-ops).
    conn.execute(
        "UPDATE community_crystals SET concept_id = cluster_id "
        "WHERE concept_id = ''"
    )
    # Seed the ledger: one row per (pack, cluster_id).  Use the
    # *latest* synthesized_at of an active crystal as last_matched_at
    # so BL-115 can compare staleness against it; created_at is the
    # earliest synthesized_at for that cluster_id so the chain has
    # a stable origin timestamp.  ``ON CONFLICT DO NOTHING`` makes
    # the seed idempotent against partial prior runs.
    conn.execute(
        """
        INSERT INTO concept_identity_ledger
            (pack, concept_id, current_cluster_id, last_matched_at,
             created_at, lineage_json)
        SELECT pack,
               cluster_id AS concept_id,
               cluster_id AS current_cluster_id,
               MAX(synthesized_at) AS last_matched_at,
               MIN(synthesized_at) AS created_at,
               '[]' AS lineage_json
          FROM community_crystals
         GROUP BY pack, cluster_id
        ON CONFLICT(pack, concept_id) DO NOTHING
        """
    )

    # Same auto-seed triggers that fresh DBs get from TRUTH_STORE_SCHEMA.
    # Adding them here keeps upgraded vaults consistent with the
    # canonical shape.  ``IF NOT EXISTS`` is idempotent — fresh DBs
    # (which already ran the SCHEMA script) ignore this; upgrades that
    # didn't have the triggers now get them.  Two triggers split on
    # WHEN NEW.concept_id = '' so legacy INSERTs (no concept_id) get
    # backfilled while BL-115's explicit-concept_id path stays in
    # control of the ledger row.
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_community_crystal_seed_ledger
        AFTER INSERT ON community_crystals
        WHEN NEW.concept_id = ''
        BEGIN
          UPDATE community_crystals
             SET concept_id = NEW.cluster_id
           WHERE pack = NEW.pack
             AND cluster_id = NEW.cluster_id
             AND synthesized_at = NEW.synthesized_at;
          INSERT OR IGNORE INTO concept_identity_ledger
              (pack, concept_id, current_cluster_id,
               last_matched_at, created_at, lineage_json)
          VALUES (NEW.pack, NEW.cluster_id, NEW.cluster_id,
                  NEW.synthesized_at, NEW.synthesized_at, '[]');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_community_crystal_seed_ledger_explicit
        AFTER INSERT ON community_crystals
        WHEN NEW.concept_id <> ''
        BEGIN
          INSERT OR IGNORE INTO concept_identity_ledger
              (pack, concept_id, current_cluster_id,
               last_matched_at, created_at, lineage_json)
          VALUES (NEW.pack, NEW.concept_id, NEW.cluster_id,
                  NEW.synthesized_at, NEW.synthesized_at, '[]');
        END;
    """)


SCHEMA_MIGRATIONS: dict[int, SchemaMigration] = {
    6: SchemaMigration(
        from_version=6,
        kind=SchemaMigrationKind.ADDITIVE,
        reason="BL-061 — evergreen_revisions audit table",
        runner=_migrate_6_to_7_evergreen_revisions,
    ),
    7: SchemaMigration(
        from_version=7,
        kind=SchemaMigrationKind.ADDITIVE,
        reason="BL-085 — chats projection table",
        runner=_migrate_7_to_8_chats,
    ),
    8: SchemaMigration(
        from_version=8,
        kind=SchemaMigrationKind.ADDITIVE,
        reason="PR3 — page_embeddings.chunk_hash for embedding reuse",
        runner=_migrate_8_to_9_embedding_hash,
    ),
    9: SchemaMigration(
        from_version=9,
        kind=SchemaMigrationKind.ADDITIVE,
        reason="BL-114 — concept_identity_ledger seeded from community_crystals",
        runner=_migrate_9_to_10_concept_identity,
    ),
}


def _plan_schema_upgrade(
    from_version: int,
    to_version: int,
) -> tuple[list[SchemaMigration], list[int]]:
    """Return ``(steps, unregistered_versions)`` for the version range.

    Caller can short-circuit to full rebuild when ``unregistered``
    is non-empty (no migration registered → safer to rebuild than
    silently skip).  When every step is registered AND classified
    as ``ADDITIVE`` or ``RECOMPUTE``, the delta path is safe.
    A ``BREAKING`` step in the chain forces the full rebuild too.
    """
    steps: list[SchemaMigration] = []
    missing: list[int] = []
    for v in range(from_version, to_version):
        migration = SCHEMA_MIGRATIONS.get(v)
        if migration is None:
            missing.append(v)
            continue
        steps.append(migration)
    return steps, missing


def _can_delta_migrate(steps: list[SchemaMigration]) -> bool:
    """All steps are additive/recompute → safe to delta-migrate."""
    return bool(steps) and all(
        s.kind in (SchemaMigrationKind.ADDITIVE, SchemaMigrationKind.RECOMPUTE) for s in steps
    )


from .embedding import (
    assert_consistent_with as _assert_embedding_consistent,
    embed_text as _embed_text_semantic,
    get_dimensions,
    get_model_name,
)

TRUTH_PROJECTION_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "objects": (
        "pack",
        "object_id",
        "object_kind",
        "title",
        "canonical_path",
        "source_slug",
        "source_url",
    ),
    "claims": ("pack", "claim_id", "object_id", "claim_kind", "claim_text", "confidence"),
    "claim_evidence": (
        "pack",
        "claim_id",
        "source_slug",
        "evidence_kind",
        "quote_text",
        "locator",
        "content_hash",
        "retrieval_context",
        "quote_start_line",
        "quote_end_line",
        "quote_start_char",
        "quote_end_char",
        "status",
        "verified_at",
    ),
    "relations": (
        "pack",
        "source_object_id",
        "target_object_id",
        "relation_type",
        "evidence_source_slug",
        "quote_text",
        "locator",
        "content_hash",
        "retrieval_context",
        "quote_start_line",
        "quote_end_line",
        "quote_start_char",
        "quote_end_char",
        "status",
        "verified_at",
    ),
    "compiled_summaries": ("pack", "object_id", "summary_text", "source_slug"),
    "contradictions": (
        "pack",
        "contradiction_id",
        "subject_key",
        "positive_claim_ids_json",
        "negative_claim_ids_json",
        "status",
        "resolution_note",
        "resolved_at",
    ),
    "graph_edges": (
        "pack",
        "edge_id",
        "source_object_id",
        "target_object_id",
        "edge_kind",
        "weight",
        "evidence_source_slug",
    ),
    "graph_clusters": (
        "pack",
        "cluster_id",
        "cluster_kind",
        "label",
        "center_object_id",
        "member_object_ids_json",
        "score",
    ),
}


# Canonical State tables that the index rebuild does NOT recompute
# from pages_index — they are independently materialized by LLM
# synthesis (community_crystals + contradiction_crystals).  They must
# be carried across a knowledge-index rebuild for ALL packs (no
# ``exclude_pack`` filter), otherwise rebuilding silently wipes the
# LLM-synthesized corpus and the user pays to regenerate it.
#
# ``crystal_scores`` is intentionally NOT in this list — it is a true
# Projection over ``community_crystals``/``contradiction_crystals``
# and is rebuilt deterministically by ``rebuild_crystal_scores`` later
# in the index pipeline.  Preserving it here would only get
# overwritten by the rebuild call.
INDEPENDENT_CANONICAL_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    # BL-055: provenance audit history.  Pre-fix it lived in
    # ``TRUTH_PROJECTION_TABLE_COLUMNS`` which excludes the current
    # pack from preservation — so every rebuild wiped any
    # ``stage='extract'``/``'promote'``/``'synthesize_*'`` rows for
    # the current pack and only kept the fresh ``stage='ingest'``
    # row this rebuild writes.  As an audit log, history needs to
    # survive across rebuilds for ALL packs, including the current
    # one — same treatment as ``community_crystals`` (BL-049).
    "provenance": (
        "pack",
        "object_id",
        "source_url",
        "source_fingerprint",
        "derived_via_stage",
        "derived_at",
        "parent_object_id",
        "metadata_json",
    ),
    "community_crystals": (
        "pack",
        "cluster_id",
        "body_md",
        "source_evergreen_slugs_json",
        "synthesized_at",
        "llm_model",
        "prompt_version",
        "superseded_by_synthesized_at",
    ),
    "contradiction_crystals": (
        "pack",
        "contradiction_id",
        "subject_key",
        "body_md",
        "positive_claim_ids_json",
        "negative_claim_ids_json",
        "source_object_ids_json",
        "synthesized_at",
        "llm_model",
        "prompt_version",
        "superseded_by_synthesized_at",
    ),
    # BL-061: prose-level evergreen revision history.  Same
    # treatment as ``provenance`` — an immutable append-only audit
    # log that must survive projection rebuilds, otherwise every
    # ``ovp-knowledge-index`` invocation would wipe BL-061's
    # rollback semantics.  Discovered post-PR-#193 review: revisions
    # written by ``review_candidate_concept`` or ``cli:auto_promote``
    # were silently lost on the next rebuild.
    "evergreen_revisions": (
        "pack",
        "object_id",
        "version",
        "content_md",
        "change_type",
        "changed_by",
        "derived_at",
        "change_note",
    ),
}


def _truth_pack_name(pack_name: str | None = None) -> str:
    return str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)


def _source_fingerprint(source_url: str) -> str:
    """BL-055: 12-char SHA-256 prefix of a source URL.  Same shape
    the extractor writes so frontmatter and DB rows agree."""
    if not source_url:
        return ""
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12]


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_authority_schema_version(vault_dir: Path | str) -> int:
    path = resolve_vault_dir(vault_dir) / ".ovp" / "schema_version"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{AUTHORITY_SCHEMA_VERSION}\n", encoding="utf-8")
        return AUTHORITY_SCHEMA_VERSION
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        path.write_text(f"{AUTHORITY_SCHEMA_VERSION}\n", encoding="utf-8")
        return AUTHORITY_SCHEMA_VERSION


def _read_knowledge_db_projection_metadata(db_path: Path) -> tuple[int, int] | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT authority_schema_version, projection_schema_version
                FROM projection_metadata
                WHERE projection_kind = ?
                """,
                (KNOWLEDGE_DB_PROJECTION_KIND,),
            ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    return int(row[0] or 0), int(row[1] or 0)


def _projection_metadata(pack_name: str) -> tuple[str, str]:
    try:
        spec = resolve_truth_projection_builder(pack_name=pack_name)
    except Exception:
        return pack_name, ""
    return spec.pack, getattr(spec, "name", "")


def _preserve_existing_truth_rows(
    source_db_path: Path,
    dest_conn: sqlite3.Connection,
    *,
    exclude_pack: str,
) -> None:
    if not source_db_path.exists():
        return
    preserved_packs: set[str] = set()
    preserved_metadata_packs: set[str] = set()
    metadata_rows: list[tuple[Any, ...]] = []
    try:
        source_conn = sqlite3.connect(source_db_path)
    except sqlite3.DatabaseError:
        return

    def _copy_table(
        table_name: str,
        columns: tuple[str, ...],
        *,
        where_excludes_current: bool,
    ) -> list[tuple[Any, ...]]:
        column_sql = ", ".join(columns)
        if where_excludes_current:
            sql = f"SELECT {column_sql} FROM {table_name} WHERE pack != ? ORDER BY pack"
            params: tuple[Any, ...] = (exclude_pack,)
        else:
            sql = f"SELECT {column_sql} FROM {table_name} ORDER BY pack"
            params = ()
        try:
            rows = source_conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            error_text = str(exc).lower()
            if "no such table" in error_text or "no such column" in error_text:
                return []
            raise
        if not rows:
            return []
        placeholders = ", ".join("?" for _ in columns)
        dest_conn.executemany(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
            rows,
        )
        return rows

    try:
        for table_name, columns in TRUTH_PROJECTION_TABLE_COLUMNS.items():
            rows = _copy_table(table_name, columns, where_excludes_current=True)
            preserved_packs.update(str(row[0]) for row in rows if row and row[0])

        # INDEPENDENT_CANONICAL_TABLE_COLUMNS rows are LLM-synthesized —
        # they cannot be recomputed by the rebuild, so they must be
        # carried over for ALL packs, including the current one.
        # Without this, every ``ovp-knowledge-index`` run silently
        # wipes the crystal corpus and the user has to pay LLM cost
        # to regenerate.  Intentionally NOT updating ``preserved_packs``
        # here: that set drives ``truth_projections`` metadata
        # backfill, which only applies to packs that produce
        # truth-projection rows; a crystal-only pack does not.
        for table_name, columns in INDEPENDENT_CANONICAL_TABLE_COLUMNS.items():
            _copy_table(table_name, columns, where_excludes_current=False)

        try:
            metadata_rows = source_conn.execute(
                """
                SELECT pack, owner_pack, builder_name, built_at
                FROM truth_projections
                WHERE pack != ?
                ORDER BY pack
                """,
                (exclude_pack,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            error_text = str(exc).lower()
            if "no such table" not in error_text and "no such column" not in error_text:
                raise
            metadata_rows = []
    finally:
        source_conn.close()

    if metadata_rows:
        dest_conn.executemany(
            """
            INSERT INTO truth_projections (pack, owner_pack, builder_name, built_at)
            VALUES (?, ?, ?, ?)
            """,
            metadata_rows,
        )
        preserved_metadata_packs.update(str(row[0]) for row in metadata_rows if row and row[0])

    missing_metadata_packs = sorted(preserved_packs - preserved_metadata_packs)
    if not missing_metadata_packs:
        return

    built_at = _utc_now_text()
    for pack in missing_metadata_packs:
        owner_pack, builder_name = _projection_metadata(pack)
        dest_conn.execute(
            """
            INSERT INTO truth_projections (pack, owner_pack, builder_name, built_at)
            VALUES (?, ?, ?, ?)
            """,
            (pack, owner_pack, builder_name, built_at),
        )


def _split_frontmatter_body(content: str) -> str:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\n")
    return content


def _build_surface_map(metadata_items: list[NoteMetadata]) -> dict[str, str]:
    surfaces: dict[str, str] = {}
    for meta in metadata_items:
        surfaces[canonicalize_note_id(meta.note_id)] = meta.note_id
        surfaces[canonicalize_note_id(meta.title)] = meta.note_id
        for alias in meta.aliases:
            normalized = canonicalize_note_id(str(alias))
            if normalized:
                surfaces[normalized] = meta.note_id
    return surfaces


def _resolve_target_slug(
    raw_target: str, registry: ConceptRegistry, surface_map: dict[str, str]
) -> str | None:
    resolved = registry.resolve_mention(raw_target, include_related_context=False)
    if resolved.action == ResolutionAction.LINK_EXISTING and resolved.entry:
        return resolved.entry.slug

    normalized = canonicalize_note_id(raw_target)
    if normalized in surface_map:
        return surface_map[normalized]
    return None


def _extract_timeline_events(meta: NoteMetadata, body: str) -> list[tuple[str, str, str, str, str]]:
    events: list[tuple[str, str, str, str, str]] = []

    if meta.day_id:
        events.append(
            (
                meta.note_id,
                meta.day_id,
                "page_date",
                "",
                json.dumps({"path": meta.path, "title": meta.title}, ensure_ascii=False),
            )
        )

    heading_date_pattern = re.compile(r"^#{2,3}\s+(\d{4}-\d{2}(?:-\d{2})?)\s*$")
    for line in body.splitlines():
        match = heading_date_pattern.match(line.strip())
        if not match:
            continue
        event_date = match.group(1)
        events.append(
            (
                meta.note_id,
                event_date,
                "heading_date",
                line.strip().lstrip("#").strip(),
                json.dumps({"path": meta.path, "title": meta.title}, ensure_ascii=False),
            )
        )
    return events


def _collect_entity_mention_rows(
    vault_dir: Path,
    link_rows: list[tuple[str, str, str, str, int]],
    known_slugs: set[str],
) -> list[tuple[str, str, str, float, str, str, str]]:
    """Collect entity mentions from two sources:

    1. Wikilinks whose target resolves to an entity in EntityRegistry
    2. Stored LLM extraction results from entity_extractor (JSONL sidecar)
    """
    from .entity_registry import EntityRegistry

    entity_dir = vault_dir / "10-Knowledge" / "Entity"
    if not entity_dir.exists():
        return []

    registry = EntityRegistry(vault_dir).load()
    if len(registry) == 0:
        return []

    entity_slugs = {e.slug for e in registry.all_entries() if e.status in ("active", "candidate")}
    entity_map = {e.slug: e for e in registry.all_entries() if e.slug in entity_slugs}

    rows: list[tuple[str, str, str, float, str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for source_slug, target_slug, target_raw, link_type, _line in link_rows:
        if target_slug not in entity_slugs:
            continue
        key = (target_slug, source_slug)
        if key in seen:
            continue
        seen.add(key)
        entry = entity_map[target_slug]
        rows.append(
            (
                target_slug,
                entry.entity_type,
                source_slug,
                1.0,
                "wikilink",
                target_raw or target_slug,
                "",
            )
        )

    for source_slug in known_slugs:
        for entry in registry.all_entries():
            if entry.slug not in entity_slugs:
                continue
            if entry.slug == source_slug:
                continue
            match = registry.resolve_mention(source_slug)
            if match and match.slug == entry.slug:
                key = (entry.slug, source_slug)
                if key not in seen:
                    seen.add(key)
                    rows.append(
                        (
                            entry.slug,
                            entry.entity_type,
                            source_slug,
                            entry.confidence_avg or 0.8,
                            "alias_match",
                            source_slug,
                            "",
                        )
                    )

    extraction_log = vault_dir / "60-Logs" / "entity-extractions.jsonl"
    if extraction_log.exists():
        try:
            for line in extraction_log.read_text(encoding="utf-8").strip().splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                src = record.get("source_slug", "")
                for m in record.get("mentions", []):
                    e_slug = m.get("resolved_slug", "")
                    if not e_slug or e_slug not in entity_slugs:
                        continue
                    key = (e_slug, src)
                    if key in seen:
                        continue
                    seen.add(key)
                    entry = entity_map.get(e_slug)
                    e_type = entry.entity_type if entry else m.get("kind", "")
                    rows.append(
                        (
                            e_slug,
                            e_type,
                            src,
                            m.get("confidence", 0.8),
                            m.get("resolution", "llm_ner"),
                            m.get("text", ""),
                            m.get("snippet", ""),
                        )
                    )
        except (json.JSONDecodeError, OSError):
            pass

    return rows


def _collect_raw_rows(layout: VaultLayout) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    if not layout.link_resolution_dir.exists():
        return rows

    for sidecar_path in sorted(layout.link_resolution_dir.glob("*.json")):
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        slug = canonicalize_note_id(payload.get("article") or sidecar_path.stem)
        if not slug:
            continue
        rows.append(
            (
                slug,
                "link_resolution",
                json.dumps(payload, ensure_ascii=False),
                str(sidecar_path),
            )
        )
    return rows


def _infer_audit_slug(payload: dict[str, object]) -> str:
    """Value for the ``audit_events.slug`` column.

    M24 audit-identity normalization (PR-B): delegates to the
    shared ``audit_identity.audit_slug_for_column`` so this and the
    lifecycle kernel's index cannot drift.  The new behavior over
    the legacy slug/targets/target_path-only logic: ``source`` /
    ``file`` / ``path`` now produce a non-empty slug column, so the
    ~13k historical source-class events (``source_archived_to_processed``,
    ``article_processed``, ``article_intake_only``,
    ``absorb_route_decision``) finally become visible to source
    lifecycle discovery.  ``target_path`` still returns raw for the
    lint zone-boundary contract.
    """
    return audit_slug_for_column(payload)


def _collect_reuse_rows(
    layout: VaultLayout,
) -> list[tuple[str, str, str, str, str, str, str, int, int, int, str]]:
    rows: list[tuple[str, str, str, str, str, str, str, int, int, int, str]] = []
    seen_event_ids: set[str] = set()
    for payload in iter_for_index(layout, "reuse-events.jsonl"):
        event_id = str(payload.get("event_id") or "")
        if not event_id or event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        rows.append(
            (
                event_id,
                str(payload.get("ts") or ""),
                str(payload.get("pack") or ""),
                str(payload.get("object_id") or ""),
                str(payload.get("object_kind") or ""),
                str(payload.get("surface") or ""),
                str(payload.get("consumer_ref") or ""),
                int(bool(payload.get("evidence_present"))),
                int(bool(payload.get("provenance_clean"))),
                int(bool(payload.get("trusted"))),
                json.dumps(payload, ensure_ascii=False),
            )
        )
    return rows


def _collect_audit_rows(layout: VaultLayout) -> list[tuple[str, str, str, str, str, str]]:
    # BL-108: stream via ``iter_for_index`` instead of
    # ``read_text().splitlines()``.  ``pipeline.jsonl`` is 36k+ rows
    # and growing on the operator vault; the old full-file read
    # spiked RSS during every ``ovp-knowledge-index`` rebuild
    # (memory: audit-rows-streaming-debt, flagged at the M24
    # review).  Same line-by-line pattern ``_collect_reuse_rows``
    # already uses.  Bonus: ``iter_for_index`` skips a malformed
    # line instead of raising, so one corrupt row no longer aborts
    # the whole rebuild.
    rows: list[tuple[str, str, str, str, str, str]] = []
    log_specs = [
        ("pipeline", "pipeline.jsonl"),
        ("refine", "refine-mutations.jsonl"),
        ("review-actions", "review-actions.jsonl"),
    ]
    for source_log, log_name in log_specs:
        for payload in iter_for_index(layout, log_name):
            rows.append(
                (
                    source_log,
                    str(payload.get("event_type") or "unknown"),
                    _infer_audit_slug(payload),
                    str(payload.get("session_id") or ""),
                    # codex #246 P2: ``event_emitter.emit`` writes the
                    # timestamp under ``ts``, while PipelineLogger
                    # writes ``timestamp``.  Fall back to ``ts`` so
                    # date-filtered consumers (/ops/today Activity
                    # zone, /ops/events/audit?date=) see emit-based
                    # rows (community_crystal_synthesized,
                    # promote_concept, candidates_upserted, …)
                    # instead of dropping them on an empty timestamp.
                    str(payload.get("timestamp") or payload.get("ts") or ""),
                    json.dumps(payload, ensure_ascii=False),
                )
            )
    return rows


# A single embedding chunk is hard-capped: an un-sectioned page body
# (no ``## `` headings) would otherwise become one chunk of the whole
# body — observed up to ~903k chars — and be fed whole into the
# embedding backend.  The cap bounds both memory and the embedded text.
_MAX_CHUNK_CHARS = 3000
_CHUNK_OVERLAP_CHARS = 200


def _split_to_cap(text: str, max_chars: int, overlap: int) -> list[str]:
    """Slice *text* into pieces no longer than *max_chars*, each
    overlapping the previous by *overlap* chars so a concept split
    across a boundary still embeds with local context.  Empty / blank
    input yields no pieces."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    step = max(1, max_chars - max(0, overlap))
    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        piece = text[start : start + max_chars]
        if piece.strip():
            pieces.append(piece)
        if start + max_chars >= n:
            break
        start += step
    return pieces


def _chunk_page_body(
    body: str,
    fallback_title: str,
    *,
    max_chunk_chars: int = _MAX_CHUNK_CHARS,
    overlap_chars: int = _CHUNK_OVERLAP_CHARS,
) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            if current_title is not None and "\n".join(current_lines).strip():
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)

    if current_title is not None and "\n".join(current_lines).strip():
        sections.append((current_title, "\n".join(current_lines).strip()))

    if sections:
        raw = sections
    else:
        normalized_body = body.strip()
        if not normalized_body:
            return []
        raw = [(fallback_title, normalized_body)]

    capped: list[tuple[str, str]] = []
    for title, text in raw:
        for piece in _split_to_cap(text, max_chunk_chars, overlap_chars):
            capped.append((title, piece))
    return capped


# Bounded-flush batch sizes for the rebuild loop.  The rebuild must
# not hold every page body + every embedding blob in a Python list at
# once (observed: 10k+ pages, 31k+ chunks, 332MB db) — rows are
# flushed to the DB in capped batches.  Only the (smaller) object-slug
# subset is retained in memory, because the truth-projection builder
# takes it as an in-process argument.
_PAGE_FLUSH_BATCH = 200
_EMBED_FLUSH_BATCH = 128
# Timeline events are flushed independently of the page batch: a
# single page can emit many events (e.g. a long changelog), so a
# page-boundary-only flush could let timeline_batch grow unbounded
# between page flushes.
_TIMELINE_FLUSH_BATCH = 500


def _flush_pages(
    conn: sqlite3.Connection,
    page_batch: list[tuple],
    fts_batch: list[tuple],
) -> int:
    """Insert a page batch into ``pages_index`` + ``page_fts`` and
    clear the batches.  Returns the number of pages flushed."""
    if not page_batch:
        return 0
    conn.executemany(
        """
        INSERT INTO pages_index (slug, title, note_type, path, day_id, frontmatter_json, body)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        page_batch,
    )
    conn.executemany(
        "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
        fts_batch,
    )
    n = len(page_batch)
    page_batch.clear()
    fts_batch.clear()
    return n


def _flush_timeline(conn: sqlite3.Connection, timeline_batch: list[tuple]) -> int:
    if not timeline_batch:
        return 0
    conn.executemany(
        """
        INSERT INTO timeline_events (slug, event_date, event_type, heading, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        timeline_batch,
    )
    n = len(timeline_batch)
    timeline_batch.clear()
    return n


def _flush_embeddings(conn: sqlite3.Connection, embed_batch: list[tuple]) -> int:
    if not embed_batch:
        return 0
    conn.executemany(
        """
        INSERT INTO page_embeddings (slug, chunk_index, section_title, chunk_text, embedding_blob, embedding_model, chunk_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        embed_batch,
    )
    n = len(embed_batch)
    embed_batch.clear()
    return n


def _chunk_embed_hash(embed_input: str) -> str:
    """Stable content hash of the exact text handed to the embedding
    backend (``section_title\\nchunk_text``).  Same text + same model
    ⇒ the prior embedding can be reused verbatim."""
    return hashlib.sha256(embed_input.encode("utf-8")).hexdigest()


class _EmbeddingReuseCache:
    """Bounded reuse of prior embeddings across a rebuild.

    The previous ``knowledge.db`` is still on disk while the temp DB
    is built (the atomic replace happens last).  This opens it
    read-only and looks a stored embedding up by
    ``(chunk_hash, embedding_model)`` through the
    ``idx_page_embeddings_hash`` index — no full-table load, so memory
    stays bounded (the PR2b discipline).  A missing DB / table /
    column makes every lookup miss, so the chunk is recomputed and
    the cache self-heals on the next rebuild.
    """

    def __init__(self, old_db: Path) -> None:
        self._conn: sqlite3.Connection | None = None
        self.hits = 0
        self.misses = 0
        if not old_db.exists():
            return
        try:
            conn = sqlite3.connect(f"file:{old_db}?mode=ro", uri=True)
            cols = {
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(page_embeddings)"
                ).fetchall()
            }
            if "chunk_hash" not in cols:
                conn.close()
                return
            self._conn = conn
        except sqlite3.Error:
            self._conn = None

    def get(self, chunk_hash: str, model: str) -> bytes | None:
        if self._conn is None or not chunk_hash:
            self.misses += 1
            return None
        try:
            row = self._conn.execute(
                "SELECT embedding_blob FROM page_embeddings "
                "WHERE chunk_hash = ? AND embedding_model = ? LIMIT 1",
                (chunk_hash, model),
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return cast("bytes", row[0])

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _embed_text(text: str) -> bytes:
    """Delegate to the semantic embedding backend (Qwen3-Embedding MLX or hash fallback)."""
    return _embed_text_semantic(text)


def _get_embedding_model_name() -> str:
    from .embedding import get_model_name

    return get_model_name()


def _decode_embedding(blob: bytes) -> list[float]:
    decoded = array("f")
    decoded.frombytes(blob)
    return list(decoded)


def _dot_product(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _remove_sqlite_artifacts(db_path: Path) -> None:
    for candidate in (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ):
        if candidate.exists():
            candidate.unlink()


def _initialize_database(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_sqlite_artifacts(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def _ensure_knowledge_db(vault_dir: Path) -> tuple[Path, VaultLayout]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    authority_schema_version = _ensure_authority_schema_version(resolved_vault)
    rebuild_reason = ""
    projection_schema_version = 0
    if not layout.knowledge_db.exists():
        rebuild_reason = "knowledge_db_missing"
    elif not _knowledge_db_supports_pack_schema(layout.knowledge_db):
        rebuild_reason = "knowledge_db_schema_incompatible"
    else:
        projection_metadata = _read_knowledge_db_projection_metadata(layout.knowledge_db)
        if projection_metadata is None:
            rebuild_reason = "knowledge_db_projection_metadata_missing"
        else:
            built_authority_schema_version, projection_schema_version = projection_metadata
            if authority_schema_version > built_authority_schema_version:
                rebuild_reason = "authority_schema_version_newer_than_projection"
            elif KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION > projection_schema_version:
                rebuild_reason = "projection_schema_version_newer_than_metadata"

    if not rebuild_reason:
        return resolved_vault, layout

    # Try the delta-migration fast path for projection_schema_version
    # bumps where every step has a registered ADDITIVE / RECOMPUTE
    # migration.  Authority-schema bumps + missing-DB + schema-
    # incompatible cases keep going through the slow rebuild.
    if rebuild_reason == "projection_schema_version_newer_than_metadata":
        steps, missing = _plan_schema_upgrade(
            from_version=projection_schema_version,
            to_version=KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
        )
        if not missing and _can_delta_migrate(steps):
            # ``metadata_only`` is the closest match in the existing
            # ProjectionRepairKind vocabulary (Literal in
            # projection_lifecycle.py:22).  Codex P2 — an unknown
            # ``kind`` is silently dropped by
            # ``ProjectionRepairMarker.from_dict``, so doctor +
            # close_projection_repair_marker would never see this
            # marker; the close call would silently no-op and the
            # marker would replay on every subsequent start.
            marker = write_projection_repair_marker(
                resolved_vault,
                kind="metadata_only",
                scope={"projection_kind": KNOWLEDGE_DB_PROJECTION_KIND},
                reason=rebuild_reason,
                caused_by="ensure_knowledge_db_current",
                authority_schema_version=authority_schema_version,
                projection_schema_version=projection_schema_version,
            )
            try:
                _run_delta_migrations(
                    resolved_vault,
                    db_path=layout.knowledge_db,
                    steps=steps,
                    authority_schema_version=authority_schema_version,
                )
            finally:
                close_projection_repair_marker(resolved_vault, marker.marker_id)
            return resolved_vault, layout
        logger.info(
            "knowledge.db delta-migration path unavailable for "
            "version range %d → %d (missing=%s, breaking=%s); "
            "falling through to full rebuild.",
            projection_schema_version,
            KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
            missing,
            [s.from_version for s in steps if s.kind == SchemaMigrationKind.BREAKING],
        )

    marker = write_projection_repair_marker(
        resolved_vault,
        kind="full_rebuild",
        scope={"projection_kind": KNOWLEDGE_DB_PROJECTION_KIND},
        reason=rebuild_reason,
        caused_by="ensure_knowledge_db_current",
        authority_schema_version=authority_schema_version,
        projection_schema_version=projection_schema_version,
    )
    rebuild_knowledge_index(resolved_vault)
    close_projection_repair_marker(resolved_vault, marker.marker_id)
    return resolved_vault, layout


def _run_delta_migrations(
    vault_dir: Path,
    *,
    db_path: Path,
    steps: list[SchemaMigration],
    authority_schema_version: int,
) -> None:
    """Run an ordered list of additive/recompute migrations.

    **Atomicity contract**: each individual migration runner is
    expected to be **idempotent** (``CREATE TABLE IF NOT EXISTS``,
    ``ALTER TABLE … ADD COLUMN`` guarded by a ``PRAGMA table_info``
    check, etc.) because Python's ``sqlite3.Connection.executescript``
    implicitly commits any pending transaction before running its
    body — so even if we open ``BEGIN`` here it would be silently
    discarded by the first runner that uses ``executescript`` (codex
    + CodeRabbit High).  We therefore do not wrap the runners in a
    transaction; partial failures fall back to "operator re-runs the
    migration" and the idempotent runners pick up where they stopped.

    The ``projection_metadata`` bump itself is a single ``INSERT OR
    REPLACE`` and runs after every migration step succeeded, so a
    half-run upgrade leaves the version at the *old* value and the
    next start re-attempts cleanly.

    Connection is opened with ``contextlib.closing`` because
    ``sqlite3.connect`` as a context manager commits on exit but
    does NOT close the file descriptor (CodeRabbit M).
    """
    target = KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION
    with knowledge_db_write_lock(vault_dir):
        with closing(sqlite3.connect(db_path)) as conn:
            for step in steps:
                logger.info(
                    "knowledge.db migrating %d → %d (%s, %s)",
                    step.from_version,
                    step.from_version + 1,
                    step.kind.value,
                    step.reason,
                )
                step.runner(conn, vault_dir)
            conn.execute(
                """
                INSERT OR REPLACE INTO projection_metadata (
                    projection_kind, authority_schema_version,
                    projection_schema_version, built_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    KNOWLEDGE_DB_PROJECTION_KIND,
                    authority_schema_version,
                    target,
                    _utc_now_text(),
                ),
            )
            conn.commit()


def _knowledge_db_supports_pack_schema(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    required_columns = {
        "timeline_events": {"slug", "event_date", "event_type", "heading", "payload_json"},
        "objects": {"pack"},
        "claims": {"pack"},
        "claim_evidence": {
            "pack",
            "locator",
            "content_hash",
            "quote_start_line",
            "quote_end_line",
            "quote_start_char",
            "quote_end_char",
            "status",
            "verified_at",
        },
        "relations": {
            "pack",
            "quote_text",
            "locator",
            "content_hash",
            "quote_start_line",
            "quote_end_line",
            "quote_start_char",
            "quote_end_char",
            "status",
            "verified_at",
        },
        "compiled_summaries": {"pack"},
        "contradictions": {"pack"},
        "graph_edges": {"pack"},
        "graph_clusters": {"pack"},
        "truth_projections": {"pack", "owner_pack", "builder_name", "built_at"},
        "reuse_events": {"event_id", "ts", "pack", "surface", "trusted"},
        "page_metrics": {"slug", "last_seen_ts", "reuse_count", "citation_count"},
        "projection_metadata": {
            "projection_kind",
            "authority_schema_version",
            "projection_schema_version",
            "built_at",
        },
        "entity_mentions": {
            "entity_slug",
            "entity_type",
            "source_slug",
            "confidence",
            "detection_method",
        },
        # NOTE: do NOT add NEW additive tables (BL-085 chats,
        # future projections) to this map.  This is the compat
        # gate that triggers a *full rebuild* — additive changes
        # belong in ``SCHEMA_MIGRATIONS`` so existing vaults pay
        # seconds, not minutes.  Only column-level requirements
        # on *existing* tables earn an entry here.
    }
    try:
        with sqlite3.connect(db_path) as conn:
            for table_name, expected_columns in required_columns.items():
                rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                if not rows:
                    return False
                existing_columns = {str(row[1]) for row in rows}
                if not expected_columns.issubset(existing_columns):
                    return False
    except sqlite3.DatabaseError:
        return False
    return True


def ensure_knowledge_db_current(vault_dir: Path | str) -> Path:
    _, layout = _ensure_knowledge_db(resolve_vault_dir(vault_dir))
    return layout.knowledge_db


def _read_jsonl_items(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    items: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def _latest_contradiction_review_overrides(vault_dir: Path) -> dict[str, dict[str, str]]:
    layout = VaultLayout.from_vault(vault_dir)
    items = sorted(
        [
            item
            for item in _read_jsonl_items(layout.logs_dir / "review-actions.jsonl")
            if item.get("event_type") == "ui_contradictions_resolved"
        ],
        key=lambda item: str(item.get("timestamp") or ""),
    )
    overrides: dict[str, dict[str, str]] = {}
    for item in items:
        status = str(item.get("status") or "")
        note = str(item.get("note") or "")
        resolved_at = str(item.get("timestamp") or "")
        for contradiction_id in item.get("contradiction_ids", []) or []:
            contradiction_key = str(contradiction_id or "")
            if contradiction_key:
                overrides[contradiction_key] = {
                    "status": status,
                    "resolution_note": note,
                    "resolved_at": resolved_at,
                }
    return overrides


def rebuild_knowledge_index(
    vault_dir: Path,
    *,
    pack_name: str | None = None,
) -> dict[str, int | str]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    truth_pack = _truth_pack_name(pack_name)
    authority_schema_version = _ensure_authority_schema_version(resolved_vault)

    # Detect embedding-backend mismatch with existing page_embeddings rows.
    # A rebuild rewrites every row (so post-rebuild is consistent regardless),
    # but warning surfaces the implicit migration to operators.
    if layout.knowledge_db.exists():
        try:
            with sqlite3.connect(layout.knowledge_db) as _check_conn:
                row = _check_conn.execute(
                    "SELECT embedding_model, length(embedding_blob) " "FROM page_embeddings LIMIT 1"
                ).fetchone()
            if row is not None:
                stored_model = row[0] or ""
                # Each float32 takes 4 bytes; embedding_blob length / 4 = dim
                stored_dim = (row[1] or 0) // 4
                ok, msg = _assert_embedding_consistent(stored_model, stored_dim)
                if not ok:
                    logger.warning("[rebuild] %s", msg)
        except sqlite3.OperationalError:
            # page_embeddings doesn't exist yet — first-time rebuild
            pass

    with knowledge_db_write_lock(resolved_vault):
        evergreen_dir = layout.evergreen_dir
        atlas_dir = layout.atlas_dir
        areas_dir = resolved_vault / "20-Areas"
        parser = FrontmatterParser(resolved_vault)
        link_parser = LinkParser(resolved_vault)
        registry = ConceptRegistry(resolved_vault).load()

        object_metadata_items: list[NoteMetadata] = []
        entity_dir = resolved_vault / "10-Knowledge" / "Entity"
        if entity_dir.exists():
            for meta in parser.parse_directory(entity_dir, recursive=True):
                if "_Candidates" not in Path(meta.path).parts:
                    object_metadata_items.append(meta)
        for meta in parser.parse_directory(evergreen_dir, recursive=True):
            if "_Candidates" not in Path(meta.path).parts:
                object_metadata_items.append(meta)
        page_metadata_items = list(object_metadata_items)
        for extra_dir in (atlas_dir, areas_dir):
            if not extra_dir.exists():
                continue
            for meta in parser.parse_directory(extra_dir, recursive=True):
                if "_Candidates" in Path(meta.path).parts:
                    continue
                page_metadata_items.append(meta)

        deduped_page_metadata_items: list[NoteMetadata] = []
        seen_page_keys: set[str] = set()
        for meta in page_metadata_items:
            key = meta.note_id
            if key in seen_page_keys:
                continue
            seen_page_keys.add(key)
            deduped_page_metadata_items.append(meta)

        surface_map = _build_surface_map(object_metadata_items)
        known_slugs = {meta.note_id for meta in object_metadata_items}

        temp_db_path = layout.knowledge_db.with_name(f"{layout.knowledge_db.name}.tmp")
        _remove_sqlite_artifacts(temp_db_path)

        conn = None
        reuse_cache: _EmbeddingReuseCache | None = None
        try:
            conn = _initialize_database(temp_db_path)
            # BL-054: source_authority lives in its own module — make
            # sure its schema lands and replay the JSONL audit log so
            # ``credibility_norm`` has data to read.  Without this the
            # table is wiped on every rebuild and crystal_scoring's
            # credibility lookup silently returns 0 for every row.
            from .source_authority import (
                ensure_schema as _ensure_source_authority_schema,
                replay_authority_log as _replay_source_authority_log,
            )

            _ensure_source_authority_schema(conn)
            authority_log = layout.logs_dir / "source_authority.jsonl"
            authority_rows = _replay_source_authority_log(conn, authority_log)
            if authority_rows:
                logger.debug(
                    "source_authority replayed %d rows from %s",
                    authority_rows,
                    authority_log,
                )
            _preserve_existing_truth_rows(layout.knowledge_db, conn, exclude_pack=truth_pack)
            # Bounded rebuild: stream pages / FTS / timeline / embeddings
            # to the DB in capped batches instead of accumulating every
            # body and every embedding blob in Python lists.  Only the
            # object-slug subset (``object_page_rows``) is retained,
            # because the truth-projection builder consumes it as an
            # in-process argument (see the ``execute_truth_projection_builder``
            # call below).  This is the PR2b memory-safety floor; the
            # second full-body FTS list and the full ``embedding_rows``
            # accumulation are gone.
            page_batch: list[tuple] = []
            fts_batch: list[tuple] = []
            timeline_batch: list[tuple] = []
            embed_batch: list[tuple] = []
            object_page_rows: list[tuple] = []
            pages_indexed = 0
            timeline_events_indexed = 0
            embedding_chunks_indexed = 0
            # PR3: reuse unchanged embeddings from the prior DB (still
            # on disk until the atomic replace) instead of re-running
            # the embedding backend for every chunk every rebuild.
            reuse_cache = _EmbeddingReuseCache(layout.knowledge_db)
            for meta in deduped_page_metadata_items:
                file_path = Path(meta.path)
                body = _split_frontmatter_body(file_path.read_text(encoding="utf-8"))
                page_row = (
                    meta.note_id,
                    meta.title,
                    meta.note_type,
                    str(file_path),
                    meta.day_id,
                    json.dumps(meta.to_dict(), ensure_ascii=False),
                    body,
                )
                page_batch.append(page_row)
                fts_batch.append((meta.note_id, meta.title, body))
                if meta.note_id in known_slugs:
                    object_page_rows.append(page_row)
                timeline_batch.extend(_extract_timeline_events(meta, body))
                if len(timeline_batch) >= _TIMELINE_FLUSH_BATCH:
                    timeline_events_indexed += _flush_timeline(conn, timeline_batch)
                for chunk_index, (section_title, chunk_text) in enumerate(
                    _chunk_page_body(body, meta.title)
                ):
                    embed_input = f"{section_title}\n{chunk_text}"
                    chunk_hash = _chunk_embed_hash(embed_input)
                    model_name = get_model_name()
                    blob = reuse_cache.get(chunk_hash, model_name)
                    if blob is None:
                        blob = _embed_text(embed_input)
                    embed_batch.append(
                        (
                            meta.note_id,
                            chunk_index,
                            section_title,
                            chunk_text,
                            blob,
                            model_name,
                            chunk_hash,
                        )
                    )
                    if len(embed_batch) >= _EMBED_FLUSH_BATCH:
                        embedding_chunks_indexed += _flush_embeddings(conn, embed_batch)
                if len(page_batch) >= _PAGE_FLUSH_BATCH:
                    pages_indexed += _flush_pages(conn, page_batch, fts_batch)
                    timeline_events_indexed += _flush_timeline(conn, timeline_batch)

            pages_indexed += _flush_pages(conn, page_batch, fts_batch)
            timeline_events_indexed += _flush_timeline(conn, timeline_batch)
            embedding_chunks_indexed += _flush_embeddings(conn, embed_batch)
            embedding_chunks_reused = reuse_cache.hits
            reuse_cache.close()

            link_rows = []
            for meta in deduped_page_metadata_items:
                file_path = Path(meta.path)
                for link in link_parser.parse_file(file_path):
                    target_slug = _resolve_target_slug(
                        link.target_raw or link.target, registry, surface_map
                    )
                    if not target_slug or target_slug not in known_slugs:
                        continue
                    link_rows.append(
                        (
                            link.source,
                            target_slug,
                            link.target_raw,
                            link.link_type,
                            link.line_number,
                        )
                    )

            conn.executemany(
                """
                INSERT INTO page_links (source_slug, target_slug, target_raw, link_type, line_number)
                VALUES (?, ?, ?, ?, ?)
                """,
                link_rows,
            )

            # object_page_rows was retained incrementally during the
            # bounded page loop above (slug ∈ known_slugs).
            object_link_rows = [row for row in link_rows if row[0] in known_slugs]
            projection_spec, truth_projection = execute_truth_projection_builder(
                vault_dir=resolved_vault,
                page_rows=object_page_rows,
                link_rows=object_link_rows,
                pack_name=pack_name,
            )
            # BL-060: writes go through the canonical owner modules
            # (``truth_store_writers``, ``provenance``, ``relation_writer``)
            # so the single-writer invariant for canonical tables holds.
            # See ``docs/canonical-write-ownership.md``.
            insert_objects(
                conn,
                (row.to_row() for row in truth_projection.objects),
            )
            # BL-055: provenance spine.  Write one ``stage='ingest'``
            # row per object that has a source_url — but only when no
            # such row already exists for this (pack, object_id,
            # source_url) tuple.  Preservation in
            # ``INDEPENDENT_CANONICAL_TABLE_COLUMNS`` carries every
            # historical row across rebuilds (gemini PR #152 review
            # fix); the dedup guard inside ``bulk_upsert_provenance_ingest``
            # keeps rebuild-noise from accumulating duplicate ingest
            # rows for objects whose source URL hasn't changed.
            now_iso = _utc_now_text()
            bulk_upsert_provenance_ingest(
                conn,
                [
                    {
                        "pack": row.pack,
                        "object_id": row.object_id,
                        "source_url": row.source_url,
                        "source_fingerprint": _source_fingerprint(row.source_url),
                        "derived_at": now_iso,
                        "metadata_json": "{}",
                    }
                    for row in truth_projection.objects
                    if row.source_url
                ],
            )
            insert_claims(
                conn,
                (row.to_row() for row in truth_projection.claims),
            )
            conn.executemany(
                """
                INSERT INTO claim_evidence (
                    pack, claim_id, source_slug, evidence_kind, quote_text,
                    locator, content_hash, retrieval_context,
                    quote_start_line, quote_end_line, quote_start_char, quote_end_char,
                    status, verified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.claim_evidence],
            )
            bulk_insert_relations(
                conn,
                (row.to_row() for row in truth_projection.relations),
            )
            conn.executemany(
                """
                INSERT INTO compiled_summaries (pack, object_id, summary_text, source_slug)
                VALUES (?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.compiled_summaries],
            )
            conn.executemany(
                """
                INSERT INTO contradictions (
                    pack,
                    contradiction_id,
                    subject_key,
                    positive_claim_ids_json,
                    negative_claim_ids_json,
                    status,
                    resolution_note,
                    resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.contradictions],
            )
            conn.executemany(
                """
                INSERT INTO graph_edges (
                    pack,
                    edge_id,
                    source_object_id,
                    target_object_id,
                    edge_kind,
                    weight,
                    evidence_source_slug
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.graph_edges],
            )
            conn.executemany(
                """
                INSERT INTO graph_clusters (
                    pack,
                    cluster_id,
                    cluster_kind,
                    label,
                    center_object_id,
                    member_object_ids_json,
                    score
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.graph_clusters],
            )
            conn.execute(
                """
                INSERT INTO truth_projections (pack, owner_pack, builder_name, built_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    truth_pack,
                    projection_spec.pack,
                    getattr(projection_spec, "name", ""),
                    _utc_now_text(),
                ),
            )
            conn.execute(
                """
                INSERT INTO projection_metadata (
                    projection_kind,
                    authority_schema_version,
                    projection_schema_version,
                    built_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    KNOWLEDGE_DB_PROJECTION_KIND,
                    authority_schema_version,
                    KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
                    _utc_now_text(),
                ),
            )

            raw_rows = _collect_raw_rows(layout)
            conn.executemany(
                """
                INSERT INTO raw_data (slug, source_name, payload_json, source_path)
                VALUES (?, ?, ?, ?)
                """,
                raw_rows,
            )

            # timeline_events were streamed in the bounded page loop.

            audit_rows = _collect_audit_rows(layout)
            conn.executemany(
                """
                INSERT INTO audit_events (source_log, event_type, slug, session_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                audit_rows,
            )
            reuse_rows = _collect_reuse_rows(layout)
            conn.executemany(
                """
                INSERT INTO reuse_events (
                    event_id, ts, pack, object_id, object_kind, surface,
                    consumer_ref, evidence_present, provenance_clean, trusted,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                reuse_rows,
            )
            # page_embeddings were streamed in the bounded page loop.

            entity_mention_rows = _collect_entity_mention_rows(
                resolved_vault, link_rows, known_slugs
            )
            conn.executemany(
                """
                INSERT INTO entity_mentions (
                    entity_slug, entity_type, source_slug,
                    confidence, detection_method, mention_text, snippet
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                entity_mention_rows,
            )

            from .relation_promotion import replay_relation_promotions
            from .evidence_replay import replay_evidence_verifications

            relations_replayed = replay_relation_promotions(conn, layout, pack_name=truth_pack)
            evidence_updates = replay_evidence_verifications(conn, layout, pack_name=truth_pack)

            page_metrics_indexed = _rebuild_page_metrics(conn)

            # M14 BL-045: rebuild crystal_scores Projection.  Lazy
            # import to avoid a sqlite3-only module pulling synthesis
            # dependencies into knowledge_index startup.  No-op when
            # there are no community/contradiction crystals yet.
            try:
                from .synthesis.crystal_scoring import rebuild_crystal_scores

                rebuild_crystal_scores(
                    conn,
                    vault_dir=layout.vault_dir,
                    pack=truth_pack,
                )
            except Exception as exc:
                # Score rebuild is best-effort — never block the
                # primary index rebuild on a scoring failure.  The
                # next ``ovp-rescore-crystals`` run will catch up.
                logger.warning(
                    "crystal_scores rebuild skipped: %s",
                    exc,
                )

            # M14 BL-047: append crystal bodies to page_fts so the
            # existing ``/search`` Access Surface returns crystals
            # alongside evergreen pages.  Best-effort — same
            # rationale as the scoring rebuild.
            try:
                from .synthesis.crystal_fts import index_crystals_into_page_fts

                n_fts = index_crystals_into_page_fts(conn, pack=truth_pack)
                if n_fts:
                    logger.debug(
                        "indexed %d crystal bodies into page_fts",
                        n_fts,
                    )
            except Exception as exc:
                logger.warning(
                    "crystal page_fts indexing skipped: %s",
                    exc,
                )

            # M21 BL-085: rebuild chats projection.  Indexed sessions
            # also land in pages_index + page_fts so /search finds
            # them; unindexed sessions get a chats row only.  Best-
            # effort — never block the primary index rebuild.
            try:
                from .chats_projection import rebuild_chats_projection

                chats_counts = rebuild_chats_projection(
                    conn,
                    vault_dir=layout.vault_dir,
                )
                if chats_counts.get("total"):
                    logger.debug(
                        "chats projection rebuilt: %s",
                        chats_counts,
                    )
            except Exception as exc:
                logger.warning(
                    "chats projection rebuild skipped: %s",
                    exc,
                )

            conn.commit()
        except Exception:
            if reuse_cache is not None:
                reuse_cache.close()
            if conn is not None:
                conn.close()
            _remove_sqlite_artifacts(temp_db_path)
            raise
        else:
            assert conn is not None
            conn.close()
            _remove_sqlite_artifacts(layout.knowledge_db)
            temp_db_path.replace(layout.knowledge_db)

            return {
                "db_path": str(layout.knowledge_db),
                "projection_pack": truth_pack,
                "pages_indexed": pages_indexed,
                "links_indexed": len(link_rows),
                "raw_records_indexed": len(raw_rows),
                "timeline_events_indexed": timeline_events_indexed,
                "audit_events_indexed": len(audit_rows),
                "reuse_events_indexed": len(reuse_rows),
                "embedding_chunks_indexed": embedding_chunks_indexed,
                "embedding_chunks_reused": embedding_chunks_reused,
                "objects_indexed": len(truth_projection.objects),
                "claims_indexed": len(truth_projection.claims),
                "relations_indexed": len(truth_projection.relations),
                "relations_replayed": relations_replayed,
                "evidence_updates_replayed": evidence_updates,
                "page_metrics_indexed": page_metrics_indexed,
                "compiled_summaries_indexed": len(truth_projection.compiled_summaries),
                "contradictions_indexed": len(truth_projection.contradictions),
                "graph_edges_indexed": len(truth_projection.graph_edges),
                "graph_clusters_indexed": len(truth_projection.graph_clusters),
                "entity_mentions_indexed": len(entity_mention_rows),
            }


def _iso_to_epoch(value: object) -> int:
    """Best-effort ISO-8601 → unix epoch. Returns 0 for unparseable inputs.

    Both ``audit_events.timestamp`` and ``reuse_events.ts`` are stored as
    text; we want a single integer to drive the recency decay in
    ``search_fused``. Stripping a trailing ``Z`` makes ``fromisoformat`` happy
    on Python <3.11.
    """
    if not value:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def _rebuild_page_metrics(conn: sqlite3.Connection) -> int:
    """Aggregate per-slug recency / reuse / citation signals into ``page_metrics``.

    Called from ``rebuild_knowledge_index`` after ``audit_events``,
    ``reuse_events``, and ``page_links`` have been populated. The table is
    consumed by ``search_fused`` to apply bi-temporal decay on top of RRF.

    ``last_seen_ts`` is the max of any audit-event timestamp on this slug and
    any reuse-event timestamp where ``object_id == slug``. ``reuse_count``
    counts reuse events; ``citation_count`` counts inbound wikilinks. Slugs
    with no signal still get a row (zeros) so callers can left-join freely.
    """
    conn.execute("DELETE FROM page_metrics")

    audit_max: dict[str, int] = {}
    for slug, ts in conn.execute(
        "SELECT slug, MAX(timestamp) FROM audit_events WHERE slug != '' GROUP BY slug"
    ):
        audit_max[str(slug)] = _iso_to_epoch(ts)

    reuse_stats: dict[str, tuple[int, int]] = {}
    for object_id, max_ts, count in conn.execute(
        "SELECT object_id, MAX(ts), COUNT(*) FROM reuse_events "
        "WHERE object_id != '' GROUP BY object_id"
    ):
        reuse_stats[str(object_id)] = (_iso_to_epoch(max_ts), int(count or 0))

    citations: dict[str, int] = {}
    for target_slug, count in conn.execute(
        "SELECT target_slug, COUNT(*) FROM page_links "
        "WHERE target_slug != '' GROUP BY target_slug"
    ):
        citations[str(target_slug)] = int(count or 0)

    rows: list[tuple[str, int, int, int]] = []
    seen: set[str] = set()
    for (slug,) in conn.execute("SELECT slug FROM pages_index"):
        slug = str(slug)
        seen.add(slug)
        audit_ts = audit_max.get(slug, 0)
        reuse_ts, reuse_count = reuse_stats.get(slug, (0, 0))
        last_seen = max(audit_ts, reuse_ts)
        rows.append((slug, last_seen, reuse_count, citations.get(slug, 0)))

    # Cover slugs that only appear via reuse / link tables (e.g. an object_id
    # that doesn't map to a current page yet) so the metrics view is complete.
    for slug in set(reuse_stats) | set(citations) | set(audit_max):
        if slug in seen:
            continue
        audit_ts = audit_max.get(slug, 0)
        reuse_ts, reuse_count = reuse_stats.get(slug, (0, 0))
        rows.append((slug, max(audit_ts, reuse_ts), reuse_count, citations.get(slug, 0)))

    conn.executemany(
        "INSERT OR REPLACE INTO page_metrics "
        "(slug, last_seen_ts, reuse_count, citation_count) VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def query_knowledge_index(
    vault_dir: Path, query: str, limit: int = 5
) -> list[dict[str, str | int | float]]:
    _, layout = _ensure_knowledge_db(vault_dir)

    query_vector = _decode_embedding(_embed_text(query))
    current_model = _get_embedding_model_name()
    query_dim = len(query_vector)
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT slug, chunk_index, section_title, chunk_text, embedding_blob
            FROM page_embeddings
            WHERE embedding_model = ?
            """,
            (current_model,),
        ).fetchall()

    scored = []
    for slug, chunk_index, section_title, chunk_text, embedding_blob in rows:
        stored_vector = _decode_embedding(embedding_blob)
        if len(stored_vector) != query_dim:
            continue
        score = _dot_product(query_vector, stored_vector)
        scored.append(
            {
                "slug": slug,
                "chunk_index": chunk_index,
                "section_title": section_title,
                "chunk_text": chunk_text,
                "score": score,
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def search_knowledge_index(
    vault_dir: Path, query: str, limit: int = 10
) -> list[dict[str, str | float]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    with sqlite3.connect(layout.knowledge_db) as conn:
        matched_rows = conn.execute(
            """
            SELECT slug, title, bm25(page_fts) AS score
            FROM page_fts
            WHERE page_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        matched_slugs = {row[0] for row in matched_rows}
        remaining = max(limit - len(matched_rows), 0)
        fallback_rows = []
        if remaining:
            if matched_slugs:
                placeholders = ",".join("?" for _ in matched_slugs)
                fallback_rows = conn.execute(
                    f"""
                    SELECT slug, title
                    FROM pages_index
                    WHERE slug NOT IN ({placeholders})
                    ORDER BY title
                    LIMIT ?
                    """,
                    (*matched_slugs, remaining),
                ).fetchall()
            else:
                fallback_rows = conn.execute(
                    """
                    SELECT slug, title
                    FROM pages_index
                    ORDER BY title
                    LIMIT ?
                    """,
                    (remaining,),
                ).fetchall()

    results = [
        {
            "slug": slug,
            "title": title,
            "score": float(-score),
        }
        for slug, title, score in matched_rows
    ]
    results.extend(
        {
            "slug": slug,
            "title": title,
            "score": 0.0,
        }
        for slug, title in fallback_rows
    )
    return results


def search_fused(
    vault_dir: Path,
    query: str,
    *,
    limit: int = 10,
    rrf_k: int = 60,
    tau_days: float = 30.0,
    now_ts: int | None = None,
) -> list[dict[str, str | float]]:
    """Hybrid retrieval: RRF over BM25 + vector, then bi-temporal decay.

    The two existing retrievers (``search_knowledge_index`` BM25 and
    ``query_knowledge_index`` vector) each emit a ranked list. We fuse them
    with reciprocal rank fusion — robust to per-engine score scale
    differences — then multiply by:

    * **Recency**: ``exp(-(now - last_seen_ts) / (tau_days * 86400))``
    * **Frequency**: ``1 + log(1 + reuse_count)``
    * **Importance**: ``1 + log(1 + citation_count)``

    Slugs missing from ``page_metrics`` get a 1.0 multiplier (no penalty), so
    a cold vault still returns its BM25/vector top-N. We over-fetch each
    branch by 3x ``limit`` so the fused ranking has room to reorder.
    """
    import math

    _, layout = _ensure_knowledge_db(vault_dir)

    fetch = max(limit * 3, limit)
    try:
        bm25_results = search_knowledge_index(vault_dir, query, limit=fetch)
    except sqlite3.OperationalError:
        normalized_terms = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
        if normalized_terms:
            safe_query = " ".join(f'"{term}"' for term in normalized_terms)
            bm25_results = search_knowledge_index(vault_dir, safe_query, limit=fetch)
        else:
            bm25_results = []
    vector_chunks = query_knowledge_index(vault_dir, query, limit=fetch)

    rrf_scores: dict[str, float] = {}
    titles: dict[str, str] = {}

    for rank, item in enumerate(bm25_results):
        slug = str(item.get("slug") or "")
        if not slug:
            continue
        rrf_scores[slug] = rrf_scores.get(slug, 0.0) + 1.0 / (rrf_k + rank + 1)
        if item.get("title"):
            titles.setdefault(slug, str(item["title"]))

    seen_vector: set[str] = set()
    vector_rank = 0
    for chunk in vector_chunks:
        slug = str(chunk.get("slug") or "")
        if not slug or slug in seen_vector:
            continue
        seen_vector.add(slug)
        rrf_scores[slug] = rrf_scores.get(slug, 0.0) + 1.0 / (rrf_k + vector_rank + 1)
        vector_rank += 1

    if not rrf_scores:
        return []

    if now_ts is None:
        from datetime import datetime, timezone

        now_ts = int(datetime.now(timezone.utc).timestamp())
    tau_seconds = max(tau_days * 86400.0, 1.0)

    metrics: dict[str, tuple[int, int, int]] = {}
    titles_db: dict[str, str] = {}
    with sqlite3.connect(layout.knowledge_db) as conn:
        placeholders = ",".join("?" for _ in rrf_scores)
        for slug, last_seen_ts, reuse_count, citation_count in conn.execute(
            f"SELECT slug, last_seen_ts, reuse_count, citation_count "
            f"FROM page_metrics WHERE slug IN ({placeholders})",
            list(rrf_scores.keys()),
        ):
            metrics[str(slug)] = (
                int(last_seen_ts or 0),
                int(reuse_count or 0),
                int(citation_count or 0),
            )
        missing_titles = [s for s in rrf_scores if s not in titles]
        if missing_titles:
            placeholders = ",".join("?" for _ in missing_titles)
            for slug, title in conn.execute(
                f"SELECT slug, title FROM pages_index WHERE slug IN ({placeholders})",
                missing_titles,
            ):
                titles_db[str(slug)] = str(title or "")

    fused: list[dict[str, str | float]] = []
    for slug, base in rrf_scores.items():
        last_seen_ts, reuse_count, citation_count = metrics.get(slug, (0, 0, 0))
        if last_seen_ts > 0:
            recency = math.exp(-max(now_ts - last_seen_ts, 0) / tau_seconds)
        else:
            recency = 1.0
        frequency = 1.0 + math.log(1.0 + reuse_count)
        importance = 1.0 + math.log(1.0 + citation_count)
        fused.append(
            {
                "slug": slug,
                "title": titles.get(slug) or titles_db.get(slug) or slug,
                "score": base * recency * frequency * importance,
                "rrf_score": base,
                "recency": recency,
                "frequency": frequency,
                "importance": importance,
            }
        )

    fused.sort(key=lambda item: item["score"], reverse=True)
    return fused[:limit]


def search_truth_store(
    vault_dir: Path,
    query: str,
    limit: int = 10,
    *,
    pack_name: str | None = None,
) -> list[dict[str, object]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    like_query = f"%{query.strip()}%"
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT claims.object_id, objects.title, claims.claim_kind, claims.claim_text, compiled_summaries.summary_text
            FROM claims
            JOIN objects ON objects.pack = claims.pack AND objects.object_id = claims.object_id
            LEFT JOIN compiled_summaries
              ON compiled_summaries.pack = claims.pack
             AND compiled_summaries.object_id = claims.object_id
            WHERE claims.pack = ?
              AND (claims.claim_text LIKE ? OR compiled_summaries.summary_text LIKE ? OR objects.title LIKE ?)
            ORDER BY claims.object_id
            LIMIT ?
            """,
            (truth_pack, like_query, like_query, like_query, limit),
        ).fetchall()

    return [
        {
            "object_id": row[0],
            "title": row[1],
            "claim_kind": row[2],
            "claim_text": row[3],
            "summary_text": row[4] or "",
        }
        for row in rows
    ]


def list_contradictions(
    vault_dir: Path,
    limit: int = 20,
    subject: str | None = None,
    *,
    pack_name: str | None = None,
) -> list[dict[str, object]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    query = """
        SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
        FROM contradictions
    """
    params: tuple[object, ...]
    if subject:
        query += " WHERE pack = ? AND subject_key LIKE ?"
        params = (truth_pack, f"%{subject}%", limit)
    else:
        query += " WHERE pack = ?"
        params = (truth_pack, limit)
    query += " ORDER BY subject_key LIMIT ?"

    try:
        with sqlite3.connect(layout.knowledge_db) as conn:
            rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    overrides = _latest_contradiction_review_overrides(vault_dir)
    items = []
    for row in rows:
        contradiction_id = str(row[0])
        item = {
            "contradiction_id": contradiction_id,
            "subject_key": row[1],
            "positive_claim_ids": json.loads(row[2]),
            "negative_claim_ids": json.loads(row[3]),
            "status": row[4],
            "resolution_note": row[5] or "",
            "resolved_at": row[6] or "",
        }
        override = overrides.get(contradiction_id)
        if override:
            item["status"] = override["status"]
            item["resolution_note"] = override["resolution_note"]
            item["resolved_at"] = override["resolved_at"]
        items.append(item)
    return items


def resolve_contradictions(
    vault_dir: Path,
    contradiction_ids: list[str],
    *,
    status: str,
    note: str = "",
    pack_name: str | None = None,
) -> dict[str, object]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    resolved_ids = list(dict.fromkeys(contradiction_ids))
    if not resolved_ids:
        return {
            "resolved_count": 0,
            "contradiction_ids": [],
            "status": status,
            "resolution_note": note,
            "db_path": str(layout.knowledge_db),
        }

    placeholders = ",".join("?" for _ in resolved_ids)
    with sqlite3.connect(layout.knowledge_db) as conn:
        existing = conn.execute(
            f"""
            SELECT contradiction_id
            FROM contradictions
            WHERE pack = ? AND contradiction_id IN ({placeholders})
            ORDER BY contradiction_id
            """,
            (truth_pack, *resolved_ids),
        ).fetchall()
        found_ids = [row[0] for row in existing]

    return {
        "resolved_count": len(found_ids),
        "contradiction_ids": found_ids,
        "status": status,
        "resolution_note": note,
        "db_path": str(layout.knowledge_db),
    }


def contradiction_object_ids(
    vault_dir: Path,
    contradiction_ids: list[str],
    *,
    pack_name: str | None = None,
) -> list[str]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    resolved_ids = list(dict.fromkeys(contradiction_ids))
    if not resolved_ids:
        return []

    placeholders = ",".join("?" for _ in resolved_ids)
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            f"""
            SELECT positive_claim_ids_json, negative_claim_ids_json
            FROM contradictions
            WHERE pack = ? AND contradiction_id IN ({placeholders})
            """,
            (truth_pack, *resolved_ids),
        ).fetchall()

        claim_ids: list[str] = []
        for positive_json, negative_json in rows:
            claim_ids.extend(json.loads(positive_json))
            claim_ids.extend(json.loads(negative_json))
        claim_ids = list(dict.fromkeys(claim_ids))
        if not claim_ids:
            return []

        claim_placeholders = ",".join("?" for _ in claim_ids)
        object_rows = conn.execute(
            f"""
            SELECT DISTINCT object_id
            FROM claims
            WHERE pack = ? AND claim_id IN ({claim_placeholders})
            ORDER BY object_id
            """,
            (truth_pack, *claim_ids),
        ).fetchall()

    return [row[0] for row in object_rows]


def rebuild_compiled_summaries(
    vault_dir: Path,
    object_ids: list[str] | None = None,
    *,
    pack_name: str | None = None,
) -> dict[str, object]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    with sqlite3.connect(layout.knowledge_db) as conn:
        if object_ids:
            placeholders = ",".join("?" for _ in object_ids)
            object_rows = conn.execute(
                f"""
                SELECT object_id
                FROM objects
                WHERE pack = ? AND object_id IN ({placeholders})
                ORDER BY object_id
                """,
                (truth_pack, *object_ids),
            ).fetchall()
        else:
            object_rows = conn.execute(
                """
                SELECT object_id
                FROM objects
                WHERE pack = ?
                ORDER BY object_id
                """,
                (truth_pack,),
            ).fetchall()

    rebuilt_ids = [str(row[0]) for row in object_rows]
    rebuild_knowledge_index(vault_dir, pack_name=truth_pack)

    return {
        "objects_rebuilt": len(rebuilt_ids),
        "object_ids": rebuilt_ids,
        "db_path": str(layout.knowledge_db),
    }


def get_knowledge_page(vault_dir: Path, slug: str) -> dict[str, object] | None:
    _, layout = _ensure_knowledge_db(vault_dir)
    canonical_slug = canonicalize_note_id(slug)
    with sqlite3.connect(layout.knowledge_db) as conn:
        row = conn.execute(
            """
            SELECT slug, title, note_type, path, day_id, frontmatter_json, body
            FROM pages_index
            WHERE slug = ?
            """,
            (canonical_slug,),
        ).fetchone()
    if row is None:
        return None
    page_slug, title, note_type, path, day_id, frontmatter_json, body = row
    return {
        "slug": page_slug,
        "title": title,
        "note_type": note_type,
        "path": path,
        "day_id": day_id,
        "frontmatter": json.loads(frontmatter_json),
        "body": body,
    }


def knowledge_index_stats(vault_dir: Path, *, pack_name: str | None = None) -> dict[str, object]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    queries = {
        "pages": "SELECT COUNT(*) FROM pages_index",
        "links": "SELECT COUNT(*) FROM page_links",
        "raw_records": "SELECT COUNT(*) FROM raw_data",
        "timeline_events": "SELECT COUNT(*) FROM timeline_events",
        "audit_events": "SELECT COUNT(*) FROM audit_events",
        "reuse_events": "SELECT COUNT(*) FROM reuse_events",
        "embedding_chunks": "SELECT COUNT(*) FROM page_embeddings",
        "objects": "SELECT COUNT(*) FROM objects WHERE pack = ?",
        "claims": "SELECT COUNT(*) FROM claims WHERE pack = ?",
        "relations": "SELECT COUNT(*) FROM relations WHERE pack = ?",
        "compiled_summaries": "SELECT COUNT(*) FROM compiled_summaries WHERE pack = ?",
        "contradictions": "SELECT COUNT(*) FROM contradictions WHERE pack = ?",
        "graph_edges": "SELECT COUNT(*) FROM graph_edges WHERE pack = ?",
        "graph_clusters": "SELECT COUNT(*) FROM graph_clusters WHERE pack = ?",
    }
    stats: dict[str, object] = {"db_path": str(layout.knowledge_db)}
    with sqlite3.connect(layout.knowledge_db) as conn:
        for key, query in queries.items():
            stats[key] = (
                int(conn.execute(query, (truth_pack,)).fetchone()[0])
                if "pack = ?" in query
                else int(conn.execute(query).fetchone()[0])
            )
        try:
            rows = conn.execute("""
                SELECT pack, owner_pack, builder_name, built_at
                FROM truth_projections
                ORDER BY pack
                """).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            rows = []
        stats["materialized_truth_packs"] = [
            {
                "pack": str(pack),
                "owner_pack": str(owner_pack),
                "builder_name": str(builder_name or ""),
                "built_at": str(built_at or ""),
            }
            for pack, owner_pack, builder_name, built_at in rows
        ]
    return stats


def sync_audit_events_from_jsonl(vault_dir: Path) -> dict[str, object]:
    """BL-070: re-ingest ``audit_events`` from the JSONL logs without
    a full projection rebuild.

    A full ``rebuild_knowledge_index`` on a 9K-evergreen vault takes
    20+ minutes because it re-embeds every chunk.  Operators doing
    shadow-mode batches (BL-062) want their audit data queryable
    via SQL within seconds of an ``ovp-absorb`` run — not at the
    next scheduled rebuild.

    This helper:

    1. Resolves the layout WITHOUT calling :func:`_ensure_knowledge_db`
       — that helper would trigger a full rebuild on a stale schema
       version, defeating the "fast no-rebuild" promise (codex P2).
    2. Returns ``{"status": "skipped"}`` when the DB doesn't exist
       (fresh vault) or when the projection schema is too old to
       safely write to.  Operator must run a full
       ``ovp-knowledge-index`` first in either case.
    3. Reads every JSONL row via the same ``_collect_audit_rows``
       path the rebuild uses, so semantics are identical.
    4. Truncates ``audit_events`` and re-inserts in one transaction.

    Idempotent (truncate-and-insert; running it twice produces the
    same final state).  Doesn't touch any other table.  Doesn't
    write provenance.  Doesn't refresh embeddings.  Pure projection-
    sync operation against the audit ledger.
    """
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    if not layout.knowledge_db.exists():
        return {
            "status": "skipped",
            "reason": "knowledge.db does not exist; run ovp-knowledge-index first",
            "db_path": str(layout.knowledge_db),
        }
    if not _knowledge_db_supports_pack_schema(layout.knowledge_db):
        return {
            "status": "skipped",
            "reason": (
                "knowledge.db schema is incompatible with current code; "
                "run ovp-knowledge-index for a full rebuild first"
            ),
            "db_path": str(layout.knowledge_db),
        }
    audit_rows = _collect_audit_rows(layout)
    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.execute("DELETE FROM audit_events")
        conn.executemany(
            "INSERT INTO audit_events (source_log, event_type, slug, "
            "session_id, timestamp, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            audit_rows,
        )
        conn.commit()
    # Count per event_type for quick verification — surfaces e.g.
    # "absorb_route_decision: 7" so the operator can confirm the
    # shadow data is queryable.
    with sqlite3.connect(layout.knowledge_db) as conn:
        type_counts = dict(
            conn.execute(
                "SELECT event_type, COUNT(*) FROM audit_events GROUP BY event_type"
            ).fetchall()
        )
    return {
        "status": "synced",
        "audit_events_indexed": len(audit_rows),
        "type_counts": type_counts,
        "db_path": str(layout.knowledge_db),
    }


def recent_audit_events(
    vault_dir: Path,
    limit: int = 20,
    source_log: str | None = None,
    *,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, object]]:
    """Tail recent ``audit_events`` rows, ordered newest-first.

    ``event_type`` filters at the SQL layer — important for
    consumers like the BL-063 Live Concept scheduler that only care
    about one event kind (``absorb_route_decision``).  Pre-fix
    those callers fetched ``limit=500`` then filtered in Python,
    which silently dropped relevant rows when the recent log was
    dominated by other event types.

    ``since`` is an ISO-8601 timestamp (e.g. ``"2026-05-10T00:00:00Z"``)
    pushed into a ``timestamp >= ?`` clause.  Lets time-window
    consumers (Live Concept's ``since_hours`` argument) push the
    cutoff down to SQLite instead of filtering Python-side.
    """
    _, layout = _ensure_knowledge_db(vault_dir)
    query = (
        "SELECT source_log, event_type, slug, session_id, timestamp, "
        "payload_json FROM audit_events"
    )
    clauses: list[str] = []
    params: list[object] = []
    if source_log:
        clauses.append("source_log = ?")
        params.append(source_log)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY timestamp DESC, rowid DESC LIMIT ?"
    params.append(limit)

    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    return [
        {
            "source_log": row[0],
            "event_type": row[1],
            "slug": row[2],
            "session_id": row[3],
            "timestamp": row[4],
            "payload": json.loads(row[5]),
        }
        for row in rows
    ]


def knowledge_tools_json() -> list[dict[str, object]]:
    return [
        {
            "name": "knowledge_search",
            "description": "Keyword search against the derived knowledge index",
            "args": {"query": "string", "limit": "integer?"},
        },
        {
            "name": "knowledge_query",
            "description": "Read-only semantic-style chunk retrieval from local embeddings",
            "args": {"query": "string", "limit": "integer?"},
        },
        {
            "name": "knowledge_truth_search",
            "description": "Search truth-store claims and compiled summaries",
            "args": {"query": "string", "limit": "integer?"},
        },
        {
            "name": "knowledge_contradictions",
            "description": "List contradiction records from the truth store",
            "args": {"limit": "integer?", "subject": "string?"},
        },
        {
            "name": "knowledge_get",
            "description": "Fetch a canonical page payload by slug",
            "args": {"slug": "string"},
        },
        {
            "name": "knowledge_stats",
            "description": "Return knowledge index table counts and db path",
            "args": {},
        },
        {
            "name": "knowledge_audit_recent",
            "description": "Return recent audit events from the derived knowledge index",
            "args": {"limit": "integer?", "source_log": "string?"},
        },
    ]


def dispatch_knowledge_tool(
    vault_dir: Path, tool_name: str, args: dict[str, object]
) -> dict[str, object]:
    if tool_name == "knowledge_search":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 10)
        return {"results": search_knowledge_index(vault_dir, query, limit=limit)}
    if tool_name == "knowledge_query":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 5)
        return {"results": query_knowledge_index(vault_dir, query, limit=limit)}
    if tool_name == "knowledge_truth_search":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 10)
        return {"results": search_truth_store(vault_dir, query, limit=limit)}
    if tool_name == "knowledge_contradictions":
        limit = int(args.get("limit") or 20)
        subject = args.get("subject")
        subject_value = str(subject) if subject else None
        return {"items": list_contradictions(vault_dir, limit=limit, subject=subject_value)}
    if tool_name == "knowledge_get":
        slug = str(args.get("slug") or "")
        return {"page": get_knowledge_page(vault_dir, slug)}
    if tool_name == "knowledge_stats":
        return {"stats": knowledge_index_stats(vault_dir)}
    if tool_name == "knowledge_audit_recent":
        limit = int(args.get("limit") or 20)
        source_log = args.get("source_log")
        source_log_value = str(source_log) if source_log else None
        return {"events": recent_audit_events(vault_dir, limit=limit, source_log=source_log_value)}
    raise ValueError(f"unknown tool: {tool_name}")


def serve_knowledge_index(vault_dir: Path, stdin: TextIOBase, stdout: TextIOBase) -> None:
    for line in stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            request = json.loads(stripped)
            tool_name = str(request.get("tool") or "")
            args = request.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            result = dispatch_knowledge_tool(vault_dir, tool_name, args)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
