"""Tests for synthesis/curated_atlas.py — BL-046 Curated Atlas
Access Surface (markdown export half).

Three layers:

1. **Teaser extraction** — pure helpers that strip frontmatter,
   the M13 sampling-disclosure blockquote, and section headers
   to find a usable one-line snippet.
2. **Atlas composition** — builds the dataclass from a seeded DB.
3. **Markdown rendering** — produces the on-disk file shape with
   the standard ``projection_*`` fields, score breakdowns, and
   wikilinks back to the underlying crystal markdowns.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.synthesis.curated_atlas import (
    CURATED_ATLAS_FORMAT_VERSION,
    CURATED_ATLAS_REL,
    CuratedAtlas,
    CuratedEntry,
    _crystal_safe_id,
    _crystal_wikilink,
    _extract_teaser,
    build_curated_atlas,
    render_curated_atlas_markdown,
    write_curated_atlas,
)


SCHEMA = """
CREATE TABLE objects (
  pack TEXT NOT NULL, object_id TEXT NOT NULL, object_kind TEXT NOT NULL,
  title TEXT NOT NULL, canonical_path TEXT NOT NULL, source_slug TEXT NOT NULL,
  PRIMARY KEY (pack, object_id)
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
CREATE TABLE crystal_scores (
  pack TEXT NOT NULL, crystal_kind TEXT NOT NULL, crystal_id TEXT NOT NULL,
  score REAL NOT NULL, size_norm REAL NOT NULL DEFAULT 0,
  credibility_norm REAL NOT NULL DEFAULT 0,
  contradiction_norm REAL NOT NULL DEFAULT 0,
  reuse_recency_norm REAL NOT NULL DEFAULT 0,
  evergreen_recency_norm REAL NOT NULL DEFAULT 0,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (pack, crystal_kind, crystal_id)
);
"""


def _make_atlas(entries: list[CuratedEntry]) -> CuratedAtlas:
    return CuratedAtlas(
        pack="t", top_n=30, total_chains=len(entries),
        entries=tuple(entries),
        generated_at="2026-05-04T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Teaser extraction
# ---------------------------------------------------------------------------


class TestExtractTeaser:
    def test_first_chinese_sentence(self):
        body = "## 概念核心\n\n双金库架构是一种隔离设计。它将人和 AI 的写入空间分开。"
        teaser = _extract_teaser(body)
        assert teaser == "双金库架构是一种隔离设计。"

    def test_first_western_sentence(self):
        body = "## Concept core\n\nThis is the first sentence! And then a second."
        teaser = _extract_teaser(body)
        assert teaser == "This is the first sentence!"

    def test_strips_sampling_disclosure(self):
        # PR-136's blockquote must not pollute the teaser.
        body = (
            "> **采样说明**: 本 crystal 基于该社区 454 个节点中按 object_id 排序的"
            "前 8 个 evergreen 合成,长尾未覆盖。\n\n## 概念核心\n\n实际正文起点。"
            "后续句子。"
        )
        teaser = _extract_teaser(body)
        assert "采样说明" not in teaser
        assert teaser == "实际正文起点。"

    def test_strips_h1_title(self):
        # Some crystals open with ``# Title`` before the standard ## sections.
        body = "# 多智能体协作\n\n## 概念核心\n\n核心命题是协作。"
        teaser = _extract_teaser(body)
        assert teaser == "核心命题是协作。"

    def test_skips_pure_list_paragraphs(self):
        # Some crystals' first non-blank paragraph is a list of
        # related notes; teaser should pull from prose, not the list.
        body = (
            "## 概念核心\n\n"
            "- [[note-a]]\n- [[note-b]]\n\n"
            "Real prose that should become the teaser.  More details follow."
        )
        teaser = _extract_teaser(body)
        assert "[[" not in teaser
        assert teaser.startswith("Real prose")

    def test_truncation(self):
        long_body = "## body\n\n" + "x" * 300 + "."
        teaser = _extract_teaser(long_body, max_chars=50)
        assert len(teaser) <= 50
        assert teaser.endswith("…")

    def test_empty_body_returns_empty(self):
        assert _extract_teaser("") == ""

    def test_no_terminator_falls_back_to_paragraph(self):
        # No sentence-ending punctuation — return the whole first line.
        body = "## body\n\nA single line without a period"
        assert _extract_teaser(body) == "A single line without a period"


# ---------------------------------------------------------------------------
# Filename + wikilink translation
# ---------------------------------------------------------------------------


class TestSafeId:
    def test_community_strips_prefix(self):
        assert _crystal_safe_id("community", "cluster::abc12345") == "abc12345"

    def test_contradiction_adds_prefix(self):
        assert _crystal_safe_id(
            "contradiction", "contradiction::xyz98765",
        ) == "contradiction-xyz98765"

    def test_unknown_kind_passes_through(self):
        assert _crystal_safe_id("other", "weird-id") == "weird-id"


class TestWikilink:
    def test_uses_safe_id_with_label(self):
        link = _crystal_wikilink(
            "community", "cluster::aaa1", label="AI alignment",
        )
        # Format: [[<safe-id>|<label>]]
        assert link == "[[aaa1|AI alignment]]"


# ---------------------------------------------------------------------------
# Atlas composition (DB-backed)
# ---------------------------------------------------------------------------


def _seed_minimal(tmp_path: Path) -> tuple[Path, Path]:
    """Three communities + one contradiction with pre-computed scores
    so we can assert ordering + content faithfully."""
    vault = tmp_path / "vault"
    vault.mkdir()
    db = vault / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    pack = "research-tech"

    communities = [
        # cluster_id, label, body_md, slugs, score
        ("cluster::high", "Top topic",
         "## 概念核心\n\nHigh-scoring crystal here. More.",
         ["a", "b"], 0.85),
        ("cluster::mid", "Middle topic",
         "## 概念核心\n\nMid-scoring crystal here. More.",
         ["c"], 0.50),
        ("cluster::low", "Low topic",
         "## 概念核心\n\nLow-scoring crystal here. More.",
         ["d"], 0.20),
    ]
    for cid, label, body, slugs, score in communities:
        conn.execute(
            "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack, cid, "louvain_community", label,
             slugs[0], json.dumps(slugs), float(len(slugs))),
        )
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack, cid, body, json.dumps(slugs),
             "2026-05-04T01:00:00.000000+00:00", "m", "v1"),
        )
        conn.execute(
            "INSERT INTO crystal_scores (pack, crystal_kind, crystal_id, "
            "score, size_norm, credibility_norm, contradiction_norm, "
            "reuse_recency_norm, evergreen_recency_norm, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pack, "community", cid, score,
             0.5, 0.4, 0.3, 0.0, 0.6,
             "2026-05-04T12:00:00+00:00"),
        )

    # One contradiction crystal, score 0.65 — should land between
    # high and mid in the rank order.
    conn.execute(
        "INSERT INTO contradiction_crystals (pack, contradiction_id, "
        "subject_key, body_md, positive_claim_ids_json, "
        "negative_claim_ids_json, source_object_ids_json, "
        "synthesized_at, llm_model, prompt_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pack, "contradiction::xx", "memory definition",
         "## 争议核心\n\nContradiction body. More text.",
         json.dumps(["a::aa"]), json.dumps(["b::bb"]),
         json.dumps(["a", "b"]),
         "2026-05-04T01:00:00.000000+00:00", "m", "v1"),
    )
    conn.execute(
        "INSERT INTO crystal_scores VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pack, "contradiction", "contradiction::xx", 0.65,
         0.4, 0.3, 1.0, 0.0, 0.5,
         "2026-05-04T12:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return vault, db


class TestBuildCuratedAtlas:
    def test_orders_by_score_descending(self, tmp_path):
        vault, db = _seed_minimal(tmp_path)
        conn = sqlite3.connect(db)
        try:
            atlas = build_curated_atlas(conn, pack="research-tech")
        finally:
            conn.close()
        assert atlas.total_chains == 4
        scores = [e.score for e in atlas.entries]
        assert scores == sorted(scores, reverse=True)
        # Highest-scored crystal is rank 1.
        assert atlas.entries[0].crystal_id == "cluster::high"
        # Contradiction at 0.65 ranks ahead of mid at 0.50.
        assert atlas.entries[1].crystal_kind == "contradiction"

    def test_top_n_caps_results(self, tmp_path):
        vault, db = _seed_minimal(tmp_path)
        conn = sqlite3.connect(db)
        try:
            atlas = build_curated_atlas(conn, pack="research-tech", top_n=2)
        finally:
            conn.close()
        assert len(atlas.entries) == 2
        # total_chains reports the FULL count, not the truncated one.
        assert atlas.total_chains == 4

    def test_empty_pack_yields_empty_atlas(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        db = vault / "60-Logs" / "knowledge.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA)
        try:
            atlas = build_curated_atlas(conn, pack="research-tech")
        finally:
            conn.close()
        assert atlas.total_chains == 0
        assert atlas.entries == ()

    def test_ranks_are_one_indexed_and_dense(self, tmp_path):
        vault, db = _seed_minimal(tmp_path)
        conn = sqlite3.connect(db)
        try:
            atlas = build_curated_atlas(conn, pack="research-tech")
        finally:
            conn.close()
        for i, entry in enumerate(atlas.entries, start=1):
            assert entry.rank == i

    def test_entry_carries_score_breakdown(self, tmp_path):
        vault, db = _seed_minimal(tmp_path)
        conn = sqlite3.connect(db)
        try:
            atlas = build_curated_atlas(conn, pack="research-tech")
        finally:
            conn.close()
        # The reasoning fields are required so BL-046's "why this
        # crystal" rendering is data-driven, not recomputed.
        for entry in atlas.entries:
            assert 0.0 <= entry.size_norm <= 1.0
            assert 0.0 <= entry.credibility_norm <= 1.0
            assert 0.0 <= entry.contradiction_norm <= 1.0


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


class TestRenderCuratedAtlasMarkdown:
    def test_frontmatter_carries_projection_metadata(self, tmp_path):
        atlas = _make_atlas([CuratedEntry(
            rank=1, crystal_kind="community", crystal_id="cluster::aa",
            label="L", score=0.5,
            size_norm=0.5, credibility_norm=0.5, contradiction_norm=0.5,
            reuse_recency_norm=0.0, evergreen_recency_norm=0.5,
            teaser="Teaser sentence.", source_slugs=("a",),
        )])
        md = render_curated_atlas_markdown(atlas)
        assert md.startswith("---\n")
        assert "type: curated_atlas" in md
        assert f"format_version: {CURATED_ATLAS_FORMAT_VERSION}" in md
        assert "top_n: 30" in md
        assert "total_chains: 1" in md
        # Standard projection_* lineage from PR #139 / projection_labels.
        assert "projection_kind: compiled_wiki_projection" in md
        assert "projection_surface: curated_atlas" in md
        assert "projection_generated_by: ovp-build-curated-atlas" in md

    def test_entry_includes_wikilink_and_score_breakdown(self):
        atlas = _make_atlas([CuratedEntry(
            rank=1, crystal_kind="community",
            crystal_id="cluster::deadbeef1234", label="Test label",
            score=0.789,
            size_norm=0.95, credibility_norm=0.78, contradiction_norm=0.50,
            reuse_recency_norm=0.0, evergreen_recency_norm=0.81,
            teaser="The teaser sentence.", source_slugs=("a", "b"),
        )])
        md = render_curated_atlas_markdown(atlas)
        # Wikilink renders with safe-id (no `cluster::` prefix).
        assert "[[deadbeef1234|Test label]]" in md
        # Score breakdown line shows every signal value.
        assert "size 0.95" in md
        assert "credibility 0.78" in md
        assert "contradiction 0.50" in md
        # Teaser appears in the body.
        assert "The teaser sentence." in md
        # Score appears in the heading.
        assert "score 0.789" in md

    def test_contradiction_uses_contradiction_prefix(self):
        atlas = _make_atlas([CuratedEntry(
            rank=1, crystal_kind="contradiction",
            crystal_id="contradiction::abc999",
            label="Memory: stored vs emergent", score=0.65,
            size_norm=0.4, credibility_norm=0.3, contradiction_norm=1.0,
            reuse_recency_norm=0.0, evergreen_recency_norm=0.5,
            teaser="The contradiction teaser.",
            source_slugs=("a", "b"),
        )])
        md = render_curated_atlas_markdown(atlas)
        # contradiction crystal filename has the ``contradiction-`` prefix.
        assert "[[contradiction-abc999|Memory: stored vs emergent]]" in md

    def test_empty_atlas_explains_state(self):
        empty = CuratedAtlas(
            pack="t", top_n=30, total_chains=0, entries=(),
            generated_at="2026-05-04T12:00:00+00:00",
        )
        md = render_curated_atlas_markdown(empty)
        # Keep the file usable even when no crystals are scored —
        # tells the operator how to populate it.
        assert "No crystals scored" in md
        assert "ovp-knowledge-index" in md


# ---------------------------------------------------------------------------
# write_curated_atlas — atomic write + safety
# ---------------------------------------------------------------------------


class TestWriteCuratedAtlas:
    def test_writes_to_curated_atlas_md(self, tmp_path):
        vault, db = _seed_minimal(tmp_path)
        atlas, target = write_curated_atlas(
            vault, db_path=db, pack="research-tech",
        )
        # Lands exactly at 40-Resources/CuratedAtlas.md.
        assert target == (vault / CURATED_ATLAS_REL).resolve()
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "type: curated_atlas" in content
        assert "Top topic" in content

    def test_dry_run_does_not_write(self, tmp_path):
        vault, db = _seed_minimal(tmp_path)
        atlas, target = write_curated_atlas(
            vault, db_path=db, pack="research-tech", dry_run=True,
        )
        # Reports the path but doesn't create it.
        assert target == (vault / CURATED_ATLAS_REL).resolve()
        assert not target.exists()

    def test_atomic_replace_on_overwrite(self, tmp_path):
        # Re-writing must not leave a half-written file.  Hard to
        # test the actual atomicity from outside, but at minimum
        # verify that re-running produces a consistent file.
        vault, db = _seed_minimal(tmp_path)
        atlas1, target = write_curated_atlas(
            vault, db_path=db, pack="research-tech",
        )
        first_content = target.read_text(encoding="utf-8")
        atlas2, _ = write_curated_atlas(
            vault, db_path=db, pack="research-tech",
        )
        second_content = target.read_text(encoding="utf-8")
        # Same DB state → same atlas content (modulo generated_at line).
        assert first_content.replace(atlas1.generated_at, "_") == \
            second_content.replace(atlas2.generated_at, "_")
