from __future__ import annotations

import json


def test_doctor_command_reports_primary_and_compatibility_roles(capsys):
    from openclaw_pipeline.commands.doctor import main

    exit_code = main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["defaults"]["workflow_pack"] == "research-tech"
    assert payload["defaults"]["compatibility_pack"] == "default-knowledge"
    assert payload["storage"]["selected_engine"] == "sqlite"
    assert payload["storage"]["pglite_migration"] == "defer"
    assert payload["pack"]["name"] == "research-tech"
    assert payload["pack"]["role"] == "primary"
    assert payload["docs"]["skillpack"]["exists"] is True
    assert payload["docs"]["verify"]["exists"] is True
    assert payload["contracts"]["declared"]["truth_projection"]["pack"] == "research-tech"
    assert any(
        item["action_kind"] == "deep_dive_workflow"
        for item in payload["contracts"]["effective"]["stage_handlers"]
    )
    assert {
        item["surface_kind"] for item in payload["contracts"]["effective"]["observation_surfaces"]
    } >= {"signals", "briefing", "production_chains"}


def test_doctor_command_reports_compatibility_pack_metadata(capsys):
    from openclaw_pipeline.commands.doctor import main

    exit_code = main(["--pack", "default-knowledge", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["pack"]["name"] == "default-knowledge"
    assert payload["pack"]["role"] == "compatibility"
    assert payload["pack"]["compatibility_base"] == "research-tech"
    assert payload["docs"]["skillpack"]["exists"] is False
    assert payload["docs"]["verify"]["exists"] is False
    assert payload["contracts"]["declared"]["truth_projection"] is None
    assert payload["contracts"]["effective"]["truth_projection"]["pack"] == "research-tech"
    assert any(
        item["runtime_adapter"] == "focused_action"
        for item in payload["contracts"]["effective"]["stage_handlers"]
    )
    assert "compatibility packs inherit" in payload["contracts"]["contract_notes"]["compatibility_behavior"].lower()


def test_doctor_command_reports_vault_health(temp_vault, capsys):
    from openclaw_pipeline.commands.doctor import main

    raw = temp_vault / "50-Inbox" / "01-Raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "sample.md").write_text("# Sample\n", encoding="utf-8")
    (temp_vault / "Clippings").mkdir(parents=True, exist_ok=True)
    (temp_vault / "Clippings" / "clip.md").write_text("# Clip\n", encoding="utf-8")

    exit_code = main(["--vault-dir", str(temp_vault), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["vault"]["raw_count"] == 1
    assert payload["vault"]["clippings_count"] == 1
    assert payload["vault"]["knowledge_db_exists"] is False


def test_doctor_command_text_output_includes_processing_count(temp_vault, capsys):
    from openclaw_pipeline.commands.doctor import main

    processing = temp_vault / "50-Inbox" / "02-Processing"
    processing.mkdir(parents=True, exist_ok=True)
    (processing / "processing.md").write_text("# Processing\n", encoding="utf-8")

    exit_code = main(["--vault-dir", str(temp_vault)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "processing=1" in output


def test_doctor_help_mentions_pglite(capsys):
    from openclaw_pipeline.commands.doctor import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "PGlite" in output
