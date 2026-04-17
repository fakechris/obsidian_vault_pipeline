from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
import sys

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
from openclaw_pipeline.unified_pipeline_enhanced import (
    EnhancedPipeline,
    build_execution_plan,
    detect_pinboard_processor,
)


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


def test_run_pipeline_dispatches_profile_stages_via_handler_registry(tmp_path, monkeypatch):
    import openclaw_pipeline.unified_pipeline_enhanced as pipeline_source
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(
        pipeline,
        "_count_output_files",
        lambda step, before_counts, cmd_result: {"produced": 1},
    )
    monkeypatch.setattr(
        pipeline,
        "step_articles",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("direct step dispatch")),
    )

    calls: list[str] = []

    def fake_execute_profile_stage_handler(pipeline_runtime, stage, **kwargs):
        calls.append(stage)
        return {"success": True}

    monkeypatch.setattr(
        pipeline_source,
        "execute_profile_stage_handler",
        fake_execute_profile_stage_handler,
        raising=False,
    )

    results = pipeline.run_pipeline(steps=["articles"], dry_run=True)

    assert calls == ["articles"]
    assert results["articles"]["success"] is True


def test_run_pipeline_restores_pack_and_profile_after_override(tmp_path, monkeypatch):
    import openclaw_pipeline.unified_pipeline_enhanced as pipeline_source
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)
    original_pack = pipeline.workflow_pack_name
    original_profile = pipeline.workflow_profile_name

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(
        pipeline,
        "_count_output_files",
        lambda step, before_counts, cmd_result: {"produced": 1},
    )
    monkeypatch.setattr(
        pipeline_source,
        "execute_profile_stage_handler",
        lambda *args, **kwargs: {"success": True},
        raising=False,
    )

    pipeline.run_pipeline(
        steps=["articles"],
        dry_run=True,
        pack_name="default-knowledge",
        profile_name="full",
    )

    assert pipeline.workflow_pack_name == original_pack
    assert pipeline.workflow_profile_name == original_profile


def test_run_pipeline_uses_profile_stages_when_steps_omitted(tmp_path, monkeypatch):
    import openclaw_pipeline.unified_pipeline_enhanced as pipeline_source
    from openclaw_pipeline.packs.loader import load_pack
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(
        pipeline,
        "_count_output_files",
        lambda step, before_counts, cmd_result: {"produced": 1},
    )

    calls: list[str] = []

    def fake_execute_profile_stage_handler(pipeline_runtime, stage, **kwargs):
        calls.append(stage)
        return {"success": True}

    monkeypatch.setattr(
        pipeline_source,
        "execute_profile_stage_handler",
        fake_execute_profile_stage_handler,
        raising=False,
    )

    results = pipeline.run_pipeline(
        dry_run=True,
        pack_name="research-tech",
        profile_name="autopilot",
    )

    expected_steps = load_pack("research-tech").profile("autopilot").stages

    assert list(results) == expected_steps
    assert calls == expected_steps


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
    assert "--pack" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--pack") + 1] == pipeline.workflow_pack_name


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


