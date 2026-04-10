from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from openclaw_pipeline.auto_github_processor import build_default_output_dir as github_output_dir
from openclaw_pipeline.auto_paper_processor import build_default_output_dir as paper_output_dir
from openclaw_pipeline.runtime import (
    VaultLayout,
    iter_markdown_files,
    markdown_title,
    read_markdown_frontmatter,
    resolve_vault_dir,
)
from openclaw_pipeline.unified_pipeline_enhanced import EnhancedPipeline, build_execution_plan


def test_resolve_vault_dir_returns_absolute_path(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()

    monkeypatch.chdir(workspace)

    resolved = resolve_vault_dir(Path("..") / "vault")

    assert resolved == vault.resolve()


def test_vault_layout_uses_resolved_vault_dir(tmp_path):
    layout = VaultLayout.from_vault(tmp_path / "vault")

    assert layout.pipeline_log == (tmp_path / "vault" / "60-Logs" / "pipeline.jsonl").resolve()
    assert layout.knowledge_db == (tmp_path / "vault" / "60-Logs" / "knowledge.db").resolve()
    assert layout.transactions_dir == (tmp_path / "vault" / "60-Logs" / "transactions").resolve()
    assert layout.derived_dir == (tmp_path / "vault" / "60-Logs" / "derived").resolve()
    assert layout.extraction_runs_dir == (tmp_path / "vault" / "60-Logs" / "derived" / "extraction-runs").resolve()
    assert layout.review_queue_dir == (tmp_path / "vault" / "60-Logs" / "derived" / "review-queue").resolve()
    assert layout.compiled_views_dir == (tmp_path / "vault" / "60-Logs" / "derived" / "compiled-views").resolve()
    assert layout.processing_dir == (tmp_path / "vault" / "50-Inbox" / "02-Processing").resolve()
    assert layout.classification_output_dir("tools").parts[-3:-1] == ("Tools", "Topics")
    assert layout.papers_dir == (tmp_path / "vault" / "20-Areas" / "AI-Research" / "Papers").resolve()
    assert layout.queries_dir == (tmp_path / "vault" / "20-Areas" / "Queries").resolve()


def test_specialized_processors_derive_default_outputs_from_vault(tmp_path):
    vault = tmp_path / "vault"

    github_dir = github_output_dir(vault)
    paper_dir = paper_output_dir(vault)

    assert github_dir.is_absolute()
    assert github_dir.parts[-3:-1] == ("Tools", "Topics")
    assert paper_dir == (vault / "20-Areas" / "AI-Research" / "Papers").resolve()


def test_build_execution_plan_includes_pinboard_process_for_history():
    args = Namespace(
        full=False,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=("2026-04-01", "2026-04-07"),
        pinboard_days=None,
        step=None,
        from_step=None,
    )

    plan = build_execution_plan(args)

    assert plan["steps"] == ["pinboard", "pinboard_process", "articles", "quality", "fix_links", "absorb", "registry_sync", "moc", "knowledge_index"]


def test_build_execution_plan_includes_pinboard_process_for_recent_days():
    args = Namespace(
        full=False,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=7,
        step=None,
        from_step=None,
    )

    plan = build_execution_plan(args)

    assert "pinboard_process" in plan["steps"]
    assert plan["steps"][-1] == "knowledge_index"


def test_build_execution_plan_full_can_insert_refine_before_knowledge_index():
    args = Namespace(
        full=True,
        with_refine=True,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step=None,
    )

    plan = build_execution_plan(args)

    assert plan["steps"][-2:] == ["refine", "knowledge_index"]
    assert "absorb" in plan["steps"]


def test_step_knowledge_index_invokes_rebuild_command(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["cmd"] = cmd
        captured["step_name"] = step_name
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_knowledge_index(dry_run=True)

    assert result["success"] is True
    assert captured["step_name"] == "knowledge_index"
    assert "openclaw_pipeline.commands.knowledge_index" in " ".join(captured["cmd"])
    assert "--vault-dir" in captured["cmd"]


def test_step_absorb_invokes_absorb_command(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["cmd"] = cmd
        captured["step_name"] = step_name
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_absorb(dry_run=True)

    assert result["success"] is True
    assert captured["step_name"] == "absorb"
    assert "openclaw_pipeline.commands.absorb" in " ".join(captured["cmd"])
    assert "--vault-dir" in captured["cmd"]


def test_step_refine_runs_cleanup_then_breakdown(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    commands: list[tuple[str, list[str]]] = []

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        commands.append((step_name, cmd))
        return {"success": True, "stdout": "{\"applied_count\": 1}", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_refine(dry_run=False)

    assert result["success"] is True
    assert [step_name for step_name, _ in commands] == ["refine_cleanup", "refine_breakdown"]
    assert "openclaw_pipeline.commands.cleanup" in " ".join(commands[0][1])
    assert "openclaw_pipeline.commands.breakdown" in " ".join(commands[1][1])


def test_iter_markdown_files_does_not_drop_parent_relative_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    workspace.mkdir()
    evergreen_dir.mkdir(parents=True)
    (evergreen_dir / "Example.md").write_text("# Example\n", encoding="utf-8")

    monkeypatch.chdir(workspace)

    files = list(iter_markdown_files(Path("..") / "vault" / "10-Knowledge" / "Evergreen"))

    assert len(files) == 1
    assert files[0].name == "Example.md"


def test_markdown_helpers_read_frontmatter_and_title(tmp_path):
    note = tmp_path / "Example.md"
    note.write_text(
        """---
title: Runtime Helpers
type: evergreen
---

# Runtime Helpers
""",
        encoding="utf-8",
    )

    metadata = read_markdown_frontmatter(note)

    assert metadata["title"] == "Runtime Helpers"
    assert markdown_title(note) == "Runtime Helpers"
