from __future__ import annotations


def test_extraction_profile_spec_declares_grounded_projection_contract():
    from openclaw_pipeline.extraction.specs import (
        ExtractionFieldSpec,
        ExtractionProfileSpec,
        GroundingPolicy,
        MergePolicy,
        ProjectionTarget,
    )

    profile = ExtractionProfileSpec(
        name="tech/doc_structure",
        pack="default-knowledge",
        input_object_kinds=["document"],
        output_mode="record_list",
        fields=[
            ExtractionFieldSpec(name="section_title", field_type="string", description="Heading text"),
            ExtractionFieldSpec(name="section_kind", field_type="string", description="Section category"),
        ],
        relations=[],
        grounding_policy=GroundingPolicy(require_quote=True, include_char_offsets=True),
        identifier_fields=["section_title"],
        merge_policy=MergePolicy(strategy="by_identifier", allow_partial_updates=True),
        projection_target=ProjectionTarget(object_kind="document", channel="extraction"),
        display_fields=["section_title", "section_kind"],
        notes="Inspired by technical document structure extraction",
    )

    assert profile.name == "tech/doc_structure"
    assert profile.grounding_policy.require_quote is True
    assert profile.merge_policy.strategy == "by_identifier"
    assert profile.projection_target.channel == "extraction"
    assert [field.name for field in profile.fields] == ["section_title", "section_kind"]
