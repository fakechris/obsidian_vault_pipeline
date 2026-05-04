"""Tests for synthesis/community_crystal.py — BL-042 Crystal MVP.

These tests mock the LLM client so they don't hit MiniMax.  The
real LLM wiring (``llm_client.get_litellm_client``) is exercised
by the CLI, which is not unit-tested here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.synthesis.community_crystal import (
    CRYSTAL_DIR_REL,
    CRYSTAL_PROMPT_VERSION,
    CommunityCrystal,
    _crystal_filename,
    _select_top_members,
    _strip_frontmatter,
    render_crystal_markdown,
    synthesize_community_crystals,
)


SCHEMA_CREATE = """
CREATE TABLE objects (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
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
  PRIMARY KEY (pack, cluster_id, synthesized_at)
);
"""


class _StubLLM:
    """Minimal LLM client that records calls and returns a fixed body."""

    def __init__(self, body: str = "## 概念核心\n\n这是合成的 crystal 正文。"):
        self.body = body
        self.calls: list[tuple[str, str, int]] = []

    def call(self, system_prompt: str, user_prompt: str,
             *, max_tokens: int = 2000) -> str:
        self.calls.append((system_prompt, user_prompt, max_tokens))
        return self.body


class _RaisingLLM:
    """Stub that always raises — used to test failure-mode resilience."""

    def __init__(self):
        self.calls = 0

    def call(self, *_, **__) -> str:
        self.calls += 1
        raise RuntimeError("simulated LLM outage")


def _seed_vault(
    tmp_path: Path,
    *,
    pack: str = "research-tech",
    clusters: list[tuple[str, str, list[str]]] | None = None,
    objects: list[tuple[str, str, str]] | None = None,  # (object_id, title, body)
) -> tuple[Path, Path]:
    """Build a synthetic vault: schema + seeded rows + evergreen files.

    Returns ``(vault_dir, db_path)``.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = vault / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_CREATE)
    for object_id, title, body in (objects or []):
        canonical = f"10-Knowledge/Evergreen/{object_id}.md"
        full = vault / canonical
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            (pack, object_id, "evergreen", title, canonical, ""),
        )
    for cluster_id, label, members in (clusters or []):
        conn.execute(
            "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pack, cluster_id, "louvain_community", label,
                members[0] if members else "",
                json.dumps(members, ensure_ascii=False),
                float(len(members)),
            ),
        )
    conn.commit()
    conn.close()
    return vault, db_path


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class TestSelectTopMembers:
    def test_takes_top_k_sorted(self):
        # Deterministic by object_id so re-runs produce stable prompts.
        out = _select_top_members(["c", "a", "b", "d"], top_k=2)
        assert out == ["a", "b"]

    def test_returns_all_when_k_exceeds_count(self):
        out = _select_top_members(["b", "a"], top_k=10)
        assert out == ["a", "b"]

    def test_zero_k_returns_empty(self):
        assert _select_top_members(["a", "b"], top_k=0) == []


# ---------------------------------------------------------------------------
# Filename safety
# ---------------------------------------------------------------------------


class TestStripFrontmatter:
    """Frontmatter on ~7000 evergreens × top_k notes per crystal call
    is a meaningful slice of the prompt budget.  Pre-fix the loader
    sent the whole file including frontmatter; post-fix only the
    body content reaches the LLM."""

    def test_strips_simple_frontmatter(self):
        text = (
            "---\n"
            "note_id: x\n"
            "title: X\n"
            "---\n"
            "body content"
        )
        assert _strip_frontmatter(text) == "body content"

    def test_no_frontmatter_passes_through(self):
        # File with no leading ``---`` returns unchanged.
        text = "# Title\n\nbody"
        assert _strip_frontmatter(text) == text

    def test_unclosed_frontmatter_passes_through(self):
        # Malformed input returns unchanged — better than dropping content.
        text = "---\nnote_id: x\nbody never closed"
        assert _strip_frontmatter(text) == text

    def test_frontmatter_stripped_in_pipeline(self, tmp_path):
        # End-to-end: the frontmatter on a seeded evergreen does NOT
        # appear in the user prompt sent to the LLM.
        vault, db = _seed_vault(
            tmp_path,
            clusters=[("cluster::xx", "C", ["a"])],
            objects=[(
                "a", "A",
                "---\nnote_id: a\ntitle: A\n---\n实际正文内容\n",
            )],
        )
        llm = _StubLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # One LLM call.  The user prompt must contain the body but
        # NOT the frontmatter keys.
        assert len(llm.calls) == 1
        _, user_prompt, _ = llm.calls[0]
        assert "实际正文内容" in user_prompt
        assert "note_id: a" not in user_prompt
        assert "title: A" not in user_prompt


