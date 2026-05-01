"""Tests for M9: Pack as Domain Ontology (BL-031 ~ BL-034).

Validates the enhanced ObjectKindSpec API, typed relation constraints,
and schema registry functionality.
"""

from __future__ import annotations

import pytest

from ovp_pipeline.packs.base import (
    BaseDomainPack,
    ObjectKindPropertySpec,
    ObjectKindSpec,
    SemanticRelationTypeSpec,
)


class TestObjectKindSpecEnhancements:
    def test_properties_field_default_empty(self):
        spec = ObjectKindSpec(kind="test", display_name="Test", description="test")
        assert spec.properties == ()

    def test_properties_roundtrip(self):
        props = (
            ObjectKindPropertySpec(name="url", field_type="url", required=True),
            ObjectKindPropertySpec(name="founded", field_type="date"),
        )
        spec = ObjectKindSpec(
            kind="company",
            display_name="Company",
            description="A company",
            properties=props,
        )
        assert len(spec.properties) == 2
        assert spec.properties[0].name == "url"
        assert spec.properties[0].required is True

    def test_reader_layout_and_extraction_hint(self):
        spec = ObjectKindSpec(
            kind="person",
            display_name="Person",
            description="A person",
            reader_layout="entity_brief",
            extraction_hint="Named individuals",
        )
        assert spec.reader_layout == "entity_brief"
        assert spec.extraction_hint == "Named individuals"


class TestBaseDomainPackOntologyAPI:
    @pytest.fixture
    def sample_pack(self):
        specs = [
            ObjectKindSpec(kind="concept", display_name="Concept", description="", canonical=True),
            ObjectKindSpec(kind="person", display_name="Person", description="", canonical=True),
            ObjectKindSpec(kind="document", display_name="Document", description="", canonical=False),
        ]
        return BaseDomainPack(
            name="test-pack",
            version="1.0",
            api_version=1,
            _object_kinds=specs,
        )

    def test_object_kind_spec_lookup(self, sample_pack):
        spec = sample_pack.object_kind_spec("concept")
        assert spec.kind == "concept"

    def test_object_kind_spec_not_found(self, sample_pack):
        with pytest.raises(ValueError, match="Unknown object kind"):
            sample_pack.object_kind_spec("alien")

    def test_valid_entity_types_excludes_non_canonical(self, sample_pack):
        types = sample_pack.valid_entity_types()
        assert "concept" in types
        assert "person" in types
        assert "document" not in types

    def test_validate_entity_type(self, sample_pack):
        assert sample_pack.validate_entity_type("concept") is True
        assert sample_pack.validate_entity_type("document") is False
        assert sample_pack.validate_entity_type("unknown") is False


class TestSemanticRelationKindConstraints:
    def test_empty_kinds_accept_all(self):
        rt = SemanticRelationTypeSpec(name="relates_to", description="generic")
        assert rt.accepts_source_kind("anything") is True
        assert rt.accepts_target_kind("anything") is True
        assert rt.validate_pair("a", "b") is True

    def test_constrained_source_kinds(self):
        rt = SemanticRelationTypeSpec(
            name="authored_by",
            description="authorship",
            source_object_kinds=("paper", "project"),
        )
        assert rt.accepts_source_kind("paper") is True
        assert rt.accepts_source_kind("concept") is False
        assert rt.accepts_target_kind("person") is True

    def test_constrained_target_kinds(self):
        rt = SemanticRelationTypeSpec(
            name="created_by",
            description="creation",
            target_object_kinds=("person", "company"),
        )
        assert rt.accepts_target_kind("person") is True
        assert rt.accepts_target_kind("tool") is False

    def test_validate_pair_both_constrained(self):
        rt = SemanticRelationTypeSpec(
            name="employs",
            description="employment",
            source_object_kinds=("company",),
            target_object_kinds=("person",),
        )
        assert rt.validate_pair("company", "person") is True
        assert rt.validate_pair("person", "company") is False
        assert rt.validate_pair("tool", "person") is False


class TestResearchTechPackIntegration:
    def test_pack_has_extraction_hints(self):
        from ovp_pipeline.packs.research_tech.shared import build_object_kinds

        specs = build_object_kinds()
        hints = {s.kind: s.extraction_hint for s in specs if s.extraction_hint}
        assert "concept" in hints
        assert "person" in hints
        assert "tool" in hints

    def test_pack_has_reader_layouts(self):
        from ovp_pipeline.packs.research_tech.shared import build_object_kinds

        specs = build_object_kinds()
        layouts = {s.kind: s.reader_layout for s in specs if s.reader_layout}
        assert layouts.get("concept") == "concept_brief"
        assert layouts.get("person") == "entity_brief"

    def test_semantic_relation_kinds_covered_by_pack(self):
        from ovp_pipeline.packs.research_tech.semantic_relations import (
            build_semantic_relation_contracts,
        )
        from ovp_pipeline.packs.research_tech.shared import build_object_kinds

        pack_kinds = {s.kind for s in build_object_kinds()}
        contracts = build_semantic_relation_contracts()
        for c in contracts:
            for rt in c.relation_types:
                for k in rt.source_object_kinds:
                    assert k in pack_kinds, f"source kind {k!r} not in pack"
                for k in rt.target_object_kinds:
                    assert k in pack_kinds, f"target kind {k!r} not in pack"
