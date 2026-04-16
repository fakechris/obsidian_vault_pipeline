from __future__ import annotations

import json
import os
import subprocess
import sys


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
        item["name"] == "articles" and item["mode"] == "llm_structured"
        for item in payload["contracts"]["declared"]["processor_contracts"]
    )
    assert any(
        item["action_kind"] == "deep_dive_workflow"
        for item in payload["contracts"]["effective"]["stage_handlers"]
    )
    assert any(
        item["action_kind"] == "deep_dive_workflow"
        for item in payload["contracts"]["effective"]["processor_contracts"]
    )
    assert any(
        item["stage"] == "articles"
        for item in payload["contracts"]["effective"]["execution_contracts"]
    )
    assert payload["contracts"]["contract_integrity"]["missing_processor_contracts"] == []
    assert payload["contracts"]["contract_integrity"]["orphan_processor_contracts"] == []
    assert payload["contracts"]["contract_integrity"]["observation_surfaces"]["missing_shell_surface_kinds"] == []
    assert {
        item["status"]
        for item in payload["contracts"]["contract_integrity"]["observation_surfaces"]["shell_surface_support"]
    } == {"declared"}
    assert {
        item["surface_kind"] for item in payload["contracts"]["effective"]["observation_surfaces"]
    } >= {"signals", "briefing", "production_chains"}
    assert any(
        item["path"] == "/signals" and item["status"] == "declared" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["shared_routes"]
    )
    assert any(
        item["path"] == "/clusters" and item["status"] == "declared" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["research_routes"]
    )
    assert any(
        item["screen"] == "object/page"
        and item["capability"] == "research_review_affordances"
        and item["status"] == "declared"
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["embedded_research_capabilities"]
    )


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
    assert payload["contracts"]["declared"]["processor_contracts"] == []
    assert any(
        item["stage"] == "articles" and item["pack"] == "research-tech"
        for item in payload["contracts"]["effective"]["processor_contracts"]
    )
    assert any(
        item["runtime_adapter"] == "focused_action"
        for item in payload["contracts"]["effective"]["stage_handlers"]
    )
    assert payload["contracts"]["contract_integrity"]["observation_surfaces"]["missing_shell_surface_kinds"] == []
    assert any(
        item["status"] == "inherited" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["contract_integrity"]["observation_surfaces"]["shell_surface_support"]
    )
    assert "compatibility packs inherit" in payload["contracts"]["contract_notes"]["compatibility_behavior"].lower()
    assert "signals, briefing, production_chains" in payload["contracts"]["contract_notes"]["ui_shell_required_surfaces"]
    assert any(
        item["path"] == "/signals" and item["status"] == "inherited" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["shared_routes"]
    )
    assert any(
        item["path"] == "/clusters" and item["status"] == "inherited" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["research_routes"]
    )
    assert any(
        item["screen"] == "overview/topic"
        and item["capability"] == "research_review_affordances"
        and item["status"] == "inherited"
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["embedded_research_capabilities"]
    )
    assert "research-specific routes stay hidden" in payload["contracts"]["contract_notes"]["research_shell_behavior"].lower()
    assert "object/topic/dashboard" in payload["contracts"]["contract_notes"]["embedded_research_behavior"].lower()


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


def test_doctor_module_cli_emits_json():
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"

    result = subprocess.run(
        [sys.executable, "-m", "openclaw_pipeline.commands.doctor", "--json"],
        cwd="/Users/chris/Documents/openclaw-template",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    payload = json.loads(result.stdout)

    assert payload["pack"]["name"] == "research-tech"
