from __future__ import annotations

import json
import pytest


def _seed_truth_store(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    beta = temp_vault / "10-Knowledge" / "Evergreen" / "Beta.md"
    conflict = temp_vault / "10-Knowledge" / "Evergreen" / "Conflict.md"

    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-10
---

# Alpha

Alpha connects to [[Beta]].
Alpha is always reliable.
""",
        encoding="utf-8",
    )
    beta.write_text(
        """---
note_id: beta
title: Beta
type: evergreen
date: 2026-04-10
---

# Beta

Beta extends Alpha.
""",
        encoding="utf-8",
    )
    conflict.write_text(
        """---
note_id: conflict
title: Conflict
type: evergreen
date: 2026-04-11
---

# Conflict

Alpha is not reliable.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)


def test_export_command_can_export_object_page(temp_vault, tmp_path, capsys):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "alpha-object.md"

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--target",
            "object-page",
            "--object-id",
            "alpha",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["target"] == "object-page"
    assert output_path.exists()
    assert "Alpha" in output_path.read_text(encoding="utf-8")


def test_export_command_can_export_topic_overview(temp_vault, tmp_path, capsys):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "topic.md"

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--target",
            "topic-overview",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["target"] == "topic-overview"
    assert payload["recipe_name"] == "topic_overview"
    assert payload["recipe_provider_pack"] == "research-tech"
    assert payload["view_name"] == "overview/topic"
    assert payload["view_provider_pack"] == "research-tech"
    assert output_path.exists()
    assert "# overview/topic" in output_path.read_text(encoding="utf-8")


def test_export_command_can_export_orientation_brief(temp_vault, tmp_path, capsys):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "orientation-brief.json"

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--target",
            "orientation-brief",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["target"] == "orientation-brief"
    assert payload["recipe_name"] == "orientation_brief"
    assert payload["recipe_provider_pack"] == "research-tech"
    assert payload["source_name"] == "briefing"
    assert payload["source_provider_pack"] == "research-tech"
    assert payload["source_provider_name"] == "research-tech-briefing"
    assert output_path.exists()
    assert exported["screen"] == "briefing/intelligence"
    assert exported["assembly_contract"]["recipe_name"] == "orientation_brief"
    assert [section["id"] for section in exported["compiled_sections"]] == [
        "what_changed",
        "what_matters",
        "needs_review",
        "next_reads",
        "next_actions",
    ]


def test_export_command_can_use_inherited_assembly_recipe_for_compatibility_pack(
    temp_vault, tmp_path, capsys
):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "compat-topic.md"

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--target",
            "topic-overview",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["pack"] == "default-knowledge"
    assert payload["recipe_name"] == "topic_overview"
    assert payload["recipe_provider_pack"] == "research-tech"
    assert payload["view_name"] == "overview/topic"
    assert payload["view_provider_pack"] == "default-knowledge"
    assert output_path.exists()
    assert "# overview/topic" in output_path.read_text(encoding="utf-8")


def test_export_command_can_export_event_dossier(temp_vault, tmp_path, capsys):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "events.md"

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--target",
            "event-dossier",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["target"] == "event-dossier"
    assert output_path.exists()
    assert "# event/dossier" in output_path.read_text(encoding="utf-8")


def test_export_command_can_export_contradictions(temp_vault, tmp_path, capsys):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "contradictions.md"

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--target",
            "contradictions",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["target"] == "contradictions"
    assert output_path.exists()
    assert "# truth/contradictions" in output_path.read_text(encoding="utf-8")


def test_export_command_requires_object_id_for_object_page(temp_vault, tmp_path):
    from openclaw_pipeline.commands.export_artifact import main

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "missing-object-id.md"

    try:
        main(
            [
                "--vault-dir",
                str(temp_vault),
                "--target",
                "object-page",
                "--output-path",
                str(output_path),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected object-page export to require --object-id")


def test_export_command_resolve_view_errors_when_source_provider_missing(monkeypatch):
    from openclaw_pipeline.commands import export_artifact
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")
    recipe_provider_pack, recipe = export_artifact._resolve_export_recipe(pack, "topic-overview")

    monkeypatch.setattr(
        export_artifact,
        "resolve_assembly_source_contract",
        lambda *, pack_name, recipe: {
            "source_provider_pack": "",
            "source_provider_name": "",
            "source_status": "missing",
        },
    )

    with pytest.raises(ValueError, match="has no resolved wiki-view provider"):
        export_artifact._resolve_export_view(pack, recipe_provider_pack, recipe)


def test_export_command_handles_missing_contradictions_table(temp_vault, tmp_path, capsys):
    import sqlite3

    from openclaw_pipeline.commands.export_artifact import main
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.execute("CREATE TABLE compiled_summaries (object_id TEXT, summary TEXT)")
        conn.commit()

    output_path = tmp_path / "contradictions-empty.md"
    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--target",
            "contradictions",
            "--output-path",
            str(output_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["target"] == "contradictions"
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "# truth/contradictions" in content
    assert "(none)" in content


def test_export_command_surfaces_build_errors_as_cli_errors(temp_vault, tmp_path, monkeypatch):
    from openclaw_pipeline.commands import export_artifact

    _seed_truth_store(temp_vault)
    output_path = tmp_path / "topic.md"

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(export_artifact, "build_view", raise_error)

    try:
        export_artifact.main(
            [
                "--vault-dir",
                str(temp_vault),
                "--target",
                "topic-overview",
                "--output-path",
                str(output_path),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected export command to convert build errors into parser failures")
