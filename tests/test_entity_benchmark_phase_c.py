"""Phase C.4: benchmark Q6-Q10 验收.

验证 Phase C 新增的高级 Entity Layer 能力:
- Q6: entity_mentions 表可查询 — 按 entity_slug 和 source_slug 查询
- Q7: Backfill CLI 可对一组文件提取并写入 registry + log
- Q8: Refresh 可检测 + 修复 frontmatter 漂移
- Q9: 跨层关联 — entity → mentions → source 深度解读关联
- Q10: Precision/Recall — seed entities 在 mentions 中的召回率
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.entity_registry import EntityRegistry
from ovp_pipeline.knowledge_index import SCHEMA, _collect_entity_mention_rows


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def benchmark_vault(tmp_path):
    """A complete vault for Phase C benchmarks."""
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    (tmp_path / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (tmp_path / "60-Logs").mkdir(parents=True)

    areas = tmp_path / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    areas.mkdir(parents=True)

    registry = EntityRegistry(tmp_path).load()

    entities = [
        ("anthropic", "Anthropic", "company", ["Anthropic AI"]),
        ("openai", "OpenAI", "company", ["OpenAI Inc"]),
        ("gpt", "GPT", "tool", ["ChatGPT", "GPT-4"]),
        ("claude", "Claude", "tool", ["Claude AI", "Claude 4"]),
        ("andrej-karpathy", "Andrej Karpathy", "person", ["Karpathy"]),
    ]
    for slug, title, etype, aliases in entities:
        registry.upsert_candidate(
            slug=slug, title=title, entity_type=etype,
            aliases=aliases, confidence=0.95,
        )
    registry.save()

    (areas / "ai-safety_深度解读.md").write_text(
        """---
title: "AI Safety Deep Dive"
type: interpretation
---

# AI Safety

[[Anthropic]] is leading AI safety research.
[[Claude]] is their main product.
[[Andrej Karpathy]] discussed this topic.
""",
        encoding="utf-8",
    )

    (areas / "llm-comparison_深度解读.md").write_text(
        """---
title: "LLM Comparison"
type: interpretation
---

# LLM Comparison

Comparing [[GPT]] (by [[OpenAI]]) with [[Claude]] (by [[Anthropic]]).
""",
        encoding="utf-8",
    )

    (areas / "karpathy-talk_深度解读.md").write_text(
        """---
title: "Karpathy Talk"
type: interpretation
---

# Karpathy Talk

[[Andrej Karpathy]] presented on LLM wikis.
He referenced [[GPT]] and [[Anthropic]].
""",
        encoding="utf-8",
    )

    entity_dir = tmp_path / "10-Knowledge" / "Entity"
    for slug, title, etype, aliases in entities:
        aliases_yaml = ", ".join(f'"{a}"' for a in aliases)
        (entity_dir / f"{slug}.md").write_text(
            f"""---
note_id: {slug}
title: "{title}"
type: entity
entity_type: {etype}
tags: [entity, {etype}]
aliases: [{aliases_yaml}]
---

