from __future__ import annotations

from openclaw_pipeline.extraction.llm_extractor import DefaultProfileExtractor, _split_sections
from openclaw_pipeline.extraction.prompt_builder import build_extraction_prompt
from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionSpan
from openclaw_pipeline.extraction.validator import validate_record
from openclaw_pipeline.packs.loader import load_pack


def test_workflow_graph_spans_preserve_duplicate_step_offsets():
    profile = load_pack("default-knowledge").extraction_profile("tech/workflow_graph")
    extractor = DefaultProfileExtractor()
    text = "- Fetch source\n- Fetch source\n- Persist artifacts\n"

    records = extractor.extract(text, chunk_index=0, source_path="workflow.md", profile=profile)

    assert [record.values["step_name"] for record in records] == ["Fetch source", "Fetch source", "Persist artifacts"]
    assert records[0].spans[0].char_start != records[1].spans[0].char_start
    assert records[0].spans[0].quote == "Fetch source"
    assert records[1].spans[0].quote == "Fetch source"


def test_split_sections_body_offsets_start_after_heading():
    text = "# Heading\nBody line.\n"

    sections = _split_sections(text)

    assert len(sections) == 1
    section = sections[0]
    body_index = text.find("Body line.")
    assert abs(body_index - section.char_start) <= 1


def test_prompt_builder_includes_field_constraints():
    profile = load_pack("default-knowledge").extraction_profile("tech/doc_structure")

    prompt = build_extraction_prompt(profile)

    assert "section_title" in prompt
    assert "required: True" in prompt
    assert "type: string" in prompt
    assert "span_required: True" in prompt


def test_validator_requires_non_empty_quote_when_grounding_required():
    profile = load_pack("default-knowledge").extraction_profile("tech/doc_structure")
    record = ExtractionRecord(
        values={
            "section_title": "Architecture",
            "section_kind": "body",
            "summary": "Overview",
            "references": [],
        },
        spans=[ExtractionSpan(source_path="doc.md", section_title="Architecture", char_start=0, char_end=10, quote="   ")],
    )

    assert not validate_record(profile, record)
