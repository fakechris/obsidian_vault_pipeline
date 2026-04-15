from __future__ import annotations

import json
import os
from types import SimpleNamespace


def test_list_packs_command_outputs_builtin_roles(capsys):
    from openclaw_pipeline.commands.list_packs import main

    exit_code = main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    builtin = {item["name"]: item for item in payload["builtin"]}
    assert exit_code == 0
    assert builtin["research-tech"]["role"] == "primary"
    assert builtin["default-knowledge"]["role"] == "compatibility"
    assert builtin["default-knowledge"]["compatibility_base"] == "research-tech"


def test_list_packs_command_help_mentions_domain_packs(capsys):
    from openclaw_pipeline.commands.list_packs import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "domain packs" in output.lower()


def test_list_packs_command_lists_entrypoint_packs(monkeypatch, capsys):
    from openclaw_pipeline.commands import list_packs

    class FakePack:
        name = "media"
        role = "domain"
        compatibility_base = None
        version = "0.1.0"
        api_version = 1

        @staticmethod
        def workflow_profiles():
            return [SimpleNamespace(name="daily-desk"), SimpleNamespace(name="feature-dossier")]

    monkeypatch.setattr(list_packs, "discover_entrypoint_packs", lambda: {"media": FakePack()})

    exit_code = list_packs.main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    external = {item["name"]: item for item in payload["external"]}
    assert external["media"]["source"] == "entrypoint"
    assert external["media"]["profiles"] == ["daily-desk", "feature-dossier"]


def test_list_packs_command_reads_manifest_paths_from_os_pathsep(tmp_path, monkeypatch, capsys):
    from openclaw_pipeline.commands.list_packs import main

    first = tmp_path / "external-pack.yaml"
    first.write_text(
        """
name: external-pack
version: 0.1.0
api_version: 1
entrypoints:
  pack: external.module:get_pack
""".strip(),
        encoding="utf-8",
    )
    second = tmp_path / "external-pack-two.yaml"
    second.write_text(
        """
name: external-pack-two
version: 0.1.0
api_version: 1
entrypoints:
  pack: external.two:get_pack
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openclaw_pipeline.commands.list_packs.discover_entrypoint_packs",
        lambda: {},
    )
    monkeypatch.setenv("OPENCLAW_PACK_MANIFESTS", f"{first}{os.pathsep}{second}")

    exit_code = main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    names = {item["name"] for item in payload["external"]}
    assert names == {"external-pack", "external-pack-two"}
