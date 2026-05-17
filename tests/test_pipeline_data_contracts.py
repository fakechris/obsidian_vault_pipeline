"""
Pipeline data contract tests.

Ensure consistency of enums, slug normalization, and data contracts
across pipeline stages (extractor -> registry -> candidate -> promote -> knowledge.db).
"""

import pytest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ovp_pipeline.identity import canonicalize_note_id
from ovp_pipeline.object_kinds import (
    ALL_OBJECT_KINDS,
    CORE_OBJECT_KINDS,
    REGISTRY_VALID_KINDS,
    STRUCTURAL_OBJECT_KINDS,
    KIND_CONCEPT,
    KIND_PERSON,
    KIND_TOOL,
    KIND_COMPANY,
    KIND_EVERGREEN,
    LEGACY_KIND_MAP,
    normalize_kind,
)
from ovp_pipeline.concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_CANDIDATE,
    STATUS_ACTIVE,
)
from ovp_pipeline.promote_candidates import write_candidate_file, promote_candidate


class TestKindTaxonomyConsistency:
    """Verify the kind enum sets are consistent and complete."""

    def test_core_kinds_subset_of_all(self):
        assert CORE_OBJECT_KINDS <= ALL_OBJECT_KINDS

    def test_structural_kinds_subset_of_all(self):
        assert STRUCTURAL_OBJECT_KINDS <= ALL_OBJECT_KINDS

    def test_core_and_structural_disjoint(self):
        overlap = CORE_OBJECT_KINDS & STRUCTURAL_OBJECT_KINDS
        assert not overlap, f"Core and structural kinds overlap: {overlap}"

    def test_core_plus_structural_plus_v2_units_equals_all(self):
        # BL-025/026: ALL now spans three axes — entity-side kinds
        # (CORE), structural roles (evergreen/claim/document), and
        # v2 unit kinds (fact/method/procedure/...).
        from ovp_pipeline.object_kinds import V2_UNIT_TYPES
        assert (
            CORE_OBJECT_KINDS | STRUCTURAL_OBJECT_KINDS | V2_UNIT_TYPES
            == ALL_OBJECT_KINDS
        )

    def test_registry_valid_kinds_equals_core_plus_v2_units(self):
        # BL-025/026: registry accepts both entity-side and v2
        # unit kinds.
        from ovp_pipeline.object_kinds import V2_UNIT_TYPES
        assert REGISTRY_VALID_KINDS == CORE_OBJECT_KINDS | V2_UNIT_TYPES

    def test_evergreen_not_in_core(self):
        assert KIND_EVERGREEN not in CORE_OBJECT_KINDS

    def test_legacy_kinds_normalize_to_core(self):
        for legacy, canonical in LEGACY_KIND_MAP.items():
            assert canonical in CORE_OBJECT_KINDS, (
                f"Legacy kind '{legacy}' maps to '{canonical}' "
                f"which is not in CORE_OBJECT_KINDS"
            )


class TestSlugContract:
    """Slug normalization contract across pipeline stages."""

    def test_slug_is_lowercase(self):
        assert canonicalize_note_id("MyThing") == "mything"

    def test_slug_replaces_spaces_with_hyphens(self):
        assert canonicalize_note_id("My Thing") == "my-thing"

    def test_slug_replaces_underscores_with_hyphens(self):
        assert canonicalize_note_id("my_thing") == "my-thing"

    def test_slug_collapses_repeated_hyphens(self):
        assert canonicalize_note_id("my--thing") == "my-thing"

    def test_slug_strips_path_prefix(self):
        assert canonicalize_note_id("path/to/Note Name") == "note-name"

    def test_slug_strips_heading_suffix(self):
        assert canonicalize_note_id("Note Name#section") == "note-name"

    def test_slug_strips_query_suffix(self):
        assert canonicalize_note_id("Note Name?query") == "note-name"

    def test_slug_strips_leading_trailing_hyphens(self):
        assert canonicalize_note_id("-note-name-") == "note-name"

    def test_slug_unicode_preserved(self):
        result = canonicalize_note_id("注意力机制")
        assert result == "注意力机制"


