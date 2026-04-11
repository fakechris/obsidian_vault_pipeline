from __future__ import annotations


def test_extraction_span_preserves_grounding_metadata():
    from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionSpan

    span = ExtractionSpan(
        source_path="50-Inbox/01-Raw/example.md",
        section_title="Introduction",
        char_start=10,
        char_end=42,
        quote="Grounded evidence quote",
    )
    record = ExtractionRecord(
        values={"section_title": "Introduction", "summary": "Overview"},
        spans=[span],
    )

    assert record.values["section_title"] == "Introduction"
    assert record.spans[0].section_title == "Introduction"
    assert record.spans[0].quote == "Grounded evidence quote"

