from __future__ import annotations

from openclaw_pipeline.extraction.specs import (
    ExtractionFieldSpec,
    ExtractionProfileSpec,
    GroundingPolicy,
    MergePolicy,
    ProjectionTarget,
)


class FakeExtractor:
    def extract(self, chunk_text, *, chunk_index, source_path, profile):  # noqa: ANN001
        from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionSpan

        quote = "first quote" if chunk_index == 0 else "second quote"
        summary = "first summary" if chunk_index == 0 else "refined summary"
        return [
            ExtractionRecord(
                values={"section_title": "Architecture", "summary": summary},
                spans=[
                    ExtractionSpan(
                        source_path=str(source_path),
                        section_title="Architecture",
                        char_start=chunk_index * 10,
                        char_end=chunk_index * 10 + 10,
                        quote=quote,
                    )
                ],
            )
        ]


def test_runtime_merges_records_by_identifier_fields(tmp_path):
    from openclaw_pipeline.extraction.runtime import ExtractionRuntime

    profile = ExtractionProfileSpec(
        name="tech/doc_structure",
        pack="default-knowledge",
        input_object_kinds=["document"],
        output_mode="record_list",
        fields=[
            ExtractionFieldSpec("section_title", "string", "Heading text", required=True),
            ExtractionFieldSpec("summary", "string", "Section summary"),
        ],
        grounding_policy=GroundingPolicy(require_quote=True, include_char_offsets=True),
        identifier_fields=["section_title"],
        merge_policy=MergePolicy(strategy="by_identifier", allow_partial_updates=True),
        projection_target=ProjectionTarget(object_kind="document", channel="extraction"),
        display_fields=["section_title", "summary"],
    )

    runtime = ExtractionRuntime(extractor=FakeExtractor(), chunk_size=24, overlap=0)

    result = runtime.run_text(
        profile=profile,
        text="Architecture chunk one. Architecture chunk two.",
        source_path=tmp_path / "example.md",
    )

    assert len(result.records) == 1
    assert result.records[0].values["section_title"] == "Architecture"
    assert result.records[0].values["summary"] == "refined summary"
    assert [span.quote for span in result.records[0].spans] == ["first quote", "second quote"]