class TestCrystalFilename:
    def test_strips_cluster_prefix(self):
        # The DB cluster_id format is `cluster::<sha1>`, but `:` is
        # not a portable filename character.  Strip the prefix so
        # the file lands as `<sha1>.md`.
        assert _crystal_filename("cluster::abc123def456") == "abc123def456.md"

    def test_falls_back_when_prefix_absent(self):
        # Defensive — if a future caller passes a different shape
        # we still produce a usable filename.
        assert _crystal_filename("foo") == "foo.md"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


class TestRenderCrystalMarkdown:
    def test_frontmatter_carries_lineage(self):
        c = CommunityCrystal(
            pack="research-tech",
            cluster_id="cluster::deadbeef1234",
            body_md="## 概念核心\n\nbody",
            source_evergreen_slugs=("a", "b"),
            synthesized_at="2026-05-03T10:00:00+00:00",
            llm_model="anthropic/MiniMax-M2.7-highspeed",
            prompt_version=CRYSTAL_PROMPT_VERSION,
        )
        md = render_crystal_markdown(c, label="Test community")
        # Frontmatter present + key lineage fields rendered.
        assert md.startswith("---\n")
        assert "type: community_crystal" in md
        assert "cluster_id: cluster::deadbeef1234" in md
        assert "label: \"Test community\"" in md
        assert "synthesized_at: 2026-05-03T10:00:00+00:00" in md
        assert "llm_model: anthropic/MiniMax-M2.7-highspeed" in md
        assert f"prompt_version: {CRYSTAL_PROMPT_VERSION}" in md
        assert "  - a" in md
        assert "  - b" in md
        assert "tags: [crystal, community]" in md
        # Standard projection_* metadata is rendered, matching the
        # convention shared with cluster_crystal / topic_view.
        assert "projection_kind: compiled_wiki_projection" in md
        assert "projection_surface: community_crystal" in md
        assert "projection_owner_pack: research-tech" in md
        assert "projection_generated_by: synthesize_community_crystals" in md
        # Body comes after the closing ---
        assert "## 概念核心" in md


# ---------------------------------------------------------------------------
# End-to-end via synthesize_community_crystals
# ---------------------------------------------------------------------------


