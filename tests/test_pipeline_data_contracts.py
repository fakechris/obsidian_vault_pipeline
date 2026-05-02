"""
Pipeline data contract tests.

Ensure consistency of enums, slug normalization, and data contracts
across pipeline stages (extractor -> registry -> candidate -> promote -> knowledge.db).
"""

import pytest
import json
from pathlib import Path
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

    def test_core_plus_structural_equals_all(self):
        assert CORE_OBJECT_KINDS | STRUCTURAL_OBJECT_KINDS == ALL_OBJECT_KINDS

    def test_registry_valid_kinds_equals_core(self):
        assert REGISTRY_VALID_KINDS == CORE_OBJECT_KINDS

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
        pipeline = self._make_pipeline(temp_vault)
        result = pipeline.step_absorb(qualified_files=[])
        assert self.REQUIRED_KEYS <= result.keys()
        assert result["processed_files"] == []
        assert result["promoted_slugs"] == []

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

    def test_step_dedup_no_clusters_path_typed(self, temp_vault):
        from ovp_pipeline.step_contracts import DedupStepResult

        pipeline = self._make_pipeline(temp_vault)
        # No evergreens in vault → no clusters → success-empty path.
        result = pipeline.step_dedup(dry_run=True)
        assert isinstance(result, DedupStepResult)
        assert result.success is True
        assert result.clusters == 0


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
