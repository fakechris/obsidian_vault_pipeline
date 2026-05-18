from __future__ import annotations

from argparse import Namespace
from datetime import datetime
import json
import os
from pathlib import Path
import sys

import pytest

from ovp_pipeline.auto_github_processor import build_default_output_dir as github_output_dir
from ovp_pipeline.auto_paper_processor import build_default_output_dir as paper_output_dir
from ovp_pipeline.runtime import (
    VaultLayout,
    iter_markdown_files,
    looks_like_vault_dir,
    markdown_title,
    read_markdown_frontmatter,
    resolve_vault_dir,
)
from ovp_pipeline.unified_pipeline_enhanced import (
    EnhancedPipeline,
    build_execution_plan,
    check_environment,
    detect_pinboard_processor,
    init_env_file,
)

QUALITY_TEST_MONTH = datetime.now().strftime("%Y-%m")


def test_resolve_vault_dir_returns_absolute_path(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()

    monkeypatch.chdir(workspace)

    resolved = resolve_vault_dir(Path("..") / "vault")

    assert resolved == vault.resolve()


def test_resolve_vault_dir_prefers_environment_when_not_explicit(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("OVP_VAULT_DIR", raising=False)
    monkeypatch.setenv("VAULT_DIR", str(vault))

    resolved = resolve_vault_dir()

    assert resolved == vault.resolve()


def test_resolve_vault_dir_prefers_ovp_vault_dir_over_vault_dir(tmp_path, monkeypatch):
    ovp_vault = tmp_path / "ovp-vault"
    legacy_vault = tmp_path / "legacy-vault"
    ovp_vault.mkdir()
    legacy_vault.mkdir()
    monkeypatch.setenv("OVP_VAULT_DIR", str(ovp_vault))
    monkeypatch.setenv("VAULT_DIR", str(legacy_vault))

    resolved = resolve_vault_dir()

    assert resolved == ovp_vault.resolve()


def test_resolve_vault_dir_explicit_argument_overrides_environment(tmp_path, monkeypatch):
    env_vault = tmp_path / "env-vault"
    explicit_vault = tmp_path / "explicit-vault"
    env_vault.mkdir()
    explicit_vault.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(env_vault))

    resolved = resolve_vault_dir(explicit_vault)

    assert resolved == explicit_vault.resolve()


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


def test_check_environment_rejects_non_vault_directory(tmp_path, monkeypatch):
    non_vault = tmp_path / "template-checkout"
    non_vault.mkdir()
    (non_vault / ".env").write_text("AUTO_VAULT_API_KEY=sk-test-valid-key\n", encoding="utf-8")
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "sk-test-valid-key")

    ok, issues = check_environment(non_vault)

    assert ok is False
    assert any("not a vault" in issue for issue in issues)


def test_check_environment_rejects_package_checkout_with_vault_scaffold(tmp_path, monkeypatch):
    package_checkout = tmp_path / "obsidian-vault-pipeline"
    for rel in (
        "10-Knowledge",
        "20-Areas",
        "50-Inbox",
        "60-Logs",
        "src/ovp_pipeline",
    ):
        (package_checkout / rel).mkdir(parents=True)
    (package_checkout / "pyproject.toml").write_text(
        "[project]\nname = 'obsidian-vault-pipeline'\n",
        encoding="utf-8",
    )
    (package_checkout / ".env").write_text("AUTO_VAULT_API_KEY=sk-test-valid-key\n", encoding="utf-8")
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "sk-test-valid-key")

    ok, issues = check_environment(package_checkout)

    assert ok is False
    assert any("not a vault" in issue for issue in issues)


def test_check_environment_accepts_obsidian_vault_layout(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    for rel in ("10-Knowledge", "20-Areas", "50-Inbox", "60-Logs", ".obsidian"):
        (vault / rel).mkdir(parents=True)
    (vault / ".env").write_text("AUTO_VAULT_API_KEY=sk-test-valid-key\n", encoding="utf-8")
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "sk-test-valid-key")

    ok, issues = check_environment(vault)

    assert ok is True
    assert any("Vault root: OK" in issue for issue in issues)


def test_fresh_obsidian_vault_does_not_require_logs_dir(tmp_path):
    vault = tmp_path / "fresh-vault"
    for rel in ("10-Knowledge", "20-Areas", "50-Inbox", ".obsidian"):
        (vault / rel).mkdir(parents=True)

    assert looks_like_vault_dir(vault) is True


