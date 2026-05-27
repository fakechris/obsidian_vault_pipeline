"""BL-114 — concept_identity_ledger behaviour + migration round-trip.

The contract this BL introduces:

* A stable ``concept_id`` decoupled from membership snapshots.
* ``concept_identity_ledger`` maps each concept_id to its CURRENT
  Louvain ``cluster_id`` (BL-115 keeps that in sync after re-clusters).
* At seed time every existing ``community_crystals.cluster_id``
  becomes its own ``concept_id`` (``lineage_json='[]'``) so behaviour
  is byte-identical to the pre-BL-114 join shape.
* Two AFTER-INSERT triggers keep the ledger in sync with crystals:
  one for legacy callers (no concept_id passed) that backfills
  concept_id == cluster_id, one for explicit-concept_id callers
  (BL-115 onwards).

These tests pin all three slices of the contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ovp_pipeline.knowledge_index import (
    KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
    SCHEMA,
    SCHEMA_MIGRATIONS,
    _migrate_9_to_10_concept_identity,
)


def _make_v9_with_crystals(db_path: Path) -> None:
    """Build a v9-shaped DB with seeded ``community_crystals`` rows
    so the BL-114 migration has something to migrate.  Mirrors what
    ``rebuild_knowledge_index`` would have produced on a v9 vault."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE projection_metadata (
              projection_kind TEXT PRIMARY KEY,
              authority_schema_version INTEGER NOT NULL,
              projection_schema_version INTEGER NOT NULL,
              built_at TEXT NOT NULL
            );
            INSERT INTO projection_metadata VALUES
              ('knowledge_db', 1, 9, '2026-05-25T00:00:00Z');
            CREATE TABLE community_crystals (
              pack TEXT NOT NULL,
              cluster_id TEXT NOT NULL,
              body_md TEXT NOT NULL,
              source_evergreen_slugs_json TEXT NOT NULL,
              synthesized_at TEXT NOT NULL,
              llm_model TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (pack, cluster_id, synthesized_at)
            );
        """)
        # Three crystals: two for the same cluster (one current, one
        # superseded — a version chain) plus one for a different
        # cluster.  Tests that the ledger seeds one row per
        # (pack, cluster_id) regardless of version chain length.
        rows = [
            ("research-tech", "cluster::aa", "body v1", "[]",
             "2026-05-10T00:00:00.000000+00:00", "m", "v1",
             "2026-05-12T00:00:00.000000+00:00"),  # superseded
            ("research-tech", "cluster::aa", "body v2", "[]",
             "2026-05-12T00:00:00.000000+00:00", "m", "v1",
             ""),                                  # current
            ("research-tech", "cluster::bb", "body x", "[]",
             "2026-05-11T00:00:00.000000+00:00", "m", "v1",
             ""),                                  # current
        ]
        conn.executemany(
            "INSERT INTO community_crystals "
            "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
            " synthesized_at, llm_model, prompt_version,"
            " superseded_by_synthesized_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


# ── Migration runner ──────────────────────────────────────────────


def test_migration_creates_ledger_and_backfills_concept_id(tmp_path: Path):
    db = tmp_path / "knowledge.db"
    _make_v9_with_crystals(db)
    with sqlite3.connect(db) as conn:
        _migrate_9_to_10_concept_identity(conn, tmp_path)

    with sqlite3.connect(db) as conn:
        # Both new tables/columns exist.
        ledger_table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='concept_identity_ledger'"
        ).fetchone()
        assert ledger_table == (1,)
        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(community_crystals)"
            ).fetchall()
        }
        assert "concept_id" in cols
        assert "supersede_reason" in cols

        # Every crystal got concept_id == cluster_id.
        rows = conn.execute(
            "SELECT cluster_id, concept_id, supersede_reason "
            "FROM community_crystals ORDER BY synthesized_at"
        ).fetchall()
        for cluster_id, concept_id, supersede_reason in rows:
            assert concept_id == cluster_id
            assert supersede_reason == ""

        # Ledger has one row per (pack, cluster_id) — the version
        # chain on cluster::aa collapses to a single ledger row.
        ledger = conn.execute(
            "SELECT pack, concept_id, current_cluster_id, "
            "       last_matched_at, created_at, lineage_json "
            "FROM concept_identity_ledger ORDER BY concept_id"
        ).fetchall()
        assert len(ledger) == 2
        aa, bb = ledger
        # cluster::aa — created at v1, last_matched at v2.
        assert aa[0] == "research-tech"
        assert aa[1] == "cluster::aa"
        assert aa[2] == "cluster::aa"
        assert aa[3] == "2026-05-12T00:00:00.000000+00:00"  # MAX
        assert aa[4] == "2026-05-10T00:00:00.000000+00:00"  # MIN
        assert aa[5] == "[]"
        # cluster::bb — single version, MAX == MIN.
        assert bb[1] == "cluster::bb"
        assert bb[3] == "2026-05-11T00:00:00.000000+00:00"
        assert bb[4] == "2026-05-11T00:00:00.000000+00:00"


def test_migration_is_idempotent(tmp_path: Path):
    """Running the migration twice must produce identical state —
    the ON CONFLICT clause + duplicate-column guards keep partial
    prior runs from breaking the retry."""
    db = tmp_path / "knowledge.db"
    _make_v9_with_crystals(db)
    with sqlite3.connect(db) as conn:
        _migrate_9_to_10_concept_identity(conn, tmp_path)
    with sqlite3.connect(db) as conn:
        before = conn.execute(
            "SELECT * FROM concept_identity_ledger ORDER BY concept_id"
        ).fetchall()
        # Second run.
        _migrate_9_to_10_concept_identity(conn, tmp_path)
        after = conn.execute(
            "SELECT * FROM concept_identity_ledger ORDER BY concept_id"
        ).fetchall()
        assert before == after


def test_migration_skips_minimal_db_without_community_crystals(tmp_path: Path):
    """A v9-but-minimal DB (no crystals table yet — e.g. the path
    used by the schema-migration registry tests) must NOT raise.
    The migration creates the ledger + trigger but skips the
    column/backfill steps."""
    db = tmp_path / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE projection_metadata (
              projection_kind TEXT PRIMARY KEY,
              authority_schema_version INTEGER NOT NULL,
              projection_schema_version INTEGER NOT NULL,
              built_at TEXT NOT NULL
            )
        """)
        _migrate_9_to_10_concept_identity(conn, tmp_path)
        # Ledger created, community_crystals still absent — no crash.
        assert conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='concept_identity_ledger'"
        ).fetchone() == (1,)


