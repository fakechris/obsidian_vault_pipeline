from __future__ import annotations

import json


def test_run_actions_main_runs_once_and_prints_payload(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands.run_actions import main

    monkeypatch.setattr(
        "openclaw_pipeline.commands.run_actions.run_next_action_queue_item",
        lambda vault_dir, *, safe_only=False: {
            "ran": True,
            "safe_only": safe_only,
            "action": {"action_id": "action::demo"},
        },
    )

    exit_code = main(["--vault-dir", str(temp_vault), "--once"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ran"] is True
    assert payload["action"]["action_id"] == "action::demo"


def test_run_actions_main_can_loop_for_multiple_iterations(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands.run_actions import main

    calls = {"count": 0}

    def fake_run_next(vault_dir, *, safe_only=False):
        calls["count"] += 1
        return {"ran": False, "safe_only": safe_only, "action": None, "iteration": calls["count"]}

    monkeypatch.setattr(
        "openclaw_pipeline.commands.run_actions.run_next_action_queue_item",
        fake_run_next,
    )

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--loop",
            "--interval",
            "0",
            "--max-runs",
            "2",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert calls["count"] == 2
    assert payload["loop"] is True
    assert payload["iterations"] == 2
    assert payload["last_result"]["iteration"] == 2
