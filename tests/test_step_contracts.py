"""Tests for step_contracts — the typed pipeline data contract layer."""

from __future__ import annotations

import warnings

import pytest

from ovp_pipeline.step_contracts import (
    AbsorbStepResult,
    DedupStepResult,
    EntityExtractStepResult,
    QualityStepResult,
    STEP_CONTRACTS,
    StepContractError,
    StepContractWarning,
    StepResult,
    coerce_step_result,
    with_derived,
)


class TestRegistry:
    """STEP_CONTRACTS must cover every step the dispatcher knows about."""

    EXPECTED_STEPS = {
        "pinboard", "pinboard_process", "clippings", "articles",
        "quality", "fix_links", "absorb", "entity_extract", "dedup",
        "note_type_normalize", "registry_sync", "moc", "refine",
        "knowledge_index",
    }

    def test_all_pipeline_steps_have_contracts(self):
        assert set(STEP_CONTRACTS.keys()) >= self.EXPECTED_STEPS, (
            f"Missing contracts for: "
            f"{self.EXPECTED_STEPS - set(STEP_CONTRACTS.keys())}"
        )

    def test_every_contract_subclasses_step_result(self):
        for step, cls in STEP_CONTRACTS.items():
            assert issubclass(cls, StepResult), f"{step}: {cls} not a StepResult"


