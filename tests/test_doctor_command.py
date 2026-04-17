from __future__ import annotations

import json
import os
from pathlib import Path
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
        item["path"] == "/actions/run-next" and item["status"] == "always_available"
        for item in payload["contracts"]["shell"]["shared_mutations"]
    )
    assert any(
        item["path"] == "/evolution/review" and item["status"] == "declared" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["research_mutations"]
    )
    assert any(
        item["screen"] == "object/page"
        and item["capability"] == "research_review_affordances"
        and item["status"] == "declared"
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["embedded_research_capabilities"]
    )
    assert any(
        item["name"] == "object/page"
        and item["builder"] == "object_page"
        and item["required_args"] == ["object_id"]
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["wiki_views"]
    )
    assert any(
        item["name"] == "cluster/crystal"
        and item["required_args"] == ["cluster_id"]
        for item in payload["contracts"]["wiki_views"]
    )
    assert any(
        item["name"] == "tech/doc_structure"
        and item["projection_target"]["channel"] == "extraction"
        and any(field["name"] == "section_title" and field["required"] is True for field in item["fields"])
        for item in payload["contracts"]["extraction_profiles"]
    )
    assert any(
        item["name"] == "autopilot"
        and item["supports_autopilot"] is True
        and "knowledge_index" in item["stages"]
        for item in payload["contracts"]["workflow_profiles"]
    )
    assert any(
        item["kind"] == "concept"
        and item["canonical"] is True
        and item["discoverable"] is True
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["object_kinds"]
    )
    assert any(
        item["name"] == "truth/contradiction_review"
        and item["scope"] == "truth"
        and item["proposal_types"][0]["queue_name"] == "contradictions"
        for item in payload["contracts"]["operation_profiles"]
    )
    assert any(
        item["name"] == "operator_briefing"
        and item["recipe_kind"] == "operator_briefing"
        and item["source_contract_kind"] == "observation_surface"
        and item["source_contract_name"] == "briefing"
        and item["source_provider_pack"] == "research-tech"
        and item["source_provider_name"] == "research-tech-briefing"
        and item["output"]["output_mode"] == "json"
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["declared"]["assembly_recipes"]
    )
    assert any(
        item["name"] == "topic_overview"
        and item["recipe_kind"] == "topic_overview"
        and item["source_contract_kind"] == "wiki_view"
        and item["source_contract_name"] == "overview/topic"
        and item["source_provider_pack"] == "research-tech"
        and item["source_provider_name"] == "overview/topic"
        and item["output"]["publish_target"] == "compiled_markdown"
        for item in payload["contracts"]["declared"]["assembly_recipes"]
    )
    assert {
        item["name"] for item in payload["contracts"]["effective"]["assembly_recipes"]
    } >= {
        "operator_briefing",
        "topic_overview",
        "object_brief",
        "event_dossier",
        "contradiction_view",
    }
    assert any(
        item["family"] == "object"
        and item["layer"] == "canonical"
        and item["pack"] == "research-tech"
        and item["provider_pack"] == "research-tech"
        and item["storage_policy"]["storage_mode"] == "markdown_note"
        for item in payload["contracts"]["declared"]["artifact_specs"]
    )
    assert any(
        item["family"] == "review_item"
        and item["layer"] == "governance"
        and item["lifecycle_policy"]["review_required_on_create"] is True
        for item in payload["contracts"]["declared"]["artifact_specs"]
    )
    assert {
        item["family"] for item in payload["contracts"]["effective"]["artifact_specs"]
    } >= {"object", "claim", "evidence", "overview", "review_item"}
    assert any(
        item["name"] == "research_governance"
        and item["pack"] == "research-tech"
        and item["provider_pack"] == "research-tech"
        and {queue["name"] for queue in item["review_queues"]} >= {"review", "contradictions", "stale-summaries"}
        and any(
            signal["signal_type"] == "source_needs_deep_dive"
            and signal["resolver_rule"] == "deep_dive_workflow"
            and signal["auto_queue"] is True
            for signal in item["signal_rules"]
        )
        and any(
            rule["name"] == "review_contradiction"
            and rule["resolution_kind"] == "review_mutation"
            and rule["dispatch_mode"] == "direct"
            and rule["executable"] is True
            for rule in item["resolver_rules"]
        )
        and any(
            rule["name"] == "deep_dive_workflow"
            and rule["resolution_kind"] == "focused_action"
            and rule["dispatch_mode"] == "queue_only"
            and rule["safe_to_run"] is True
            for rule in item["resolver_rules"]
        )
        for item in payload["contracts"]["declared"]["governance_specs"]
    )
    assert any(
        item["name"] == "research_governance"
        and item["status"] == "declared"
        for item in payload["contracts"]["effective"]["governance_specs"]
    )
    assert payload["contracts"]["shell"]["governance_contract"]["status"] == "declared"
    assert payload["contracts"]["shell"]["governance_contract"]["provider_pack"] == "research-tech"
    assert payload["contracts"]["shell"]["governance_contract"]["provider_name"] == "research_governance"
    assert payload["contracts"]["shell"]["governance_contract"]["resolver_rule_count"] >= 1
    assert (
        payload["contracts"]["truth_projection_contract"]["effective_builder"]["pack"]
        == "research-tech"
    )
    assert any(
        item["name"] == "graph_clusters"
        and item["family_kind"] == "graph_projection"
        and item["pack_scoped"] is True
        for item in payload["contracts"]["truth_projection_contract"]["row_families"]
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
        item["path"] == "/actions/enqueue" and item["status"] == "always_available"
        for item in payload["contracts"]["shell"]["shared_mutations"]
    )
    assert any(
        item["path"] == "/summaries/rebuild" and item["status"] == "inherited" and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["research_mutations"]
    )
    assert any(
        item["screen"] == "overview/topic"
        and item["capability"] == "research_review_affordances"
        and item["status"] == "inherited"
        and item["provider_pack"] == "research-tech"
        for item in payload["contracts"]["shell"]["embedded_research_capabilities"]
    )
    assert any(
        item["name"] == "overview/topic"
        and item["builder"] == "topic_view"
        and item["provider_pack"] == "default-knowledge"
        for item in payload["contracts"]["wiki_views"]
    )
    assert any(
        item["name"] == "saved_answer/query"
        and item["input_sources"][0]["source_kind"] == "query"
        for item in payload["contracts"]["wiki_views"]
    )
    assert any(
        item["name"] == "media/news_timeline"
        and item["provider_pack"] == "default-knowledge"
        and any(field["name"] == "claim" and field["required"] is True for field in item["fields"])
        for item in payload["contracts"]["extraction_profiles"]
    )
    assert any(
        item["name"] == "full"
        and item["supports_autopilot"] is False
        and item["stages"][0] == "pinboard"
        for item in payload["contracts"]["workflow_profiles"]
    )
    assert any(
        item["kind"] == "document"
        and item["canonical"] is False
        and item["discoverable"] is True
        and item["provider_pack"] == "default-knowledge"
        for item in payload["contracts"]["object_kinds"]
    )
    assert any(
        item["name"] == "vault/review_queue"
        and item["provider_pack"] == "default-knowledge"
        and item["proposal_types"][0]["queue_name"] == "review"
        for item in payload["contracts"]["operation_profiles"]
    )
    assert payload["contracts"]["declared"]["assembly_recipes"] == []
    assert any(
        item["name"] == "operator_briefing"
        and item["provider_pack"] == "research-tech"
        and item["source_provider_pack"] == "research-tech"
        and item["source_provider_name"] == "research-tech-briefing"
        and item["status"] == "inherited"
        for item in payload["contracts"]["effective"]["assembly_recipes"]
    )
    assert any(
        item["name"] == "topic_overview"
        and item["provider_pack"] == "research-tech"
        and item["source_provider_pack"] == "default-knowledge"
        and item["source_provider_name"] == "overview/topic"
        and item["status"] == "inherited"
        for item in payload["contracts"]["effective"]["assembly_recipes"]
    )
    assert payload["contracts"]["declared"]["artifact_specs"] == []
    assert any(
        item["family"] == "object"
        and item["pack"] == "research-tech"
        and item["provider_pack"] == "research-tech"
        and item["status"] == "inherited"
        for item in payload["contracts"]["effective"]["artifact_specs"]
    )
    assert payload["contracts"]["declared"]["governance_specs"] == []
    assert any(
        item["name"] == "research_governance"
        and item["provider_pack"] == "research-tech"
        and item["status"] == "inherited"
        for item in payload["contracts"]["effective"]["governance_specs"]
    )
    assert payload["contracts"]["shell"]["governance_contract"]["status"] == "inherited"
    assert payload["contracts"]["shell"]["governance_contract"]["provider_pack"] == "research-tech"
    assert payload["contracts"]["shell"]["governance_contract"]["provider_name"] == "research_governance"
    assert (
        payload["contracts"]["truth_projection_contract"]["declared_builder"] is None
    )
    assert (
        payload["contracts"]["truth_projection_contract"]["effective_builder"]["pack"]
        == "research-tech"
    )
    assert any(
        item["name"] == "claim_evidence"
        and item["storage_table"] == "claim_evidence"
        and item["pack_scoped"] is True
        for item in payload["contracts"]["truth_projection_contract"]["row_families"]
    )
    assert "research-specific routes stay hidden" in payload["contracts"]["contract_notes"]["research_shell_behavior"].lower()
    assert "object/topic/dashboard" in payload["contracts"]["contract_notes"]["embedded_research_behavior"].lower()
    assert "action queue mutations remain available" in payload["contracts"]["contract_notes"]["mutation_shell_behavior"].lower()
    assert "wiki view specs are pack-owned declarations" in payload["contracts"]["contract_notes"]["wiki_view_behavior"].lower()
    assert "pack-scoped row families" in payload["contracts"]["contract_notes"]["truth_projection_behavior"].lower()
    assert "record shapes, grounding rules, review queues, and proposal flows" in payload["contracts"]["contract_notes"]["profile_contract_behavior"].lower()
    assert "canonical and discoverable" in payload["contracts"]["contract_notes"]["object_kind_behavior"].lower()
    assert "stage order and autopilot support explicitly" in payload["contracts"]["contract_notes"]["workflow_profile_behavior"].lower()
    assert "review queues, signal semantics, and resolver rules explicit" in payload["contracts"]["contract_notes"]["governance_contract_behavior"].lower()


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
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "openclaw_pipeline.commands.doctor", "--json"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    payload = json.loads(result.stdout)

    assert payload["pack"]["name"] == "research-tech"