def test_step_quality_batches_and_aggregates_qc_results(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for idx in range(3):
        path = topic_dir / f"batch_{idx}_深度解读.md"
        path.write_text(f"# {idx}\n", encoding="utf-8")
        files.append(path)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    calls: list[tuple[list[str], int | None]] = []
    payloads = [
        {
            "checked": 2,
            "qualified": 1,
            "failed": 1,
            "qualified_files": [str(files[0])],
            "results_json": str(vault / "60-Logs" / "quality-reports" / "batch-1.json"),
        },
        {
            "checked": 1,
            "qualified": 1,
            "failed": 0,
            "qualified_files": [str(files[2])],
            "results_json": str(vault / "60-Logs" / "quality-reports" / "batch-2.json"),
        },
    ]

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        calls.append((cmd, timeout))
        payload = payloads[len(calls) - 1]
        return {
            "success": True,
            "stdout": "__QC_JSON__: " + __import__("json").dumps(payload, ensure_ascii=False),
            "stderr": "",
        }

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_quality(batch_size=2, dry_run=False)

    assert result["success"] is True
    assert result["quality_checked"] == 3
    assert result["quality_qualified"] == 2
    assert result["quality_failed"] == 1
    assert result["quality_qualified_files"] == [str(files[0]), str(files[2])]
    assert calls[0][0][calls[0][0].index("--start-index") + 1] == "0"
    assert calls[0][0][calls[0][0].index("--batch-size") + 1] == "2"
    assert calls[1][0][calls[1][0].index("--start-index") + 1] == "2"
    assert calls[0][1] == 600
    assert calls[1][1] == 600


def test_step_quality_rejects_non_positive_batch_size(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.step_quality(batch_size=0, dry_run=False)

    assert result["success"] is False
    assert result["error"] == "invalid_batch_size (0 <= 0)"


def test_step_absorb_uses_qualified_files_even_when_quality_score_is_low(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

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
        return {
            "success": True,
            "stdout": json.dumps(
                {
                    "summary": {
                        "files_processed": 1,
                        "concepts_extracted": 1,
                        "candidates_added": 1,
                        "concepts_created": 1,
                        "concepts_promoted": 1,
                        "concepts_skipped": 0,
                        "errors": 0,
                    },
                    "results": [],
                },
                ensure_ascii=False,
            ),
            "stderr": "",
        }

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
        return {
            "success": True,
            "stdout": json.dumps(
                {
                    "summary": {
                        "files_processed": 1,
                        "concepts_extracted": 1,
                        "candidates_added": 1,
                        "concepts_created": 1,
                        "concepts_promoted": 1,
                        "concepts_skipped": 0,
                        "errors": 0,
                    },
                    "results": [],
                },
                ensure_ascii=False,
            ),
            "stderr": "",
        }

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_absorb(dry_run=False, quality_score=-1.0, qualified_files=None)

    assert result["success"] is True
    assert "--dir" in captured["cmd"]
    assert "--auto-promote" in captured["cmd"]
    assert captured["staged_files"] == ["example_深度解读.md"]
    assert captured["timeout"] == 600


def test_load_latest_qualified_files_unions_batches(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    reports_dir = vault / "60-Logs" / "quality-reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    file_a = vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "a_深度解读.md"
    file_b = vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "b_深度解读.md"
    file_a.parent.mkdir(parents=True, exist_ok=True)
    file_a.write_text("# a\n", encoding="utf-8")
    file_b.write_text("# b\n", encoding="utf-8")

    (reports_dir / "quality-results-20260409-000001.json").write_text(
        __import__("json").dumps({"qualified_files": [str(file_a)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (reports_dir / "quality-results-20260409-000002.json").write_text(
        __import__("json").dumps({"qualified_files": [str(file_a), str(file_b)]}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert pipeline._load_latest_qualified_files() == [str(file_a.resolve()), str(file_b.resolve())]


def test_step_absorb_batches_qualified_files_and_aggregates_results(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for idx in range(3):
        path = topic_dir / f"absorb_{idx}_深度解读.md"
        path.write_text(f"# {idx}\n", encoding="utf-8")
        files.append(path)

    calls: list[tuple[list[str], int | None]] = []
    payloads = [
        {
            "summary": {
                "files_processed": 2,
                "concepts_extracted": 5,
                "candidates_added": 3,
                "concepts_created": 2,
                "concepts_promoted": 2,
                "concepts_skipped": 1,
                "errors": 0,
            },
            "results": [],
        },
        {
            "summary": {
                "files_processed": 1,
                "concepts_extracted": 2,
                "candidates_added": 1,
                "concepts_created": 1,
                "concepts_promoted": 1,
                "concepts_skipped": 0,
                "errors": 0,
            },
            "results": [],
        },
    ]

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        calls.append((cmd, timeout))
        absorb_dir = Path(cmd[cmd.index("--dir") + 1])
        staged_files = sorted(p.name for p in absorb_dir.glob("*.md"))
        payload = payloads[len(calls) - 1]
        return {
            "success": True,
            "stdout": json.dumps(payload, ensure_ascii=False),
            "stderr": "",
            "staged_files": staged_files,
        }

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=4.0,
        qualified_files=[str(path) for path in files],
        batch_size=2,
    )

    assert result["success"] is True
    assert result["summary"]["files_processed"] == 3
    assert result["summary"]["concepts_extracted"] == 7
    assert result["summary"]["candidates_added"] == 4
    assert result["summary"]["concepts_created"] == 3
    assert result["summary"]["concepts_promoted"] == 3
    assert result["summary"]["concepts_skipped"] == 1
    assert len(calls) == 2
    assert calls[0][1] == 600
    assert calls[1][1] == 600


def test_absorb_timeout_scales_with_batch_size(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("absorb", batch_size=40)

    assert timeout > 300


def test_step_absorb_parses_json_payload_after_log_prefix(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    deep_dive = topic_dir / "prefixed_深度解读.md"
    deep_dive.write_text("# prefixed\n", encoding="utf-8")

    payload = {
        "summary": {
            "files_processed": 1,
            "concepts_extracted": 2,
            "candidates_added": 1,
            "concepts_created": 1,
            "concepts_promoted": 1,
            "concepts_skipped": 0,
            "errors": 0,
        },
        "results": [],
    }

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        stdout = "processing batch...\nextra note\n" + json.dumps(payload, ensure_ascii=False)
        return {"success": True, "stdout": stdout, "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=4.0,
        qualified_files=[str(deep_dive)],
        batch_size=1,
    )

    assert result["success"] is True
    assert result["summary"]["files_processed"] == 1
    assert result["summary"]["concepts_promoted"] == 1


def test_step_absorb_rejects_non_positive_batch_size(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.step_absorb(batch_size=-1)

    assert result["success"] is False
    assert result["error"] == "invalid_batch_size (-1 <= 0)"


def test_run_command_timeout_is_failure(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.run_command([sys.executable, "-c", "import time; time.sleep(2)"], "absorb", timeout=1)

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


def test_quality_timeout_scales_with_batch_size(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("quality", batch_size=12)

    assert timeout > 600


def test_fix_links_timeout_scales_with_deep_dive_count(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)

    for idx in range(12):
        (topic_dir / f"fix_links_{idx}_深度解读.md").write_text("# x\n", encoding="utf-8")

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("fix_links")

    assert timeout > 300


def test_knowledge_index_timeout_scales_with_evergreen_count(tmp_path):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    evergreen_dir.mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)

    for idx in range(400):
        (evergreen_dir / f"evergreen_{idx}.md").write_text(
            f"---\nnote_id: evergreen-{idx}\ntitle: Evergreen {idx}\ntype: evergreen\ndate: 2026-04-10\n---\n\n# Evergreen {idx}\n",
            encoding="utf-8",
        )

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("knowledge_index")

    assert timeout > 120


def test_step_fix_links_uses_dynamic_timeout(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)

    for idx in range(8):
        (topic_dir / f"fix_links_{idx}_深度解读.md").write_text("# x\n", encoding="utf-8")

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["timeout"] = timeout
        captured["step_name"] = step_name
        captured["cmd"] = cmd
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_fix_links(dry_run=False)

    assert result["success"] is True
    assert captured["step_name"] == "fix_links"
    assert "openclaw_pipeline.commands.migrate_broken_links" in " ".join(captured["cmd"])
    assert captured["timeout"] > 300


def test_step_knowledge_index_uses_dynamic_timeout(tmp_path, monkeypatch):
    from openclaw_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    evergreen_dir.mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True)

    for idx in range(400):
        (evergreen_dir / f"evergreen_{idx}.md").write_text(
            f"---\nnote_id: evergreen-{idx}\ntitle: Evergreen {idx}\ntype: evergreen\ndate: 2026-04-10\n---\n\n# Evergreen {idx}\n",
            encoding="utf-8",
        )

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["cmd"] = cmd
        captured["step_name"] = step_name
        captured["timeout"] = timeout
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_knowledge_index(dry_run=False)

    assert result["success"] is True
    assert captured["step_name"] == "knowledge_index"
    assert "openclaw_pipeline.commands.knowledge_index" in " ".join(captured["cmd"])
    assert captured["timeout"] > 120


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
