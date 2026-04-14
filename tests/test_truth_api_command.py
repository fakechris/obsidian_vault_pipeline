from __future__ import annotations

import json
import pytest

from openclaw_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_truth_store(temp_vault):
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    beta = temp_vault / "10-Knowledge" / "Evergreen" / "Beta.md"
    conflict = temp_vault / "10-Knowledge" / "Evergreen" / "Conflict.md"

    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.

Links to [[beta]].
""",
        encoding="utf-8",
    )
    beta.write_text(
        """---
note_id: beta
title: Beta
type: evergreen
date: 2026-04-13
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
date: 2026-04-13
---

# Conflict

Alpha does not support local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)


def test_truth_api_command_lists_objects(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    exit_code = main(["objects", "--vault-dir", str(temp_vault)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [item["object_id"] for item in payload["items"]] == ["alpha", "beta", "conflict"]


def test_truth_api_command_filters_objects_by_query(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    exit_code = main(["objects", "--vault-dir", str(temp_vault), "--query", "bet"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [item["object_id"] for item in payload["items"]] == ["beta"]


def test_truth_api_command_returns_object_detail(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    exit_code = main(["object", "--vault-dir", str(temp_vault), "--id", "alpha"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["object"]["object_id"] == "alpha"
    assert payload["relations"][0]["target_object_id"] == "beta"


def test_truth_api_command_lists_contradictions(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    exit_code = main(["contradictions", "--vault-dir", str(temp_vault)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["subject_key"] == "alpha"


def test_truth_api_command_filters_contradictions_by_query(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    exit_code = main(["contradictions", "--vault-dir", str(temp_vault), "--query", "alp"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["subject_key"] == "alpha"


def test_truth_api_command_returns_neighborhood(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    exit_code = main(["neighborhood", "--vault-dir", str(temp_vault), "--id", "alpha"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["center"]["object_id"] == "alpha"
    assert [item["object_id"] for item in payload["neighbors"]] == ["beta"]


def test_truth_api_command_reports_unknown_object_as_cli_error(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    with pytest.raises(SystemExit) as exc_info:
        main(["object", "--vault-dir", str(temp_vault), "--id", "missing"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "Unknown object_id: missing" in captured.err


def test_truth_api_command_rejects_unsupported_depth(temp_vault, capsys):
    from openclaw_pipeline.commands.truth_api import main

    _seed_truth_store(temp_vault)

    with pytest.raises(SystemExit) as exc_info:
        main(["neighborhood", "--vault-dir", str(temp_vault), "--id", "alpha", "--depth", "2"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "Only depth=1 is currently supported" in captured.err
