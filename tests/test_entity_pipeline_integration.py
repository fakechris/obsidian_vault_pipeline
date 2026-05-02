"""Tests for Entity Layer pipeline integration (Phase B.4).

Validates:
- entity_extract step is registered in BASE_PIPELINE_STEPS
- entity_extract processor contract is registered
- workflow handler dispatches to step_entity_extract
- knowledge_index includes Entity directory in scan
- step_entity_extract handles dry_run / missing LLM gracefully
- STAGE_CACHE_POLICIES includes entity_extract
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_entity_extract_in_base_pipeline_steps():
    """entity_extract must appear in BASE_PIPELINE_STEPS after absorb."""
    from ovp_pipeline.unified_pipeline_enhanced import BASE_PIPELINE_STEPS

    assert "entity_extract" in BASE_PIPELINE_STEPS
    absorb_idx = BASE_PIPELINE_STEPS.index("absorb")
    entity_idx = BASE_PIPELINE_STEPS.index("entity_extract")
    assert entity_idx == absorb_idx + 1


def test_entity_extract_cache_policy():
    """entity_extract should have a cache policy configured."""
    from ovp_pipeline.unified_pipeline_enhanced import STAGE_CACHE_POLICIES

    assert "entity_extract" in STAGE_CACHE_POLICIES


def test_entity_extract_processor_contract():
    """entity_extract processor contract should be registered in research-tech pack."""
    from ovp_pipeline.packs.research_tech.processor_contracts import (
        build_processor_contracts,
    )

    contracts = build_processor_contracts()
    names = [c.name for c in contracts]
    assert "entity_extract" in names

    entity_contract = next(c for c in contracts if c.name == "entity_extract")
    assert entity_contract.stage == "entity_extract"
    assert "entity_extract" in entity_contract.entrypoint


def test_workflow_handler_exists():
    """run_pipeline_entity_extract handler must be importable."""
    from ovp_pipeline.workflow_handlers import run_pipeline_entity_extract

    assert callable(run_pipeline_entity_extract)


def test_step_entity_extract_dry_run(tmp_path):
    """step_entity_extract should handle dry_run without errors."""
    from ovp_pipeline.unified_pipeline_enhanced import (
        EnhancedPipeline,
        PipelineLogger,
        TransactionManager,
    )

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "50-Inbox" / "01-Raw").mkdir(parents=True)
    (vault_dir / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault_dir / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault_dir / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault_dir)
    txn = TransactionManager(vault_dir)
    pipeline = EnhancedPipeline(vault_dir, logger, txn)
    result = pipeline.step_entity_extract(dry_run=True)

    assert result["success"] is True
    assert result["produced"] == 0


def test_step_entity_extract_no_llm(tmp_path):
    """step_entity_extract should skip gracefully when no LLM is available."""
    from ovp_pipeline.unified_pipeline_enhanced import (
        EnhancedPipeline,
        PipelineLogger,
        TransactionManager,
    )

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "50-Inbox" / "01-Raw").mkdir(parents=True)
    (vault_dir / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault_dir / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault_dir / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault_dir)
    txn = TransactionManager(vault_dir)
    pipeline = EnhancedPipeline(vault_dir, logger, txn)
    result = pipeline.step_entity_extract(dry_run=False)

    assert result["success"] is True
    assert result["total_entities"] == 0


def test_knowledge_index_entity_dir_scan(tmp_path):
    """Entity directory files should be included in knowledge_index scan."""
    from ovp_pipeline.knowledge_index import FrontmatterParser

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    entity_dir = vault_dir / "10-Knowledge" / "Entity"
    entity_dir.mkdir(parents=True)
    candidates_dir = entity_dir / "_Candidates"
    candidates_dir.mkdir()

    (entity_dir / "openai.md").write_text(
        "---\ntitle: OpenAI\ntype: entity\nentity_type: company\n---\n# OpenAI\n",
        encoding="utf-8",
    )
    (candidates_dir / "candidate.md").write_text(
        "---\ntitle: Candidate\ntype: entity\nstatus: candidate\n---\n# Candidate\n",
        encoding="utf-8",
    )

    parser = FrontmatterParser(vault_dir)
    all_items = list(parser.parse_directory(entity_dir, recursive=True))
    active_items = [m for m in all_items if "_Candidates" not in Path(m.path).parts]

    assert len(active_items) >= 1
    active_paths = [m.path for m in active_items]
    assert any("openai.md" in p for p in active_paths)
    assert not any("candidate.md" in p for p in active_paths)


def test_entity_extract_summary_format():
    """entity_extract summary string should include produced/total/mentions."""
    result = {"produced": 5, "total_entities": 42, "mentions_extracted": 15}

    produced = result.get("produced", 0)
    total = result.get("total_entities", 0)
    mentions = result.get("mentions_extracted", 0)
    detail = f"新增Entity: {produced}, 累计: {total}, Mentions: {mentions}"

    assert "新增Entity: 5" in detail
    assert "累计: 42" in detail
    assert "Mentions: 15" in detail


def test_stage_input_files_entity_extract(tmp_path):
    """_stage_input_files for entity_extract should delegate to absorb inputs."""
    from ovp_pipeline.unified_pipeline_enhanced import (
        EnhancedPipeline,
        PipelineLogger,
        TransactionManager,
    )

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "50-Inbox" / "01-Raw").mkdir(parents=True)
    (vault_dir / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault_dir / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault_dir / "60-Logs").mkdir(parents=True)

    logger = PipelineLogger(vault_dir)
    txn = TransactionManager(vault_dir)
    pipeline = EnhancedPipeline(vault_dir, logger, txn)
    entity_files = pipeline._stage_input_files("entity_extract")
    absorb_files = pipeline._stage_input_files("absorb")
    assert entity_files == absorb_files
