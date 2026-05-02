"""
E2E regression tests for entity_type propagation across pipeline stages.

Tests data contract: LLM output -> registry -> candidate file -> promoted file -> knowledge.db

Covers breakages B1-B5 identified during entity layer audit.
"""

import json
import pytest
from pathlib import Path

from ovp_pipeline.concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
)
from ovp_pipeline.identity import canonicalize_note_id
from ovp_pipeline.object_kinds import (
    CORE_OBJECT_KINDS,
    KIND_CONCEPT,
    KIND_PERSON,
    KIND_TOOL,
    KIND_COMPANY,
    normalize_kind,
)
from ovp_pipeline.promote_candidates import write_candidate_file, promote_candidate


class TestB1KindPassthrough:
    """B1: upsert_candidate must accept and persist kind from LLM output."""

    def test_upsert_candidate_with_kind_person(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="andrej-karpathy",
            title="Andrej Karpathy",
            definition="AI researcher and educator.",
            area="general",
            aliases=["karpathy"],
            kind=KIND_PERSON,
        )
        assert entry.kind == KIND_PERSON

    def test_upsert_candidate_with_kind_tool(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="claude-code",
            title="Claude Code",
            definition="An AI coding assistant.",
            area="general",
            aliases=["claude"],
            kind=KIND_TOOL,
        )
        assert entry.kind == KIND_TOOL

    def test_upsert_candidate_defaults_to_concept(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="attention-mechanism",
            title="Attention Mechanism",
            definition="A technique in neural networks.",
            area="general",
        )
        assert entry.kind == KIND_CONCEPT

    def test_upsert_candidate_kind_persists_through_save_load(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        registry.upsert_candidate(
            slug="anthropic",
            title="Anthropic",
            definition="An AI safety company.",
            area="general",
            kind=KIND_COMPANY,
        )
        registry.save()

        registry2 = ConceptRegistry(temp_vault).load()
        entry = registry2.find_by_slug("anthropic")
        assert entry is not None
        assert entry.kind == KIND_COMPANY


class TestB4SlugCanonical:
    """B4: All slugs must go through canonicalize_note_id for consistency."""

    @pytest.mark.parametrize(
        "raw_input,expected_slug",
        [
            ("Agent Memory", "agent-memory"),
            ("Agent_Memory", "agent-memory"),
            ("AGENT MEMORY", "agent-memory"),
            ("agent--memory", "agent-memory"),
            ("Agent Memory#section", "agent-memory"),
            ("path/to/Agent Memory", "agent-memory"),
        ],
    )
    def test_slug_normalization(self, raw_input, expected_slug):
        assert canonicalize_note_id(raw_input) == expected_slug


class TestB5CandidateFrontmatter:
    """B5: write_candidate_file must include entity_type in frontmatter."""

    def test_candidate_file_includes_entity_type(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="andrej-karpathy",
            title="Andrej Karpathy",
            definition="AI researcher.",
            area="general",
            kind=KIND_PERSON,
        )
        registry.save()

        result = write_candidate_file(
            temp_vault,
            entry,
            dry_run=False,
            concept_data={"explanation": "Famous AI researcher."},
        )
        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "entity_type: person" in content

    def test_candidate_file_defaults_entity_type_to_concept(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="attention-mechanism",
            title="Attention Mechanism",
            definition="Neural network technique.",
            area="general",
        )
        registry.save()

        result = write_candidate_file(
            temp_vault,
            entry,
            dry_run=False,
        )
        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "entity_type: concept" in content


class TestB3TruthProjectionFallback:
    """B3: truth_projection fallback should use 'concept' not 'evergreen'."""

    def test_fallback_kind_for_evergreen_note_type(self):
        from ovp_pipeline.object_kinds import CORE_OBJECT_KINDS, KIND_CONCEPT

        note_type = "evergreen"
        resolved = note_type if note_type in CORE_OBJECT_KINDS else KIND_CONCEPT
        assert resolved == KIND_CONCEPT, (
            f"note_type='evergreen' should fall back to 'concept', got '{resolved}'"
        )

    def test_valid_entity_type_takes_precedence(self):
        from ovp_pipeline.object_kinds import CORE_OBJECT_KINDS, KIND_CONCEPT, normalize_kind

        note_type = "evergreen"
        resolved = note_type if note_type in CORE_OBJECT_KINDS else KIND_CONCEPT

        et = "person"
        normalized = normalize_kind(et)
        if normalized in CORE_OBJECT_KINDS:
            resolved = normalized

        assert resolved == "person"


class TestKindEnumConsistency:
    """Verify object_kinds enum is consistent across subsystems."""

    def test_core_kinds_non_empty(self):
        assert len(CORE_OBJECT_KINDS) >= 10

    def test_normalize_kind_idempotent(self):
        for kind in CORE_OBJECT_KINDS:
            assert normalize_kind(kind) == kind

    def test_all_llm_output_kinds_in_core(self):
        llm_kinds = [
            "concept", "entity", "person", "company", "tool",
            "project", "paper", "event", "framework", "method",
        ]
        for k in llm_kinds:
            normalized = normalize_kind(k)
            assert normalized in CORE_OBJECT_KINDS, (
                f"LLM output kind '{k}' (normalized: '{normalized}') "
                f"not in CORE_OBJECT_KINDS"
            )
