from __future__ import annotations

import json
from pathlib import Path


def _write_run(layout, *, source_name: str, profile_name: str, values: dict[str, object]) -> Path:
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionRunResult, ExtractionSpan

    source_path = Path(f"50-Inbox/01-Raw/{source_name}")
    artifact_path = extraction_run_path(
        layout,
        pack_name="default-knowledge",
        profile_name=profile_name,
        source_path=source_path,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(
            ExtractionRunResult(
                pack_name="default-knowledge",
                profile_name=profile_name,
                source_path=str(source_path),
                records=[
                    ExtractionRecord(
                        values=values,
                        spans=[
                            ExtractionSpan(
                                source_path=str(source_path),
                                section_title=str(values.get("section_title") or values.get("subject") or ""),
                                char_start=0,
                                char_end=20,
                                quote="example quote",
                            )
                        ],
                    )
                ],
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return artifact_path


def test_extract_preview_command_returns_latest_artifact(temp_vault, capsys):
    from openclaw_pipeline.commands import extract_preview
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    _write_run(
        layout,
        source_name="doc-one.md",
        profile_name="tech/doc_structure",
        values={"section_title": "Architecture", "summary": "Overview"},
    )

    exit_code = extract_preview.main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "tech/doc_structure",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["profile_name"] == "tech/doc_structure"
    assert payload["record_count"] == 1
    assert payload["records"][0]["values"]["section_title"] == "Architecture"


def test_extraction_dashboard_command_summarizes_profiles(temp_vault, capsys):
    from openclaw_pipeline.commands import extraction_dashboard
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    _write_run(
        layout,
        source_name="doc-one.md",
        profile_name="tech/doc_structure",
        values={"section_title": "Architecture", "summary": "Overview"},
    )
    _write_run(
        layout,
        source_name="doc-two.md",
        profile_name="media/commentary_sentiment",
        values={"subject": "OpenClaw", "stance": "positive"},
    )

    exit_code = extraction_dashboard.main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["pack"] == "default-knowledge"
    assert payload["total_runs"] == 2
    assert payload["profiles"]["tech/doc_structure"]["run_count"] == 1
    assert payload["profiles"]["media/commentary_sentiment"]["run_count"] == 1