class TestExtractorToRegistryContract:
    """Data flows correctly from extractor concept dict to registry entry."""

    def test_kind_flows_from_concept_to_registry(self, temp_vault):
        registry = ConceptRegistry(temp_vault)

        concept = {
            "concept_name": "Andrej Karpathy",
            "title": "Andrej Karpathy",
            "entity_type": "person",
            "one_sentence_def": "AI researcher and educator.",
            "related_concepts": [],
        }

        canonical_slug = canonicalize_note_id(concept["concept_name"])
        resolved_kind = normalize_kind(concept["entity_type"])
        if resolved_kind not in CORE_OBJECT_KINDS:
            resolved_kind = KIND_CONCEPT

        entry = registry.upsert_candidate(
            slug=canonical_slug,
            title=concept["title"],
            definition=concept["one_sentence_def"],
            area="general",
            aliases=[concept["concept_name"]],
            kind=resolved_kind,
        )

        assert entry.slug == "andrej-karpathy"
        assert entry.kind == KIND_PERSON

    def test_invalid_kind_rejected_by_registry(self, temp_vault):
        registry = ConceptRegistry(temp_vault)

        with pytest.raises(ValueError, match="Invalid kind"):
            registry.upsert_candidate(
                slug="some-thing",
                title="Some Thing",
                definition="Test.",
                area="general",
                kind="invalid_kind_xyz",
            )


class TestRegistryToCandidateContract:
    """Data flows from registry entry to candidate .md file."""

    def test_entity_type_written_to_candidate_frontmatter(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="openai",
            title="OpenAI",
            definition="AI research company.",
            area="general",
            kind=KIND_COMPANY,
        )
        registry.save()

        path = write_candidate_file(
            temp_vault,
            entry,
            dry_run=False,
        )
        assert path is not None
        text = path.read_text(encoding="utf-8")
        assert "entity_type: company" in text, (
            f"Candidate file missing 'entity_type: company'. Content:\n{text[:300]}"
        )

    def test_slug_in_candidate_matches_registry(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="claude-code",
            title="Claude Code",
            definition="AI coding assistant.",
            area="general",
            kind=KIND_TOOL,
        )
        registry.save()

        path = write_candidate_file(temp_vault, entry, dry_run=False)
        assert path is not None
        text = path.read_text(encoding="utf-8")
        assert "note_id: claude-code" in text


