"""BL-056 e2e: prove the provenance spine traces a full lineage.

Unit tests cover each emit point in isolation.  This file walks the
chain end-to-end on a fixture vault and asserts that every node a
piece of data passed through left a provenance row, and that the
chain is followable from a synthesized crystal back to the source
URL of the article that started it.

Steps:

1. Build a tmp_path vault with one source article whose frontmatter
   carries a real ``source: <URL>`` field.
2. Place two evergreens whose frontmatter ``source_url`` points at
   that source article (BL-054 forward-data-quality contract).
3. Run ``rebuild_knowledge_index`` — should write
   ``stage='ingest'`` provenance rows for both evergreens, with
   ``source_url`` matching the source article.
4. Hand-seed ``graph_clusters`` with a community containing both
   evergreens (skips the LLM-driven Louvain pass — orthogonal to
   provenance).
5. Drive ``commit_crystal_version`` to land a community crystal —
   should add a ``stage='synthesize_community_crystal'`` row.
6. **Chain assertion**: starting from the crystal's
   ``provenance`` row, follow ``cluster_id`` → ``graph_clusters``
   → ``member_object_ids`` → ``objects.source_url`` → assert each
   member's source URL matches the source article URL.  This is
   the "lineage is traceable" claim.
7. Run ``rebuild_knowledge_index`` again — assert the
   ``stage='synthesize_community_crystal'`` row from step 5
   survives the rebuild (BL-055 review-fix preservation contract).

What this test deliberately does NOT cover:

* The ``stage='extract'`` event (no LLM running here).
* The ``stage='promote'`` event (the review path involves
  filesystem moves + ``review_candidate_concept`` glue; covered by
  ``test_provenance_emit.py``'s helper-level test instead).
* Re-synthesis idempotency (covered by ``test_crystal_materializer``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.runtime import VaultLayout
from ovp_pipeline.synthesis._versioning import commit_crystal_version


SOURCE_URL = "https://example.com/source-article"
SOURCE_FILENAME = "2026-05-04_e2e-source-article.md"


def _seed_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault / "20-Areas" / "AI-Research" / "Topics").mkdir(parents=True)
    (vault / "50-Inbox" / "03-Processed" / "2026-05").mkdir(parents=True)
    (vault / "60-Logs" / "link-resolution").mkdir(parents=True)
    (vault / "60-Logs" / "migration-reports").mkdir(parents=True)

    # Source article.
    (vault / "50-Inbox" / "03-Processed" / "2026-05" / SOURCE_FILENAME).write_text(
        f"""---
title: "E2E Source Article"
author: e2e-author
source: {SOURCE_URL}
date: 2026-05-04
type: raw
tags: [e2e]
---

# E2E source

Body of the source article.
""",
        encoding="utf-8",
    )

    # Two evergreens with backfilled provenance pointing at the source.
    for slug in ("alpha-concept", "beta-concept"):
        (vault / "10-Knowledge" / "Evergreen" / f"{slug}.md").write_text(
            f"""---
note_id: {slug}
title: "{slug.replace('-', ' ').title()}"
type: evergreen
entity_type: concept
date: 2026-05-04
tags: [evergreen]
aliases: ["{slug}"]
source_url: "{SOURCE_URL}"
source_title: "E2E Source Article"
source_authors: ["e2e-author"]
source_published_at: "2026-05-04"
source_fingerprint: "abc123e2e000"
---

# {slug}

