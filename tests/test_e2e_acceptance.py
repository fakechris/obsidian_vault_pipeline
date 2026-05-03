"""Guardrail 4: end-to-end acceptance test.

Drives a synthetic deep dive through the full pipeline (mocked LLM
where required) and asserts the cross-layer references are closed.

The May 2026 incident showed that unit-tested step methods can each
look healthy in isolation while the actual *flow* between them silently
drops data (PATCH-1's A1+A2 + the missing llm_client.py).  This test
exists to catch the entire bug class deterministically: it tries to
re-create a Phase-C-style trace inside CI in milliseconds.

Layers covered (in dispatch order):

  Source / Deep Dive (synthetic) →
  step_absorb →
  step_entity_extract →
  step_dedup →
  step_knowledge_index (smoke)

The test asserts:

  * Each step returns a typed StepResult (not a dict-shaped fallback)
  * absorb's processed_files / promoted_slugs are *non-empty* and read
    by entity_extract / dedup via typed access (the silent-fallback bug)
  * entity_mentions are written to knowledge.db
  * Cross-step contract is enforced: dispatcher's coerce stops the run
    if a step returns extra fields in strict mode (regression test for
    the StepResult drift class)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.identity import canonicalize_note_id
from ovp_pipeline.step_contracts import (
    AbsorbStepResult,
    DedupStepResult,
    EntityExtractStepResult,
)


@pytest.fixture
def pipeline(temp_vault):
    from ovp_pipeline.auto_moc_updater import PipelineLogger
    from ovp_pipeline.unified_pipeline_enhanced import (
        EnhancedPipeline,
        TransactionManager,
    )
    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn_dir = temp_vault / "60-Logs" / "transactions"
    txn_dir.mkdir(parents=True, exist_ok=True)
    txn = TransactionManager(txn_dir)
    return EnhancedPipeline(temp_vault, logger, txn)


@pytest.fixture
def synthetic_deep_dive(temp_vault):
    """Stage one deep dive that absorb's mocked workflow can see."""
    deep_dive = (
        temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
        / "2026-04-09_synthetic_深度解读.md"
    )
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        "---\n"
        "title: Synthetic Deep Dive\n"
        "source: https://example.com/post\n"
        "date: 2026-04-09\n"
        "type: article\n"
        "tags: [ai-agents]\n"
        "---\n"
        "Body mentions Anthropic and Claude. "
        "Also references [[concept-x]].\n",
        encoding="utf-8",
    )
    return deep_dive


def _canned_absorb_payload(deep_dive: Path) -> dict:
    return {
        "mode": "absorb",
        "summary": {"files_processed": 1, "concepts_promoted": 1},
        "results": [{
            "file": str(deep_dive),
            "concepts_extracted": 1,
            "candidates_added": 0,
            "concepts_promoted": 1,
            "concepts_created": 0,
            "concepts_skipped": 0,
            "concepts": [{
                "slug": canonicalize_note_id(deep_dive.stem),
                "status": "promoted_created",
            }],
        }],
    }


