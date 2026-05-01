from __future__ import annotations

import json
from datetime import datetime, timezone

from ovp_pipeline.event_emitter import emit
from ovp_pipeline.projection_lifecycle import (
    claim_projection_repair_marker,
    write_projection_repair_marker,
)
from ovp_pipeline.runtime_state import build_runtime_state, read_runtime_state, write_runtime_state


def test_runtime_state_empty_vault_is_ok(temp_vault):
    state = build_runtime_state(
        temp_vault,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert state["type"] == "operational_runtime_state"
    assert state["status"] == "ok"
    assert state["metrics"]["open_projection_repair_markers"] == 0
    assert state["metrics"]["projection_repair_events"] == 0
    assert state["metrics"]["pipeline_events"] == 0
    assert state["metrics"]["reuse_events"] == 0
    assert state["metrics"]["action_queue_items"] == 0
    assert any(node["id"] == "runtime" for node in state["graph"]["nodes"])
    assert any(node["id"] == "log:projection-repair" for node in state["graph"]["nodes"])
    assert any(node["id"] == "log:actions" for node in state["graph"]["nodes"])


def test_runtime_state_reports_open_and_expired_repair_lease(temp_vault):
    marker = write_projection_repair_marker(
        temp_vault,
        kind="full_rebuild",
        scope={"projection_kind": "knowledge_db"},
        reason="knowledge db missing",
        caused_by="startup_check",
        authority_schema_version=2,
        projection_schema_version=1,
        created_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )
    claim_projection_repair_marker(
        temp_vault,
        marker.marker_id,
        worker_id="worker-a",
        lease_seconds=60,
        now=datetime(2026, 4, 30, 12, 1, tzinfo=timezone.utc),
    )

    state = build_runtime_state(
        temp_vault,
        now=datetime(2026, 4, 30, 12, 3, tzinfo=timezone.utc),
    )

    assert state["status"] == "attention_required"
    assert state["metrics"]["claimed_projection_repair_markers"] == 1
    assert state["metrics"]["projection_repair_events"] == 2
    assert state["metrics"]["expired_projection_repair_leases"] == 1
    assert state["projection_repair_markers"][0]["lease_expired"] is True
    assert any(item["kind"] == "expired_projection_repair_lease" for item in state["attention"])
    assert {"source": f"marker:{marker.marker_id}", "target": "worker:worker-a", "kind": "claimed_by"} in state[
        "graph"
    ]["edges"]


def test_runtime_state_summarizes_pipeline_and_reuse_surfaces(temp_vault):
    emit(temp_vault, "pipeline.jsonl", "promotion", {"slug": "alpha"}, pack="default-knowledge")
    emit(temp_vault, "pipeline.jsonl", "relation_promoted", {"source": "a", "target": "b"}, pack="default-knowledge")
    emit(
        temp_vault,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        {
            "surface": "ovp_prime",
            "object_id": "obj-alpha",
            "object_kind": "concept",
            "trusted": 1,
        },
        pack="default-knowledge",
    )
    emit(
        temp_vault,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        {
            "surface": "working_memory",
            "object_id": "obj-beta",
            "object_kind": "concept",
            "trusted": 0,
        },
        pack="default-knowledge",
    )

    state = build_runtime_state(
        temp_vault,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert state["metrics"]["pipeline_events"] == 2
    assert state["metrics"]["reuse_events"] == 2
    assert state["metrics"]["trusted_reuse_events"] == 1
    assert state["pipeline_event_counts"] == {"promotion": 1, "relation_promoted": 1}

    surfaces = {row["surface"]: row for row in state["reuse_surfaces"]}
    assert surfaces["ovp_prime"]["trusted"] == 1
    assert surfaces["working_memory"]["untrusted"] == 1
    assert any(node["id"] == "surface:ovp_prime" for node in state["graph"]["nodes"])


def test_runtime_state_reports_action_queue_health_without_generalized_leases(temp_vault):
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "actions.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "action_id": "action::queued",
                        "action_kind": "deep_dive_workflow",
                        "pack": "research-tech",
                        "source_signal_id": "signal::queued",
                        "title": "Queued action",
                        "target_ref": "50-Inbox/03-Processed/Queued.md",
                        "status": "queued",
                        "created_at": "2026-04-30T11:55:00Z",
                        "safe_to_run": True,
                    }
                ),
                json.dumps(
                    {
                        "action_id": "action::running",
                        "action_kind": "object_extraction_workflow",
                        "pack": "research-tech",
                        "source_signal_id": "signal::running",
                        "title": "Running action",
                        "target_ref": "20-Areas/AI-Research/Topics/Running.md",
                        "status": "running",
                        "started_at": "2026-04-30T10:30:00Z",
                        "safe_to_run": True,
                    }
                ),
                json.dumps(
                    {
                        "action_id": "action::failed",
                        "action_kind": "deep_dive_workflow",
                        "pack": "research-tech",
                        "source_signal_id": "signal::failed",
                        "title": "Failed action",
                        "target_ref": "50-Inbox/03-Processed/Failed.md",
                        "status": "failed",
                        "failure_bucket": "workflow_failed",
                        "retry_count": 1,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    state = build_runtime_state(
        temp_vault,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert state["status"] == "attention_required"
    assert state["metrics"]["action_queue_items"] == 3
    assert state["metrics"]["queued_actions"] == 1
    assert state["metrics"]["running_actions"] == 1
    assert state["metrics"]["stale_running_actions"] == 1
    assert state["metrics"]["failed_actions"] == 1
    assert state["action_status_counts"] == {"failed": 1, "queued": 1, "running": 1}
    running = next(action for action in state["workflow_actions"] if action["status"] == "running")
    assert running["stale_running"] is True
    assert any(item["kind"] == "stale_running_action" for item in state["attention"])
    assert any(item["kind"] == "failed_action" for item in state["attention"])
    assert any(node["id"] == "action:action::running" for node in state["graph"]["nodes"])
    assert {
        "source": "action:action::running",
        "target": "signal:signal::running",
        "kind": "responds_to",
    } in state["graph"]["edges"]


def test_runtime_state_streams_large_action_queue_with_bounded_rows(temp_vault):
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "action_id": f"action::succeeded-{idx}",
            "action_kind": "deep_dive_workflow",
            "status": "succeeded",
            "finished_at": "2026-04-30T10:00:00Z",
        }
        for idx in range(250)
    ]
    rows.extend(
        [
            {
                "action_id": "action::failed-late",
                "action_kind": "deep_dive_workflow",
                "status": "failed",
                "failure_bucket": "workflow_failed",
            },
            {
                "action_id": "action::running-late",
                "action_kind": "object_extraction_workflow",
                "status": "running",
                "started_at": "2026-04-30T10:00:00Z",
            },
        ]
    )
    (logs_dir / "actions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    state = build_runtime_state(
        temp_vault,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert state["metrics"]["action_queue_items"] == 252
    assert state["metrics"]["succeeded_actions"] == 250
    assert state["metrics"]["failed_actions"] == 1
    assert state["metrics"]["stale_running_actions"] == 1
    assert len(state["workflow_actions"]) == 2
    assert {row["action_id"] for row in state["workflow_actions"]} == {
        "action::failed-late",
        "action::running-late",
    }


def test_write_runtime_state_materializes_json_and_markdown(temp_vault):
    state = build_runtime_state(
        temp_vault,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )
    paths = write_runtime_state(temp_vault, state)

    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert payload["type"] == "operational_runtime_state"
    assert paths.json_path.name == "current.json"
    assert paths.markdown_path.name == "current.md"
    assert "projection_kind: operational_runtime_projection" in markdown
    assert "# Operational Runtime State" in markdown
    assert "## Workflow Actions" in markdown
    assert read_runtime_state(temp_vault)["type"] == "operational_runtime_state"


def test_runtime_state_cli_writes_and_prints_json(temp_vault, monkeypatch, capsys):
    from ovp_pipeline.commands.runtime_state import main as runtime_state_main

    monkeypatch.setattr(
        "sys.argv",
        [
            "ovp-runtime-state",
            "--vault-dir",
            str(temp_vault),
            "--write",
            "--json",
        ],
    )

    rc = runtime_state_main()
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["type"] == "operational_runtime_state"
    assert payload["paths"]["json"].endswith("60-Logs/runtime-state/current.json")
    assert payload["paths"]["markdown"].endswith("60-Logs/runtime-state/current.md")