class TestSynthesizeEndToEnd:
    def test_writes_one_crystal_per_community(self, tmp_path):
        vault, db = _seed_vault(
            tmp_path,
            clusters=[
                ("cluster::aaaa1111", "AI alignment",
                 ["agent-loop", "tool-use", "value-learning"]),
                ("cluster::bbbb2222", "Knowledge graphs",
                 ["entity-merge", "graph-projection"]),
            ],
            objects=[
                ("agent-loop",   "Agent loop",   "agents iterate"),
                ("tool-use",     "Tool use",     "tools extend agents"),
                ("value-learning", "Value learning", "RLHF style"),
                ("entity-merge", "Entity merge", "identity merge"),
                ("graph-projection", "Graph projection", "wikilink graph"),
            ],
        )
        llm = _StubLLM(body="## 概念核心\n\n合成正文")
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # Two communities → two crystals.
        assert len(crystals) == 2
        # LLM called once per community.
        assert len(llm.calls) == 2
        # Markdown files written under 40-Resources/Crystals/.
        out_dir = vault / CRYSTAL_DIR_REL
        assert (out_dir / "aaaa1111.md").exists()
        assert (out_dir / "bbbb2222.md").exists()

    def test_db_row_persisted_with_lineage(self, tmp_path):
        vault, db = _seed_vault(
            tmp_path,
            clusters=[("cluster::xxxx0001", "C1", ["a", "b"])],
            objects=[("a", "A", "body a"), ("b", "B", "body b")],
        )
        llm = _StubLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT cluster_id, source_evergreen_slugs_json, llm_model, "
            "prompt_version FROM community_crystals"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        cluster_id, slugs_json, llm_model, prompt_version = rows[0]
        assert cluster_id == "cluster::xxxx0001"
        # Source lineage preserved as a JSON list.
        assert json.loads(slugs_json) == ["a", "b"]
        assert llm_model == "anthropic/MiniMax-M2.7-highspeed"
        assert prompt_version == CRYSTAL_PROMPT_VERSION

    def test_dry_run_skips_writes(self, tmp_path):
        vault, db = _seed_vault(
            tmp_path,
            clusters=[("cluster::yyyy0002", "C", ["a", "b"])],
            objects=[("a", "A", "body a"), ("b", "B", "body b")],
        )
        llm = _StubLLM()
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db, dry_run=True,
        )
        assert len(crystals) == 1
        # The LLM was still called (dry-run reports the intended output).
        assert len(llm.calls) == 1
        # But no file landed and no DB row was inserted.
        assert not (vault / CRYSTAL_DIR_REL / "yyyy0002.md").exists()
        conn = sqlite3.connect(db)
        n = conn.execute(
            "SELECT COUNT(*) FROM community_crystals"
        ).fetchone()[0]
        conn.close()
        assert n == 0

    def test_append_only_versioning(self, tmp_path):
        # Re-synthesizing the same community must produce a NEW row,
        # not overwrite — that's the foundation BL-044 builds on.
        vault, db = _seed_vault(
            tmp_path,
            clusters=[("cluster::zzzz0003", "C", ["a"])],
            objects=[("a", "A", "body a")],
        )
        llm = _StubLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # Force a different timestamp by re-running after a tick.
        import time
        time.sleep(1)
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT synthesized_at FROM community_crystals "
            "WHERE cluster_id = 'cluster::zzzz0003' "
            "ORDER BY synthesized_at"
        ).fetchall()
        conn.close()
        # Two rows, distinct timestamps.
        assert len(rows) == 2
        assert rows[0][0] != rows[1][0]

    def test_filters_by_only_cluster_ids(self, tmp_path):
        vault, db = _seed_vault(
            tmp_path,
            clusters=[
                ("cluster::keep0001", "Keep", ["a"]),
                ("cluster::drop0002", "Drop", ["b"]),
            ],
            objects=[("a", "A", "body"), ("b", "B", "body")],
        )
        llm = _StubLLM()
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
            only_cluster_ids={"cluster::keep0001"},
        )
        assert len(crystals) == 1
        assert crystals[0].cluster_id == "cluster::keep0001"

    def test_limit_communities(self, tmp_path):
        vault, db = _seed_vault(
            tmp_path,
            clusters=[
                ("cluster::aa", "A", ["a"]),
                ("cluster::bb", "B", ["b"]),
                ("cluster::cc", "C", ["c"]),
            ],
            objects=[
                ("a", "A", "body"), ("b", "B", "body"), ("c", "C", "body"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
            limit_communities=2,
        )
        assert len(crystals) == 2

    def test_llm_failure_does_not_sink_batch(self, tmp_path):
        # One bad LLM call must not take down the rest of the batch.
        # Without this, a single-cluster timeout on a 7000-evergreen
        # vault would lose 30 minutes of work mid-batch.
        vault, db = _seed_vault(
            tmp_path,
            clusters=[
                ("cluster::aa", "A", ["a"]),
                ("cluster::bb", "B", ["b"]),
            ],
            objects=[("a", "A", "body a"), ("b", "B", "body b")],
        )
        llm = _RaisingLLM()
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # Both calls attempted, both failed → zero crystals, no crash.
        assert llm.calls == 2
        assert crystals == []
        # No DB rows.
        conn = sqlite3.connect(db)
        n = conn.execute(
            "SELECT COUNT(*) FROM community_crystals"
        ).fetchone()[0]
        conn.close()
        assert n == 0

    def test_skips_communities_with_no_readable_evergreens(self, tmp_path):
        # A cluster pointing at object_ids that aren't in the
        # objects table (stale data) is skipped, not crashed on.
        vault, db = _seed_vault(
            tmp_path,
            clusters=[("cluster::stale", "Stale", ["ghost1", "ghost2"])],
            objects=[],
        )
        llm = _StubLLM()
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        assert crystals == []
        # LLM was not called — we short-circuit before constructing the prompt.
        assert llm.calls == []

    def test_only_louvain_kind_is_synthesized(self, tmp_path):
        # A row with cluster_kind='relation_component' (legacy) must
        # not be picked up by the loader — it filters on kind.  Even
        # though the column accepts any string, BL-042 only synthesizes
        # for Louvain communities.
        vault = tmp_path / "vault"
        vault.mkdir()
        db = vault / "60-Logs" / "knowledge.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA_CREATE)
        # Seed an evergreen + a non-louvain cluster.
        body = "body x"
        canonical = "10-Knowledge/Evergreen/x.md"
        (vault / canonical).parent.mkdir(parents=True, exist_ok=True)
        (vault / canonical).write_text(body, encoding="utf-8")
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            ("research-tech", "x", "evergreen", "X", canonical, ""),
        )
        conn.execute(
            "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "research-tech", "cluster::legacy", "relation_component",
                "Legacy", "x", json.dumps(["x"]), 1.0,
            ),
        )
        conn.commit()
        conn.close()
        llm = _StubLLM()
        crystals = synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # Filtered out — no Louvain rows present.
        assert crystals == []
        assert llm.calls == []
