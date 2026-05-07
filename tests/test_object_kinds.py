"""Architectural fitness tests for the canonical object kind taxonomy.

BL-029: These tests enforce that all subsystems — concept_registry,
truth_store, view_models, packs — agree on the canonical kind vocabulary
defined in object_kinds.py.
"""

from __future__ import annotations

import pytest

from ovp_pipeline.object_kinds import (
    ALL_OBJECT_KINDS,
    CORE_OBJECT_KINDS,
    LEGACY_KIND_MAP,
    OBJECT_KIND_LABELS,
    REGISTRY_VALID_KINDS,
    RELATABLE_OBJECT_KINDS,
    STRUCTURAL_OBJECT_KINDS,
    display_label,
    normalize_kind,
)


class TestCanonicalTaxonomy:
    def test_partitions_compose_into_all(self):
        # BL-025/026: ALL_OBJECT_KINDS now spans three axes — core
        # (entity-side object kinds), structural (evergreen / claim
        # / document), and v2 unit kinds (fact / method / procedure
        # / tradeoff / ...).  Two are disjoint; CORE and V2 overlap
        # by the single shared string ``method``.
        from ovp_pipeline.object_kinds import V2_UNIT_TYPES, KIND_METHOD
        assert (
            CORE_OBJECT_KINDS | STRUCTURAL_OBJECT_KINDS | V2_UNIT_TYPES
            == ALL_OBJECT_KINDS
        )
        # CORE and STRUCTURAL must stay disjoint — confusing them
        # would let an entity get marked ``evergreen`` (or vice
        # versa) and break the layer 1 / layer 2 type contract.
        assert CORE_OBJECT_KINDS & STRUCTURAL_OBJECT_KINDS == set()
        # V2 unit kinds and STRUCTURAL kinds must also stay disjoint
        # — a v2 unit is not a structural role.
        assert V2_UNIT_TYPES & STRUCTURAL_OBJECT_KINDS == set()
        # CORE and V2_UNIT_TYPES intersect ONLY on KIND_METHOD by
        # design: an entity can have kind=method (a named technique)
        # AND a v2 unit can have unit_type=method (extracted form).
        # Any other shared string would be an unintended collision.
        assert CORE_OBJECT_KINDS & V2_UNIT_TYPES == {KIND_METHOD}

    def test_registry_kinds_span_core_plus_v2_units(self):
        # BL-025/026: registry now also accepts v2 unit kinds so a
        # v2 absorb candidate's ``entity_type`` doesn't get silently
        # downgraded back to ``KIND_CONCEPT`` when ConceptEntry
        # validates ``kind``.  Structural kinds stay out (a registry
        # entry isn't a document).
        from ovp_pipeline.object_kinds import V2_UNIT_TYPES
        assert REGISTRY_VALID_KINDS == CORE_OBJECT_KINDS | V2_UNIT_TYPES

    def test_relatable_kinds_stay_scoped_to_core(self):
        # RELATABLE governs which kinds pack semantic-relation
        # contracts (supports / challenges / extends / replaces /
        # uses / evolves) can target.  Those relations apply at the
        # entity level; v2 unit-level relations are provenance,
        # handled by a different table.  Keeping RELATABLE = CORE
        # avoids exploding pack surface.
        assert set(RELATABLE_OBJECT_KINDS) == CORE_OBJECT_KINDS

    def test_all_kinds_have_labels(self):
        missing = ALL_OBJECT_KINDS - set(OBJECT_KIND_LABELS.keys())
        assert not missing, f"Missing labels for: {missing}"

    def test_no_extra_labels(self):
        extra = set(OBJECT_KIND_LABELS.keys()) - ALL_OBJECT_KINDS
        assert not extra, f"Extra label keys not in taxonomy: {extra}"

    def test_taxonomy_size_bounded(self):
        from ovp_pipeline.object_kinds import MAX_TAXONOMY_SIZE
        # Pre-BL-025: < 15 (core 10 + structural 3).
        # Post-BL-025: 22 (added 9 v2 unit kinds; KIND_METHOD
        # overlaps with CORE_OBJECT_KINDS so we add 9 not 10).
        # The bound is a discipline knob, not a hard product
        # constraint — bumping it requires an explicit decision.
        assert len(ALL_OBJECT_KINDS) < MAX_TAXONOMY_SIZE, (
            f"Taxonomy size {len(ALL_OBJECT_KINDS)} >= MAX_TAXONOMY_SIZE "
            f"({MAX_TAXONOMY_SIZE}) — adding kinds is fine but bumping "
            f"the cap is a deliberate decision."
        )


class TestNormalization:
    @pytest.mark.parametrize("legacy,expected", list(LEGACY_KIND_MAP.items()))
    def test_legacy_kinds_normalize(self, legacy: str, expected: str):
        assert normalize_kind(legacy) == expected

    def test_canonical_kind_unchanged(self):
        for k in CORE_OBJECT_KINDS:
            assert normalize_kind(k) == k

    def test_case_insensitive(self):
        assert normalize_kind("CONCEPT") == "concept"
        assert normalize_kind(" Tool ") == "tool"

    def test_unknown_kind_passthrough(self):
        assert normalize_kind("alien") == "alien"


class TestDisplayLabel:
    def test_known_kinds_have_title_case(self):
        for k in ALL_OBJECT_KINDS:
            label = display_label(k)
            assert label[0].isupper(), f"Label for {k!r} not title-cased: {label}"

    def test_legacy_kind_resolves_to_canonical_label(self):
        assert display_label("protocol") == display_label("method")

    def test_unknown_kind_title_cased(self):
        assert display_label("some_new_kind") == "Some New Kind"


class TestIntegrationWithConceptRegistry:
    def test_concept_registry_valid_kinds_includes_canonical(self):
        from ovp_pipeline.concept_registry import VALID_KINDS

        for k in REGISTRY_VALID_KINDS:
            assert k in VALID_KINDS

    def test_concept_registry_accepts_legacy_kinds(self):
        from ovp_pipeline.concept_registry import VALID_KINDS

        for legacy_k in LEGACY_KIND_MAP:
            assert legacy_k in VALID_KINDS


class TestIntegrationWithPacks:
    def test_research_tech_object_kinds_use_canonical(self):
        from ovp_pipeline.packs.research_tech.shared import build_object_kinds

        specs = build_object_kinds()
        spec_kinds = {s.kind for s in specs}
        for k in spec_kinds:
            assert k in ALL_OBJECT_KINDS, f"Pack kind {k!r} not in canonical taxonomy"

    def test_semantic_relation_object_kinds_canonical(self):
        from ovp_pipeline.packs.research_tech.semantic_relations import (
            build_semantic_relation_contracts,
        )

        contracts = build_semantic_relation_contracts()
        for c in contracts:
            for rt in c.relation_types:
                for k in rt.source_object_kinds:
                    assert k in ALL_OBJECT_KINDS, f"source kind {k!r} not canonical"
                for k in rt.target_object_kinds:
                    assert k in ALL_OBJECT_KINDS, f"target kind {k!r} not canonical"