def test_init_env_file_writes_to_resolved_vault_dir(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    inputs = iter(["sk-test-valid-key", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    exit_code = init_env_file(vault)

    env_file = vault / ".env"
    assert exit_code == 0
    assert env_file.exists()
    assert "AUTO_VAULT_API_KEY=sk-test-valid-key" in env_file.read_text(encoding="utf-8")


def test_specialized_processors_derive_default_outputs_from_vault(tmp_path):
    vault = tmp_path / "vault"

    github_dir = github_output_dir(vault)
    paper_dir = paper_output_dir(vault)

    assert github_dir.is_absolute()
    # BL-066: github intake no longer produces deep-dives; output now
    # lands in 50-Inbox/03-Processed/<YYYY-MM>/ alongside other
    # processed sources.  Was ``20-Areas/Tools/Topics/<YYYY-MM>/``.
    assert github_dir.parts[-3:-1] == ("50-Inbox", "03-Processed")
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

    assert plan["steps"] == [
        "pinboard",
        "pinboard_process",
        "articles",
        "quality",
        "fix_links",
        "absorb",
        "entity_extract",
        "dedup",
        "note_type_normalize",
        "registry_sync",
        "moc",
        "knowledge_index",
        # M24.1: lifecycle projection runs after knowledge_index.
        "ops_state",
    ]


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
    # M24.1: last step is now ops_state (lifecycle projection).
    assert plan["steps"][-1] == "ops_state"
    assert plan["steps"][-2] == "knowledge_index"


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

    # M24.1: refine runs BEFORE knowledge_index (refine writes
    # data knowledge_index indexes); ops_state runs last.
    assert plan["steps"][-3:] == ["refine", "knowledge_index", "ops_state"]
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
    # M24.1: see comment on the can_insert_refine test above.
    assert plan["steps"][-3:] == ["refine", "knowledge_index", "ops_state"]


def test_build_execution_plan_incremental_includes_pinboard_and_defaults_recent_days():
    args = Namespace(
        full=False,
        incremental=True,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step=None,
        pack=None,
        profile=None,
    )

    plan = build_execution_plan(args)

    assert plan["steps"][:3] == ["pinboard", "pinboard_process", "clippings"]
    # M24.1: ops_state is the new tail.
    assert plan["steps"][-1] == "ops_state"
    assert plan["steps"][-2] == "knowledge_index"
    assert plan["pinboard_days"] == 7
    assert plan["description"] == "Incremental pipeline (research-tech/full)"


def test_run_pipeline_dispatches_profile_stages_via_handler_registry(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.packs.base import BaseDomainPack, WorkflowProfile
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    monkeypatch.setattr(
        pipeline_source,
        "resolve_workflow_profile",
        lambda **kwargs: (
            BaseDomainPack(
                name="research-tech",
                version="0.1.0",
                api_version=1,
                _workflow_profiles=[],
            ),
            WorkflowProfile(
                name="full",
                description="Custom staged profile",
                stages=["articles", "quality", "knowledge_index"],
            ),
        ),
        raising=False,
    )

    results = pipeline.run_pipeline(
        dry_run=True,
        pack_name="research-tech",
        profile_name="full",
    )

    expected_steps = ["articles", "quality", "knowledge_index"]

    assert list(results) == expected_steps
    assert calls == expected_steps


def test_run_pipeline_checkouts_cacheable_stage_artifact_without_dispatching_handler(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "cached_深度解读.md").write_text("# cached\n", encoding="utf-8")
    (vault / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Cache checkout", planned_steps=["fix_links"])

    context = pipeline._build_stage_artifact_context("fix_links")
    pipeline._stage_artifact_store().write_completed(
        stage="fix_links",
        fingerprint=context["fingerprint"],
        input_digest=context["input_digest"],
        algorithm_digest=context["algorithm_digest"],
        run_id="previous-run",
        pack_name=pipeline.workflow_pack_name,
        workflow_profile=pipeline.workflow_profile_name,
        inputs=context["inputs"],
        outputs=context["outputs"],
    )

    def fail_if_dispatched(*_args, **_kwargs):
        raise AssertionError("cacheable stage handler should not be dispatched on cache hit")

    monkeypatch.setattr(pipeline_source, "execute_profile_stage_handler", fail_if_dispatched, raising=False)

    results = pipeline.run_pipeline(steps=["fix_links"], dry_run=False)

    result = results["fix_links"]
    assert result["success"] is True
    assert result["cache_hit"] is True
    assert result["skipped"] is True
    assert result["stage_fingerprint"] == context["fingerprint"]
    payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
    step = payload["steps"]["fix_links"]
    assert step["status"] == "completed"
    assert "Cache hit" in step["output"]


def test_run_pipeline_does_not_skip_record_only_source_stage(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "50-Inbox" / "02-Pinboard").mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)

    context = pipeline._build_stage_artifact_context("pinboard")
    pipeline._stage_artifact_store().write_completed(
        stage="pinboard",
        fingerprint=context["fingerprint"],
        input_digest=context["input_digest"],
        algorithm_digest=context["algorithm_digest"],
        run_id="previous-run",
        pack_name=pipeline.workflow_pack_name,
        workflow_profile=pipeline.workflow_profile_name,
        inputs=context["inputs"],
        outputs=context["outputs"],
    )

    calls: list[str] = []

    def fake_execute_profile_stage_handler(_pipeline_runtime, stage, **_kwargs):
        calls.append(stage)
        return {"success": True}

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {"pinboard": 0})
    monkeypatch.setattr(pipeline, "_count_output_files", lambda step, before_counts, cmd_result: {"produced": 0})
    monkeypatch.setattr(pipeline_source, "execute_profile_stage_handler", fake_execute_profile_stage_handler, raising=False)

    results = pipeline.run_pipeline(steps=["pinboard"], dry_run=False)

    assert calls == ["pinboard"]
    assert results["pinboard"]["success"] is True
    assert results["pinboard"].get("cache_hit") is None


def test_run_pipeline_ignores_cache_artifact_when_declared_output_is_missing(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    evergreen_dir.mkdir(parents=True, exist_ok=True)
    (evergreen_dir / "state.md").write_text("# state\n", encoding="utf-8")
    (vault / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)

    context = pipeline._build_stage_artifact_context("knowledge_index")
    pipeline._stage_artifact_store().write_completed(
        stage="knowledge_index",
        fingerprint=context["fingerprint"],
        input_digest=context["input_digest"],
        algorithm_digest=context["algorithm_digest"],
        run_id="previous-run",
        pack_name=pipeline.workflow_pack_name,
        workflow_profile=pipeline.workflow_profile_name,
        inputs=context["inputs"],
        outputs={"paths": ["60-Logs/knowledge.db"]},
    )

    calls: list[str] = []

    def fake_execute_profile_stage_handler(_pipeline_runtime, stage, **_kwargs):
        calls.append(stage)
        return {"success": True}

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {"knowledge_db_mtime": 0.0})
    monkeypatch.setattr(
        pipeline,
        "_count_output_files",
        lambda step, before_counts, cmd_result: {"produced": 0, "db_path": str(vault / "60-Logs" / "knowledge.db")},
    )
    monkeypatch.setattr(pipeline_source, "execute_profile_stage_handler", fake_execute_profile_stage_handler, raising=False)

    results = pipeline.run_pipeline(steps=["knowledge_index"], dry_run=False)

    assert calls == ["knowledge_index"]
    assert results["knowledge_index"]["success"] is True
    assert results["knowledge_index"].get("cache_hit") is None


def test_run_pipeline_writes_stage_artifact_after_cacheable_stage_success(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "write_artifact_深度解读.md").write_text("# write artifact\n", encoding="utf-8")
    (vault / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Artifact write", planned_steps=["fix_links"])

    def fake_execute_profile_stage_handler(_pipeline_runtime, stage, **_kwargs):
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(pipeline, "_count_output_files", lambda step, before_counts, cmd_result: {"produced": 0})
    monkeypatch.setattr(pipeline_source, "execute_profile_stage_handler", fake_execute_profile_stage_handler, raising=False)

    results = pipeline.run_pipeline(steps=["fix_links"], dry_run=False)

    context = pipeline._build_stage_artifact_context("fix_links")
    manifest = pipeline._stage_artifact_store().load("fix_links", context["fingerprint"])
    assert results["fix_links"]["success"] is True
    assert manifest is not None
    assert manifest["run_id"] == pipeline.txn_id
    assert manifest["inputs"]["file_count"] == 1


def test_run_pipeline_writes_record_only_article_artifact_without_skipping(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    raw_dir = vault / "50-Inbox" / "01-Raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "article.md").write_text("# article\n", encoding="utf-8")
    (vault / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = pipeline_source.EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Article artifact", planned_steps=["articles"])

    calls: list[str] = []

    def fake_execute_profile_stage_handler(_pipeline_runtime, stage, **_kwargs):
        calls.append(stage)
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(pipeline, "_count_output_files", lambda step, before_counts, cmd_result: {"produced": 1})
    monkeypatch.setattr(pipeline_source, "execute_profile_stage_handler", fake_execute_profile_stage_handler, raising=False)

    first_results = pipeline.run_pipeline(steps=["articles"], dry_run=False)
    context = pipeline._build_stage_artifact_context("articles")
    manifest = pipeline._stage_artifact_store().load("articles", context["fingerprint"])

    assert first_results["articles"]["success"] is True
    assert manifest is not None
    assert manifest["inputs"]["file_count"] == 1

    second_results = pipeline.run_pipeline(steps=["articles"], dry_run=False)

    assert calls == ["articles", "articles"]
    assert second_results["articles"].get("cache_hit") is None


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
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    invocations: list[list[str]] = []

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        invocations.append(list(cmd))
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    # dry-run must NOT trigger the heavy rebuild (gemini review):
    # returns a skipped result, invokes no command.
    dry = pipeline.step_knowledge_index(dry_run=True)
    assert dry["success"] is True
    assert dry["skipped"] is True
    assert invocations == []

    # Real run, no knowledge.db → decision = full_rebuild
    # (knowledge_db_missing) → the rebuild command runs, well-formed.
    result = pipeline.step_knowledge_index(dry_run=False)

    assert result["success"] is True
    rebuild = next(
        cmd for cmd in invocations
        if "ovp_pipeline.commands.knowledge_index" in " ".join(cmd)
    )
    assert "--vault-dir" in rebuild
    assert "--pack" in rebuild
    assert rebuild[rebuild.index("--pack") + 1] == pipeline.workflow_pack_name


def test_step_absorb_invokes_absorb_command(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    captured: dict[str, object] = {}

    def fake_run_absorb_workflow(vault_dir, *, recent=None, dry_run=False, **_):
        captured["vault_dir"] = Path(vault_dir)
        captured["recent"] = recent
        captured["dry_run"] = dry_run
        return {"summary": {"files_processed": 0}, "results": []}

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    result = pipeline.step_absorb(dry_run=True)

    assert result["success"] is True
    assert captured["vault_dir"] == vault
    assert captured["recent"] == 7
    assert captured["dry_run"] is True


def test_step_quality_parses_qualified_files_from_qc_json(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "example_深度解读.md"
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


def test_incremental_quality_uses_article_outputs_instead_of_all_current_month(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    from ovp_pipeline.workflow_handlers import run_pipeline_quality

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.run_mode = "incremental"

    month = datetime.now().strftime("%Y-%m")
    old_file = vault / "20-Areas" / "Tools" / "Topics" / month / "old_深度解读.md"
    new_file = vault / "20-Areas" / "Tools" / "Topics" / month / "new_深度解读.md"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text("# old\n", encoding="utf-8")
    new_file.write_text("# new\n", encoding="utf-8")

    captured_commands: list[list[str]] = []
    stdout = (
        "__QC_JSON__: "
        '{"checked": 1, "qualified": 1, "failed": 0, '
        f'"qualified_files": ["{new_file}"]'
        "}"
    )

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured_commands.append(cmd)
        return {"success": True, "stdout": stdout, "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = run_pipeline_quality(
        pipeline=pipeline,
        results={"articles": {"produced_files": [str(new_file)]}},
        dry_run=False,
    )

    assert result["success"] is True
    assert result["quality_checked"] == 1
    assert captured_commands
    command = captured_commands[0]
    assert "--all" not in command
    assert "--dir" in command
    assert command[command.index("--batch-size") + 1] == "1"


def test_incremental_load_quality_artifact_finds_recent_targeted_manifest(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.run_mode = "incremental"

    month = datetime.now().strftime("%Y-%m")
    old_file = vault / "20-Areas" / "Tools" / "Topics" / month / "old_深度解读.md"
    target_file = vault / "20-Areas" / "Tools" / "Topics" / month / "target_深度解读.md"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text("# old\n", encoding="utf-8")
    target_file.write_text("# target\n", encoding="utf-8")
    old_mtime = datetime.now().timestamp() - (3 * 24 * 60 * 60)
    os.utime(old_file, (old_mtime, old_mtime))

    stdout = (
        "__QC_JSON__: "
        '{"checked": 1, "qualified": 1, "failed": 0, '
        f'"qualified_files": ["{target_file}"]'
        "}"
    )

    monkeypatch.setattr(
        pipeline,
        "run_command",
        lambda *_args, **_kwargs: {"success": True, "stdout": stdout, "stderr": ""},
    )

    result = pipeline.step_quality(target_files=[target_file], dry_run=False)

    assert result["success"] is True
    assert pipeline._load_quality_stage_artifact() is not None


def test_articles_output_count_matches_new_interpretation_files(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    month = datetime.now().strftime("%Y-%m")
    topics_dir = vault / "20-Areas" / "Tools" / "Topics" / month
    topics_dir.mkdir(parents=True, exist_ok=True)
    old_file = topics_dir / "old_深度解读.md"
    new_file = topics_dir / "new_深度解读.md"
    old_file.write_text("# old\n", encoding="utf-8")
    before_counts = pipeline._get_before_counts()

    old_file.unlink()
    new_file.write_text("# new\n", encoding="utf-8")

    result = pipeline._count_output_files("articles", before_counts, {})

    assert result["produced"] == 1
    assert result["produced_files"] == [str(new_file.resolve())]


def test_step_quality_writes_reusable_stage_artifact(tmp_path, monkeypatch):
    from ovp_pipeline.stage_artifacts import StageArtifactStore
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Quality artifact")

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "artifact_深度解读.md"
    qualified_file.parent.mkdir(parents=True, exist_ok=True)
    qualified_file.write_text("# artifact\n", encoding="utf-8")

    stdout = (
        "__QC_JSON__: "
        '{"checked": 1, "qualified": 1, "failed": 0, '
        f'"qualified_files": ["{qualified_file}"], '
        '"results_json": "quality-results-demo.json"}'
    )

    monkeypatch.setattr(
        pipeline,
        "run_command",
        lambda *_args, **_kwargs: {"success": True, "stdout": stdout, "stderr": ""},
    )

    result = pipeline.step_quality(dry_run=False)

    store = StageArtifactStore(vault / "60-Logs" / "stage-artifacts")
    manifest = store.load("quality", result["quality_stage_fingerprint"])
    assert manifest is not None
    assert manifest["run_id"] == pipeline.txn_id
    assert manifest["outputs"]["qualified_files"] == [str(qualified_file.resolve())]


def test_step_quality_writes_empty_reusable_stage_artifact(tmp_path):
    from ovp_pipeline.stage_artifacts import StageArtifactStore
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Empty quality artifact")

    result = pipeline.step_quality(dry_run=False)

    store = StageArtifactStore(vault / "60-Logs" / "stage-artifacts")
    manifest = store.load("quality", result["quality_stage_fingerprint"])
    assert result["success"] is True
    assert result["quality_qualified_files"] == []
    assert manifest is not None
    assert manifest["outputs"]["qualified_files"] == []


def test_step_absorb_checkouts_matching_quality_stage_artifact(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    quality_pipeline = EnhancedPipeline(vault, logger, txn)
    quality_pipeline.txn_id = txn.start("enhanced-pipeline", "Quality artifact source")

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "checkout_深度解读.md"
    qualified_file.parent.mkdir(parents=True, exist_ok=True)
    qualified_file.write_text("# checkout\n", encoding="utf-8")

    stdout = (
        "__QC_JSON__: "
        '{"checked": 1, "qualified": 1, "failed": 0, '
        f'"qualified_files": ["{qualified_file}"]'
        "}"
    )
    monkeypatch.setattr(
        quality_pipeline,
        "run_command",
        lambda *_args, **_kwargs: {"success": True, "stdout": stdout, "stderr": ""},
    )
    quality_pipeline.step_quality(dry_run=False)

    absorb_pipeline = EnhancedPipeline(vault, logger, txn)
    captured: dict[str, object] = {}

    def fake_run_absorb_workflow(vault_dir, *, directory=None, **_):
        captured["staged_files"] = sorted(p.name for p in Path(directory).glob("*.md"))
        return {
            "summary": {
                "files_processed": 1,
                "concepts_extracted": 1,
                "candidates_added": 0,
                "concepts_created": 0,
                "concepts_promoted": 0,
                "concepts_skipped": 1,
                "errors": 0,
            },
            "results": [],
        }

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    result = absorb_pipeline.step_absorb(
        dry_run=False,
        quality_score=-1.0,
        qualified_files=None,
        require_quality_artifact=True,
    )

    assert result["success"] is True
    assert result["input_artifact"]["stage"] == "quality"
    assert captured["staged_files"] == ["checkout_深度解读.md"]


def test_step_absorb_skips_empty_quality_stage_artifact(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    pipeline.step_quality(dry_run=False)

    def fake_run_absorb_workflow(*_args, **_kwargs):
        raise AssertionError("empty quality artifact should skip absorb")

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=-1.0,
        qualified_files=None,
        require_quality_artifact=True,
    )

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["reason"] == "no_qualified_files"
    assert result["input_artifact"]["stage"] == "quality"


def test_step_quality_batches_and_aggregates_qc_results(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
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


def test_step_quality_updates_parent_run_progress_before_each_batch(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for idx in range(3):
        path = topic_dir / f"progress_{idx}_深度解读.md"
        path.write_text(f"# {idx}\n", encoding="utf-8")
        files.append(path)

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Quality progress")

    snapshots: list[dict] = []
    payloads = [
        {"checked": 2, "qualified": 1, "failed": 1, "qualified_files": [str(files[0])]},
        {"checked": 1, "qualified": 1, "failed": 0, "qualified_files": [str(files[2])]},
    ]

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        payload = json.loads(
            (vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8")
        )
        snapshots.append(payload["run_ledger"]["current_step"])
        batch_payload = payloads[len(snapshots) - 1]
        return {
            "success": True,
            "stdout": "__QC_JSON__: " + json.dumps(batch_payload, ensure_ascii=False),
            "stderr": "",
        }

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_quality(batch_size=2, dry_run=False)

    assert result["success"] is True
    assert len(snapshots) == 2
    assert snapshots[0]["progress_mode"] == "counted"
    assert snapshots[0]["work_units_total"] == 3
    assert snapshots[0]["work_units_done"] == 0
    assert snapshots[0]["current_item"] == "quality batch 1/2"
    assert snapshots[0]["progress_percent"] == 0.0
    assert snapshots[1]["progress_mode"] == "counted"
    assert snapshots[1]["work_units_total"] == 3
    assert snapshots[1]["work_units_done"] == 2
    assert snapshots[1]["current_item"] == "quality batch 2/2"
    assert snapshots[1]["progress_percent"] == 66.7


def test_step_quality_rejects_non_positive_batch_size(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.step_quality(batch_size=0, dry_run=False)

    assert result["success"] is False
    assert result["error"] == "invalid_batch_size (0 <= 0)"


def test_step_pinboard_process_updates_txn_ledger_with_counted_progress(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

    vault = tmp_path / "vault"
    pinboard_dir = vault / "50-Inbox" / "02-Pinboard"
    pinboard_dir.mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Phase 25 pinboard progress")

    for name in ("one.md", "two.md"):
        (pinboard_dir / name).write_text("---\ntitle: Demo\nsource: https://example.com\n---\n", encoding="utf-8")

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.detect_pinboard_processor", lambda content: "article")

    monkeypatch.setattr(
        pipeline,
        "run_command",
        lambda *args, **kwargs: {"success": True, "returncode": 0, "stdout": "", "stderr": ""},
    )

    result = pipeline.step_pinboard_process(dry_run=False)

    assert result["success"] is True
    payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
    current = payload["run_ledger"]["current_step"]
    assert current["step_name"] == "pinboard_process"
    assert current["progress_mode"] == "counted"
    assert current["work_units_total"] == 2
    assert current["work_units_done"] == 2
    assert current["progress_percent"] == 100.0
    assert current["current_item"] == "two.md"
    assert payload["run_ledger"]["last_meaningful_event"]["event_type"] == "pinboard_process_file_completed"


def test_step_absorb_uses_qualified_files_even_when_quality_score_is_low(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "example_深度解读.md"
    qualified_file.parent.mkdir(parents=True, exist_ok=True)
    qualified_file.write_text("# example\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_absorb_workflow(
        vault_dir,
        *,
        directory=None,
        dry_run=False,
        auto_promote=False,
        promote_threshold=0,
        progress_callback=None,
        **_,
    ):
        captured["vault_dir"] = Path(vault_dir)
        captured["directory"] = Path(directory)
        captured["dry_run"] = dry_run
        captured["auto_promote"] = auto_promote
        captured["promote_threshold"] = promote_threshold
        captured["staged_files"] = sorted(p.name for p in Path(directory).glob("*.md"))
        if progress_callback is not None:
            progress_callback(
                {
                    "event_type": "absorb_file_processed",
                    "file": "example_深度解读.md",
                    "files_total": 1,
                    "files_done": 1,
                    "files_failed": 0,
                    "current_item": "example_深度解读.md",
                }
            )
        return {
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
        }

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=1.3,
        qualified_files=[str(qualified_file)],
    )

    assert result["success"] is True
    assert captured["vault_dir"] == vault
    assert captured["auto_promote"] is True
    assert captured["promote_threshold"] == 1
    assert captured["staged_files"] == ["example_深度解读.md"]


def test_step_absorb_skips_cleanly_when_no_qualified_files(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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


def test_step_absorb_requires_quality_artifact_without_using_quality_report_history(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

    vault = tmp_path / "vault"
    (vault / "60-Logs" / "quality-reports").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    qualified_file = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "example_深度解读.md"
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

    def fake_run_absorb_workflow(vault_dir, *, directory=None, **_):
        raise AssertionError("absorb must not scan historical quality reports without a stage artifact")

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=-1.0,
        qualified_files=None,
        require_quality_artifact=True,
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["reason"] == "missing_quality_stage_artifact"


def test_load_quality_stage_artifact_returns_none_without_matching_manifest(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    reports_dir = vault / "60-Logs" / "quality-reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    file_a = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "a_深度解读.md"
    file_b = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH / "b_深度解读.md"
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

    assert pipeline._load_quality_stage_artifact() is None


def test_load_quality_stage_artifact_rejects_output_paths_outside_vault(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    external_file = tmp_path / "outside.md"
    external_file.write_text("# outside\n", encoding="utf-8")
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    target_files, input_digest, algorithm_digest, fingerprint = pipeline._quality_stage_inputs()

    pipeline._stage_artifact_store().write_completed(
        stage="quality",
        fingerprint=fingerprint,
        input_digest=input_digest,
        algorithm_digest=algorithm_digest,
        run_id="previous-run",
        pack_name=pipeline.workflow_pack_name,
        workflow_profile=pipeline.workflow_profile_name,
        inputs={"files": [str(path) for path in target_files], "file_count": len(target_files)},
        outputs={"qualified_files": [str(external_file)]},
    )

    assert pipeline._load_quality_stage_artifact() is None


def test_step_absorb_batches_qualified_files_and_aggregates_results(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for idx in range(3):
        path = topic_dir / f"absorb_{idx}_深度解读.md"
        path.write_text(f"# {idx}\n", encoding="utf-8")
        files.append(path)

    calls: list[list[str]] = []
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

    def fake_run_absorb_workflow(vault_dir, *, directory=None, **_):
        absorb_dir = Path(directory)
        calls.append(sorted(p.name for p in absorb_dir.glob("*.md")))
        payload = payloads[len(calls) - 1]
        return payload

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

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
    assert calls[0] == ["absorb_0_深度解读.md", "absorb_1_深度解读.md"]
    assert calls[1] == ["absorb_2_深度解读.md"]


def test_step_absorb_skips_previously_succeeded_items_and_retries_failed_items(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    succeeded = topic_dir / "succeeded_深度解读.md"
    failed = topic_dir / "failed_深度解读.md"
    succeeded.write_text("# succeeded\n", encoding="utf-8")
    failed.write_text("# failed\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run_absorb_workflow(vault_dir, *, directory=None, progress_callback=None, **_):
        staged = sorted(p.name for p in Path(directory).glob("*.md"))
        calls.append(staged)
        errors = 0
        result_rows = []
        for idx, name in enumerate(staged, start=1):
            result = {
                "file": str(Path(directory) / name),
                "concepts_extracted": 1,
                "concepts_created": 1,
                "concepts_skipped": 0,
                "candidates_added": 0,
                "concepts_promoted": 1,
                "concepts": [],
            }
            if name == failed.name and len(calls) == 1:
                result["error"] = "transient failure"
                errors += 1
            result_rows.append(result)
            if progress_callback is not None:
                progress_callback(
                    {
                        "event_type": "absorb_file_processed",
                        "file": name,
                        "current_item": name,
                        "files_total": len(staged),
                        "files_done": idx,
                        "files_failed": errors,
                        "result": result,
                    }
                )
        return {
            "summary": {
                "files_processed": len(staged),
                "concepts_extracted": len(staged),
                "candidates_added": 0,
                "concepts_created": len(staged) - errors,
                "concepts_promoted": len(staged) - errors,
                "concepts_skipped": 0,
                "errors": errors,
            },
            "results": result_rows,
        }

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    first = pipeline.step_absorb(
        dry_run=False,
        quality_score=4.0,
        qualified_files=[str(succeeded), str(failed)],
        batch_size=2,
    )
    second = pipeline.step_absorb(
        dry_run=False,
        quality_score=4.0,
        qualified_files=[str(succeeded), str(failed)],
        batch_size=2,
    )
    ledger_path = pipeline._absorb_item_ledger_path()
    ledger_before_dry_run = ledger_path.read_text(encoding="utf-8")
    third = pipeline.step_absorb(
        dry_run=True,
        quality_score=4.0,
        qualified_files=[str(succeeded), str(failed)],
        batch_size=2,
    )

    assert first["success"] is False
    assert second["success"] is True
    assert third["success"] is True
    assert third["skipped"] is True
    assert calls == [[failed.name, succeeded.name], [failed.name]]
    assert first["summary"]["files_processed"] == 2
    assert first["summary"]["errors"] == 1
    assert len(first["results"]) == 2
    assert second["summary"]["files_processed"] == 1
    assert second["item_cache_hits"] == 1
    assert second["item_cache_hit_files"] == [str(succeeded.resolve())]
    assert third["item_cache_hits"] == 2
    assert ledger_path.read_text(encoding="utf-8") == ledger_before_dry_run


def test_absorb_timeout_scales_with_batch_size(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("absorb", batch_size=40)

    assert timeout > 300


def test_step_absorb_updates_txn_ledger_with_counted_progress(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager
    import json

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Phase 25 absorb progress")

    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    deep_dives = []
    for name in ("alpha_深度解读.md", "beta_深度解读.md"):
        deep_dive = topic_dir / name
        deep_dive.write_text("# item\n", encoding="utf-8")
        deep_dives.append(deep_dive)

    def fake_run_absorb_workflow(vault_dir, *, directory=None, progress_callback=None, **_):
        staged = sorted(p.name for p in Path(directory).glob("*.md"))
        if progress_callback is not None:
            for idx, name in enumerate(staged, start=1):
                progress_callback(
                    {
                        "event_type": "absorb_file_processed",
                        "file": name,
                        "files_total": len(staged),
                        "files_done": idx,
                        "files_failed": 0,
                        "current_item": name,
                    }
                )
        return {
            "summary": {
                "files_processed": len(staged),
                "concepts_extracted": 2,
                "candidates_added": 1,
                "concepts_created": 1,
                "concepts_promoted": 1,
                "concepts_skipped": 0,
                "errors": 0,
            },
            "results": [],
        }

    monkeypatch.setattr("ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow", fake_run_absorb_workflow)

    result = pipeline.step_absorb(
        dry_run=False,
        quality_score=4.0,
        qualified_files=[str(path) for path in deep_dives],
        batch_size=2,
    )

    assert result["success"] is True
    payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
    current = payload["run_ledger"]["current_step"]
    assert current["step_name"] == "absorb"
    assert current["progress_mode"] == "counted"
    assert current["work_units_total"] == 2
    assert current["work_units_done"] == 2
    assert current["progress_percent"] == 100.0
    assert current["current_item"] == "beta_深度解读.md"
    assert payload["run_ledger"]["last_meaningful_event"]["event_type"] == "absorb_file_processed"


def test_run_absorb_workflow_direct_reports_file_level_errors(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_module
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    def fake_run_absorb_workflow(*_args, **_kwargs):
        return {
            "summary": {
                "files_processed": 1,
                "errors": 1,
            },
            "results": [
                {
                    "file": "broken_深度解读.md",
                    "error": "extract failed",
                }
            ],
        }

    monkeypatch.setattr(pipeline_module, "run_absorb_workflow", fake_run_absorb_workflow)

    result = pipeline._run_absorb_workflow_direct(dry_run=False, recent=7, total_files=1)

    assert result["success"] is False
    assert result["error"] == "1 absorb file(s) failed"


def test_step_absorb_rejects_non_positive_batch_size(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.step_absorb(batch_size=-1)

    assert result["success"] is False
    assert result["error"] == "invalid_batch_size (-1 <= 0)"


def test_run_pipeline_absorb_requires_artifact_when_quality_result_missing():
    from ovp_pipeline.workflow_handlers import run_pipeline_absorb

    captured: dict[str, object] = {}

    class FakePipeline:
        def step_absorb(self, recent_days, dry_run, **kwargs):
            captured["recent_days"] = recent_days
            captured["dry_run"] = dry_run
            captured.update(kwargs)
            return {"success": False, "reason": "missing_quality_stage_artifact"}

    result = run_pipeline_absorb(pipeline=FakePipeline(), results={})

    assert result["success"] is False
    assert captured["qualified_files"] is None
    assert captured["require_quality_artifact"] is True


def test_run_pipeline_records_blocked_stage_as_blocked_transaction_reason(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Blocked transaction", planned_steps=["absorb"])

    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(pipeline, "_count_output_files", lambda *_args, **_kwargs: {"produced": 0})
    monkeypatch.setattr(
        pipeline_source,
        "execute_profile_stage_handler",
        lambda *_args, **_kwargs: {
            "success": False,
            "blocked": True,
            "reason": "missing_quality_stage_artifact",
            "error": "Absorb requires a matching quality stage artifact.",
        },
        raising=False,
    )

    result = pipeline.run_pipeline(steps=["absorb"], dry_run=False)

    payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
    assert result["absorb"]["blocked"] is True
    assert payload["steps"]["absorb"]["status"] == "blocked"
    assert payload["steps"]["absorb"]["blocked_reason"] == "missing_quality_stage_artifact"
    assert payload["failure_reason"] == "Blocked at step: absorb (missing_quality_stage_artifact)"
    assert payload["run_ledger"]["error_summary"] == "Blocked at step: absorb (missing_quality_stage_artifact)"


def test_run_command_timeout_is_failure(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    result = pipeline.run_command([sys.executable, "-c", "import time; time.sleep(2)"], "absorb", timeout=1)

    assert result["success"] is False
    assert result["timeout"] is True


def test_subprocess_env_preserves_fetcher_proxy_vars_by_default(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, "http://external-proxy.example:41474")

    env = pipeline._subprocess_env()

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert env[key] == "http://external-proxy.example:41474"


def test_pipeline_runs_note_type_normalize_step(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    calls: list[list[str]] = []

    def fake_run_command(cmd, step_name, **kwargs):
        calls.append(cmd)
        return {"success": True, "stdout": "changed:  2\nskipped:  3\n", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_note_type_normalize(dry_run=False)

    assert result["success"] is True
    assert result["note_type_changed"] == 2
    assert result["note_type_skipped"] == 3
    assert calls[0][:3] == [sys.executable, "-m", "ovp_pipeline.commands.note_type_normalize"]


def test_pipeline_uses_vault_workflow_lock(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_source
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    calls: list[str] = []

    class FakeLock:
        def __enter__(self):
            calls.append("enter")

        def __exit__(self, exc_type, exc, tb):
            calls.append("exit")

    monkeypatch.setattr(pipeline_source, "vault_workflow_lock", lambda vault_dir: FakeLock())
    monkeypatch.setattr(pipeline, "_get_before_counts", lambda: {})
    monkeypatch.setattr(pipeline, "_count_output_files", lambda *args, **kwargs: {})
    def fake_step(**kwargs):
        calls.append("step")
        return {"success": True}

    monkeypatch.setattr(pipeline, "step_pinboard", fake_step)

    result = pipeline.run_pipeline(steps=["pinboard"], dry_run=True)

    assert result["pinboard"]["success"] is True
    assert calls == ["enter", "step", "exit"]


def test_step_pinboard_process_heartbeats_current_item_before_processor_runs(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_module
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "50-Inbox" / "02-Pinboard").mkdir(parents=True)
    (vault / "50-Inbox" / "02-Pinboard-Archive").mkdir(parents=True)
    (vault / "60-Logs").mkdir(parents=True)
    pinboard_file = vault / "50-Inbox" / "02-Pinboard" / "sample.md"
    pinboard_file.write_text(
        """---
title: Sample
type: pinboard-article
source: https://example.com/sample
---

# Sample
""",
        encoding="utf-8",
    )
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Pinboard processor heartbeat")
    current_items_seen: list[str | None] = []

    def direct_subprocess_run_is_not_allowed(*_args, **_kwargs):
        raise AssertionError("pinboard processors must run through run_command")

    def fake_run_command(_cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        assert step_name == "pinboard_process"
        assert timeout == 600
        payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
        current_items_seen.append(payload["run_ledger"]["current_step"].get("current_item"))
        return {"success": True, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(pipeline_module.subprocess, "run", direct_subprocess_run_is_not_allowed)
    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_pinboard_process(dry_run=False)

    assert result["success"] is True
    assert current_items_seen == ["sample.md"]
    assert not pinboard_file.exists()


def test_step_pinboard_decomposes_cross_day_history_into_daily_requests(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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


def test_collect_absorb_targets_recent_filters_by_file_mtime(tmp_path):
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets

    vault = tmp_path / "vault"
    layout = VaultLayout.from_vault(vault)
    month_dir = layout.vault_dir / "20-Areas" / "AI-Research" / "Topics" / datetime.now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    recent_file = month_dir / "recent_深度解读.md"
    old_file = month_dir / "old_深度解读.md"
    recent_file.write_text("# recent\n", encoding="utf-8")
    old_file.write_text("# old\n", encoding="utf-8")
    old_ts = datetime.now().timestamp() - (30 * 24 * 60 * 60)
    os.utime(old_file, (old_ts, old_ts))

    targets = _collect_absorb_targets(layout, recent=7)

    assert targets == [recent_file]


def test_collect_absorb_targets_rejects_intake_source_files(tmp_path):
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets

    vault = tmp_path / "vault"
    layout = VaultLayout.from_vault(vault)
    clipping = layout.clippings_dir / "Raw Clip.md"
    clipping.parent.mkdir(parents=True, exist_ok=True)
    clipping.write_text("# raw\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source lifecycle"):
        _collect_absorb_targets(layout, file_path=clipping)


def test_run_absorb_workflow_rejects_intake_source_directories(tmp_path, monkeypatch):
    from ovp_pipeline import auto_evergreen_extractor as extractor_module
    from ovp_pipeline.auto_evergreen_extractor import run_absorb_workflow

    vault = tmp_path / "vault"
    layout = VaultLayout.from_vault(vault)
    clipping = layout.clippings_dir / "Raw Clip_深度解读.md"
    clipping.parent.mkdir(parents=True, exist_ok=True)
    clipping.write_text("# raw\n", encoding="utf-8")

    class FakeExtractor:
        def __init__(self, vault_dir, logger):
            self.vault_dir = vault_dir

        def init_llm(self, *args, **kwargs):
            return None

        def process_directory(self, *args, **kwargs):
            raise AssertionError("intake directory must be rejected before process_directory")

    monkeypatch.setattr(extractor_module, "AutoEvergreenExtractor", FakeExtractor)

    with pytest.raises(ValueError, match="source lifecycle"):
        run_absorb_workflow(vault, directory=layout.clippings_dir)


def test_before_counts_include_monthly_processed_files(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)

    timeout = pipeline._calculate_timeout("quality", batch_size=12)

    assert timeout > 600


def test_fix_links_timeout_scales_with_deep_dive_count(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
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
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    topic_dir = vault / "20-Areas" / "Tools" / "Topics" / QUALITY_TEST_MONTH
    topic_dir.mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)

    for idx in range(8):
        (topic_dir / f"fix_links_{idx}_深度解读.md").write_text("# x\n", encoding="utf-8")

    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Fix links progress")

    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        captured["timeout"] = timeout
        captured["step_name"] = step_name
        captured["cmd"] = cmd
        payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
        captured["current_step"] = payload["run_ledger"]["current_step"]
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_fix_links(dry_run=False)

    assert result["success"] is True
    assert captured["step_name"] == "fix_links"
    assert "ovp_pipeline.commands.migrate_broken_links" in " ".join(captured["cmd"])
    assert "--exact-only" in captured["cmd"]
    assert captured["timeout"] > 300
    current = captured["current_step"]
    assert current["progress_mode"] == "counted"
    assert current["work_units_total"] == 8
    assert current["work_units_done"] == 0
    assert current["current_item"] == "migrate broken wikilinks"


def test_step_knowledge_index_uses_dynamic_timeout(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    pipeline.txn_id = txn.start("enhanced-pipeline", "Knowledge index progress")

    invocations: list[dict] = []

    def fake_run_command(cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        record = {"cmd": cmd, "step_name": step_name, "timeout": timeout}
        payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
        record["current_step"] = payload["run_ledger"]["current_step"]
        invocations.append(record)
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "run_command", fake_run_command)

    result = pipeline.step_knowledge_index(dry_run=False)

    assert result["success"] is True
    # Phase 38: step_knowledge_index now also fires build_crystals + working_memory
    # piggyback subprocesses; the *first* invocation is still the rebuild itself.
    rebuild = next(
        inv for inv in invocations
        if "ovp_pipeline.commands.knowledge_index" in " ".join(inv["cmd"])
    )
    assert rebuild["step_name"] == "knowledge_index"
    assert rebuild["timeout"] > 120
    current = rebuild["current_step"]
    assert current["progress_mode"] == "counted"
    assert current["work_units_total"] == 400
    assert current["work_units_done"] == 0
    assert current["current_item"] == "rebuild knowledge index"


def test_run_command_streams_resolve_progress_to_run_ledger(tmp_path, monkeypatch):
    from ovp_pipeline import unified_pipeline_enhanced as pipeline_module
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("enhanced-pipeline", "Stream subprocess progress")

    class FakeProcess:
        def __init__(self, cmd, cwd, stdout, stderr, text, env):
            self.returncode = 0
            self.stdout = stdout
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                self.stdout.write(
                    "Loaded 4730 registry entries\n"
                    "Scanning for broken links...\n"
                    "Found 5625 unique broken mentions\n"
                    "Resolving...\n"
                    "  Resolved 50/5625...\n"
                )
                self.stdout.flush()
                return None
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self):
            return self.returncode

    monkeypatch.setattr(pipeline_module.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(pipeline_module.time, "sleep", lambda _seconds: None)

    result = pipeline.run_command(["fake-progress-command"], "fix_links", timeout=30)

    payload = json.loads((vault / "60-Logs" / "transactions" / f"{pipeline.txn_id}.json").read_text(encoding="utf-8"))
    current = payload["run_ledger"]["current_step"]
    assert result["success"] is True
    assert current["progress_mode"] == "counted"
    assert current["work_units_total"] == 5625
    assert current["work_units_done"] == 50
    assert current["current_item"] == "Resolved 50/5625"
    assert payload["run_ledger"]["last_meaningful_event"]["event_type"] == "command_progress"


def test_step_refine_runs_cleanup_then_breakdown(tmp_path, monkeypatch):
    from ovp_pipeline.unified_pipeline_enhanced import PipelineLogger, TransactionManager

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
    assert "ovp_pipeline.commands.cleanup" in " ".join(commands[0][1])
    assert "ovp_pipeline.commands.breakdown" in " ".join(commands[1][1])


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
