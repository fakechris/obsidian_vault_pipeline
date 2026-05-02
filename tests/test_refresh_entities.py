"""Phase C.3: Entity .md 自动刷新 + 数据清洗 测试.

验证:
- frontmatter 刷新逻辑
- 孤儿 Entity .md 文件检测
- Registry 条目无对应 .md 文件检测
- entity_type mismatch 检测
- --fix 实际写入修复
- dry-run 不修改文件
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.commands.refresh_entities import run
from ovp_pipeline.entity_registry import EntityRegistry


@pytest.fixture
def refresh_vault(tmp_path):
    """Vault with Entity files and a registry that may be out of sync."""
    entity_dir = tmp_path / "10-Knowledge" / "Entity"
    candidates_dir = entity_dir / "_Candidates"
    candidates_dir.mkdir(parents=True)
    (tmp_path / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (tmp_path / "60-Logs").mkdir(parents=True)

    registry = EntityRegistry(tmp_path).load()
    registry.upsert_candidate(
        slug="anthropic", title="Anthropic", entity_type="company",
        aliases=["Anthropic AI"], confidence=0.95,
    )
    registry.promote_to_active("anthropic")

    registry.upsert_candidate(
        slug="openai", title="OpenAI", entity_type="company",
        aliases=["OpenAI Inc"], confidence=0.90,
    )
    registry.promote_to_active("openai")

    registry.upsert_candidate(
        slug="gpt", title="GPT", entity_type="tool",
        aliases=["ChatGPT"], confidence=0.90,
    )
    registry.save()

    (entity_dir / "anthropic.md").write_text(
        """---
note_id: anthropic
title: "Anthropic"
type: entity
entity_type: company
date: 2026-04-01
tags: [entity, company]
aliases: ["Anthropic AI"]
---

# Anthropic

Anthropic is an AI safety company.
""",
        encoding="utf-8",
    )

    (entity_dir / "openai.md").write_text(
        """---
note_id: openai
title: "OpenAI"
type: entity
entity_type: tool
date: 2026-04-01
tags: [entity, tool]
aliases: ["Open AI"]
---

# OpenAI

OpenAI builds GPT models.
""",
        encoding="utf-8",
    )

    (entity_dir / "orphan-entity.md").write_text(
        """---
note_id: orphan-entity
title: "Orphan Entity"
type: entity
entity_type: company
---

# Orphan Entity

This file has no registry entry.
""",
        encoding="utf-8",
    )

    return tmp_path


class TestDryRun:
    def test_dry_run_reports_issues(self, refresh_vault):
        result = run(refresh_vault, dry_run=True)
        assert result["dry_run"] is True
        assert result["fix_applied"] is False

    def test_dry_run_no_file_modification(self, refresh_vault):
        entity_dir = refresh_vault / "10-Knowledge" / "Entity"
        before = (entity_dir / "openai.md").read_text(encoding="utf-8")
        run(refresh_vault, dry_run=True)
        after = (entity_dir / "openai.md").read_text(encoding="utf-8")
        assert before == after


class TestOrphanDetection:
    def test_detects_orphan_files(self, refresh_vault):
        result = run(refresh_vault, dry_run=True)
        assert "orphan-entity" in result["orphan_files"]

    def test_no_false_positives_for_active(self, refresh_vault):
        result = run(refresh_vault, dry_run=True)
        assert "anthropic" not in result["orphan_files"]
        assert "openai" not in result["orphan_files"]


class TestMissingFiles:
    def test_detects_missing_entity_file(self, refresh_vault):
        entity_dir = refresh_vault / "10-Knowledge" / "Entity"
        registry = EntityRegistry(refresh_vault).load()
        registry.upsert_candidate(
            slug="new-entity", title="New Entity", entity_type="person",
            aliases=[], confidence=0.95,
        )
        registry.promote_to_active("new-entity")
        registry.save()

        result = run(refresh_vault, dry_run=True)
        assert "new-entity" in result["missing_files"]

    def test_fix_creates_missing_file(self, refresh_vault):
        entity_dir = refresh_vault / "10-Knowledge" / "Entity"
        registry = EntityRegistry(refresh_vault).load()
        registry.upsert_candidate(
            slug="new-entity", title="New Entity", entity_type="person",
            aliases=[], confidence=0.95,
        )
        registry.promote_to_active("new-entity")
        registry.save()

        run(refresh_vault, dry_run=False, fix=True)
        assert (entity_dir / "new-entity.md").exists()


class TestTypeMismatch:
    def test_detects_type_mismatch(self, refresh_vault):
        result = run(refresh_vault, dry_run=True)
        mismatches = result["type_mismatches"]
        slugs = [m["slug"] for m in mismatches]
        assert "openai" in slugs
        openai_m = next(m for m in mismatches if m["slug"] == "openai")
        assert openai_m["file_type"] == "tool"
        assert openai_m["registry_type"] == "company"

    def test_fix_corrects_type_mismatch(self, refresh_vault):
        run(refresh_vault, dry_run=False, fix=True)

        entity_dir = refresh_vault / "10-Knowledge" / "Entity"
        text = (entity_dir / "openai.md").read_text(encoding="utf-8")
        assert "entity_type: company" in text


class TestAliasRefresh:
    def test_detects_alias_drift(self, refresh_vault):
        result = run(refresh_vault, dry_run=True)
        assert "openai" in result["refreshed"]

    def test_fix_updates_aliases(self, refresh_vault):
        run(refresh_vault, dry_run=False, fix=True)
        entity_dir = refresh_vault / "10-Knowledge" / "Entity"
        text = (entity_dir / "openai.md").read_text(encoding="utf-8")
        assert "OpenAI Inc" in text


class TestMissingDirectory:
    def test_missing_entity_dir(self, tmp_path):
        result = run(tmp_path, dry_run=True)
        assert result.get("error") == "directory_not_found"
