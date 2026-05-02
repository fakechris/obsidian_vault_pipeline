"""Phase C.1: entity_mentions 表 + 增量写入 测试.

验证:
- entity_mentions 表在 knowledge.db schema 中存在
- _collect_entity_mention_rows 从 wikilinks 中收集 entity 提及
- _collect_entity_mention_rows 从 extraction JSONL 中收集 entity 提及
- 去重逻辑 (同一 entity+source 只记一次)
- rebuild_knowledge_index 返回 entity_mentions_indexed 计数
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.entity_registry import EntityRegistry
from ovp_pipeline.knowledge_index import (
    SCHEMA,
    _collect_entity_mention_rows,
)


@pytest.fixture
def entity_vault(tmp_path):
    """Temp vault with Entity directory and seeded registry."""
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    (tmp_path / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (tmp_path / "20-Areas").mkdir(parents=True)
    (tmp_path / "60-Logs").mkdir(parents=True)

    registry = EntityRegistry(tmp_path).load()
    registry.upsert_candidate(
        slug="anthropic", title="Anthropic", entity_type="company",
        aliases=["Anthropic AI"], confidence=0.95,
    )
    registry.upsert_candidate(
        slug="gpt", title="GPT", entity_type="tool",
        aliases=["ChatGPT", "GPT-4"], confidence=0.90,
    )
    registry.upsert_candidate(
        slug="andrej-karpathy", title="Andrej Karpathy", entity_type="person",
        aliases=["Karpathy"], confidence=0.95,
    )
    registry.save()
    return tmp_path


class TestSchemaContainsEntityMentions:
    """Verify entity_mentions table exists in SCHEMA DDL."""

    def test_table_defined(self):
        assert "CREATE TABLE entity_mentions" in SCHEMA

    def test_index_on_entity(self):
        assert "idx_entity_mentions_entity" in SCHEMA

    def test_index_on_source(self):
        assert "idx_entity_mentions_source" in SCHEMA

    def test_index_on_type(self):
        assert "idx_entity_mentions_type" in SCHEMA

    def test_schema_creates_table(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_mentions'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_schema_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        cursor = conn.execute("PRAGMA table_info(entity_mentions)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "entity_slug", "entity_type", "source_slug",
            "confidence", "detection_method", "mention_text", "snippet",
        }
        assert expected.issubset(columns)
        conn.close()


class TestCollectFromWikilinks:
    """Wikilinks that resolve to entity slugs produce mention rows."""

    def test_wikilink_to_entity_produces_row(self, entity_vault):
        link_rows = [
            ("my-article", "anthropic", "Anthropic", "wikilink", 10),
        ]
        known_slugs = {"my-article", "anthropic"}

        rows = _collect_entity_mention_rows(entity_vault, link_rows, known_slugs)
        entity_rows = [r for r in rows if r[0] == "anthropic" and r[2] == "my-article"]
        assert len(entity_rows) >= 1
        row = entity_rows[0]
        assert row[1] == "company"
        assert row[3] == 1.0
        assert row[4] == "wikilink"

    def test_wikilink_to_non_entity_ignored(self, entity_vault):
        link_rows = [
            ("my-article", "some-concept", "Some Concept", "wikilink", 5),
        ]
        known_slugs = {"my-article", "some-concept"}

        rows = _collect_entity_mention_rows(entity_vault, link_rows, known_slugs)
        assert len(rows) == 0

    def test_duplicate_wikilinks_deduped(self, entity_vault):
        link_rows = [
            ("my-article", "anthropic", "Anthropic", "wikilink", 10),
            ("my-article", "anthropic", "Anthropic AI", "wikilink", 20),
        ]
        known_slugs = {"my-article", "anthropic"}

        rows = _collect_entity_mention_rows(entity_vault, link_rows, known_slugs)
        entity_rows = [r for r in rows if r[0] == "anthropic" and r[2] == "my-article"]
        assert len(entity_rows) == 1


class TestCollectFromExtractionLog:
    """LLM extraction JSONL sidecar produces mention rows."""

    def test_extraction_log_produces_rows(self, entity_vault):
        log_path = entity_vault / "60-Logs" / "entity-extractions.jsonl"
        record = {
            "source_slug": "deep-dive-article",
            "mentions": [
                {
                    "text": "Anthropic",
                    "kind": "company",
                    "confidence": 0.92,
                    "snippet": "Anthropic released Claude 4",
                    "resolved_slug": "anthropic",
                    "resolution": "alias_hit",
                },
                {
                    "text": "GPT-4",
                    "kind": "tool",
                    "confidence": 0.88,
                    "snippet": "compared to GPT-4",
                    "resolved_slug": "gpt",
                    "resolution": "alias_hit",
                },
            ],
        }
        log_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

        rows = _collect_entity_mention_rows(entity_vault, [], set())
        assert len(rows) == 2

        anthro_rows = [r for r in rows if r[0] == "anthropic"]
        assert len(anthro_rows) == 1
        assert anthro_rows[0][2] == "deep-dive-article"
        assert anthro_rows[0][3] == 0.92
        assert "alias_hit" in anthro_rows[0][4]

    def test_extraction_log_dedup_with_wikilinks(self, entity_vault):
        log_path = entity_vault / "60-Logs" / "entity-extractions.jsonl"
        record = {
            "source_slug": "my-article",
            "mentions": [
                {
                    "text": "Anthropic",
                    "kind": "company",
                    "confidence": 0.92,
                    "resolved_slug": "anthropic",
                    "resolution": "alias_hit",
                },
            ],
        }
        log_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

        link_rows = [
            ("my-article", "anthropic", "Anthropic", "wikilink", 10),
        ]
        known_slugs = {"my-article", "anthropic"}

        rows = _collect_entity_mention_rows(entity_vault, link_rows, known_slugs)
        anthro_rows = [r for r in rows if r[0] == "anthropic" and r[2] == "my-article"]
        assert len(anthro_rows) == 1

    def test_missing_log_file_no_crash(self, entity_vault):
        rows = _collect_entity_mention_rows(entity_vault, [], set())
        assert isinstance(rows, list)

    def test_unresolved_mentions_skipped(self, entity_vault):
        log_path = entity_vault / "60-Logs" / "entity-extractions.jsonl"
        record = {
            "source_slug": "my-article",
            "mentions": [
                {
                    "text": "Unknown Corp",
                    "kind": "company",
                    "confidence": 0.5,
                    "resolved_slug": "",
                    "resolution": "skipped",
                },
            ],
        }
        log_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

        rows = _collect_entity_mention_rows(entity_vault, [], set())
        assert len(rows) == 0


class TestAllEntries:
    """Verify EntityRegistry.all_entries() method."""

    def test_all_entries_returns_all(self, entity_vault):
        registry = EntityRegistry(entity_vault).load()
        entries = registry.all_entries()
        assert len(entries) == 3
        slugs = {e.slug for e in entries}
        assert "anthropic" in slugs
        assert "gpt" in slugs
        assert "andrej-karpathy" in slugs

    def test_all_entries_includes_rejected(self, entity_vault):
        registry = EntityRegistry(entity_vault).load()
        entry = registry.find_by_slug("gpt")
        entry.status = "rejected"
        registry.save()

        reloaded = EntityRegistry(entity_vault).load()
        entries = reloaded.all_entries()
        assert len(entries) == 3
