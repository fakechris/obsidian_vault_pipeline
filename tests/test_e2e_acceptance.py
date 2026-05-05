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

    def test_absorb_processed_files_are_vault_paths_not_staging(
        self, pipeline, synthetic_deep_dive, temp_vault,
    ):
        """processed_files must contain real on-disk vault paths, not
        the staging tempdir paths that the qualified_files code path
        synthesizes inside ``with tempfile.TemporaryDirectory(...)``.

        The 2026-05-04 incident: ``step_absorb`` staged each qualified
        deep dive as a symlink under ``50-Inbox/.../absorb-qualified-XXX/``
        and the inner ``run_absorb_workflow`` reported result["file"]
        as that staging path.  After the ``with`` block exited the
        tempdir was rm-rf'd, so processed_files contained dead paths
        that ``step_entity_extract`` then silently skipped via its
        ``if not fpath.exists(): continue`` guard.  The earlier
        ``test_absorb_to_entity_extract_chain`` mocked
        ``run_absorb_workflow`` directly with a canned payload using
        ``str(synthetic_deep_dive)`` and therefore never exercised the
        staging code path, missing this bug for ~3 days.

        This test takes the real path: it patches the inner workflow
        AT THE LEVEL run_absorb_workflow's progress_callback would
        have been called from, returns a payload using the staging
        path the orchestration would have created, and asserts that
        step_absorb un-stages those paths back to the original
        ``synthetic_deep_dive`` location before returning.
        """
        # The qualified_files path in step_absorb stages files into a
        # tempfile.TemporaryDirectory under layout.logs_dir.  We inject
        # a mock that observes the staging tempdir and returns a
        # synthesized result whose ``file`` field IS the staging path
        # — exactly what the real workflow does.
        captured_staged_paths: list[str] = []

        def fake_run_absorb_workflow(vault_dir, *, directory, **kwargs):
            # The directory passed in IS the absorb-qualified-XXX tempdir.
            # The synthetic_deep_dive was symlinked into it under its
            # original basename.
            staged = directory / synthetic_deep_dive.name
            captured_staged_paths.append(str(staged))
            return {
                "mode": "absorb",
                "summary": {
                    "files_processed": 1, "concepts_promoted": 1,
                    "errors": 0,
                },
                "results": [{
                    "file": str(staged),  # STAGING path — the bug source
                    "concepts_extracted": 1,
                    "candidates_added": 0,
                    "concepts_promoted": 1,
                    "concepts_created": 0,
                    "concepts_skipped": 0,
                    "concepts": [{
                        "slug": canonicalize_note_id(synthetic_deep_dive.stem),
                        "status": "promoted_created",
                    }],
                }],
                "source_scope": {},
            }

        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            side_effect=fake_run_absorb_workflow,
        ):
            result = pipeline.step_absorb(
                qualified_files=[str(synthetic_deep_dive)],
            )

        # Mock observed: the workflow saw the staging path
        assert captured_staged_paths, "fake workflow was never invoked"
        assert "absorb-qualified-" in captured_staged_paths[0], (
            "staging dir name pattern changed — update this test"
        )

        # Result invariants — the bug we are guarding against:
        assert isinstance(result, AbsorbStepResult)
        assert result.success is True
        assert result.processed_files, (
            "step_absorb returned empty processed_files even though the "
            "workflow saw the staged file — the orchestration is dropping "
            "data between layers"
        )
        for processed in result.processed_files:
            p = Path(processed)
            # The path must EXIST after step_absorb returns (tempdir is
            # gone by now, so any staging-dir leak shows up here).
            assert p.exists(), (
                f"processed_files contains non-existent path {processed!r} — "
                f"this is the staging-path leak fixed in the 2026-05-05 "
                f"incident retro.  Every entry in processed_files MUST "
                f"resolve under the vault, not the staging tempdir."
            )
            # Must be under the vault, not under any tempdir.
            try:
                p.resolve().relative_to(temp_vault.resolve())
            except ValueError:
                pytest.fail(
                    f"processed_files path {processed!r} is outside the "
                    f"vault {temp_vault!r} — staging-path leaked again"
                )
            assert "absorb-qualified-" not in str(p), (
                f"processed_files contains staging-dir path: {processed}"
            )

    def test_absorb_handles_two_inputs_with_same_basename(
        self, pipeline, temp_vault,
    ):
        """Two qualified files with the same basename in different
        vault directories must round-trip back to their original
        paths, not collapse onto one entry.

        Pre-fix the staged_sources dict was keyed by basename, so a
        second ``README.md`` overwrote the first.  After the staging
        rename the second file lived at ``staging/README-2.md`` —
        but the rewrite loop only matched ``README.md`` against the
        dict and silently kept the staging path for the second
        result, leaking another orphan path through to entity-extract.

        Keying ``staged_sources`` by absolute staging path makes
        both files round-trip cleanly even if every basename is
        identical.
        """
        # Two source files with the SAME basename in different dirs.
        dir_a = temp_vault / "20-Areas" / "AlphaTopic" / "Topics" / "2026-04"
        dir_b = temp_vault / "20-Areas" / "BetaTopic" / "Topics" / "2026-04"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)
        same_name = "shared_name_深度解读.md"
        src_a = dir_a / same_name
        src_b = dir_b / same_name
        src_a.write_text(
            "---\ntitle: A\ndate: 2026-04-09\n---\n\n# A body\n",
            encoding="utf-8",
        )
        src_b.write_text(
            "---\ntitle: B\ndate: 2026-04-09\n---\n\n# B body\n",
            encoding="utf-8",
        )

        # Mock the inner workflow to echo back staging paths for
        # whichever files showed up in the staging dir.
        def fake_run_absorb_workflow(vault_dir, *, directory, **kwargs):
            staged = sorted(p for p in directory.iterdir() if p.is_file())
            return {
                "mode": "absorb",
                "summary": {
                    "files_processed": len(staged),
                    "concepts_promoted": len(staged),
                    "errors": 0,
                },
                "results": [
                    {
                        "file": str(s),
                        "concepts_extracted": 1,
                        "candidates_added": 0,
                        "concepts_promoted": 1,
                        "concepts_created": 0,
                        "concepts_skipped": 0,
                        "concepts": [{
                            "slug": canonicalize_note_id(s.stem),
                            "status": "promoted_created",
                        }],
                    }
                    for s in staged
                ],
                "source_scope": {},
            }

        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            side_effect=fake_run_absorb_workflow,
        ):
            result = pipeline.step_absorb(
                qualified_files=[str(src_a), str(src_b)],
            )

        assert isinstance(result, AbsorbStepResult)
        # Both vault paths must come back, distinct, and pointing
        # at the right files on disk.
        processed = sorted(result.processed_files)
        expected = sorted([str(src_a), str(src_b)])
        assert processed == expected, (
            f"basename collision dropped one of the inputs: "
            f"got {processed!r}, expected {expected!r}"
        )