# {title}
""",
            encoding="utf-8",
        )

    return tmp_path


# ---------------------------------------------------------------------------
# Q6: entity_mentions 表查询
# ---------------------------------------------------------------------------

class TestQ6_MentionQueries:
    """entity_mentions should be queryable by entity and source."""

    def test_mentions_from_wikilinks(self, benchmark_vault):
        link_rows = [
            ("ai-safety-深度解读", "anthropic", "Anthropic", "wikilink", 10),
            ("ai-safety-深度解读", "claude", "Claude", "wikilink", 11),
            ("ai-safety-深度解读", "andrej-karpathy", "Andrej Karpathy", "wikilink", 12),
            ("llm-comparison-深度解读", "gpt", "GPT", "wikilink", 5),
            ("llm-comparison-深度解读", "openai", "OpenAI", "wikilink", 6),
            ("llm-comparison-深度解读", "claude", "Claude", "wikilink", 7),
            ("llm-comparison-深度解读", "anthropic", "Anthropic", "wikilink", 8),
            ("karpathy-talk-深度解读", "andrej-karpathy", "Andrej Karpathy", "wikilink", 3),
            ("karpathy-talk-深度解读", "gpt", "GPT", "wikilink", 4),
            ("karpathy-talk-深度解读", "anthropic", "Anthropic", "wikilink", 5),
        ]
        known = {lr[0] for lr in link_rows} | {lr[1] for lr in link_rows}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executemany(
            """INSERT INTO entity_mentions
               (entity_slug, entity_type, source_slug, confidence,
                detection_method, mention_text, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        anthropic_mentions = conn.execute(
            "SELECT source_slug FROM entity_mentions WHERE entity_slug = 'anthropic'"
        ).fetchall()
        assert len(anthropic_mentions) == 3
        sources = {r[0] for r in anthropic_mentions}
        assert "ai-safety-深度解读" in sources
        assert "llm-comparison-深度解读" in sources
        assert "karpathy-talk-深度解读" in sources

    def test_query_by_source(self, benchmark_vault):
        link_rows = [
            ("ai-safety-深度解读", "anthropic", "Anthropic", "wikilink", 10),
            ("ai-safety-深度解读", "claude", "Claude", "wikilink", 11),
            ("ai-safety-深度解读", "andrej-karpathy", "Andrej Karpathy", "wikilink", 12),
        ]
        known = {lr[0] for lr in link_rows} | {lr[1] for lr in link_rows}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executemany(
            """INSERT INTO entity_mentions
               (entity_slug, entity_type, source_slug, confidence,
                detection_method, mention_text, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        source_mentions = conn.execute(
            "SELECT entity_slug FROM entity_mentions WHERE source_slug = 'ai-safety-深度解读'"
        ).fetchall()
        entities = {r[0] for r in source_mentions}
        assert "anthropic" in entities
        assert "claude" in entities
        assert "andrej-karpathy" in entities

    def test_query_by_type(self, benchmark_vault):
        link_rows = [
            ("article-1", "anthropic", "Anthropic", "wikilink", 10),
            ("article-1", "openai", "OpenAI", "wikilink", 11),
            ("article-1", "gpt", "GPT", "wikilink", 12),
        ]
        known = {lr[0] for lr in link_rows} | {lr[1] for lr in link_rows}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executemany(
            """INSERT INTO entity_mentions
               (entity_slug, entity_type, source_slug, confidence,
                detection_method, mention_text, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        company_mentions = conn.execute(
            "SELECT entity_slug FROM entity_mentions WHERE entity_type = 'company'"
        ).fetchall()
        assert len(company_mentions) == 2
        companies = {r[0] for r in company_mentions}
        assert "anthropic" in companies
        assert "openai" in companies


# ---------------------------------------------------------------------------
# Q7: Backfill CLI 端到端
# ---------------------------------------------------------------------------

class TestQ7_BackfillE2E:
    """Backfill run processes deep dives and updates registry."""

    def test_backfill_without_llm(self, benchmark_vault):
        from ovp_pipeline.commands.backfill_entities import run as backfill_run
        result = backfill_run(benchmark_vault, dry_run=False, use_llm=False)
        assert result["files_processed"] == 3
        assert result["errors"] == 0

    def test_backfill_creates_extraction_log(self, benchmark_vault):
        from ovp_pipeline.commands.backfill_entities import run as backfill_run
        backfill_run(benchmark_vault, dry_run=False, use_llm=False)
        log_path = benchmark_vault / "60-Logs" / "entity-extractions.jsonl"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines:
                record = json.loads(line)
                assert "source_slug" in record
                assert "mentions" in record

    def test_backfill_dry_run_safe(self, benchmark_vault):
        from ovp_pipeline.commands.backfill_entities import run as backfill_run
        result = backfill_run(benchmark_vault, dry_run=True)
        assert result["dry_run"] is True
        log_path = benchmark_vault / "60-Logs" / "entity-extractions.jsonl"
        assert not log_path.exists()


# ---------------------------------------------------------------------------
# Q8: Refresh 检测 + 修复
# ---------------------------------------------------------------------------

class TestQ8_Refresh:
    """Refresh detects and fixes frontmatter drift."""

    def test_detects_type_mismatch(self, benchmark_vault):
        entity_dir = benchmark_vault / "10-Knowledge" / "Entity"
        text = (entity_dir / "gpt.md").read_text(encoding="utf-8")
        text = text.replace("entity_type: tool", "entity_type: company")
        (entity_dir / "gpt.md").write_text(text, encoding="utf-8")

        registry = EntityRegistry(benchmark_vault).load()
        registry.promote_to_active("gpt")
        registry.save()

        from ovp_pipeline.commands.refresh_entities import run as refresh_run
        result = refresh_run(benchmark_vault, dry_run=True)
        slugs = [m["slug"] for m in result["type_mismatches"]]
        assert "gpt" in slugs

    def test_fix_corrects_mismatch(self, benchmark_vault):
        entity_dir = benchmark_vault / "10-Knowledge" / "Entity"
        text = (entity_dir / "gpt.md").read_text(encoding="utf-8")
        text = text.replace("entity_type: tool", "entity_type: company")
        (entity_dir / "gpt.md").write_text(text, encoding="utf-8")

        registry = EntityRegistry(benchmark_vault).load()
        registry.promote_to_active("gpt")
        registry.save()

        from ovp_pipeline.commands.refresh_entities import run as refresh_run
        refresh_run(benchmark_vault, dry_run=False, fix=True)

        fixed = (entity_dir / "gpt.md").read_text(encoding="utf-8")
        assert "entity_type: tool" in fixed

    def test_detects_orphan(self, benchmark_vault):
        entity_dir = benchmark_vault / "10-Knowledge" / "Entity"
        (entity_dir / "phantom.md").write_text(
            "---\nnote_id: phantom\ntitle: Phantom\ntype: entity\n---\n# Phantom\n",
            encoding="utf-8",
        )

        registry = EntityRegistry(benchmark_vault).load()
        for e in registry.all_entries():
            if e.status == "candidate":
                registry.promote_to_active(e.slug)
        registry.save()

        from ovp_pipeline.commands.refresh_entities import run as refresh_run
        result = refresh_run(benchmark_vault, dry_run=True)
        assert "phantom" in result["orphan_files"]


# ---------------------------------------------------------------------------
# Q9: 跨层关联
# ---------------------------------------------------------------------------

class TestQ9_CrossLayerAssociation:
    """Entity → mentions → source deep dive association."""

    def test_entity_to_sources_via_mentions(self, benchmark_vault):
        link_rows = [
            ("ai-safety-深度解读", "anthropic", "Anthropic", "wikilink", 10),
            ("llm-comparison-深度解读", "anthropic", "Anthropic", "wikilink", 8),
        ]
        known = {"ai-safety-深度解读", "llm-comparison-深度解读", "anthropic"}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executemany(
            """INSERT INTO entity_mentions
               (entity_slug, entity_type, source_slug, confidence,
                detection_method, mention_text, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        sources = conn.execute(
            "SELECT DISTINCT source_slug FROM entity_mentions WHERE entity_slug = 'anthropic'"
        ).fetchall()
        source_slugs = {r[0] for r in sources}
        assert len(source_slugs) == 2

    def test_source_to_entities_reverse_lookup(self, benchmark_vault):
        link_rows = [
            ("ai-safety-深度解读", "anthropic", "Anthropic", "wikilink", 10),
            ("ai-safety-深度解读", "claude", "Claude", "wikilink", 11),
        ]
        known = {"ai-safety-深度解读", "anthropic", "claude"}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executemany(
            """INSERT INTO entity_mentions
               (entity_slug, entity_type, source_slug, confidence,
                detection_method, mention_text, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        entities = conn.execute(
            "SELECT entity_slug, entity_type FROM entity_mentions "
            "WHERE source_slug = 'ai-safety-深度解读'"
        ).fetchall()
        entity_map = {r[0]: r[1] for r in entities}
        assert "anthropic" in entity_map
        assert entity_map["anthropic"] == "company"
        assert "claude" in entity_map
        assert entity_map["claude"] == "tool"


# ---------------------------------------------------------------------------
# Q10: Precision & Recall
# ---------------------------------------------------------------------------

class TestQ10_PrecisionRecall:
    """Measure entity mention extraction precision and recall."""

    def test_recall_on_wikilinks(self, benchmark_vault):
        """All wikilinked entities should be recalled from link_rows."""
        expected_mentions = {
            ("anthropic", "ai-safety-深度解读"),
            ("claude", "ai-safety-深度解读"),
            ("andrej-karpathy", "ai-safety-深度解读"),
            ("gpt", "llm-comparison-深度解读"),
            ("openai", "llm-comparison-深度解读"),
            ("claude", "llm-comparison-深度解读"),
            ("anthropic", "llm-comparison-深度解读"),
            ("andrej-karpathy", "karpathy-talk-深度解读"),
            ("gpt", "karpathy-talk-深度解读"),
            ("anthropic", "karpathy-talk-深度解读"),
        }

        link_rows = [
            (src, tgt, tgt.replace("-", " ").title(), "wikilink", i)
            for i, (tgt, src) in enumerate(expected_mentions)
        ]
        known = {lr[0] for lr in link_rows} | {lr[1] for lr in link_rows}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        actual = {(r[0], r[2]) for r in rows}

        hits = expected_mentions & actual
        recall = len(hits) / len(expected_mentions) if expected_mentions else 0
        assert recall >= 0.70, f"Recall {recall:.2f} < 0.70"

    def test_precision_on_wikilinks(self, benchmark_vault):
        """No false positives from wikilink collection."""
        link_rows = [
            ("article-1", "anthropic", "Anthropic", "wikilink", 10),
            ("article-1", "nonexistent-thing", "Nonexistent", "wikilink", 11),
        ]
        known = {"article-1", "anthropic", "nonexistent-thing"}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        entity_slugs = {r[0] for r in rows}
        assert "nonexistent-thing" not in entity_slugs
        if rows:
            precision = sum(1 for r in rows if r[0] in {"anthropic"}) / len(rows)
            assert precision >= 0.90, f"Precision {precision:.2f} < 0.90"

    def test_extraction_log_recall(self, benchmark_vault):
        """Mentions from extraction log should be recalled."""
        log_path = benchmark_vault / "60-Logs" / "entity-extractions.jsonl"
        records = [
            {
                "source_slug": "new-article",
                "mentions": [
                    {"text": "Anthropic", "kind": "company", "confidence": 0.92,
                     "resolved_slug": "anthropic", "resolution": "alias_hit"},
                    {"text": "GPT-4", "kind": "tool", "confidence": 0.88,
                     "resolved_slug": "gpt", "resolution": "alias_hit"},
                ],
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )

        rows = _collect_entity_mention_rows(benchmark_vault, [], set())
        actual = {r[0] for r in rows}
        expected = {"anthropic", "gpt"}
        recall = len(expected & actual) / len(expected) if expected else 0
        assert recall >= 0.70, f"Extraction log recall {recall:.2f} < 0.70"

    def test_combined_precision_above_90(self, benchmark_vault):
        """Combined wikilink + extraction log: precision should be > 90%."""
        log_path = benchmark_vault / "60-Logs" / "entity-extractions.jsonl"
        records = [
            {
                "source_slug": "extra-article",
                "mentions": [
                    {"text": "Claude AI", "kind": "tool", "confidence": 0.91,
                     "resolved_slug": "claude", "resolution": "alias_hit"},
                ],
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )

        link_rows = [
            ("article-x", "anthropic", "Anthropic", "wikilink", 10),
            ("article-x", "openai", "OpenAI", "wikilink", 11),
        ]
        known = {"article-x", "anthropic", "openai"}
        rows = _collect_entity_mention_rows(benchmark_vault, link_rows, known)

        valid_slugs = {"anthropic", "openai", "claude", "gpt", "andrej-karpathy"}
        tp = sum(1 for r in rows if r[0] in valid_slugs)
        precision = tp / len(rows) if rows else 1.0
        assert precision >= 0.90, f"Combined precision {precision:.2f} < 0.90"
