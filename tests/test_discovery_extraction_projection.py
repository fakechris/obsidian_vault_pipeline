from __future__ import annotations

import json
from pathlib import Path


def test_discover_related_can_include_projected_extraction_rows(temp_vault):
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.discovery import discover_related
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
    artifact_path.write_text(
        json.dumps(
            ExtractionRunResult(
                pack_name="default-knowledge",
                profile_name="tech/doc_structure",
                source_path=str(source_path),
                records=[
                    ExtractionRecord(
                        values={"section_title": "Architecture", "summary": "Tool orchestration"},
                        spans=[
                            ExtractionSpan(
                                source_path=str(source_path),
                                section_title="Architecture",
                                char_start=0,
                                char_end=20,
                                quote="Tool orchestration",
                            )
                        ],
                    )
                ],
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    results = discover_related(
        temp_vault,
        "architecture",
        limit=5,
        include_extraction=True,
        extraction_profile="tech/doc_structure",
    )

    assert results
    assert results[0]["engine"] == "extraction"
    assert results[0]["pack"] == "default-knowledge"
    assert results[0]["object_kind"] == "document"
    assert results[0]["title"] == "Architecture"

