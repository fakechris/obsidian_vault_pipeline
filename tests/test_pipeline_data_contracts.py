"""
Pipeline data contract tests.

Ensure consistency of enums, slug normalization, and data contracts
across pipeline stages (extractor -> registry -> candidate -> promote -> knowledge.db).
"""

import pytest
import json
from pathlib import Path

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