class TestAbsorbStepResultContract:
    """step_absorb / _run_absorb_workflow_direct must always expose
    ``processed_files`` and ``promoted_slugs`` so downstream steps
    (entity_extract, dedup) can rely on them without ``.get(default=...)``.

    These keys had been silently absent from several return paths, causing
    entity_extract to fall back to a 7-day rglob and dedup to fall back
    to full-vault scope.  Lock the contract here.
    """

    REQUIRED_KEYS = {"processed_files", "promoted_slugs"}

    def _make_pipeline(self, vault_dir):
        from ovp_pipeline.auto_moc_updater import PipelineLogger
        from ovp_pipeline.unified_pipeline_enhanced import (
            EnhancedPipeline,
            TransactionManager,
        )
        logger = PipelineLogger(vault_dir / "60-Logs" / "pipeline.jsonl")
        txn_dir = vault_dir / "60-Logs" / "transactions"
        txn_dir.mkdir(parents=True, exist_ok=True)
        txn = TransactionManager(txn_dir)
        return EnhancedPipeline(vault_dir, logger, txn)

    def _canned_payload(self, files):
        results = [
            {
                "file": str(f),
                "concepts_extracted": 1,
                "candidates_added": 0,
                "concepts_promoted": 1,
                "concepts_created": 0,
                "concepts_skipped": 0,
                "concepts": [
                    {"slug": canonicalize_note_id(Path(f).stem), "status": "promoted_created"},
                ],
            }
            for f in files
        ]
        return {
            "mode": "absorb",
            "dry_run": False,
            "summary": {
                "files_processed": len(results),
                "concepts_extracted": len(results),
                "candidates_added": 0,
                "concepts_promoted": len(results),
                "concepts_created": 0,
                "concepts_skipped": 0,
                "errors": 0,
            },
            "results": results,
        }

    def test_direct_workflow_includes_required_keys(self, temp_vault):
        pipeline = self._make_pipeline(temp_vault)
        files = [
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "a_深度解读.md",
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "b_深度解读.md",
        ]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("---\ntitle: x\n---\nbody", encoding="utf-8")

        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            return_value=self._canned_payload(files),
        ):
            result = pipeline._run_absorb_workflow_direct(dry_run=False)

        assert self.REQUIRED_KEYS <= result.keys(), (
            f"Missing keys: {self.REQUIRED_KEYS - result.keys()}; got {sorted(result.keys())}"
        )
        assert sorted(result["processed_files"]) == sorted(str(f) for f in files)
        assert sorted(result["promoted_slugs"]) == ["a-深度解读", "b-深度解读"]

    def test_step_absorb_no_qualified_files_path(self, temp_vault):
        # No intake sources in 03-Processed → the BL-029 fallback
        # finds nothing → existing ``no_qualified_files`` skip
        # still holds.
        pipeline = self._make_pipeline(temp_vault)
        result = pipeline.step_absorb(qualified_files=[])
        assert self.REQUIRED_KEYS <= result.keys()
        assert result["processed_files"] == []
        assert result["promoted_slugs"] == []
        assert not result.get("bl029_intake_fallback")

    def test_step_absorb_bl029_fallback_when_intake_sources_exist(
        self, temp_vault
    ):
        """PR-A: pipeline absorb is called with
        ``require_quality_artifact``-derived ``qualified_files=[]``
        (post-BL-029 the quality stage only scans the removed
        deep-dive layer).  When eligible intake sources exist in
        50-Inbox/03-Processed, absorb must fall back to its own
        recent-target discovery instead of the
        ``no_qualified_files`` skip — otherwise post-BL-029 vaults
        never absorb anything via the pipeline."""
        from datetime import datetime, timezone

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        proc_dir = (
            temp_vault / "50-Inbox" / "03-Processed" / month
        )
        proc_dir.mkdir(parents=True, exist_ok=True)
        src = proc_dir / "2026-05-16_example_source.md"
        # Frontmatter with ``source:`` + body >200 chars →
        # passes _is_intake_only_source_markdown.
        src.write_text(
            "---\ntitle: Example\nsource: https://example.com/x\n---\n\n"
            + ("Real article body. " * 30),
            encoding="utf-8",
        )

        pipeline = self._make_pipeline(temp_vault)
        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            return_value=self._canned_payload([src]),
        ):
            result = pipeline.step_absorb(qualified_files=[])

        assert self.REQUIRED_KEYS <= result.keys()
        # The skip path must NOT have been taken.
        assert result.get("reason") != "no_qualified_files"
        assert result.get("bl029_intake_fallback") is True
        assert (
            result.get("fallback_reason")
            == "quality_artifact_empty_post_bl029"
        )
        assert result.get("fallback_intake_targets", 0) >= 1

    def test_step_absorb_fallback_ignores_qc_failed_deep_dives(
        self, temp_vault
    ):
        """codex PR #248 P1: a recent ``20-Areas`` deep-dive that
        FAILED quality (so the artifact is empty for THAT reason,
        not the BL-029 empty-scan) must NOT trigger the fallback.
        Falling back on it would bypass the quality gate.  With
        only a deep-dive present and no 03-Processed intake
        source, the skip must hold."""
        from datetime import datetime, timezone

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        dd_dir = (
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / month
        )
        dd_dir.mkdir(parents=True, exist_ok=True)
        (dd_dir / "qc_failed_深度解读.md").write_text(
            "---\ntitle: QC failed\n---\n" + ("low quality. " * 30),
            encoding="utf-8",
        )

        pipeline = self._make_pipeline(temp_vault)
        result = pipeline.step_absorb(qualified_files=[])
        # Skip preserved — the deep-dive is NOT a fallback target.
        assert result.get("reason") == "no_qualified_files"
        assert not result.get("bl029_intake_fallback")
        assert result["processed_files"] == []

    def test_step_absorb_quality_blocked_path(self, temp_vault):
        pipeline = self._make_pipeline(temp_vault)
        result = pipeline.step_absorb(quality_score=2.5)
        assert self.REQUIRED_KEYS <= result.keys()
        assert result["processed_files"] == []
        assert result["promoted_slugs"] == []

    def test_step_absorb_recent_days_path(self, temp_vault):
        pipeline = self._make_pipeline(temp_vault)
        files = [
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "z_深度解读.md",
        ]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("---\ntitle: z\n---\nbody", encoding="utf-8")

        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            return_value=self._canned_payload(files),
        ):
            result = pipeline.step_absorb(recent_days=7)

        assert self.REQUIRED_KEYS <= result.keys()
        assert result["processed_files"] == [str(files[0])]
        assert result["promoted_slugs"] == ["z-深度解读"]

    def test_step_absorb_qualified_files_batched_path(self, temp_vault):
        pipeline = self._make_pipeline(temp_vault)
        files = [
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "p_深度解读.md",
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "q_深度解读.md",
        ]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("---\ntitle: x\n---\nbody", encoding="utf-8")

        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            return_value=self._canned_payload(files),
        ):
            result = pipeline.step_absorb(qualified_files=[str(f) for f in files])

        assert self.REQUIRED_KEYS <= result.keys(), (
            f"Batched absorb path dropped keys: {self.REQUIRED_KEYS - result.keys()}"
        )
        # Both files were absorbed in this run via the batched path.
        assert sorted(result["processed_files"]) == sorted(str(f) for f in files)
        assert sorted(result["promoted_slugs"]) == ["p-深度解读", "q-深度解读"]


