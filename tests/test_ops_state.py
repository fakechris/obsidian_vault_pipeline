"""Tests for the ``ops_state`` projection writer (M24.1).

The projection's contract is simple: it must match
:func:`ops_lifecycle.lifecycle_counts` exactly, and a second
``rebuild`` with no audit changes must produce byte-identical
non-timestamp rows.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ovp_pipeline.ops_lifecycle import (
    ALL_STATES,
    STATE_ACCEPTED,
    STATE_NEEDS_ACTION,
    STATE_RECEIVED,
    lifecycle_counts,
)
from ovp_pipeline.ops_state import (
    counts_from_projection,
    ensure_schema,
    rebuild,
)


PACK = "research-tech"


def _make_db() -> sqlite3.Connection:
    """Same schema as ``test_ops_lifecycle`` — kept local so the
    projection tests stand on their own."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE audit_events (
            source_log TEXT NOT NULL,
            event_type TEXT NOT NULL,
            slug TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL
        );
        CREATE TABLE objects (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            object_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            canonical_path TEXT NOT NULL,
            source_slug TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, object_id)
        );
        CREATE TABLE graph_clusters (
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            cluster_kind TEXT NOT NULL,
            label TEXT NOT NULL,
            center_object_id TEXT NOT NULL,
            member_object_ids_json TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (pack, cluster_id)
        );
        CREATE TABLE community_crystals (
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            body_md TEXT NOT NULL,
            source_evergreen_slugs_json TEXT NOT NULL,
            synthesized_at TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
          concept_id TEXT NOT NULL DEFAULT '',
          supersede_reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, cluster_id, synthesized_at)
        );
CREATE TABLE concept_identity_ledger (
  pack TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  current_cluster_id TEXT NOT NULL DEFAULT '',
  last_matched_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  lineage_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (pack, concept_id)
);
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
        CREATE TABLE evergreen_revisions (
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
        """
    )
    return conn


def _seed_mixed(conn: sqlite3.Connection) -> None:
    # Received source.
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "article_intake_only", "src-1", "s",
         "2026-05-13T08:00:00+00:00", "{}"),
    )
    # Failure source → NeedsAction.
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "absorb_parse_error", "src-2", "s",
         "2026-05-13T08:01:00+00:00", "{}"),
    )
    # Accepted object via projection.
    conn.execute(
        "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "obj-1", "evergreen", "T",
         "10-Knowledge/Evergreen/T.md", "src-1", ""),
    )
    conn.commit()


# ── Schema + rebuild ──────────────────────────────────────────────


def test_ensure_schema_creates_table_idempotently():
    conn = _make_db()
    ensure_schema(conn)
    ensure_schema(conn)  # second call must be a no-op
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='ops_state'"
    ).fetchone()
    assert row is not None


def test_rebuild_returns_kernel_counts():
    conn = _make_db()
    _seed_mixed(conn)
    counts = rebuild(conn, pack=PACK)
    kernel_counts = lifecycle_counts(conn, pack=PACK)
    assert counts == kernel_counts


def test_rebuild_truncates_prior_rows_for_pack():
    conn = _make_db()
    _seed_mixed(conn)
    rebuild(conn, pack=PACK)
    total_after_first = conn.execute(
        "SELECT COUNT(*) FROM ops_state WHERE pack = ?", (PACK,)
    ).fetchone()[0]
    # Drop the failure source from audit_events and rebuild — count
    # must shrink.
    conn.execute(
        "DELETE FROM audit_events WHERE slug = 'src-2'"
    )
    conn.commit()
    rebuild(conn, pack=PACK)
    total_after_second = conn.execute(
        "SELECT COUNT(*) FROM ops_state WHERE pack = ?", (PACK,)
    ).fetchone()[0]
    assert total_after_second == total_after_first - 1


def test_rebuild_is_content_idempotent():
    """Two consecutive rebuilds with no audit changes produce
    identical non-timestamp rows."""
    conn = _make_db()
    _seed_mixed(conn)
    rebuild(conn, pack=PACK)
    rows_a = conn.execute(
        "SELECT pack, item_kind, item_id, state, sub_state, "
        "       last_evidence_at, evidence_event_types_json, "
        "       needs_action_reason "
        "  FROM ops_state WHERE pack = ? "
        " ORDER BY item_kind, item_id",
        (PACK,),
    ).fetchall()
    rebuild(conn, pack=PACK)
    rows_b = conn.execute(
        "SELECT pack, item_kind, item_id, state, sub_state, "
        "       last_evidence_at, evidence_event_types_json, "
        "       needs_action_reason "
        "  FROM ops_state WHERE pack = ? "
        " ORDER BY item_kind, item_id",
        (PACK,),
    ).fetchall()
    assert rows_a == rows_b


def test_rebuild_other_pack_unaffected():
    """Rebuilding ``pack=A`` must not touch rows where ``pack=B``."""
    conn = _make_db()
    _seed_mixed(conn)
    rebuild(conn, pack=PACK)
    # Inject a row under a different pack.
    conn.execute(
        "INSERT INTO ops_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("other-pack", "source", "ghost", "Received", None,
         "2026-05-13T08:00:00+00:00", "[]", None,
         "2026-05-13T08:00:00+00:00"),
    )
    conn.commit()
    rebuild(conn, pack=PACK)
    other_row = conn.execute(
        "SELECT item_id FROM ops_state WHERE pack = 'other-pack'"
    ).fetchone()
    assert other_row is not None
    assert other_row[0] == "ghost"


# ── Read API ──────────────────────────────────────────────────────


def test_counts_from_projection_matches_rebuild():
    conn = _make_db()
    _seed_mixed(conn)
    rebuild_counts = rebuild(conn, pack=PACK)
    read_counts = counts_from_projection(conn, pack=PACK)
    assert read_counts == rebuild_counts


def test_counts_from_projection_returns_all_states_keys():
    conn = _make_db()
    ensure_schema(conn)  # empty table
    counts = counts_from_projection(conn, pack=PACK)
    assert set(counts.keys()) == set(ALL_STATES)
    assert all(v == 0 for v in counts.values())


def test_evidence_event_types_stored_as_json_list():
    conn = _make_db()
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "article_intake_only", "src-x", "s",
         "2026-05-13T08:00:00+00:00", "{}"),
    )
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "absorb_route_decision", "src-x", "s",
         "2026-05-13T08:01:00+00:00", "{}"),
    )
    conn.commit()
    rebuild(conn, pack=PACK)
    row = conn.execute(
        "SELECT evidence_event_types_json FROM ops_state "
        " WHERE pack = ? AND item_id = ?",
        (PACK, "src-x"),
    ).fetchone()
    assert row is not None
    decoded = json.loads(row[0])
    assert decoded[0] == "absorb_route_decision"  # newest first
    assert "article_intake_only" in decoded
