"""Regression test: ``rebuild_knowledge_index`` must preserve the
LLM-synthesized crystal corpus across runs.

The pre-fix bug: ``_preserve_existing_truth_rows`` only carried the
``TRUTH_PROJECTION_TABLE_COLUMNS`` set across the rebuild, and only for
non-current packs.  ``community_crystals`` / ``contradiction_crystals``
/ ``crystal_scores`` were not on that list, so every rebuild silently
wiped them — costing the user an LLM re-synthesis bill.

This test seeds rows for the current pack, runs a rebuild, and asserts
the rows are still there.
"""

from __future__ import annotations

import json
import sqlite3

from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.runtime import VaultLayout


PACK = "research-tech"


def _seed_crystals(db_path):
    with sqlite3.connect(db_path) as conn:
        # Seed a graph_clusters row so the rebuild_crystal_scores
        # JOIN against community_crystals produces a row downstream.
        # Without this, rebuild_crystal_scores filters out the
        # crystal as "no matching cluster".
        conn.execute(
            """
            INSERT INTO graph_clusters
              (pack, cluster_id, cluster_kind, label, center_object_id,
               member_object_ids_json, score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PACK, "cluster::a1", "louvain_community", "Vector search",
                "ev-a", json.dumps(["ev-a", "ev-b"]), 0.0,
            ),
        )
        conn.execute(
            """
            INSERT INTO community_crystals
              (pack, cluster_id, body_md, source_evergreen_slugs_json,
               synthesized_at, llm_model, prompt_version,
               superseded_by_synthesized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PACK, "cluster::a1", "## body\n\nVector search overview.",
                json.dumps(["ev-a", "ev-b"]),
                "2026-05-04T12:00:00+00:00",
                "minimax-m2.7-highspeed", "v1", "",
            ),
        )
        conn.execute(
            """
            INSERT INTO contradiction_crystals
              (pack, contradiction_id, subject_key, body_md,
               positive_claim_ids_json, negative_claim_ids_json,
               source_object_ids_json, synthesized_at, llm_model,
               prompt_version, superseded_by_synthesized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PACK, "contradiction::c1", "RAG vs long context",
                "## open question\n\nDoes long context replace RAG?",
                json.dumps(["claim-pos"]), json.dumps(["claim-neg"]),
                json.dumps(["obj-1"]),
                "2026-05-04T12:01:00+00:00",
                "minimax-m2.7-highspeed", "v1", "",
            ),
        )
        conn.execute(
            """
            INSERT INTO crystal_scores
              (pack, crystal_kind, crystal_id, score,
               size_norm, credibility_norm, contradiction_norm,
               reuse_recency_norm, evergreen_recency_norm, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PACK, "community", "cluster::a1", 0.8,
                0.6, 0.7, 0.0, 0.0, 0.5,
                "2026-05-04T12:02:00+00:00",
            ),
        )
        conn.commit()


def test_rebuild_preserves_community_crystals(temp_vault):
    rebuild_knowledge_index(temp_vault, pack_name=PACK)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    _seed_crystals(db_path)
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM community_crystals WHERE pack=?", (PACK,),
        ).fetchone()[0]
    assert before == 1

    rebuild_knowledge_index(temp_vault, pack_name=PACK)

    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM community_crystals WHERE pack=?", (PACK,),
        ).fetchone()[0]
        body = conn.execute(
            "SELECT body_md FROM community_crystals WHERE pack=? AND cluster_id=?",
            (PACK, "cluster::a1"),
        ).fetchone()[0]
    assert after == 1
    assert "Vector search overview" in body


def test_rebuild_preserves_contradiction_crystals(temp_vault):
    rebuild_knowledge_index(temp_vault, pack_name=PACK)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    _seed_crystals(db_path)

    rebuild_knowledge_index(temp_vault, pack_name=PACK)

    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM contradiction_crystals WHERE pack=?", (PACK,),
        ).fetchone()[0]
        subject = conn.execute(
            "SELECT subject_key FROM contradiction_crystals "
            "WHERE pack=? AND contradiction_id=?",
            (PACK, "contradiction::c1"),
        ).fetchone()[0]
    assert n == 1
    assert subject == "RAG vs long context"


def test_preserve_from_v9_source_without_concept_columns(tmp_path):
    """Codex P1 regression: a SOURCE knowledge.db at the pre-BL-114
    (v9) shape has no ``concept_id`` / ``supersede_reason`` columns on
    ``community_crystals``.  Pre-fix, ``_preserve_existing_truth_rows``
    selected those columns, hit ``no such column``, and silently
    dropped every crystal — schema bump → LLM-corpus data loss.

    Post-fix: the column introspection defaults the missing columns to
    '' and the dest's BL-114 AFTER-INSERT trigger backfills
    ``concept_id = cluster_id`` + seeds the ledger.  So the v9 crystal
    survives AND lands in the v10 shape.
    """
    from ovp_pipeline.knowledge_index import (
        SCHEMA,
        _preserve_existing_truth_rows,
    )

    # v9-shaped SOURCE: community_crystals WITHOUT concept_id /
    # supersede_reason, and no concept_identity_ledger table at all.
    source = tmp_path / "v9.db"
    with sqlite3.connect(source) as src:
        src.executescript(
            """
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
            """
        )
        src.execute(
            "INSERT INTO community_crystals "
            "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
            " synthesized_at, llm_model, prompt_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PACK, "cluster::legacy", "## legacy body", json.dumps(["x"]),
             "2026-05-01T00:00:00+00:00", "m", "v1"),
        )
        src.commit()

    # v10-shaped DEST (full SCHEMA → has the new columns + triggers).
    dest = tmp_path / "v10.db"
    dest_conn = sqlite3.connect(dest)
    try:
        dest_conn.executescript(SCHEMA)
        _preserve_existing_truth_rows(source, dest_conn, exclude_pack=PACK)
        dest_conn.commit()
        # Crystal survived.
        row = dest_conn.execute(
            "SELECT cluster_id, concept_id, supersede_reason "
            "FROM community_crystals WHERE pack=?", (PACK,),
        ).fetchone()
        assert row is not None, "v9 crystal was dropped — data loss regression"
        assert row[0] == "cluster::legacy"
        # Trigger backfilled concept_id = cluster_id.
        assert row[1] == "cluster::legacy"
        assert row[2] == ""
        # Ledger seeded by the trigger.
        ledger = dest_conn.execute(
            "SELECT concept_id, current_cluster_id "
            "FROM concept_identity_ledger WHERE pack=?", (PACK,),
        ).fetchone()
        assert ledger == ("cluster::legacy", "cluster::legacy")
    finally:
        dest_conn.close()


# Note: ``crystal_scores`` is a Projection, not Canonical State.  The
# rebuild intentionally drops + re-derives it from the (now-preserved)
# crystal rows, so a "scores-survived" test is brittle — the
# truth_projection rebuild ALSO recomputes ``graph_clusters`` for the
# current pack from the live page graph, and a synthetic test fixture
# without real source pages will end up with an empty graph_clusters
# regardless of whether crystal_scores was preserved.  Score rebuild
# correctness is covered by ``test_crystal_scoring.py``.
