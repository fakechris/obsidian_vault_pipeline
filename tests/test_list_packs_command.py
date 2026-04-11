from __future__ import annotations

import json


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