Body.
""",
            encoding="utf-8",
        )

    return vault


def _seed_community(conn: sqlite3.Connection, pack: str = "research-tech") -> str:
    cluster_id = "cluster::e2e123"
    conn.execute(
        "INSERT INTO graph_clusters (pack, cluster_id, cluster_kind, label, "
        "center_object_id, member_object_ids_json, score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            pack, cluster_id, "louvain_community", "E2E topic",
            "alpha-concept",
            json.dumps(["alpha-concept", "beta-concept"]),
            2.0,
        ),
    )
    conn.commit()
    return cluster_id


# ---------------------------------------------------------------------------
# The chain assertion
# ---------------------------------------------------------------------------


def test_provenance_chain_traces_back_to_source(tmp_path):
    """Walk the lineage:

        synthesize_community_crystal row
              ↓ object_id == cluster_id
        graph_clusters.member_object_ids_json
              ↓ each member is an object_id
        objects.source_url
              ↓ matches source article frontmatter

    Every link must hold for the spine to be useful.
    """
    vault = _seed_vault(tmp_path)

    # Step 1: rebuild → ingest provenance for both evergreens.
    rebuild_knowledge_index(vault, pack_name="research-tech")
    db = VaultLayout.from_vault(vault).knowledge_db

    with sqlite3.connect(db) as conn:
        ingest_rows = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT object_id, source_url FROM provenance "
                "WHERE pack='research-tech' AND derived_via_stage='ingest'"
            )
        }
    assert ingest_rows.get("alpha-concept") == SOURCE_URL
    assert ingest_rows.get("beta-concept") == SOURCE_URL

    # Step 2: hand-seed the community + write a community crystal via
    # commit_crystal_version (the same call site real synthesis uses).
    with sqlite3.connect(db) as conn:
        cluster_id = _seed_community(conn)
        live_path = vault / "40-Resources" / "Crystals" / "e2e123.md"
        archive_subdir = vault / "70-Archive" / "Crystals" / "e2e123"

        commit_crystal_version(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="research-tech",
            key_value=cluster_id,
            new_synthesized_at="2026-05-05T10:00:00.000000+00:00",
            insert_sql=(
                "INSERT INTO community_crystals "
                "(pack, cluster_id, body_md, source_evergreen_slugs_json, "
                " synthesized_at, llm_model, prompt_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_params=(
                "research-tech", cluster_id, "## body",
                json.dumps(["alpha-concept", "beta-concept"]),
                "2026-05-05T10:00:00.000000+00:00",
                "test-llm", "v1",
            ),
            new_markdown="## body\n",
            live_path=live_path,
            archive_subdir=archive_subdir,
            provenance_stage="synthesize_community_crystal",
            provenance_metadata={"llm_model": "test-llm", "sample_size": 2},
        )

    # Step 3 — the chain:
    with sqlite3.connect(db) as conn:
        # 3a. crystal-stage row exists.
        crystal_rows = conn.execute(
            "SELECT object_id, derived_via_stage, metadata_json FROM provenance "
            "WHERE pack='research-tech' "
            "  AND derived_via_stage='synthesize_community_crystal'"
        ).fetchall()
        assert len(crystal_rows) == 1
        crystal_object_id = crystal_rows[0][0]
        assert crystal_object_id == cluster_id
        meta = json.loads(crystal_rows[0][2])
        assert meta["llm_model"] == "test-llm"
        assert meta["sample_size"] == 2

        # 3b. follow cluster_id → members.
        cluster_row = conn.execute(
            "SELECT member_object_ids_json FROM graph_clusters "
            "WHERE pack='research-tech' AND cluster_id=?",
            (cluster_id,),
        ).fetchone()
        members = json.loads(cluster_row[0])
        assert set(members) == {"alpha-concept", "beta-concept"}

        # 3c. each member's source_url hops back to the source article.
        for member in members:
            row = conn.execute(
                "SELECT source_url FROM objects "
                "WHERE pack='research-tech' AND object_id=?",
                (member,),
            ).fetchone()
            assert row is not None, f"missing object row for {member}"
            assert row[0] == SOURCE_URL, (
                f"chain broken at member {member}: "
                f"objects.source_url={row[0]!r} != {SOURCE_URL!r}"
            )

        # 3d. each member also has its own ingest provenance pointing
        # at the same URL (closes the loop).
        for member in members:
            row = conn.execute(
                "SELECT source_url FROM provenance "
                "WHERE pack='research-tech' AND object_id=? "
                "  AND derived_via_stage='ingest'",
                (member,),
            ).fetchone()
            assert row is not None, (
                f"missing ingest provenance for {member}"
            )
            assert row[0] == SOURCE_URL


def test_synthesize_provenance_survives_rebuild(tmp_path):
    """BL-055 review-fix dedup guard: rebuilding after a synthesize
    event must preserve the ``synthesize_*`` row.  Otherwise the
    audit log would only ever show the latest rebuild's ingest rows
    and the synthesis history would silently disappear."""
    vault = _seed_vault(tmp_path)

    # Initial rebuild + synthesize.
    rebuild_knowledge_index(vault, pack_name="research-tech")
    db = VaultLayout.from_vault(vault).knowledge_db

    with sqlite3.connect(db) as conn:
        cluster_id = _seed_community(conn)
        live_path = vault / "40-Resources" / "Crystals" / "e2e123.md"
        archive_subdir = vault / "70-Archive" / "Crystals" / "e2e123"
        commit_crystal_version(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="research-tech",
            key_value=cluster_id,
            new_synthesized_at="2026-05-05T10:00:00.000000+00:00",
            insert_sql=(
                "INSERT INTO community_crystals "
                "(pack, cluster_id, body_md, source_evergreen_slugs_json, "
                " synthesized_at, llm_model, prompt_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_params=(
                "research-tech", cluster_id, "## body",
                json.dumps(["alpha-concept", "beta-concept"]),
                "2026-05-05T10:00:00.000000+00:00",
                "test-llm", "v1",
            ),
            new_markdown="## body\n",
            live_path=live_path,
            archive_subdir=archive_subdir,
            provenance_stage="synthesize_community_crystal",
            provenance_metadata={"llm_model": "test-llm"},
        )

    # Second rebuild — synthesize row must survive.
    rebuild_knowledge_index(vault, pack_name="research-tech")
    with sqlite3.connect(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM provenance "
            "WHERE pack='research-tech' "
            "  AND derived_via_stage='synthesize_community_crystal' "
            "  AND object_id=?",
            (cluster_id,),
        ).fetchone()[0]
    assert n == 1, "synthesize provenance should survive rebuild"