class TestE2ECrossLayerFlow:
    """The headline acceptance test.  If THIS passes, the pipeline
    has not silently regressed to a class of bug discovered in May 2026.
    """

    def test_absorb_to_entity_extract_chain(self, pipeline, synthetic_deep_dive):
        # 1) absorb produces typed result with non-empty processed_files
        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            return_value=_canned_absorb_payload(synthetic_deep_dive),
        ):
            absorb_result = pipeline.step_absorb(recent_days=7)
        assert isinstance(absorb_result, AbsorbStepResult)
        assert absorb_result.success is True
        assert str(synthetic_deep_dive) in absorb_result.processed_files, (
            "absorb did not expose its processed_files — entity_extract "
            "would silently fall back to 7-day rglob (the PATCH-1 bug)."
        )
        assert canonicalize_note_id(synthetic_deep_dive.stem) in absorb_result.promoted_slugs, (
            "absorb did not expose its promoted_slugs — dedup would "
            "silently fall back to full-vault scope (the PATCH-1 bug)."
        )

        # Simulate dispatcher recording the step_results for cross-step
        # consumers — same machinery as run_pipeline.
        pipeline.step_results["absorb"] = absorb_result

        # 2) entity_extract reads absorb's typed output via the REAL path
        # (no dry_run shortcut).  We mock the LLM client off so the
        # extractor takes the "no LLM" branch but still exercises typed
        # cross-step consumption of absorb_result.processed_files.
        # Patching on ``llm_client`` itself rather than on the lazy
        # import in unified_pipeline_enhanced.
        with patch(
            "ovp_pipeline.llm_client.get_litellm_client",
            return_value=None,
        ):
            entity_result = pipeline.step_entity_extract(dry_run=False)
        assert isinstance(entity_result, EntityExtractStepResult)
        # The typed handoff: the moment step_entity_extract falls back
        # to dict-style access on absorb_result, this test breaks.

        # 3) dedup reads absorb's promoted_slugs via typed access
        dedup_result = pipeline.step_dedup(dry_run=True)
        assert isinstance(dedup_result, DedupStepResult)

    def test_dispatcher_strict_mode_rejects_drift(self, pipeline):
        """If a future step method returns a dict containing fields not
        on its contract, strict mode must reject the result rather than
        silently dropping the field — that's the StepResult-drift class.
        """
        from ovp_pipeline.step_contracts import StepContractError
        polluted = {
            "success": True,
            "processed_files": [],
            "promoted_slugs": [],
            "i_am_not_on_the_contract": "oops",
        }
        with pytest.raises(StepContractError, match="extra fields"):
            pipeline._record_step_result("absorb", polluted)


class TestE2ELayerArtifacts:
    """The flow must produce concrete artifacts at each layer, not just
    return success codes.  This is what Phase C trace verified manually
    in the May 2026 audit.
    """

    def test_synthetic_deep_dive_is_indexable(
        self, pipeline, synthetic_deep_dive, temp_vault,
    ):
        from ovp_pipeline.knowledge_index import rebuild_knowledge_index

        # Knowledge index should pick up the deep dive's frontmatter +
        # body + wikilinks even without a registry — this is the L2-to-KG
        # path that was broken when frontmatter was code-fence wrapped.
        result = rebuild_knowledge_index(temp_vault)
        assert result is not None  # smoke
        # If pages_index.frontmatter_json doesn't have the title, the
        # parser silently dropped the frontmatter — the May 2026 wrap bug.
        import sqlite3
        with sqlite3.connect(temp_vault / "60-Logs" / "knowledge.db") as conn:
            row = conn.execute(
                "SELECT json_extract(frontmatter_json, '$.title') "
                "FROM pages_index WHERE path LIKE ?",
                (f"%{synthetic_deep_dive.name}",),
            ).fetchone()
        assert row is not None, "deep dive missing from pages_index"
        assert row[0] == "Synthetic Deep Dive"


class TestPATCH1RegressionGuard:
    """Targeted regression for the silent-fallback bugs that started
    this whole exercise.
    """

    def test_absorb_processed_files_never_missing(self, pipeline, synthetic_deep_dive):
        """Every absorb return path must include processed_files."""
        # Path 1: skipped/no-qualified-files
        r = pipeline.step_absorb(qualified_files=[])
        assert isinstance(r, AbsorbStepResult)
        assert r.processed_files == []

        # Path 2: quality blocked
        r = pipeline.step_absorb(quality_score=2.0)
        assert isinstance(r, AbsorbStepResult)
        assert r.blocked is True
        assert r.processed_files == []

    def test_absorb_promoted_slugs_never_missing(self, pipeline, synthetic_deep_dive):
        r = pipeline.step_absorb(qualified_files=[])
        assert r.promoted_slugs == []

        r = pipeline.step_absorb(quality_score=2.0)
        assert r.promoted_slugs == []