class TestStrictModeCrossStepIntegration:
    """End-to-end contract enforcement: every step produces a typed
    StepResult, the dispatcher coerces in strict mode (raising on unknown
    fields), and downstream consumers read typed attributes that are
    *guaranteed* to exist.

    Together these tests prove the silent-fallback class of bug
    (PATCH-1's A1 + A2) is structurally impossible going forward.
    """

    def _make_pipeline(self, vault_dir):
        from ovp_pipeline.auto_moc_updater import PipelineLogger
        from ovp_pipeline.unified_pipeline_enhanced import (
            EnhancedPipeline,
            TransactionManager,
        )
        logger = PipelineLogger(vault_dir / "60-Logs" / "pipeline.jsonl")
        txn_dir = vault_dir / "60-Logs" / "transactions"
        txn_dir.mkdir(parents=True, exist_ok=True)
        txn = TransactionManager(txn_dir)
        return EnhancedPipeline(vault_dir, logger, txn)

    def test_strict_mode_default(self, temp_vault):
        pipeline = self._make_pipeline(temp_vault)
        assert pipeline.step_contract_mode == "strict"

    def test_step_absorb_returns_typed_in_strict_mode(self, temp_vault):
        from unittest.mock import patch
        from ovp_pipeline.step_contracts import AbsorbStepResult

        pipeline = self._make_pipeline(temp_vault)
        files = [
            temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "x_深度解读.md",
        ]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("---\ntitle: x\n---\nbody", encoding="utf-8")

        canned = {
            "mode": "absorb",
            "summary": {"files_processed": 1, "concepts_promoted": 1},
            "results": [{
                "file": str(files[0]),
                "concepts": [{"slug": canonicalize_note_id(files[0].stem),
                              "status": "promoted_created"}],
            }],
        }
        with patch(
            "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
            return_value=canned,
        ):
            result = pipeline.step_absorb(recent_days=7)

        assert isinstance(result, AbsorbStepResult)
        # Typed attribute access — no .get(default) escape hatch needed:
        assert result.success is True
        assert result.processed_files == [str(files[0])]
        assert result.promoted_slugs == [canonicalize_note_id(files[0].stem)]

    def test_dispatcher_coerce_strict_rejects_unknown_fields(self, temp_vault):
        """If a step (or stage cache) somehow injects a field not on the
        contract, strict mode rejects it instead of silently dropping."""
        from ovp_pipeline.step_contracts import (
            AbsorbStepResult,
            StepContractError,
        )

        pipeline = self._make_pipeline(temp_vault)
        # Direct invocation of the recorder with a polluted dict — this is
        # what would happen if a future _write_stage_artifact mutation
        # introduced a key not on the contract.
        polluted = {
            "success": True,
            "processed_files": [],
            "promoted_slugs": [],
            "i_am_not_on_the_contract": "oops",
        }
        with pytest.raises(StepContractError, match="extra fields"):
            pipeline._record_step_result("absorb", polluted)

    def test_cross_step_consumer_reads_absorb_typed_fields(self, temp_vault):
        """entity_extract and dedup must be able to read absorb's outputs
        as typed attributes — the bug PATCH-1 fixed structurally."""
        from ovp_pipeline.step_contracts import AbsorbStepResult

        pipeline = self._make_pipeline(temp_vault)
        # Simulate what dispatcher does after step_absorb returns:
        absorb_result = AbsorbStepResult(
            success=True,
            processed_files=["a.md", "b.md"],
            promoted_slugs=["concept-x", "concept-y"],
        )
        pipeline.step_results["absorb"] = absorb_result

        # entity_extract's consumer pattern (line 2700-ish in
        # unified_pipeline_enhanced.py):
        retrieved = pipeline.step_results.get("absorb")
        assert retrieved is not None
        absorb_files = list(retrieved.get("processed_files", []))
        assert absorb_files == ["a.md", "b.md"]

        # dedup's consumer pattern:
        promoted = list(retrieved.get("promoted_slugs", []))
        assert promoted == ["concept-x", "concept-y"]

    def test_step_entity_extract_skipped_path_typed(self, temp_vault):
        from ovp_pipeline.step_contracts import EntityExtractStepResult

        pipeline = self._make_pipeline(temp_vault)
        result = pipeline.step_entity_extract(dry_run=True)
        assert isinstance(result, EntityExtractStepResult)
        assert result.skipped is True
        assert result.reason == "dry_run"

    def test_step_dedup_skips_when_no_promoted_scope(self, temp_vault):
        """PR1 fail-closed: no absorb / no promoted_slugs ⇒ step_dedup
        SKIPS and must NOT trigger an implicit full-vault O(N²) scan."""
        from unittest.mock import patch

        from ovp_pipeline.step_contracts import DedupStepResult

        pipeline = self._make_pipeline(temp_vault)
        with patch("ovp_pipeline.concept_dedup.find_clusters") as fc:
            result = pipeline.step_dedup(dry_run=True)
        assert isinstance(result, DedupStepResult)
        assert result.success is True
        assert result.skipped is True
        assert result.reason == "no_promoted_scope"
        assert result.clusters == 0
        fc.assert_not_called()

    def test_step_dedup_empty_promoted_slugs_skips(self, temp_vault):
        """An absorb result with an *empty* promoted_slugs list is still
        'no scope' — skip, never full-vault."""
        from unittest.mock import patch

        from ovp_pipeline.step_contracts import AbsorbStepResult

        pipeline = self._make_pipeline(temp_vault)
        pipeline.step_results["absorb"] = AbsorbStepResult(
            success=True, processed_files=["a.md"], promoted_slugs=[]
        )
        with patch("ovp_pipeline.concept_dedup.find_clusters") as fc:
            result = pipeline.step_dedup(dry_run=True)
        assert result.skipped is True
        assert result.reason == "no_promoted_scope"
        fc.assert_not_called()

    def test_step_dedup_scopes_to_promoted_slugs(self, temp_vault):
        """With promoted_slugs, dedup runs SCOPED — find_clusters is
        called with exactly that scope and never the full vault."""
        from unittest.mock import patch

        from ovp_pipeline.step_contracts import AbsorbStepResult

        pipeline = self._make_pipeline(temp_vault)
        pipeline.step_results["absorb"] = AbsorbStepResult(
            success=True,
            processed_files=["a.md"],
            promoted_slugs=["concept-x", "concept-y"],
        )
        with patch(
            "ovp_pipeline.concept_dedup.find_clusters", return_value=[]
        ) as fc:
            result = pipeline.step_dedup(dry_run=True)
        assert result.success is True
        assert result.skipped is False
        fc.assert_called_once()
        _, kwargs = fc.call_args
        assert kwargs["scope_slugs"] == {"concept-x", "concept-y"}
        assert kwargs.get("allow_full_scan", False) is False

    def test_run_autopilot_dedup_skips_no_full_scan(self, temp_vault):
        """PR1 fail-closed: autopilot carries no promoted scope, so the
        dedup stage SKIPS instead of an unconditional full-vault scan."""
        from unittest.mock import patch

        from ovp_pipeline.workflow_handlers import run_autopilot_dedup

        daemon = SimpleNamespace(vault_dir=temp_vault)
        with patch("ovp_pipeline.concept_dedup.find_clusters") as fc:
            out = run_autopilot_dedup(daemon=daemon)
        assert out["stage"] == "dedup"
        assert out["skipped"] is True
        assert out["reason"] == "no_promoted_scope"
        fc.assert_not_called()

    def test_run_pipeline_handles_frozen_step_result(self, temp_vault, monkeypatch):
        """Regression: run_pipeline used to call cmd_result.update() / write
        cmd_result["output"] = ..., which fails on frozen StepResult.  The
        dispatcher must funnel mutations through a dict copy and re-coerce.
        """
        from unittest.mock import patch
        from ovp_pipeline.step_contracts import (
            ClippingsStepResult,
            EntityExtractStepResult,
        )

        pipeline = self._make_pipeline(temp_vault)

        # Make handler_registry route every step to a frozen StepResult so
        # we exercise the mutation path on real typed objects.
        canned_clippings = ClippingsStepResult(success=True, migrated=0, remaining=0)
        canned_entity = EntityExtractStepResult(success=True, total_entities=0)

        def fake_handler(self_, step, **_kwargs):
            if step == "clippings":
                return canned_clippings
            if step == "entity_extract":
                return canned_entity
            raise ValueError(f"Unknown stage handler: {step}")

        monkeypatch.setattr(
            "ovp_pipeline.unified_pipeline_enhanced.execute_profile_stage_handler",
            fake_handler,
        )

        # Drive a minimal 2-step run; the original bug would explode here
        # with AttributeError on cmd_result.update().
        result = pipeline.run_pipeline(steps=["clippings", "entity_extract"])

        # If we got here without AttributeError, the typed-result mutation
        # path is safe.  Also verify both step_results entries are typed.
        assert isinstance(pipeline.step_results["clippings"], ClippingsStepResult)
        assert isinstance(pipeline.step_results["entity_extract"], EntityExtractStepResult)


class TestCandidateToPromotedContract:
    """Data flows correctly from candidate to promoted Evergreen file."""

    def test_promoted_file_preserves_entity_type(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="anthropic",
            title="Anthropic",
            definition="AI safety company.",
            area="general",
            kind=KIND_COMPANY,
        )
        registry.save()

        write_candidate_file(temp_vault, entry, dry_run=False)

        promote_candidate(temp_vault, "anthropic", dry_run=False)

        reloaded = ConceptRegistry(temp_vault).load()
        promoted_entry = reloaded.find_by_slug("anthropic")
        assert promoted_entry is not None
        assert promoted_entry.status == STATUS_ACTIVE

        evergreen_path = temp_vault / "10-Knowledge" / "Evergreen" / "anthropic.md"
        assert evergreen_path.exists()
        text = evergreen_path.read_text(encoding="utf-8")
        assert "entity_type: company" in text, (
            f"Promoted file missing 'entity_type: company'. Content:\n{text[:300]}"
        )
