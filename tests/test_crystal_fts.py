"""Tests for synthesis/crystal_fts.py — BL-047 first slice (M14).

Verifies that crystal bodies are appended to ``page_fts`` so the
existing FTS-driven ``/search`` Access Surface returns crystals
alongside evergreen pages without a new route.
"""

from __future__ import annotations

import json
import sqlite3

from ovp_pipeline.synthesis.crystal_fts import index_crystals_into_page_fts


SCHEMA = """
CREATE VIRTUAL TABLE page_fts USING fts5(
  slug UNINDEXED,
  title,
  body,
  tokenize='trigram'
);
CREATE TABLE graph_clusters (
  pack TEXT NOT NULL, cluster_id TEXT NOT NULL, cluster_kind TEXT NOT NULL,
  label TEXT NOT NULL, center_object_id TEXT NOT NULL,
  member_object_ids_json TEXT NOT NULL, score REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (pack, cluster_id)
);
CREATE TABLE community_crystals (
  pack TEXT NOT NULL, cluster_id TEXT NOT NULL, body_md TEXT NOT NULL,
  source_evergreen_slugs_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, cluster_id, synthesized_at)
);
CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL, contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL, body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL, negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);
"""


def _seed_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


def _seed_community_crystal(
    conn, *, pack="t", cluster_id, label, body, slugs=("a",),
    superseded=False,
):
    conn.execute(
        "INSERT OR IGNORE INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pack, cluster_id, "louvain_community", label,
         slugs[0] if slugs else "", json.dumps(list(slugs)),
         float(len(slugs))),
    )
    conn.execute(
        "INSERT INTO community_crystals (pack, cluster_id, body_md, "
        "source_evergreen_slugs_json, synthesized_at, llm_model, "
        "prompt_version, superseded_by_synthesized_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pack, cluster_id, body, json.dumps(list(slugs)),
         "2026-05-04T01:00:00.000000+00:00", "m", "v1",
         "2026-05-04T02:00:00+00:00" if superseded else ""),
    )


def _seed_contradiction_crystal(
    conn, *, pack="t", contradiction_id, subject, body, sources=("a", "b"),
):
    conn.execute(
        "INSERT INTO contradiction_crystals (pack, contradiction_id, "
        "subject_key, body_md, positive_claim_ids_json, "
        "negative_claim_ids_json, source_object_ids_json, "
        "synthesized_at, llm_model, prompt_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pack, contradiction_id, subject, body,
         "[]", "[]", json.dumps(list(sources)),
         "2026-05-04T01:00:00.000000+00:00", "m", "v1"),
    )


# ---------------------------------------------------------------------------


class TestIndexCrystalsIntoPageFts:
    def test_inserts_community_with_prefix(self):
        conn = _seed_db()
        _seed_community_crystal(
            conn, cluster_id="cluster::abc12345",
            label="AI alignment topic",
            body="This crystal discusses Karpathy's view on alignment.",
        )
        n = index_crystals_into_page_fts(conn, pack="t")
        assert n == 1
        rows = conn.execute(
            "SELECT slug, title FROM page_fts"
        ).fetchall()
        assert rows == [
            ("crystal:abc12345", "[crystal] AI alignment topic"),
        ]

    def test_inserts_contradiction_with_prefix(self):
        conn = _seed_db()
        _seed_contradiction_crystal(
            conn, contradiction_id="contradiction::xy789",
            subject="memory: stored vs emergent",
            body="The contradiction body.",
        )
        n = index_crystals_into_page_fts(conn, pack="t")
        assert n == 1
        rows = conn.execute(
            "SELECT slug, title FROM page_fts"
        ).fetchall()
        assert rows == [
            ("contradiction:xy789", "[contradiction] memory: stored vs emergent"),
        ]

    def test_skips_superseded_rows(self):
        # Pre-fix every version of every crystal would appear in FTS;
        # only the current row should be searchable.
        conn = _seed_db()
        _seed_community_crystal(
            conn, cluster_id="cluster::v1",
            label="version 1 label", body="v1 body",
            superseded=True,
        )
        _seed_community_crystal(
            conn, cluster_id="cluster::v2",
            label="current label", body="current body",
        )
        n = index_crystals_into_page_fts(conn, pack="t")
        assert n == 1
        slugs = [r[0] for r in conn.execute("SELECT slug FROM page_fts")]
        assert slugs == ["crystal:v2"]

    def test_search_finds_crystal_by_body_term(self):
        conn = _seed_db()
        _seed_community_crystal(
            conn, cluster_id="cluster::aaa",
            label="agent harness",
            body="The 12-layer harness lets LLMs build reliable systems.",
        )
        index_crystals_into_page_fts(conn, pack="t")
        # Trigram tokenizer + body term query — finds the crystal.
        hits = conn.execute(
            "SELECT slug FROM page_fts WHERE page_fts MATCH 'harness'"
        ).fetchall()
        assert ("crystal:aaa",) in hits

    def test_no_page_fts_returns_zero_no_crash(self):
        # Defensive: if knowledge_index hasn't created page_fts yet
        # (corrupt DB or wrong call order), the helper must no-op
        # rather than raise.
        conn = sqlite3.connect(":memory:")
        # Note: no SCHEMA execute → page_fts doesn't exist.
        n = index_crystals_into_page_fts(conn, pack="t")
        assert n == 0

    def test_empty_pack_returns_zero(self):
        conn = _seed_db()
        # Tables exist but no crystal rows.
        n = index_crystals_into_page_fts(conn, pack="t")
        assert n == 0

    def test_handles_missing_label_gracefully(self):
        # Pre-fix a crystal with empty label could crash the title
        # composition or produce ``[crystal] ``.  Should fall back.
        conn = _seed_db()
        _seed_community_crystal(
            conn, cluster_id="cluster::nolabel",
            label="", body="some body",
        )
        index_crystals_into_page_fts(conn, pack="t")
        title = conn.execute(
            "SELECT title FROM page_fts"
        ).fetchone()[0]
        assert title == "[crystal] (untitled)"

    def test_pack_isolation(self):
        # Inserting only the requested pack's crystals — other
        # packs in the same DB stay invisible.
        conn = _seed_db()
        _seed_community_crystal(
            conn, pack="other-pack", cluster_id="cluster::other",
            label="other", body="other body",
        )
        _seed_community_crystal(
            conn, pack="t", cluster_id="cluster::ours",
            label="ours", body="our body",
        )
        n = index_crystals_into_page_fts(conn, pack="t")
        assert n == 1
        slugs = [r[0] for r in conn.execute("SELECT slug FROM page_fts")]
        assert slugs == ["crystal:ours"]

    def test_idempotent_when_called_after_full_rebuild(self):
        # In production ``rebuild_knowledge_index`` drops + recreates
        # page_fts before calling this helper, so two consecutive
        # rebuilds produce identical FTS state.  Simulate by
        # truncating page_fts between calls.
        conn = _seed_db()
        _seed_community_crystal(
            conn, cluster_id="cluster::aa",
            label="L", body="b",
        )
        first = index_crystals_into_page_fts(conn, pack="t")
        conn.execute("DELETE FROM page_fts")
        second = index_crystals_into_page_fts(conn, pack="t")
        assert first == second == 1
