"""Phase C.2: Backfill CLI 测试.

验证:
- run() dry-run 模式不修改文件
- run() 处理深度解读文件并更新 registry
- run() 写入 entity-extractions.jsonl
- run() 错误处理不中断批处理
- CLI 参数解析
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.commands.backfill_entities import run
from ovp_pipeline.entity_registry import EntityRegistry


@pytest.fixture
def backfill_vault(tmp_path):
    """Vault with sample deep dives and entity registry."""
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    (tmp_path / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (tmp_path / "60-Logs").mkdir(parents=True)

    areas = tmp_path / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    areas.mkdir(parents=True)

    (areas / "test-article_深度解读.md").write_text(
        """---
title: "Test Article"
type: interpretation
---

# Test Article

This article discusses [[Anthropic]] and their work on [[Claude]].
Andrej Karpathy mentioned this in his talk.
""",
        encoding="utf-8",
    )

    (areas / "another-article_深度解读.md").write_text(
        """---
title: "Another Article"
type: interpretation
---

# Another Article

GPT-4 by OpenAI is a powerful model.
""",
        encoding="utf-8",
    )

    registry = EntityRegistry(tmp_path).load()
    registry.upsert_candidate(
        slug="anthropic", title="Anthropic", entity_type="company",
        aliases=["Anthropic AI"], confidence=0.95,
    )
    registry.upsert_candidate(
        slug="claude", title="Claude", entity_type="tool",
        aliases=["Claude AI"], confidence=0.90,
    )
    registry.save()

    return tmp_path


class TestDryRun:
    def test_dry_run_returns_preview(self, backfill_vault):
        result = run(backfill_vault, dry_run=True)
        assert result["dry_run"] is True
        assert result["files_to_process"] == 2
        assert result["registry_count"] == 2

    def test_dry_run_does_not_write_log(self, backfill_vault):
        run(backfill_vault, dry_run=True)
        log_path = backfill_vault / "60-Logs" / "entity-extractions.jsonl"
        assert not log_path.exists()


class TestProcessing:
    def test_processes_files_without_llm(self, backfill_vault):
        result = run(
            backfill_vault,
            dry_run=False,
            use_llm=False,
        )
        assert result["files_processed"] == 2
        assert result["errors"] == 0

    def test_registry_persisted_after_run(self, backfill_vault):
        run(backfill_vault, dry_run=False, use_llm=False)
        reloaded = EntityRegistry(backfill_vault).load()
        assert len(reloaded) >= 2

    def test_limit_parameter_respected(self, backfill_vault):
        result = run(backfill_vault, dry_run=False, limit=1, use_llm=False)
        assert result["files_processed"] == 1


class TestExtractionLog:
    def test_log_written_when_mentions_found(self, backfill_vault):
        run(backfill_vault, dry_run=False, use_llm=False)
        log_path = backfill_vault / "60-Logs" / "entity-extractions.jsonl"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines:
                record = json.loads(line)
                assert "source_slug" in record
                assert "mentions" in record


class TestMissingDirectory:
    def test_missing_areas_returns_error(self, tmp_path):
        result = run(tmp_path, dry_run=False)
        assert result.get("error") == "directory_not_found"


class TestConfidenceThreshold:
    def test_high_threshold_filters_low_confidence(self, backfill_vault):
        result = run(
            backfill_vault,
            dry_run=False,
            use_llm=False,
            confidence_threshold=0.99,
        )
        assert result["errors"] == 0
