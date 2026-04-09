from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest

from openclaw_pipeline.auto_github_processor import build_default_output_dir as github_output_dir
from openclaw_pipeline.auto_paper_processor import build_default_output_dir as paper_output_dir
from openclaw_pipeline.runtime import VaultLayout, iter_markdown_files, resolve_vault_dir
from openclaw_pipeline.unified_pipeline_enhanced import EnhancedPipeline, build_execution_plan, detect_pinboard_processor


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
    assert layout.processing_dir == (tmp_path / "vault" / "50-Inbox" / "02-Processing").resolve()
    assert layout.classification_output_dir("tools").parts[-3:-1] == ("Tools", "Topics")
    assert layout.papers_dir == (tmp_path / "vault" / "20-Areas" / "AI-Research" / "Papers").resolve()
    assert layout.processed_month_dir(datetime(2026, 4, 8)) == (
        tmp_path / "vault" / "50-Inbox" / "03-Processed" / "2026-04"
    ).resolve()


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


def test_build_execution_plan_full_respects_from_step():
    args = Namespace(
        full=True,
        with_refine=True,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step="quality",
    )

    plan = build_execution_plan(args)

    assert plan["steps"][0] == "quality"
    assert "pinboard" not in plan["steps"]
    assert plan["steps"][-2:] == ["refine", "knowledge_index"]


def test_detect_pinboard_processor_routes_gist_to_article_stack():
    content = """---
title: "GBrain.md"
source: https://gist.github.com/garrytan/49c88e83cf8d7ae95e087426368809cb
date: 2026-04-05
type: pinboard-github
tags: [knowledge]
---
"""

    assert detect_pinboard_processor(content) == "website"


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


def test_step_quality_parses_qualified_files_from_qc_json(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "example_深度解读.md"
    qualified_file.parent.mkdir(parents=True, exist_ok=True)
    qualified_file.write_text("# example\n", encoding="utf-8")

    stdout = (
        "__QC_JSON__: "
        '{"checked": 2, "qualified": 1, "failed": 1, '
        f'"qualified_files": ["{qualified_file}"]'
        "}"
    )

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        return {"success": True, "stdout": stdout, "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_quality(dry_run=False)

    assert result["success"] is True
    assert result["quality_checked"] == 2
    assert result["quality_qualified"] == 1
    assert result["quality_qualified_files"] == [str(qualified_file)]


def test_step_absorb_uses_qualified_files_even_when_quality_score_is_low(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "example_深度解读.md"
    qualified_file.parent.mkdir(parents=True, exist_ok=True)
    qualified_file.write_text("# example\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["cmd"] = cmd
        captured["step_name"] = step_name
        captured["timeout"] = timeout
        absorb_dir = Path(cmd[cmd.index("--dir") + 1])
        staged_files = sorted(p.name for p in absorb_dir.glob("*.md"))
        captured["staged_files"] = staged_files
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=1.3,
        qualified_files=[str(qualified_file)],
    )

    assert result["success"] is True
    assert captured["step_name"] == "absorb"
    assert "--dir" in captured["cmd"]
    assert "--auto-promote" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--promote-threshold") + 1] == "1"
    assert captured["staged_files"] == ["example_深度解读.md"]
    assert captured["timeout"] == 600


def test_step_absorb_skips_cleanly_when_no_qualified_files(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=1.2,
        qualified_files=[],
    )

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["produced"] == 0


def test_step_absorb_falls_back_to_latest_quality_results_file(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

    vault = tmp_path / "vault"
    (vault / "60-Logs" / "quality-reports").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "example_深度解读.md"
    qualified_file.parent.mkdir(parents=True, exist_ok=True)
    qualified_file.write_text("# example\n", encoding="utf-8")

    results_file = vault / "60-Logs" / "quality-reports" / "quality-results-20260408-000000.json"
    results_file.write_text(
        json.dumps(
            {
                "checked": 1,
                "qualified": 1,
                "failed": 0,
                "qualified_files": [str(qualified_file)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        absorb_dir = Path(cmd[cmd.index("--dir") + 1])
        captured["staged_files"] = sorted(p.name for p in absorb_dir.glob("*.md"))
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_absorb(dry_run=False, quality_score=-1.0, qualified_files=None)

    assert result["success"] is True
    assert "--dir" in captured["cmd"]
    assert "--auto-promote" in captured["cmd"]
    assert captured["staged_files"] == ["example_深度解读.md"]
    assert captured["timeout"] == 600


def test_run_command_timeout_is_failure(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.run_command(["python3", "-c", "import time; time.sleep(2)"], "absorb", timeout=1)

    assert result["success"] is False
    assert result["timeout"] is True


def test_step_pinboard_decomposes_cross_day_history_into_daily_requests(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    captured_cmds: list[list[str]] = []

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured_cmds.append(cmd)
        return {"success": True, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_pinboard(
        start_date="2026-04-01",
        end_date="2026-04-03",
        dry_run=False,
    )

    assert result["success"] is True
    assert result["days_processed"] == 3
    assert len(captured_cmds) == 3
    assert captured_cmds[0][-5:] == ["--start-date", "2026-04-01", "--end-date", "2026-04-01", "--dry-run=false"]
    assert captured_cmds[1][-5:] == ["--start-date", "2026-04-02", "--end-date", "2026-04-02", "--dry-run=false"]
    assert captured_cmds[2][-5:] == ["--start-date", "2026-04-03", "--end-date", "2026-04-03", "--dry-run=false"]


def test_before_counts_include_monthly_processed_files(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    processed_file = vault / "50-Inbox" / "03-Processed" / "2026-04" / "example.md"
    processed_file.parent.mkdir(parents=True, exist_ok=True)
    processed_file.write_text("# done\n", encoding="utf-8")
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    counts = pipeline._get_before_counts()

    assert counts["processed"] == 1


def test_articles_timeout_scales_with_raw_and_processing_queue(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    raw_dir = vault / "50-Inbox" / "01-Raw"
    processing_dir = vault / "50-Inbox" / "02-Processing"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processing_dir.mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)

    for idx in range(3):
        (raw_dir / f"2026-04-08_raw_{idx}.md").write_text("x" * 1200, encoding="utf-8")
    for idx in range(2):
        (processing_dir / f"2026-04-08_processing_{idx}.md").write_text("y" * 1200, encoding="utf-8")

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("articles")

    assert timeout > 300


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
