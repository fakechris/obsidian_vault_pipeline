from __future__ import annotations

import json


class FakeCommandExtractor:
    def extract(self, chunk_text, *, chunk_index, source_path, profile):  # noqa: ANN001
        from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionSpan

        return [
            ExtractionRecord(
                values={"section_title": "Architecture", "summary": chunk_text[:20]},
                spans=[
                    ExtractionSpan(
                        source_path=str(source_path),
                        section_title="Architecture",
                        char_start=chunk_index,
                        char_end=chunk_index + 20,
                        quote="Architecture quote",
                    )
                ],
            )
        ]


def test_extract_profiles_command_writes_derived_json(temp_vault, monkeypatch):
    from openclaw_pipeline.commands import extract_profiles
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "50-Inbox" / "01-Raw" / "example.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Architecture\n\nRuntime tools.\n", encoding="utf-8")

    monkeypatch.setattr(extract_profiles, "build_extractor", lambda: FakeCommandExtractor())

    result = extract_profiles.main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "tech/doc_structure",
            "--source",
            str(source),
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.extraction_runs_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["profile_name"] == "tech/doc_structure"
    assert payload["records"][0]["values"]["section_title"] == "Architecture"
    assert not list((temp_vault / "10-Knowledge" / "Evergreen").glob("*.md"))


def test_extract_profiles_command_uses_default_profile_extractor(temp_vault):
    from openclaw_pipeline.commands import extract_profiles
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "50-Inbox" / "01-Raw" / "workflow.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "# System\n\n## Overview\n\nThis runtime coordinates tools.\n\n## Flow\n\n- Fetch source\n- Extract records\n- Persist artifacts\n",
        encoding="utf-8",
    )

    result = extract_profiles.main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "tech/doc_structure",
            "--source",
            str(source),
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.extraction_runs_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["records"]
    assert payload["records"][0]["values"]["section_title"] in {"System", "Overview", "Flow"}