class TestBaseFields:
    """Universal fields every step result must carry."""

    def test_minimal_construction(self):
        r = StepResult(success=True)
        assert r.success is True
        assert r.skipped is False
        assert r.blocked is False
        assert r.reason is None
        assert r.error is None
        assert r.produced == 0

    def test_dict_style_access(self):
        r = AbsorbStepResult(success=True, processed_files=["a.md", "b.md"])
        assert r["success"] is True
        assert r["processed_files"] == ["a.md", "b.md"]

    def test_dict_style_get_with_default(self):
        r = AbsorbStepResult(success=True)
        assert r.get("processed_files") == []
        assert r.get("nonexistent", "fallback") == "fallback"

    def test_unknown_key_raises(self):
        r = AbsorbStepResult(success=True)
        with pytest.raises(KeyError):
            _ = r["nonexistent"]

    def test_to_dict_roundtrip(self):
        r = EntityExtractStepResult(
            success=True, produced=5, total_entities=27, mentions_extracted=99,
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["produced"] == 5
        assert d["total_entities"] == 27
        assert d["mentions_extracted"] == 99

    def test_frozen(self):
        r = StepResult(success=True)
        with pytest.raises((AttributeError, Exception)):
            r.success = False  # type: ignore[misc]


class TestCoerceFromDict:
    """coerce_step_result should accept a raw dict and produce typed object."""

    def test_basic_coerce_success(self):
        raw = {
            "success": True,
            "produced": 5,
            "total_entities": 27,
            "mentions_extracted": 99,
        }
        r = coerce_step_result("entity_extract", raw)
        assert isinstance(r, EntityExtractStepResult)
        assert r.produced == 5
        assert r.total_entities == 27

    def test_coerce_drops_extra_fields_with_warning(self):
        raw = {"success": True, "produced": 1, "bogus_extra": "junk"}
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            r = coerce_step_result("entity_extract", raw)
        assert isinstance(r, EntityExtractStepResult)
        assert not hasattr(r, "bogus_extra")
        assert any(
            issubclass(w.category, StepContractWarning) for w in captured
        ), f"Expected StepContractWarning, got: {[w.category for w in captured]}"

    def test_coerce_strict_raises_on_extra_fields(self):
        raw = {"success": True, "bogus_extra": "junk"}
        with pytest.raises(StepContractError, match="extra fields"):
            coerce_step_result("entity_extract", raw, strict=True)

    def test_coerce_missing_success_raises(self):
        raw = {"produced": 5}
        with pytest.raises(StepContractError, match="missing required field 'success'"):
            coerce_step_result("entity_extract", raw)

    def test_coerce_unknown_step_raises(self):
        with pytest.raises(StepContractError, match="no registered contract"):
            coerce_step_result("nonexistent_step", {"success": True})

    def test_coerce_wrong_type_raises(self):
        with pytest.raises(StepContractError, match="returned NoneType"):
            coerce_step_result("absorb", None)  # type: ignore[arg-type]

    def test_coerce_passes_through_typed_result(self):
        original = AbsorbStepResult(success=True, processed_files=["a.md"])
        r = coerce_step_result("absorb", original)
        assert r is original  # no copy

    def test_coerce_rejects_wrong_subclass(self):
        wrong = AbsorbStepResult(success=True)
        with pytest.raises(StepContractError, match="expected EntityExtractStepResult"):
            coerce_step_result("entity_extract", wrong)


class TestWithDerived:
    """with_derived adds dispatcher-computed fields after step returns."""

    def test_with_derived_adds_field(self):
        original = AbsorbStepResult(success=True, processed_files=["a.md"])
        enriched = with_derived(original, total_evergreen=1234)
        assert enriched.total_evergreen == 1234
        assert enriched.processed_files == ["a.md"]
        assert enriched is not original  # new instance

    def test_with_derived_rejects_undeclared_key(self):
        original = AbsorbStepResult(success=True)
        with pytest.raises(StepContractError, match="not declared"):
            with_derived(original, undeclared_thing="oops")


class TestAbsorbContract:
    """The contract that PATCH-1 was protecting — verify shape is locked."""

    REQUIRED_KEYS = {
        # base StepResult fields
        "success", "skipped", "blocked", "reason", "error",
        "stdout", "stderr", "produced",
        "output", "returncode", "method",
        "cache_hit", "stage_fingerprint", "stage_artifact",
        "input_digest", "algorithm_digest", "output_digest",
        # absorb-specific fields
        "processed_files", "promoted_slugs",
        "qualified_files", "pending_qualified_files",
        "item_cache_hits", "item_cache_hit_files",
        "summary", "results", "input_artifact", "total_evergreen",
    }

    def test_absorb_contract_has_all_required_fields(self):
        from dataclasses import fields
        actual = {f.name for f in fields(AbsorbStepResult)}
        assert actual == self.REQUIRED_KEYS, (
            f"AbsorbStepResult fields drifted. "
            f"Missing: {self.REQUIRED_KEYS - actual}, Extra: {actual - self.REQUIRED_KEYS}"
        )

    def test_absorb_default_construction_safe_for_consumers(self):
        """A bare-success absorb result must be usable by consumers without
        them needing to .get() with defaults — that was the silent-bug
        pattern PATCH-1 fixed.
        """
        r = AbsorbStepResult(success=True)
        # consumers can iterate without KeyError
        assert r.processed_files == []
        assert r.promoted_slugs == []
        # entity_extract consumer pattern:
        absorb_files = r.processed_files
        assert isinstance(absorb_files, list)
        # dedup consumer pattern:
        promoted = r.promoted_slugs
        assert isinstance(promoted, list)


class TestQualityContract:
    """quality has the most fields among existing steps; lock its shape."""

    def test_quality_can_construct_with_all_fields(self):
        r = QualityStepResult(
            success=True,
            quality_checked=10,
            quality_qualified=8,
            quality_failed=2,
            quality_qualified_files=["a.md", "b.md"],
            quality_results_json='{"foo": "bar"}',
            quality_score=4.2,
        )
        assert r.quality_score == 4.2
        assert r.quality_qualified_files == ["a.md", "b.md"]


class TestDedupContract:
    """dedup has dry-run-specific extra fields."""

    def test_dedup_dry_run_fields(self):
        r = DedupStepResult(
            success=True,
            clusters=3,
            dry_run=True,
            proposal_id="prop-2026-04-29-abc",
        )
        assert r.dry_run is True
        assert r.proposal_id == "prop-2026-04-29-abc"

    def test_dedup_apply_fields(self):
        r = DedupStepResult(
            success=True, clusters=3, archived=2, rewrites=14,
        )
        assert r.archived == 2
        assert r.rewrites == 14