# ── Auto-seed triggers on a fresh DB ────────────────────────────


def _fresh_db(db_path: Path) -> sqlite3.Connection:
    """Initialize a v10-shaped DB via the canonical SCHEMA — the
    fresh-build path, not the migration path.  Exercises that the
    SCHEMA-defined triggers behave the same as the migration-
    installed ones."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def test_legacy_insert_trigger_backfills_concept_id(tmp_path: Path):
    """A caller that INSERTs into community_crystals without
    specifying concept_id gets concept_id == cluster_id via the
    AFTER-INSERT trigger, and a matching ledger row appears."""
    db = tmp_path / "knowledge.db"
    conn = _fresh_db(db)
    try:
        conn.execute(
            "INSERT INTO community_crystals "
            "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
            " synthesized_at, llm_model, prompt_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", "cluster::legacy", "body", "[]",
             "2026-05-26T00:00:00+00:00", "m", "v1"),
        )
        row = conn.execute(
            "SELECT cluster_id, concept_id FROM community_crystals"
        ).fetchone()
        assert row == ("cluster::legacy", "cluster::legacy")
        ledger = conn.execute(
            "SELECT concept_id, current_cluster_id, lineage_json "
            "FROM concept_identity_ledger"
        ).fetchone()
        assert ledger == ("cluster::legacy", "cluster::legacy", "[]")
    finally:
        conn.close()


def test_explicit_concept_insert_trigger_creates_ledger_row(tmp_path: Path):
    """The BL-115-shape INSERT (concept_id set explicitly, may differ
    from cluster_id when identity was inherited) gets its ledger
    row created via the second trigger without touching the explicit
    concept_id value."""
    db = tmp_path / "knowledge.db"
    conn = _fresh_db(db)
    try:
        # Inherited identity: cluster_id changed across re-cluster,
        # concept_id stays.
        conn.execute(
            "INSERT INTO community_crystals "
            "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
            " synthesized_at, llm_model, prompt_version,"
            " concept_id, supersede_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", "cluster::new", "body", "[]",
             "2026-05-26T00:00:00+00:00", "m", "v1",
             "cluster::original", ""),
        )
        row = conn.execute(
            "SELECT cluster_id, concept_id FROM community_crystals"
        ).fetchone()
        assert row == ("cluster::new", "cluster::original")
        ledger = conn.execute(
            "SELECT concept_id, current_cluster_id "
            "FROM concept_identity_ledger"
        ).fetchone()
        assert ledger == ("cluster::original", "cluster::new")
    finally:
        conn.close()


# ── Registry plumbing ─────────────────────────────────────────────


def test_migration_registered_at_version_9():
    """The schema-version bump 9 → 10 must have a registered
    migration in ``SCHEMA_MIGRATIONS`` — without this the bump
    would silently drop into the slow full-rebuild path (the
    ``test_every_version_bump_has_a_migration`` policy test
    catches a missing entry; this one names BL-114 specifically)."""
    assert KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION >= 10
    step = SCHEMA_MIGRATIONS.get(9)
    assert step is not None
    assert "BL-114" in step.reason
    assert step.kind.value == "additive"
