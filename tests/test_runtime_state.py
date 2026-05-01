from __future__ import annotations

import json
from datetime import datetime, timezone

from ovp_pipeline.event_emitter import emit
from ovp_pipeline.projection_lifecycle import (
    claim_projection_repair_marker,
    write_projection_repair_marker,
)
from ovp_pipeline.runtime_state import build_runtime_state, write_runtime_state


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
    assert any(node["id"] == "runtime" for node in state["graph"]["nodes"])
    assert any(node["id"] == "log:projection-repair" for node in state["graph"]["nodes"])


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
