from __future__ import annotations

import json
from pathlib import Path


def test_build_evidence_payload_can_include_extraction_evidence(temp_vault):
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.evidence import build_evidence_payload
    from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionRunResult, ExtractionSpan
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    source_path = Path("50-Inbox/01-Raw/example.md")
    artifact_path = extraction_run_path(
        layout,
        pack_name="default-knowledge",
        profile_name="tech/doc_structure",
        source_path=source_path,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    result = ExtractionRunResult(
        pack_name="default-knowledge",
        profile_name="tech/doc_structure",
        source_path=str(source_path),
        records=[
            ExtractionRecord(
                values={"section_title": "Architecture", "summary": "Overview"},
                spans=[
                    ExtractionSpan(
                        source_path=str(source_path),
                        section_title="Architecture",
                        char_start=0,
                        char_end=20,
                        quote="Architecture overview",
                    )
                ],
            )
        ],
    )
    artifact_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")

    payload = build_evidence_payload(
        temp_vault,
        extraction_profile="tech/doc_structure",
        include_extraction=True,
    )

    assert "extraction_evidence" in payload
    assert payload["extraction_evidence"][0]["channel"] == "extraction"
    assert payload["extraction_evidence"][0]["profile"] == "tech/doc_structure"
    assert payload["extraction_evidence"][0]["quote"] == "Architecture overview"


def test_build_evidence_payload_limits_extraction_evidence(temp_vault):
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.evidence import build_evidence_payload
    from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionRunResult
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    for index in range(2):
        source_path = Path(f"50-Inbox/01-Raw/example-{index}.md")
        artifact_path = extraction_run_path(
            layout,
            pack_name="default-knowledge",
            profile_name="tech/doc_structure",
            source_path=source_path,
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        result = ExtractionRunResult(
            pack_name="default-knowledge",
            profile_name="tech/doc_structure",
            source_path=str(source_path),
            records=[ExtractionRecord(values={"section_title": f"Section {index}"}, spans=[])],
        )
        artifact_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")

    payload = build_evidence_payload(
        temp_vault,
        extraction_profile="tech/doc_structure",
        include_extraction=True,
        limit=1,
    )

    assert len(payload["extraction_evidence"]) == 1
