from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection, RemoteDisconnected
from pathlib import Path
from urllib.parse import urlencode

from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _fresh_timestamp(*, seconds_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


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


def _fetch_ui_html(temp_vault, path: str) -> tuple[int, str]:
    from ovp_pipeline.commands.ui_server import create_server

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return response.status, body


def _seed_running_transaction(temp_vault) -> None:
    transactions_dir = temp_vault / "60-Logs" / "transactions"
    transactions_dir.mkdir(parents=True, exist_ok=True)
    (transactions_dir / "txn-1.json").write_text(
        json.dumps(
            {
                "id": "txn-1",
                "type": "enhanced-pipeline",
                "status": "in_progress",
                "checkpoint": "absorb",
                "last_updated": _fresh_timestamp(seconds_ago=60),
                "run_ledger": {
                    "run_id": "txn-1",
                    "run_state": "running",
                    "workflow_profile": "full",
                    "pack_name": "research-tech",
                    "planned_steps": ["pinboard", "absorb", "knowledge_index"],
                    "started_at": _fresh_timestamp(seconds_ago=360),
                    "heartbeat_at": _fresh_timestamp(seconds_ago=30),
                    "current_step_name": "absorb",
                    "current_step": {
                        "step_name": "absorb",
                        "step_state": "running",
                        "step_started_at": _fresh_timestamp(seconds_ago=300),
                        "step_heartbeat_at": _fresh_timestamp(seconds_ago=30),
                        "progress_mode": "counted",
                        "work_units_total": 10,
                        "work_units_done": 3,
                        "work_units_failed": 0,
                        "current_item": "Alpha.md",
                        "progress_percent": 30.0,
                        "progress_summary": "3/10 files processed",
                    },
                    "last_meaningful_event": {
                        "event_type": "absorb_file_processed",
                        "file": "Alpha.md",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_ui_server_root_serves_reader_library_home(temp_vault):
    _seed_truth_store(temp_vault)
    _seed_running_transaction(temp_vault)

    status, body = _fetch_ui_html(temp_vault, "/")

    assert status == 200
    assert "Knowledge Library" in body
    assert 'href="/">Library</a>' in body
    assert 'href="/map">Map</a>' in body
    assert 'href="/search">Search</a>' in body
    assert 'href="/ops">Workbench</a>' in body
    assert "Recent Knowledge" in body
    assert "Knowledge Map" in body
    assert "Open Workbench" in body
    assert "Workflow Map" not in body
    assert "OVP Truth UI" not in body


def test_ui_server_ops_route_serves_operator_dashboard(temp_vault):
    _seed_truth_store(temp_vault)
    _seed_running_transaction(temp_vault)

    status, body = _fetch_ui_html(temp_vault, "/ops")

    assert status == 200
    assert "OVP Truth UI" in body
    assert "Workflow Map" in body
    assert "Orient" in body
    assert "Inspect" in body
    assert "Review" in body
    assert "Current Workflow" in body
    assert body.count("<h2>Current Workflow</h2>") == 1
    assert "Recent Runs" in body
    assert "Stage 2/3: absorb" in body
    assert "3/10 files processed" in body
    assert "30.0%" in body
    assert "files/s" in body
    assert "files/min" in body
    assert "ETA" in body
    assert "Alpha.md" in body
    assert 'http-equiv="refresh"' in body
    assert 'content="10"' in body
    assert "Where To Start" in body
    assert body.count("<h2>Where To Start</h2>") == 1
    assert "Signals Surface Contract" in body
    assert "Production Chains Surface Contract" in body
    assert "<h2>Surface Contract</h2>" not in body
    assert "Orientation Brief" in body
    assert "/api/objects" in body


def test_ui_server_map_route_serves_readable_map_entry(temp_vault):
    _seed_truth_store(temp_vault)

    status, body = _fetch_ui_html(temp_vault, "/map")

    assert status == 200
    assert "Knowledge Graph" in body
    assert "graph-map-canvas" in body
    assert "Alpha" in body
    assert "Beta" in body
    assert "How To Read This Map" in body
    assert "Open Cluster Browser" in body
    assert "Showing the first 24 graph neighborhoods" in body
    assert "<title>Knowledge graph map</title>" in body
    assert "role='img'" not in body
    assert "action='/map'" in body
    assert "action='/clusters'" not in body
    assert 'href="/">Library</a>' in body
    assert 'href="/map">Map</a>' in body
    assert 'href="/search">Search</a>' in body
    assert 'href="/ops">Workbench</a>' in body


def test_ui_server_graph_route_serves_visual_graph_mvp(temp_vault):
    _seed_truth_store(temp_vault)

    status, body = _fetch_ui_html(temp_vault, "/graph")

    assert status == 200
    assert "Knowledge Graph" in body
    assert "graph-map-canvas" in body
    assert "Alpha" in body
    assert "Beta" in body
    assert "Showing the first 24 graph neighborhoods" in body
    assert "action='/graph'" in body
    assert "action='/clusters'" not in body
    assert 'href="/">Library</a>' in body
    assert 'href="/map">Map</a>' in body
    assert 'href="/search">Search</a>' in body
    assert 'href="/ops">Workbench</a>' in body


def test_render_library_home_hides_unavailable_map_for_non_research_pack():
    from ovp_pipeline.commands.ui_server import _render_library_home

    body = _render_library_home(
        {
            "requested_pack": "media-editorial",
            "objects": {"count": 0, "items": []},
        }
    )

    assert "Knowledge Library" in body
    assert "Search Library" in body
    assert "Open Workbench" in body
    assert "Knowledge Map" not in body
    assert 'href="/map?pack=media-editorial"' not in body


def test_render_runtime_card_shows_pipeline_process_identity():
    from ovp_pipeline.commands.ui_server import _render_runtime_card

    html = _render_runtime_card(
        {
            "active_run": {
                "id": "txn-1",
                "status": "in_progress",
                "checkpoint": "absorb",
                "run_ledger": {
                    "run_state": "running",
                    "heartbeat_at": "2026-04-09T00:20:00Z",
                    "current_step": {
                        "step_name": "absorb",
                        "progress_summary": "3/10 files processed",
                    },
                },
            },
            "stale_count": 0,
            "runtime_processes": {
                "active_count": 1,
                "items": [
                    {
                        "pid": 80018,
                        "process_kind": "one_shot",
                        "elapsed_summary": "1h 2m",
                        "args_summary": "--from-step absorb --pack research-tech",
                    }
                ],
            },
        }
    )

    assert "PID 80018" in html
    assert "one-shot" in html
    assert "1h 2m" in html
    assert "--from-step absorb --pack research-tech" in html


def test_render_runtime_card_shows_action_worker_state():
    from ovp_pipeline.commands.ui_server import _render_runtime_card

    html = _render_runtime_card(
        {
            "active_run": None,
            "stale_count": 0,
            "action_worker": {
                "active": True,
                "state": "running",
                "mode": "loop",
                "safe_only": True,
                "pid": 12345,
                "elapsed_summary": "20m",
                "heartbeat_age_summary": "10m",
                "current_action": {
                    "action_id": "action::demo",
                    "action_kind": "deep_dive_workflow",
                    "source_signal_id": "signal::demo",
                    "target_ref": "50-Inbox/03-Processed/Demo.md",
                },
            },
        }
    )

    assert "Action Worker" in html
    assert "PID 12345" in html
    assert "loop" in html
    assert "safe-only" in html
    assert "action::demo" in html
    assert "deep_dive_workflow" in html
    assert "50-Inbox/03-Processed/Demo.md" in html


def test_render_runtime_card_shows_cache_skip_and_blocked_reason():
    from ovp_pipeline.commands.ui_server import _render_runtime_card

    html = _render_runtime_card(
        {
            "active_run": {
                "id": "txn-1",
                "status": "failed",
                "checkpoint": "absorb",
                "steps": {
                    "absorb": {
                        "status": "blocked",
                        "output": "Absorb blocked",
                        "skipped": True,
                        "cache_hit": True,
                        "blocked_reason": "missing_quality_stage_artifact",
                        "stage_fingerprint": "abcdef123456",
                    }
                },
                "run_ledger": {
                    "run_state": "failed",
                    "heartbeat_at": "2026-04-09T00:20:00Z",
                    "blocked_reason": "missing_quality_stage_artifact",
                    "current_step": {
                        "step_name": "absorb",
                        "step_state": "blocked",
                        "progress_summary": "Absorb blocked",
                    },
                },
            },
            "stale_count": 0,
        }
    )

    assert "Cache: hit" in html
    assert "Skipped: yes" in html
    assert "Blocked reason: missing_quality_stage_artifact" in html
    assert "Fingerprint: abcdef123456" in html


def test_render_run_history_card_shows_duration_scope_and_work():
    from ovp_pipeline.commands.ui_server import _render_run_history_card

    html = _render_run_history_card(
        {
            "run_history": {
                "total_count": 1,
                "items": [
                    {
                        "run_id": "pipeline-complete",
                        "status": "completed",
                        "duration_summary": "10m",
                        "scope_summary": "pinboard → articles → knowledge_index",
                        "content_summary": "Produced 6 items; 20/20 files processed",
                        "started_at": "2026-04-09T00:00:00Z",
                        "finished_at": "2026-04-09T00:10:00Z",
                    }
                ],
            }
        }
    )

    assert "<h2>Recent Runs</h2>" in html
    assert "pipeline-complete" in html
    assert "completed" in html
    assert "10m" in html
    assert "pinboard → articles → knowledge_index" in html
    assert "Produced 6 items; 20/20 files processed" in html


def test_render_run_history_card_shows_cache_skip_and_blocked_step_summaries():
    from ovp_pipeline.commands.ui_server import _render_run_history_card

    html = _render_run_history_card(
        {
            "run_history": {
                "total_count": 1,
                "items": [
                    {
                        "run_id": "pipeline-with-cache",
                        "status": "failed",
                        "duration_summary": "2m",
                        "scope_summary": "articles → fix_links → absorb",
                        "content_summary": "Cache hits: 1; skipped: 1; blocked: missing_quality_stage_artifact",
                        "started_at": "2026-04-09T00:00:00Z",
                        "finished_at": "2026-04-09T00:02:00Z",
                        "step_summaries": [
                            {
                                "step_name": "fix_links",
                                "status": "completed",
                                "cache_hit": True,
                                "skipped": True,
                                "blocked_reason": "",
                            },
                            {
                                "step_name": "absorb",
                                "status": "blocked",
                                "cache_hit": False,
                                "skipped": False,
                                "blocked_reason": "missing_quality_stage_artifact",
                            },
                        ],
                    }
                ],
            }
        }
    )

    assert "fix_links" in html
    assert "cache hit" in html
    assert "skipped" in html
    assert "absorb" in html
    assert "blocked: missing_quality_stage_artifact" in html


def test_ui_server_runtime_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    transactions_dir = temp_vault / "60-Logs" / "transactions"
    transactions_dir.mkdir(parents=True, exist_ok=True)
    (transactions_dir / "txn-1.json").write_text(
        json.dumps(
            {
                "id": "txn-1",
                "type": "enhanced-pipeline",
                "status": "in_progress",
                "checkpoint": "absorb",
                "last_updated": _fresh_timestamp(seconds_ago=60),
                "run_ledger": {
                    "run_id": "txn-1",
                    "run_state": "running",
                    "workflow_profile": "full",
                    "pack_name": "research-tech",
                    "planned_steps": ["pinboard", "absorb", "knowledge_index"],
                    "started_at": _fresh_timestamp(seconds_ago=660),
                    "heartbeat_at": _fresh_timestamp(seconds_ago=30),
                    "current_step_name": "absorb",
                    "current_step": {
                        "step_name": "absorb",
                        "step_state": "running",
                        "step_started_at": _fresh_timestamp(seconds_ago=600),
                        "step_heartbeat_at": _fresh_timestamp(seconds_ago=30),
                        "progress_mode": "counted",
                        "work_units_total": 20,
                        "work_units_done": 5,
                        "work_units_failed": 0,
                        "current_item": "Beta.md",
                        "progress_percent": 25.0,
                        "progress_summary": "5/20 files processed",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/runtime")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["active_run"]["id"] == "txn-1"
    assert payload["active_run"]["run_ledger"]["current_step"]["progress_percent"] == 25.0
    assert payload["run_history"]["items"][0]["run_id"] == "txn-1"
    assert payload["active_run"]["runtime_progress"]["stage"]["summary"] == "Stage 2/3: absorb"
    assert payload["active_run"]["runtime_progress"]["work"]["summary"] == "5/20 files processed"
    assert payload["active_run"]["runtime_progress"]["performance"]["items_per_minute"] > 0


def test_ui_server_runtime_state_endpoint_returns_provider_projection(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.projection_lifecycle import write_projection_repair_marker

    write_projection_repair_marker(
        temp_vault,
        kind="full_rebuild",
        scope={"projection_kind": "knowledge_db"},
        reason="knowledge db missing",
        caused_by="ui_test",
        authority_schema_version=2,
        projection_schema_version=1,
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/runtime-state", body="limit=20")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["type"] == "operational_runtime_state"
    assert payload["status"] == "attention_required"
    assert payload["metrics"]["open_projection_repair_markers"] == 1
    assert payload["paths"]["json"].endswith("60-Logs/runtime-state/current.json")


def test_ui_server_runtime_state_get_is_read_only(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/runtime-state?write=1")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["type"] == "operational_runtime_state"
    assert "paths" not in payload
    assert not (temp_vault / "60-Logs" / "runtime-state" / "current.json").exists()


def test_ui_server_runtime_state_endpoint_returns_503_on_provider_error(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    def fail_runtime_state(*args, **kwargs):
        raise OSError("projection unavailable")

    monkeypatch.setattr(ui_server, "get_operational_runtime_state", fail_runtime_state)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/runtime-state")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 503
    assert payload["error"] == "runtime_state_unavailable"


def test_ui_server_ops_route_renders_runtime_state_card(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.projection_lifecycle import write_projection_repair_marker

    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "actions.jsonl").write_text(
        json.dumps(
            {
                "action_id": "action::queued",
                "action_kind": "deep_dive_workflow",
                "status": "queued",
                "created_at": "2026-04-30T12:00:00Z",
                "safe_to_run": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_projection_repair_marker(
        temp_vault,
        kind="metadata_only",
        scope={"projection_kind": "knowledge_db"},
        reason="metadata drift",
        caused_by="ui_test",
        authority_schema_version=1,
        projection_schema_version=1,
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/ops")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "System Health" in body
    assert "Runtime state: attention_required" in body
    assert "Open repair markers: 1" in body
    assert "Queued actions: 1" in body


def test_ui_server_runtime_endpoint_returns_structured_error(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server

    def fail_runtime_status(_vault_dir):
        raise OSError("ledger read failed")

    monkeypatch.setattr(ui_server, "get_runtime_status", fail_runtime_status)
    server = ui_server.create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/runtime")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 503
    assert payload["active_run"] is None
    assert payload["stale_count"] == 0
    assert payload["error"] == "runtime_status_unavailable"


def test_render_runtime_card_tolerates_malformed_stale_count():
    from ovp_pipeline.commands.ui_server import _render_runtime_card

    html = _render_runtime_card({"active_run": None, "stale_count": "not-a-number"})

    assert "No active workflow is currently recorded" in html


def test_ui_server_root_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/search?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert "Knowledge Library" in body


def test_ui_server_objects_endpoint_returns_json(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/objects")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert [item["object_id"] for item in payload["items"]] == ["alpha", "beta", "conflict"]


def test_ui_server_search_endpoint_returns_objects_and_notes(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    deep_dive = (
        temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Agent Harness_深度解读.md"
    )
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
title: Agent Harness Deep Dive
source: https://example.com/agent-harness
date: 2026-04-13
type: deep_dive
---

# Agent Harness Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/search?q=alpha")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["objects"]
    assert payload["notes"]
    assert any(item["note_type"] == "deep_dive" for item in payload["notes"])


def test_ui_server_search_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    deep_dive = (
        temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Agent Harness_深度解读.md"
    )
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
title: Agent Harness Deep Dive
source: https://example.com/agent-harness
date: 2026-04-13
type: deep_dive
---

# Agent Harness Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/search?q=alpha&pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["objects"][0]["object_path"] == "/object?id=alpha&pack=default-knowledge"
    assert any(item["note_path"].endswith("&pack=default-knowledge") for item in payload["notes"])


def test_ui_server_search_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/search?q=alpha&pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/search?pack=default-knowledge"' in body
    assert (
        "name='pack' value='default-knowledge'" in body
        or 'name="pack" value="default-knowledge"' in body
    )
    assert 'href="/object?id=alpha&amp;pack=default-knowledge"' in body


def test_ui_server_search_page_renders_reader_grouped_results(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.runtime import VaultLayout

    _seed_truth_store(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE objects SET object_kind = 'concept' WHERE object_id = 'alpha'")
        conn.commit()
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/search?q=alpha")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "<h1>Reader Search</h1>" in body
    assert "<h2>Concepts</h2>" in body
    assert "Alpha supports local-first execution." in body
    assert "Matched title, summary, and evidence-backed claims." in body
    assert "Evidence: 1" in body
    assert "<h2>Evergreen Notes</h2>" in body


def test_ui_server_object_endpoint_returns_detail_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/object?id=alpha")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["object"]["object_id"] == "alpha"
    assert payload["relation_count"] == 1


def test_ui_server_object_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/object?id=alpha&pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["links"]["topic_path"] == "/topic?id=alpha&pack=default-knowledge"


def test_ui_server_object_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/object?id=alpha&pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/search?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert (
        'href="/note?path=10-Knowledge%2FEvergreen%2FAlpha.md&amp;pack=default-knowledge"' in body
    )
    assert "Assembly Contract" in body
    assert "inherited from object_brief in research-tech" in body
    assert "Source contract: wiki_view · object/page" in body
    assert "Source provider: default-knowledge · object/page" in body
    assert "Why It Matters" in body
    assert "Where To Go Next" in body
    assert "Source contract: wiki_view · object/page" in body
    assert body.index("Current State") < body.index("Next Actions") < body.index("Why It Matters")


def test_ui_server_note_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness
source: https://example.com/harness
---

Processed source note.
""",
        encoding="utf-8",
    )
    deep_dive = (
        temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness_深度解读.md"
    )
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: harness-deep-dive
title: Harness Deep Dive
type: deep_dive
source: https://example.com/harness
date: 2026-04-13
---

# Harness Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.parent.mkdir(parents=True, exist_ok=True)
    evergreen.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-18T09:00:00Z",
                        "event_type": "source_archived_to_processed",
                        "source": "50-Inbox/02-Processing/Harness.md",
                        "archived": str(processed),
                    },
                    ensure_ascii=False,
                ),
                '{"event_type":"article_processed","file":"Harness.md","output":"'
                + str(deep_dive)
                + '"}',
                json.dumps(
                    {
                        "timestamp": "2026-04-18T09:11:00Z",
                        "event_type": "candidates_upserted",
                        "file": "Harness.md",
                        "candidates": ["agent-harness"],
                    },
                    ensure_ascii=False,
                ),
                '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Harness_深度解读.md","mutation":{"target_slug":"alpha"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            "/note?path=50-Inbox%2F03-Processed%2F2026-04%2FHarness.md&pack=default-knowledge",
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/search?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert 'href="/object?id=alpha&amp;pack=default-knowledge"' in body
    assert (
        'href="/note?path=20-Areas%2FAI-Research%2FTopics%2F2026-04%2FHarness_%E6%B7%B1%E5%BA%A6%E8%A7%A3%E8%AF%BB.md&amp;pack=default-knowledge"'
        in body
    )
    assert "Current State" in body
    assert "Inbound Capture" in body
    assert "Captured 4 inbound events" in body
    assert "Evidence Traceability" in body
    assert "Production Chain" in body
    assert "Next Actions" in body
    assert body.index("Current State") < body.index("Next Actions") < body.index("Inbound Capture")


def test_ui_server_contradictions_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/contradictions")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["count"] == 1
    assert payload["items"][0]["subject_key"] == "alpha"


def test_ui_server_contradictions_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/contradictions?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_contradictions_page_renders_assembly_contract(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/contradictions?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Assembly Contract" in body
    assert "inherited from contradiction_view in research-tech" in body
    assert "Source contract: wiki_view · truth/contradictions" in body
    assert "Source provider: default-knowledge · truth/contradictions" in body
    assert "Why It Matters" in body
    assert "Where To Go Next" in body


def test_ui_server_signals_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    loose_source = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/signals")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["count"] >= 2
    assert "contradiction_open" in payload["type_counts"]


def test_ui_server_signals_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/signals?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["governance_contract"]["status"] == "inherited"
    assert payload["governance_contract"]["provider_pack"] == "research-tech"
    assert payload["governance_contract"]["provider_name"] == "research_governance"


def test_ui_server_signals_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/signals?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert "inherited from research-tech-signals" in body
    assert body.index("Next Actions") < body.index("Signal Types")


def test_ui_server_signals_page_renders_missing_surface_contract_error(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_signal_browser_payload",
        lambda vault_dir, *, pack_name=None, signal_type=None, query=None: {
            "screen": "signals/browser",
            "requested_pack": pack_name or "",
            "surface_contract": {
                "surface_kind": "signals",
                "requested_pack": "media-editorial",
                "status": "missing",
                "provider_pack": "",
                "provider_name": "",
                "description": "",
            },
            "surface_error": "Pack 'media-editorial' does not expose a shared shell 'signals' surface.",
            "items": [],
            "count": 0,
            "query": "",
            "signal_type": "",
            "type_counts": {},
            "signal_type_explanations": {},
        },
    )
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/signals?pack=media-editorial")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "has no provider for signals" in body
    assert "does not expose a shared shell &#x27;signals&#x27; surface" in body


def test_ui_server_shell_nav_hides_research_links_for_non_research_pack(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_signal_browser_payload",
        lambda vault_dir, *, pack_name=None, signal_type=None, query=None: {
            "screen": "signals/browser",
            "requested_pack": pack_name or "",
            "surface_contract": {
                "surface_kind": "signals",
                "requested_pack": pack_name or "",
                "status": "missing",
                "provider_pack": "",
                "provider_name": "",
                "description": "",
            },
            "surface_error": "Pack 'media-editorial' does not expose a shared shell 'signals' surface.",
            "items": [],
            "count": 0,
            "query": "",
            "signal_type": "",
            "type_counts": {},
            "signal_type_explanations": {},
        },
    )
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/signals?pack=media-editorial")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=media-editorial"' in body
    assert 'href="/map?pack=media-editorial"' not in body
    assert 'href="/ops?pack=media-editorial"' in body
    assert 'href="/clusters?pack=media-editorial"' not in body
    assert 'href="/contradictions?pack=media-editorial"' not in body
    assert 'href="/events?pack=media-editorial"' not in body


def test_ui_server_research_api_route_rejects_non_research_pack(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_cluster_browser_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cluster builder should not run")
        ),
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/clusters?pack=media-editorial")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 409
    assert payload["status"] == "unsupported_pack"
    assert payload["route"] == "/clusters"
    assert payload["requested_pack"] == "media-editorial"


def test_ui_server_research_html_route_rejects_non_research_pack(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_cluster_browser_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cluster builder should not run")
        ),
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/clusters?pack=media-editorial")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Route Unavailable" in body
    assert "research-specific observation shell" in body
    assert "media-editorial" in body


def test_ui_server_map_route_rejects_non_research_pack_without_nav_link(
    temp_vault, monkeypatch
):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_cluster_browser_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cluster builder should not run")
        ),
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/map?pack=media-editorial")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Route Unavailable" in body
    assert "research-specific observation shell" in body
    assert 'href="/map?pack=media-editorial"' not in body


def test_ui_server_summaries_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    note.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-10
---

# Thin Note

Thin note.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/summaries?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_events_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/events?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_events_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/events?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert (
        'href="/note?path=10-Knowledge%2FEvergreen%2FAlpha.md&amp;pack=default-knowledge"' in body
    )
    assert "Assembly Contract" in body
    assert "inherited from event_dossier in research-tech" in body
    assert "Source contract: wiki_view · event/dossier" in body
    assert "Source provider: default-knowledge · event/dossier" in body
    assert "Grouping kind: object_date_rollup" in body
    assert "Anchor kind note: 3" in body
    assert "not a canonical event entity store" in body


def test_ui_server_atlas_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/atlas?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_deep_dives_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/deep-dives?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_evolution_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/evolution")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["screen"] == "evolution/browser"
    assert payload["count"] >= 1


def test_ui_server_evolution_endpoint_accepts_pack_scope(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    captured: dict[str, str | None] = {}

    def fake_build_evolution_browser_payload(
        vault_dir, *, pack_name=None, query=None, status="all", link_type=None
    ):
        captured["pack_name"] = pack_name
        captured["query"] = query
        captured["status"] = status
        captured["link_type"] = link_type
        return {
            "screen": "evolution/browser",
            "requested_pack": pack_name or "",
            "query": query or "",
            "status": status,
            "link_type": link_type or "",
            "items": [],
            "candidate_items": [],
            "accepted_links": [],
            "rejected_links": [],
            "candidate_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "count": 0,
            "type_counts": {},
            "link_types": [],
        }

    monkeypatch.setattr(
        ui_server, "build_evolution_browser_payload", fake_build_evolution_browser_payload
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET", "/api/evolution?pack=default-knowledge&q=alpha&status=all&link_type=enriches"
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert captured == {
        "pack_name": "default-knowledge",
        "query": "alpha",
        "status": "all",
        "link_type": "enriches",
    }
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_evolution_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/evolution?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert "name='pack' value='default-knowledge'" in body


def test_ui_server_clusters_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/clusters?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["screen"] == "graph/clusters"
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["count"] >= 1
    assert payload["items"][0]["cluster_kind"] == "relation_component"
    assert payload["items"][0]["priority_band"]
    assert payload["items"][0]["priority_reason"]
    assert payload["items"][0]["display_title"]
    assert payload["items"][0]["relation_pattern_preview"]


def test_ui_server_clusters_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/clusters?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body


def test_ui_server_cluster_detail_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.truth_api import list_graph_clusters

    _seed_truth_store(temp_vault)
    cluster = list_graph_clusters(temp_vault)[0]
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/api/cluster?id={cluster['cluster_id']}&pack={cluster['pack']}")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["screen"] == "graph/cluster-detail"
    assert payload["cluster"]["cluster_id"] == cluster["cluster_id"]
    assert payload["cluster"]["pack"] == cluster["pack"]
    assert payload["browser_path"].startswith("/clusters?pack=")
    assert payload["edges"]
    assert payload["summary_bullets"]
    assert payload["structural_label"]["title"]
    assert payload["relation_pattern_items"]
    assert payload["open_contradictions"]
    assert payload["stale_summaries"]


def test_ui_server_cluster_detail_endpoint_includes_related_clusters(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.truth_api import list_graph_clusters

    _seed_truth_store(temp_vault)
    gamma = temp_vault / "10-Knowledge" / "Evergreen" / "Gamma.md"
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    gamma.write_text(
        """---
note_id: gamma
title: Gamma
type: evergreen
date: 2026-04-13
---

# Gamma

Gamma links to [[delta]].
""",
        encoding="utf-8",
    )
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-13
---

# Delta
""",
        encoding="utf-8",
    )
    shared_source = (
        temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
    )
    shared_source.parent.mkdir(parents=True, exist_ok=True)
    shared_source.write_text(
        """---
note_id: shared-deep-dive
title: Shared Deep Dive
type: deep_dive
date: 2026-04-13
---

# Shared Deep Dive

Mentions [[alpha]], [[beta]], [[gamma]], and [[delta]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Shared-Atlas.md"
    atlas.write_text(
        """---
note_id: shared-atlas
title: Shared Atlas
type: moc
date: 2026-04-13
---

# Shared Atlas

- [[alpha]]
- [[beta]]
- [[gamma]]
- [[delta]]
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    cluster = next(
        item
        for item in list_graph_clusters(temp_vault)
        if "alpha" in {member["object_id"] for member in item["members"]}
    )
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/api/cluster?id={cluster['cluster_id']}&pack={cluster['pack']}")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["related_clusters"]
    assert payload["related_clusters"][0]["shared_source_count"] >= 1
    assert payload["related_clusters"][0]["shared_moc_count"] >= 1
    assert payload["related_cluster_groups"]
    assert payload["related_cluster_groups"][0]["bridge_kind"] == "source_and_atlas_overlap"
    assert payload["reading_routes"]
    assert payload["reading_routes"][0]["route_kind"] == "full_context_route"
    assert payload["reading_routes"][0]["route_rank"] == 1
    assert payload["reading_routes"][0]["route_reason"]


def test_ui_server_cluster_detail_page_shows_canonical_cluster_id(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.truth_api import list_graph_clusters

    _seed_truth_store(temp_vault)
    cluster = list_graph_clusters(temp_vault)[0]
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/cluster?id={cluster['cluster_id']}&pack={cluster['pack']}")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert f"Canonical cluster id: {cluster['cluster_id']}" in body


def test_ui_server_clusters_endpoint_includes_related_cluster_summary(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    gamma = temp_vault / "10-Knowledge" / "Evergreen" / "Gamma.md"
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    gamma.write_text(
        """---
note_id: gamma
title: Gamma
type: evergreen
date: 2026-04-13
---

# Gamma

Gamma links to [[delta]].
""",
        encoding="utf-8",
    )
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-13
---

# Delta
""",
        encoding="utf-8",
    )
    shared_source = (
        temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
    )
    shared_source.parent.mkdir(parents=True, exist_ok=True)
    shared_source.write_text(
        """---
note_id: shared-deep-dive
title: Shared Deep Dive
type: deep_dive
date: 2026-04-13
---

# Shared Deep Dive

Mentions [[alpha]], [[beta]], [[gamma]], and [[delta]].
""",
        encoding="utf-8",
    )
    shared_atlas = temp_vault / "10-Knowledge" / "Atlas" / "Shared-Atlas.md"
    shared_atlas.write_text(
        """---
note_id: shared-atlas
title: Shared Atlas
type: moc
date: 2026-04-13
---

# Shared Atlas

- [[alpha]]
- [[beta]]
- [[gamma]]
- [[delta]]
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/clusters")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    item = next(entry for entry in payload["items"] if "alpha" in entry["member_object_ids"])
    assert response.status == 200
    assert item["related_cluster_count"] >= 1
    assert item["related_cluster_preview"]
    assert item["neighborhood_score"] > 0
    assert item["neighborhood_reason"]
    assert item["neighborhood_bridge_kind"] == "source_and_atlas_overlap"
    assert item["next_read_title"]
    assert item["next_read_path"].startswith("/cluster?id=")
    assert item["top_reading_route_kind"] == "full_context_route"
    assert item["top_reading_route_title"]
    assert item["has_reading_route"] is True
    assert item["reading_intent_count"] >= 1
    assert "Full Context Route" in item["reading_intent_preview"]


def test_ui_server_can_accept_evolution_candidate_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.truth_api import list_evolution_candidates

    _seed_truth_store(temp_vault)
    candidate = next(
        item for item in list_evolution_candidates(temp_vault) if item["link_type"] == "challenges"
    )
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode(
            {
                "evolution_id": candidate["evolution_id"],
                "status": "accepted",
                "note": "Accepted in UI",
                "link_type": "challenges",
            }
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/evolution/review",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["accepted_count"] == 1


def test_ui_server_briefing_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.truth_api import record_review_action

    _seed_truth_store(temp_vault)
    record_review_action(
        temp_vault,
        event_type="ui_summaries_rebuilt",
        slug="alpha",
        payload={
            "object_ids": ["alpha"],
            "objects_rebuilt": 1,
            "rebuilt_object_ids": ["alpha"],
        },
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/briefing")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["recent_signal_count"] >= 1
    assert payload["active_topics"]
    assert payload["assembly_contract"]["recipe_name"] == "orientation_brief"
    assert payload["first_useful_sign_check"]["status"] in {"useful", "empty"}
    assert "auto_queue_enabled_signal_types" in payload["background_policy"]
    assert [section["id"] for section in payload["compiled_sections"]] == [
        "signal_loop",
        "inbound_capture",
        "what_changed",
        "what_matters",
        "needs_review",
        "next_reads",
        "next_actions",
    ]


def test_ui_server_briefing_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/briefing?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["assembly_contract"]["status"] == "inherited"
    assert payload["assembly_contract"]["provider_pack"] == "research-tech"
    assert payload["governance_contract"]["status"] == "inherited"
    assert payload["governance_contract"]["provider_pack"] == "research-tech"
    assert payload["governance_contract"]["provider_name"] == "research_governance"


def test_ui_server_briefing_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    loose_source = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-04-18T10:00:00Z",
                "event_type": "source_archived_to_processed",
                "source": "50-Inbox/02-Processing/Loose Source.md",
                "archived": str(loose_source),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/briefing?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert "inherited from research-tech-briefing" in body
    assert "inherited from orientation_brief in research-tech" in body
    assert "Source contract: observation_surface · briefing" in body
    assert "Source provider: research-tech · research-tech-briefing" in body
    assert "Governance Contract" in body
    assert "inherited from research_governance in research-tech" in body
    assert "Signal Loop" in body
    assert "Inbound Capture" in body
    assert "What Changed" in body
    assert "Next Actions" in body
    assert "Value Proof" in body
    assert "Background Policy" in body
    assert "Auto-queue enabled" in body
    assert body.index("Signal Loop") < body.index("Next Actions") < body.index("Inbound Capture")


def test_render_briefing_page_tolerates_malformed_background_policy():
    from ovp_pipeline.commands import ui_server

    body = ui_server._render_briefing_page(
        {
            "requested_pack": "",
            "generated_at": "2026-04-21T00:00:00Z",
            "recent_signal_count": 0,
            "unresolved_issue_count": 0,
            "compiled_sections": [],
            "section_nav": [],
            "first_useful_sign": None,
            "first_useful_sign_check": ["bad"],
            "background_policy": {
                "auto_queue_enabled_signal_types": "bad",
                "review_only_signal_types": "bad",
                "skipped_signal_count": "<script>bad</script>",
                "signal_type_decisions": {
                    "<b>signal</b>": {
                        "decision": "<b>review</b>",
                        "active_signal_count": "bad",
                        "queued_action_count": "<script>bad</script>",
                        "skipped_count": object(),
                    }
                },
            },
            "surface_contract": {},
            "assembly_contract": {},
            "governance_contract": {},
            "operator_rail": [],
            "queue_summary": {
                "queued_count": "bad",
                "safe_queued_count": object(),
                "running_count": "<script>bad</script>",
                "failed_count": None,
                "failure_buckets": {"<b>bucket</b>": "<script>bad</script>"},
            },
            "loop_summary": {
                "productive_count": "bad",
                "waiting_count": "<script>bad</script>",
                "failed_count": "bad",
                "stalled_count": object(),
            },
            "insights": [],
            "priority_items": [],
            "recent_signals": [],
            "unresolved_issues": [],
            "changed_objects": [],
            "active_topics": [],
        }
    )

    assert "Value Proof" in body
    assert "Background Policy" in body
    assert "Auto-queue enabled: none" in body
    assert "&lt;b&gt;bucket&lt;/b&gt;" in body
    assert "<script>bad</script>" not in body


def test_ui_server_briefing_page_renders_governance_resolver_metadata(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_briefing_payload",
        lambda vault_dir, *, pack_name=None: {
            "screen": "briefing/dashboard",
            "requested_pack": pack_name or "",
            "surface_contract": {
                "surface_kind": "briefing",
                "requested_pack": pack_name or "",
                "status": "declared",
                "provider_pack": "research-tech",
                "provider_name": "research-tech-briefing",
                "description": "Briefing surface",
            },
            "governance_contract": {
                "requested_pack": pack_name or "",
                "status": "declared",
                "provider_pack": "research-tech",
                "provider_name": "research_governance",
                "description": "Governance contract",
                "review_queue_count": 2,
                "signal_rule_count": 3,
                "resolver_rule_count": 4,
                "review_queue_names": ["contradictions", "stale-summaries"],
                "signal_rule_names": ["source_needs_deep_dive"],
                "resolver_rule_names": ["deep_dive_workflow"],
            },
            "generated_at": "2026-04-17T00:00:00Z",
            "recent_signal_count": 1,
            "unresolved_issue_count": 1,
            "first_useful_sign": None,
            "insights": [],
            "priority_items": [
                {
                    "kind": "source_needs_deep_dive",
                    "path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                    "title": "Loose Source",
                    "detail": "Processed source note has no derived deep dive yet.",
                    "signal_id": "signal::demo",
                    "recommended_action": {
                        "kind": "deep_dive_workflow",
                        "label": "Create deep dive",
                        "path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                        "executable": False,
                        "resolution_kind": "focused_action",
                        "dispatch_mode": "queue_only",
                        "resolver_rule_name": "deep_dive_workflow",
                        "governance_provider_name": "research_governance",
                        "governance_provider_pack": "research-tech",
                        "safe_to_run": True,
                    },
                }
            ],
            "recent_signals": [],
            "unresolved_issues": [],
            "changed_objects": [],
            "active_topics": [],
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/briefing")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Resolver: focused_action" in body
    assert "Dispatch: queue_only" in body
    assert "Rule: deep_dive_workflow" in body
    assert "Governance contract: research_governance · research-tech" in body
    assert "Governance Contract" in body
    assert "declared by research_governance in research-tech" in body


def test_ui_server_can_enqueue_signal_action_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.truth_api import list_signals

    _seed_truth_store(temp_vault)
    loose_source = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    source_signal = next(
        item for item in list_signals(temp_vault) if item["signal_type"] == "source_needs_deep_dive"
    )
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode({"signal_id": source_signal["signal_id"]})
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/actions/enqueue",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["created"] is False
    assert payload["action"]["status"] == "queued"


def test_ui_server_production_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/production?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"


def test_ui_server_production_page_preserves_pack_scope_in_note_links(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    loose_source = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/production?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert (
        'href="/note?path=50-Inbox%2F03-Processed%2F2026-04%2FLoose%20Source.md&amp;pack=default-knowledge"'
        in body
    )
    assert "inherited from research-tech-production-chains" in body
    assert "Chain status:" in body
    assert "Missing stages:" in body
    assert "Current State" in body
    assert "Chain Gaps" in body
    assert "Next Actions" in body
    assert body.index("Current State") < body.index("Next Actions") < body.index("Chain Gaps")


def test_ui_server_contradictions_page_renders_detection_semantics(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/contradictions")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Polarity semantics:" in body
    assert "Evidence semantics:" in body
    assert "1 positive claims vs 1 negative claims across 2 objects." in body


def test_ui_server_actions_page_renders_execution_contract_metadata(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_action_queue_payload",
        lambda vault_dir, *, pack_name=None, status=None, query=None: {
            "screen": "actions/browser",
            "requested_pack": pack_name or "",
            "governance_contract": {
                "requested_pack": pack_name or "",
                "status": "declared",
                "provider_pack": "research-tech",
                "provider_name": "research_governance",
                "description": "Governance contract",
                "review_queue_count": 2,
                "signal_rule_count": 3,
                "resolver_rule_count": 4,
                "review_queue_names": ["contradictions", "stale-summaries"],
                "signal_rule_names": ["source_needs_deep_dive"],
                "resolver_rule_names": ["deep_dive_workflow"],
            },
            "items": [
                {
                    "action_id": "action::demo",
                    "status": "queued",
                    "action_kind": "deep_dive_workflow",
                    "title": "Create deep dive",
                    "target_ref": "50-Inbox/03-Processed/Loose Source.md",
                    "created_at": "2026-04-16T00:00:00Z",
                    "retry_count": 0,
                    "failure_bucket": "",
                    "safe_to_run": True,
                    "resolution_kind": "focused_action",
                    "dispatch_mode": "queue_only",
                    "resolver_rule_name": "deep_dive_workflow",
                    "governance_provider_name": "research_governance",
                    "governance_provider_pack": "research-tech",
                    "processor_mode": "llm_structured",
                    "processor_inputs": ["source_note"],
                    "processor_outputs": ["deep_dive"],
                    "processor_quality_hooks": ["quality"],
                    "impact_summary": {
                        "impact_status": "waiting",
                        "lifecycle_stage": "queued",
                        "impact_label": "Waiting on queue execution",
                        "impact_detail": "A queueable action exists and is currently waiting to run.",
                        "produced_artifact_count": 0,
                    },
                }
            ],
            "count": 1,
            "query": "",
            "status": "",
            "status_counts": {"queued": 1},
            "impact_counts": {"waiting": 1},
            "queued_safe_count": 1,
            "failed_count": 0,
            "failure_buckets": {},
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/actions")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Resolver: focused_action" in body
    assert "Dispatch: queue_only" in body
    assert "Rule: deep_dive_workflow" in body
    assert "Governance contract: research_governance · research-tech" in body
    assert "Processor: llm_structured" in body
    assert "Inputs: source_note" in body
    assert "Outputs: deep_dive" in body
    assert "Impact: Waiting on queue execution" in body
    assert "Quality hooks: quality" in body
    assert "Governance Contract" in body
    assert "declared by research_governance in research-tech" in body


def test_ui_server_signals_page_renders_governance_resolver_metadata(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_signal_browser_payload",
        lambda vault_dir, *, pack_name=None, signal_type=None, query=None: {
            "screen": "signals/browser",
            "requested_pack": pack_name or "",
            "surface_contract": {
                "surface_kind": "signals",
                "requested_pack": pack_name or "",
                "status": "declared",
                "provider_pack": "research-tech",
                "provider_name": "research-tech-signals",
                "description": "Signal browser",
            },
            "governance_contract": {
                "requested_pack": pack_name or "",
                "status": "declared",
                "provider_pack": "research-tech",
                "provider_name": "research_governance",
                "description": "Governance contract",
                "review_queue_count": 2,
                "signal_rule_count": 3,
                "resolver_rule_count": 4,
                "review_queue_names": ["contradictions", "stale-summaries"],
                "signal_rule_names": ["source_needs_deep_dive"],
                "resolver_rule_names": ["deep_dive_workflow"],
            },
            "items": [
                {
                    "signal_id": "signal::demo",
                    "signal_type": "source_needs_deep_dive",
                    "title": "Loose Source",
                    "detail": "Processed source note has no derived deep dive yet.",
                    "source_path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                    "capture_summary": {
                        "status": "observed",
                        "summary": "Observed 1 inbound capture event but no downstream artifact yet.",
                        "captured_event_count": 1,
                        "produced_artifact_count": 0,
                    },
                    "downstream_effects": [],
                    "recommended_action": {
                        "kind": "deep_dive_workflow",
                        "label": "Create deep dive",
                        "path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                        "executable": False,
                        "resolution_kind": "focused_action",
                        "dispatch_mode": "queue_only",
                        "resolver_rule_name": "deep_dive_workflow",
                        "governance_provider_name": "research_governance",
                        "governance_provider_pack": "research-tech",
                        "safe_to_run": True,
                    },
                    "impact_summary": {
                        "impact_status": "waiting",
                        "lifecycle_stage": "queued",
                        "impact_label": "Waiting on queue execution",
                        "impact_detail": "A queueable action exists and is currently waiting to run.",
                        "produced_artifact_count": 0,
                    },
                    "payload": {
                        "brain_first_lookup": {
                            "decision": "reuse_existing",
                            "status": "existing_links_found",
                            "existing_object_count": 1,
                        },
                        "backlink_expectation": {
                            "status": "satisfied",
                            "source_note_paths": ["50-Inbox/03-Processed/Loose Source.md"],
                        },
                    },
                }
            ],
            "count": 1,
            "query": query or "",
            "signal_type": signal_type or "",
            "type_counts": {"source_needs_deep_dive": 1},
            "impact_counts": {"waiting": 1},
            "signal_type_explanations": {"source_needs_deep_dive": "demo"},
            "operator_rail": [
                {
                    "label": "Action Queue",
                    "path": "/actions",
                    "detail": "Run or inspect queued actions derived from signals.",
                }
            ],
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/signals")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Resolver: focused_action" in body
    assert "Dispatch: queue_only" in body
    assert "Rule: deep_dive_workflow" in body
    assert "Governance contract: research_governance · research-tech" in body
    assert "safe" in body
    assert "Governance Contract" in body
    assert "declared by research_governance in research-tech" in body
    assert "Next Actions" in body
    assert "Impact: Waiting on queue execution" in body
    assert (
        "Inbound capture: Observed 1 inbound capture event but no downstream artifact yet." in body
    )
    assert "Brain-first lookup: reuse_existing · existing_links_found · 1 existing objects" in body
    assert "Backlinks: satisfied · 1 source notes" in body


def test_ui_server_actions_endpoint_accepts_pack_scope(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    captured: dict[str, str | None] = {}

    def fake_build_action_queue_payload(vault_dir, *, pack_name=None, status=None, query=None):
        captured["pack_name"] = pack_name
        captured["status"] = status
        captured["query"] = query
        return {
            "screen": "actions/browser",
            "requested_pack": pack_name or "",
            "governance_contract": {
                "requested_pack": pack_name or "",
                "status": "inherited",
                "provider_pack": "research-tech",
                "provider_name": "research_governance",
                "description": "Governance contract",
                "review_queue_count": 2,
                "signal_rule_count": 3,
                "resolver_rule_count": 4,
                "review_queue_names": ["contradictions", "stale-summaries"],
                "signal_rule_names": ["source_needs_deep_dive"],
                "resolver_rule_names": ["deep_dive_workflow"],
            },
            "items": [
                {
                    "action_id": "action::demo",
                    "status": "queued",
                    "action_kind": "deep_dive_workflow",
                    "title": "Create deep dive",
                    "safe_to_run": True,
                    "resolution_kind": "focused_action",
                    "dispatch_mode": "queue_only",
                    "resolver_rule_name": "deep_dive_workflow",
                    "governance_provider_name": "research_governance",
                    "governance_provider_pack": "research-tech",
                }
            ],
            "count": 0,
            "query": query or "",
            "status": status or "",
            "status_counts": {"queued": 1},
            "queued_safe_count": 1,
            "failed_count": 0,
            "failure_buckets": {},
        }

    monkeypatch.setattr(ui_server, "build_action_queue_payload", fake_build_action_queue_payload)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/actions?pack=default-knowledge&q=alpha&status=queued")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert captured == {
        "pack_name": "default-knowledge",
        "status": "queued",
        "query": "alpha",
    }
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["governance_contract"]["status"] == "inherited"
    assert payload["governance_contract"]["provider_pack"] == "research-tech"
    assert payload["governance_contract"]["provider_name"] == "research_governance"
    assert payload["items"][0]["resolver_rule_name"] == "deep_dive_workflow"
    assert payload["items"][0]["governance_provider_name"] == "research_governance"
    assert payload["items"][0]["governance_provider_pack"] == "research-tech"


def test_ui_server_briefing_endpoint_preserves_contract_metadata(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_briefing_payload",
        lambda vault_dir, *, pack_name=None: {
            "screen": "briefing/intelligence",
            "requested_pack": pack_name or "",
            "surface_contract": {
                "surface_kind": "briefing",
                "requested_pack": pack_name or "",
                "status": "inherited",
                "provider_pack": "research-tech",
                "provider_name": "research-tech-briefing",
                "description": "Briefing surface",
            },
            "assembly_contract": {
                "recipe_name": "orientation_brief",
                "requested_pack": pack_name or "",
                "status": "inherited",
                "provider_pack": "research-tech",
                "provider_name": "orientation_brief",
                "recipe_kind": "orientation_brief",
                "description": "Orientation briefing recipe",
                "source_contract_kind": "observation_surface",
                "source_contract_name": "briefing",
                "source_provider_pack": "research-tech",
                "source_provider_name": "research-tech-briefing",
                "source_status": "inherited",
                "publish_target": "json_payload",
                "output_mode": "json",
            },
            "governance_contract": {
                "requested_pack": pack_name or "",
                "status": "inherited",
                "provider_pack": "research-tech",
                "provider_name": "research_governance",
                "description": "Governance contract",
                "review_queue_count": 2,
                "signal_rule_count": 3,
                "resolver_rule_count": 4,
                "review_queue_names": ["contradictions", "stale-summaries"],
                "signal_rule_names": ["source_needs_deep_dive"],
                "resolver_rule_names": ["deep_dive_workflow"],
            },
            "generated_at": "2026-04-17T00:00:00Z",
            "recent_signal_count": 1,
            "unresolved_issue_count": 0,
            "changed_object_count": 0,
            "active_topic_count": 0,
            "recent_signals": [],
            "unresolved_issues": [],
            "changed_objects": [],
            "active_topics": [],
            "insight_count": 0,
            "priority_item_count": 1,
            "insights": [],
            "priority_items": [
                {
                    "kind": "source_needs_deep_dive",
                    "title": "Loose Source",
                    "detail": "Processed source note has no derived deep dive yet.",
                    "signal_id": "signal::demo",
                    "recommended_action": {
                        "kind": "deep_dive_workflow",
                        "label": "Create deep dive",
                        "path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                        "executable": False,
                        "resolution_kind": "focused_action",
                        "dispatch_mode": "queue_only",
                        "resolver_rule_name": "deep_dive_workflow",
                        "governance_provider_name": "research_governance",
                        "governance_provider_pack": "research-tech",
                        "safe_to_run": True,
                    },
                }
            ],
            "first_useful_sign": None,
            "queue_summary": {
                "queued_count": 0,
                "safe_queued_count": 0,
                "running_count": 0,
                "failed_count": 0,
                "failure_buckets": {},
            },
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/briefing?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["assembly_contract"]["status"] == "inherited"
    assert payload["assembly_contract"]["provider_pack"] == "research-tech"
    assert payload["assembly_contract"]["source_provider_pack"] == "research-tech"
    assert payload["governance_contract"]["status"] == "inherited"
    assert payload["governance_contract"]["provider_pack"] == "research-tech"
    assert (
        payload["priority_items"][0]["recommended_action"]["resolver_rule_name"]
        == "deep_dive_workflow"
    )
    assert (
        payload["priority_items"][0]["recommended_action"]["governance_provider_name"]
        == "research_governance"
    )
    assert (
        payload["priority_items"][0]["recommended_action"]["governance_provider_pack"]
        == "research-tech"
    )


def test_ui_server_signals_endpoint_preserves_contract_metadata(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_signal_browser_payload",
        lambda vault_dir, *, pack_name=None, signal_type=None, query=None: {
            "screen": "signals/browser",
            "requested_pack": pack_name or "",
            "surface_contract": {
                "surface_kind": "signals",
                "requested_pack": pack_name or "",
                "status": "inherited",
                "provider_pack": "research-tech",
                "provider_name": "research-tech-signals",
                "description": "Signal browser",
            },
            "governance_contract": {
                "requested_pack": pack_name or "",
                "status": "inherited",
                "provider_pack": "research-tech",
                "provider_name": "research_governance",
                "description": "Governance contract",
                "review_queue_count": 2,
                "signal_rule_count": 3,
                "resolver_rule_count": 4,
                "review_queue_names": ["contradictions", "stale-summaries"],
                "signal_rule_names": ["source_needs_deep_dive"],
                "resolver_rule_names": ["deep_dive_workflow"],
            },
            "items": [
                {
                    "signal_id": "signal::demo",
                    "signal_type": "source_needs_deep_dive",
                    "title": "Loose Source",
                    "detail": "Processed source note has no derived deep dive yet.",
                    "source_path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                    "downstream_effects": [],
                    "recommended_action": {
                        "kind": "deep_dive_workflow",
                        "label": "Create deep dive",
                        "path": "/note?path=50-Inbox%2F03-Processed%2FLoose%20Source.md",
                        "executable": False,
                        "resolution_kind": "focused_action",
                        "dispatch_mode": "queue_only",
                        "resolver_rule_name": "deep_dive_workflow",
                        "governance_provider_name": "research_governance",
                        "governance_provider_pack": "research-tech",
                        "safe_to_run": True,
                    },
                }
            ],
            "count": 1,
            "query": query or "",
            "signal_type": signal_type or "",
            "type_counts": {"source_needs_deep_dive": 1},
            "signal_type_explanations": {"source_needs_deep_dive": "demo"},
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/signals?pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["governance_contract"]["status"] == "inherited"
    assert payload["governance_contract"]["provider_pack"] == "research-tech"
    assert payload["items"][0]["recommended_action"]["resolver_rule_name"] == "deep_dive_workflow"
    assert (
        payload["items"][0]["recommended_action"]["governance_provider_name"]
        == "research_governance"
    )
    assert payload["items"][0]["recommended_action"]["governance_provider_pack"] == "research-tech"


def test_ui_server_actions_page_preserves_pack_scope_in_shell_nav(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "build_action_queue_payload",
        lambda vault_dir, *, pack_name=None, status=None, query=None: {
            "screen": "actions/browser",
            "requested_pack": pack_name or "",
            "items": [
                {
                    "action_id": "action::demo",
                    "status": "queued",
                    "action_kind": "deep_dive_workflow",
                    "title": "Create deep dive",
                    "safe_to_run": True,
                    "handler_provider_pack": "research-tech",
                    "handler_provider_name": "deep_dive_workflow",
                    "processor_provider_pack": "research-tech",
                    "processor_provider_name": "deep_dive_workflow",
                    "source_signal_active": True,
                    "last_result_summary": "No execution result recorded yet.",
                }
            ],
            "count": 1,
            "query": query or "",
            "status": status or "",
            "status_counts": {"queued": 1},
            "queued_safe_count": 1,
            "failed_count": 0,
            "failure_buckets": {},
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/actions?pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert "name='pack' value='default-knowledge'" in body
    assert "value='/actions?pack=default-knowledge'" in body
    assert "Handler contract: deep_dive_workflow · research-tech" in body
    assert "Processor contract: deep_dive_workflow · research-tech" in body
    assert "Source signal: active" in body


def test_ui_server_can_run_next_action_via_api(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "run_next_action_queue_item",
        lambda vault_dir, *, safe_only=False, pack_name=None: {
            "ran": True,
            "safe_only": safe_only,
            "requested_pack": pack_name or "",
            "action": {
                "action_id": "action::demo",
                "action_kind": "deep_dive_workflow",
                "status": "succeeded",
            },
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/actions/run-next",
            body="",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["ran"] is True
    assert payload["action"]["status"] == "succeeded"


def test_ui_server_can_run_action_batch_via_api(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "run_action_queue",
        lambda vault_dir, *, limit, safe_only=False, pack_name=None: {
            "ran_count": 2,
            "stopped_reason": "no_queued_actions",
            "results": [],
            "limit": limit,
            "safe_only": safe_only,
            "requested_pack": pack_name or "",
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode({"limit": "5"})
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/actions/run-batch",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["ran_count"] == 2
    assert payload["limit"] == 5


def test_ui_server_can_run_safe_action_batch_via_api(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "run_action_queue",
        lambda vault_dir, *, limit, safe_only=False, pack_name=None: {
            "ran_count": 1,
            "limit": limit,
            "safe_only": safe_only,
            "requested_pack": pack_name or "",
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode({"limit": "5", "safe_only": "1"})
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/actions/run-batch",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["safe_only"] is True


def test_ui_server_can_retry_action_via_api(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "retry_action_queue_item",
        lambda vault_dir, *, action_id: {
            "retried": True,
            "action": {"action_id": action_id, "status": "queued"},
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode({"action_id": "action::demo"})
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/actions/retry",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["retried"] is True
    assert payload["action"]["status"] == "queued"


def test_ui_server_can_dismiss_action_via_api(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "dismiss_action_queue_item",
        lambda vault_dir, *, action_id: {
            "dismissed": True,
            "action": {"action_id": action_id, "status": "dismissed"},
        },
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode({"action_id": "action::demo"})
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/actions/dismiss",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["dismissed"] is True
    assert payload["action"]["status"] == "dismissed"


def test_ui_server_can_resolve_contradiction_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.runtime import VaultLayout
    from ovp_pipeline.truth_api import list_contradictions

    _seed_truth_store(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    with sqlite3.connect(layout.knowledge_db) as conn:
        contradiction_id = conn.execute("SELECT contradiction_id FROM contradictions").fetchone()[0]

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode(
            {
                "contradiction_id": contradiction_id,
                "status": "resolved_keep_positive",
                "note": "Reviewed in UI",
                "rebuild_summaries": "1",
            }
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/contradictions/resolve",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["resolved_count"] == 1
    assert payload["contradiction_ids"] == [contradiction_id]
    assert payload["rebuilt_summary_count"] == 2

    review_log = (layout.logs_dir / "review-actions.jsonl").read_text(encoding="utf-8").splitlines()
    latest_review = json.loads(review_log[-1])
    contradiction = next(
        item
        for item in list_contradictions(temp_vault, status="resolved")
        if item["contradiction_id"] == contradiction_id
    )
    assert contradiction["status"] == "resolved_keep_positive"
    assert contradiction["resolution_note"] == "Reviewed in UI"
    assert latest_review["event_type"] == "ui_contradictions_resolved"


def test_ui_server_can_bulk_resolve_contradictions_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.runtime import VaultLayout

    _seed_truth_store(temp_vault)
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-13
---

# Delta

Delta supports local-first execution.
""",
        encoding="utf-8",
    )
    delta_conflict = temp_vault / "10-Knowledge" / "Evergreen" / "Delta Conflict.md"
    delta_conflict.write_text(
        """---
note_id: delta-conflict
title: Delta Conflict
type: evergreen
date: 2026-04-13
---

# Delta Conflict

Delta does not support local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    with sqlite3.connect(layout.knowledge_db) as conn:
        contradiction_ids = [
            row[0]
            for row in conn.execute(
                "SELECT contradiction_id FROM contradictions ORDER BY contradiction_id"
            ).fetchall()
        ]

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = (
            "contradiction_id="
            + "&contradiction_id=".join(contradiction_ids)
            + "&status=dismissed&note=Batch+reviewed"
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/contradictions/resolve",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["resolved_count"] == 2
    assert sorted(payload["contradiction_ids"]) == contradiction_ids


def test_ui_server_can_rebuild_stale_summary_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    note.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-10
---

# Thin Note

Thin note.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.execute(
            "UPDATE compiled_summaries SET summary_text = ? WHERE object_id = ?",
            ("Thin.", "thin-note"),
        )
        conn.commit()

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode({"object_id": "thin-note"})
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/summaries/rebuild",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["objects_rebuilt"] == 1
    assert payload["object_ids"] == ["thin-note"]
    review_log = (
        (VaultLayout.from_vault(temp_vault).logs_dir / "review-actions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    latest_review = json.loads(review_log[-1])
    assert latest_review["event_type"] == "ui_summaries_rebuilt"


def test_ui_server_can_bulk_rebuild_summaries_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    for object_id, title in (("thin-note", "Thin Note"), ("fragile-note", "Fragile Note")):
        note = temp_vault / "10-Knowledge" / "Evergreen" / f"{title}.md"
        note.write_text(
            f"""---
note_id: {object_id}
title: {title}
type: evergreen
date: 2026-04-10
---

# {title}

Thin note.
""",
            encoding="utf-8",
        )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = "object_id=thin-note&object_id=fragile-note"
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/summaries/rebuild",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["objects_rebuilt"] == 2
    assert payload["object_ids"] == ["fragile-note", "thin-note"]


def test_ui_server_research_mutation_rejects_non_research_pack(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "resolve_contradictions",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("contradiction resolver should not run")
        ),
    )

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode(
            {
                "contradiction_id": "contradiction::demo",
                "status": "dismissed",
                "pack": "media-editorial",
            }
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/contradictions/resolve",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 409
    assert payload["status"] == "unsupported_pack"
    assert payload["route"] == "/contradictions/resolve"
    assert payload["requested_pack"] == "media-editorial"


def test_ui_server_topic_and_events_endpoints_return_payloads(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/topic?id=alpha")
        topic_response = conn.getresponse()
        topic_payload = json.loads(topic_response.read().decode("utf-8"))

        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/events")
        event_response = conn.getresponse()
        event_payload = json.loads(event_response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert topic_response.status == 200
    assert topic_payload["center"]["object_id"] == "alpha"
    assert event_response.status == 200
    assert event_payload["event_count"] == 3


def test_ui_server_topic_endpoint_accepts_pack_scope(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/topic?id=alpha&pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["requested_pack"] == "default-knowledge"
    assert payload["links"]["center_object_path"] == "/object?id=alpha&pack=default-knowledge"


def test_ui_server_topic_page_preserves_pack_scope_in_shell_nav(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/topic?id=alpha&pack=default-knowledge")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert 'href="/?pack=default-knowledge"' in body
    assert 'href="/map?pack=default-knowledge"' in body
    assert 'href="/ops?pack=default-knowledge"' in body
    assert "Assembly Contract" in body
    assert "inherited from topic_overview in research-tech" in body
    assert "Source contract: wiki_view · overview/topic" in body
    assert "Source provider: default-knowledge · overview/topic" in body


def test_render_object_page_hides_research_affordances_when_pack_lacks_research_semantics(
    temp_vault, monkeypatch
):
    import ovp_pipeline.commands.ui_server as ui_server
    import ovp_pipeline.ui.view_models as view_models

    _seed_truth_store(temp_vault)
    monkeypatch.setattr(
        view_models, "_supports_research_shell", lambda pack_name=None: False, raising=False
    )
    payload = view_models.build_object_page_payload(
        temp_vault, "alpha", pack_name="default-knowledge"
    )
    monkeypatch.setattr(ui_server, "_shell_supports_research_nav", lambda requested_pack="": False)

    body = ui_server._render_object_page(payload)

    assert "Research-specific review surfaces stay hidden" in body
    assert "Related events" not in body
    assert "Resolve Open Contradictions" not in body
    assert "<h2>Evolution</h2>" not in body
    assert "<h2>Contradictions</h2>" not in body


def test_render_object_page_surfaces_reader_profile_and_source_rail(temp_vault):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.runtime import VaultLayout
    from ovp_pipeline.ui.view_models import build_object_page_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]] as a local-first execution pattern.
""",
        encoding="utf-8",
    )
    _seed_truth_store(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE objects SET object_kind = 'concept' WHERE object_id = 'alpha'")
        conn.commit()

    body = ui_server._render_object_page(build_object_page_payload(temp_vault, "alpha"))

    assert '<span class="pill">Concept</span>' in body
    assert "Alpha supports local-first execution." in body
    assert "<h2>Sources &amp; Backlinks</h2>" in body
    assert "Source Deep Dive" in body
    assert "Mentions [[alpha]] as a local-first execution pattern." in body
    assert "Related Objects" in body
    assert "Beta" in body


def test_render_object_page_surfaces_kind_specific_reader_lens(temp_vault):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.runtime import VaultLayout
    from ovp_pipeline.ui.view_models import build_object_page_payload

    _seed_truth_store(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE objects SET object_kind = 'person' WHERE object_id = 'alpha'")
        conn.commit()

    body = ui_server._render_object_page(build_object_page_payload(temp_vault, "alpha"))

    assert "<h2>Person Profile</h2>" in body
    assert "Who is this person, what are they known for" in body
    assert "<strong>Role</strong>" in body
    assert "<h2>Profile</h2>" in body
    assert "<h2>Why They Matter</h2>" in body


def test_render_topic_page_hides_research_affordances_when_pack_lacks_research_semantics(
    temp_vault, monkeypatch
):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.ui.view_models import build_topic_overview_payload

    _seed_truth_store(temp_vault)
    payload = build_topic_overview_payload(temp_vault, "alpha")
    payload["requested_pack"] = "media-editorial"
    payload["research_shell_enabled"] = False
    monkeypatch.setattr(ui_server, "_shell_supports_research_nav", lambda requested_pack="": False)

    body = ui_server._render_topic_page(payload)

    assert "Research-specific review surfaces stay hidden" in body
    assert "Related events" not in body
    assert "Review scoped contradictions" not in body
    assert "<h2>Evolution</h2>" not in body
    assert "<h2>Atlas / MOC</h2>" not in body


def test_render_topic_page_includes_production_chain_section(temp_vault):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.ui.view_models import build_topic_overview_payload

    _seed_truth_store(temp_vault)
    payload = build_topic_overview_payload(temp_vault, "alpha")

    body = ui_server._render_topic_page(payload)

    assert "<h2>Production Chain</h2>" in body
    assert (
        "Missing source notes" in body
        or "Missing deep dives" in body
        or "Missing Atlas / MOC reach" in body
    )


def test_ui_server_main_starts_server_with_requested_bind(temp_vault, capsys, monkeypatch):
    from ovp_pipeline.commands.ui_server import main

    calls = {}

    class FakeServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls["closed"] = True

    def fake_create_server(vault_dir, *, host, port):
        calls["vault_dir"] = str(vault_dir)
        calls["host"] = host
        calls["port"] = port
        return FakeServer()

    monkeypatch.setattr("ovp_pipeline.commands.ui_server.create_server", fake_create_server)
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.build_objects_index_payload",
        lambda vault_dir, *, limit, offset: {"items": []},
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.ensure_signal_ledger_synced",
        lambda vault_dir: {"signal_count": 0, "type_counts": {}},
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server._start_ui_prewarm",
        lambda vault_dir: calls.setdefault("prewarm_vault_dir", str(vault_dir)),
    )

    exit_code = main(["--vault-dir", str(temp_vault), "--host", "127.0.0.1", "--port", "9999"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {"host": "127.0.0.1", "port": 9999, "vault_dir": str(temp_vault)}
    assert calls == {
        "vault_dir": str(temp_vault),
        "host": "127.0.0.1",
        "port": 9999,
        "prewarm_vault_dir": str(temp_vault),
        "closed": True,
    }


def test_ui_server_main_can_spawn_detached_action_worker_when_enabled(
    temp_vault, capsys, monkeypatch
):
    from ovp_pipeline.commands.ui_server import main

    calls = {}

    class FakeServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls["closed"] = True

    def fake_create_server(vault_dir, *, host, port):
        calls["vault_dir"] = str(vault_dir)
        calls["host"] = host
        calls["port"] = port
        return FakeServer()

    monkeypatch.setattr("ovp_pipeline.commands.ui_server.create_server", fake_create_server)
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.build_objects_index_payload",
        lambda vault_dir, *, limit, offset: {"items": []},
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.ensure_signal_ledger_synced",
        lambda vault_dir: {"signal_count": 0, "type_counts": {}},
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server._start_ui_prewarm",
        lambda vault_dir: None,
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.subprocess.Popen",
        lambda cmd, **kwargs: calls.setdefault("worker_process", {"cmd": cmd, "kwargs": kwargs}),
    )

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--host",
            "127.0.0.1",
            "--port",
            "9999",
            "--with-action-worker",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {"host": "127.0.0.1", "port": 9999, "vault_dir": str(temp_vault)}
    assert calls["worker_process"]["cmd"][1:4] == [
        "-m",
        "ovp_pipeline.commands.run_actions",
        "--vault-dir",
    ]
    assert "--loop" in calls["worker_process"]["cmd"]
    assert calls["worker_process"]["kwargs"]["start_new_session"] is True


def test_ui_server_main_reuses_existing_signal_ledger_during_preflight(
    temp_vault, capsys, monkeypatch
):
    from ovp_pipeline.commands.ui_server import main

    calls = {}
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "signals.jsonl").write_text("", encoding="utf-8")

    class FakeServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls["closed"] = True

    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.create_server",
        lambda vault_dir, *, host, port: FakeServer(),
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.build_objects_index_payload",
        lambda vault_dir, *, limit, offset: {"items": []},
    )
    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.ensure_signal_ledger_synced",
        lambda vault_dir: (_ for _ in ()).throw(
            AssertionError("existing signal ledger should not be rebuilt during startup")
        ),
    )
    monkeypatch.setattr("ovp_pipeline.commands.ui_server._start_ui_prewarm", lambda vault_dir: None)

    exit_code = main(["--vault-dir", str(temp_vault)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["vault_dir"] == str(temp_vault)
    assert calls == {"closed": True}


def test_ui_server_main_exits_nonzero_when_preflight_fails(temp_vault, capsys, monkeypatch):
    from ovp_pipeline.commands.ui_server import main

    class FakeServer:
        def serve_forever(self):
            raise AssertionError("serve_forever should not run when preflight fails")

        def server_close(self):
            return None

    monkeypatch.setattr(
        "ovp_pipeline.commands.ui_server.create_server",
        lambda vault_dir, *, host, port: FakeServer(),
    )

    def boom(vault_dir, *, limit, offset):
        raise ValueError("broken knowledge db")

    monkeypatch.setattr("ovp_pipeline.commands.ui_server.build_objects_index_payload", boom)

    exit_code = main(["--vault-dir", str(temp_vault)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "broken knowledge db" in captured.err


def test_ui_server_module_compiles_on_python311():
    module_path = Path("src/ovp_pipeline/commands/ui_server.py")
    result = subprocess.run(
        ["python3.11", "-m", "py_compile", str(module_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _seed_candidate_for_ui(temp_vault):
    from ovp_pipeline.concept_registry import ConceptEntry, ConceptRegistry
    from ovp_pipeline.promote_candidates import write_candidate_file

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="alpha-existing",
            title="Alpha Existing",
            aliases=["Alpha Candidate"],
            definition="Existing canonical concept.",
            area="testing",
        )
    )
    candidate = registry.upsert_candidate(
        slug="alpha-candidate",
        title="Alpha Candidate",
        definition="Candidate concept awaiting review.",
        area="testing",
        aliases=["alpha draft"],
    )
    registry.save()
    write_candidate_file(temp_vault, candidate, dry_run=False)
    return candidate


def test_ui_server_candidates_endpoint_returns_payload(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_candidate_for_ui(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/candidates")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["screen"] == "candidates/browser"
    assert payload["count"] == 1
    assert payload["items"][0]["slug"] == "alpha-candidate"


def test_ui_server_candidates_endpoint_forwards_pagination_params(temp_vault, monkeypatch):
    import ovp_pipeline.commands.ui_server as ui_server
    from ovp_pipeline.commands.ui_server import create_server

    calls = []

    def fake_build_candidate_browser_payload(
        vault_dir,
        *,
        pack_name=None,
        query=None,
        limit=25,
        offset=0,
    ):
        calls.append((pack_name, query, limit, offset))
        return {
            "screen": "candidates/browser",
            "requested_pack": pack_name or "",
            "query": query or "",
            "limit": limit,
            "offset": offset,
            "count": 100,
            "status_counts": {"candidate": 100},
            "items": [],
            "operator_rail": [],
        }

    monkeypatch.setattr(ui_server, "build_candidate_browser_payload", fake_build_candidate_browser_payload)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/candidates?q=alpha&limit=7&offset=14&pack=default-knowledge")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["limit"] == 7
    assert payload["offset"] == 14
    assert calls == [("default-knowledge", "alpha", 7, 14)]


def test_ui_server_candidates_page_renders_review_controls(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server

    _seed_candidate_for_ui(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/candidates")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "Candidate Workbench" in body
    assert "Alpha Candidate" in body
    assert "Promote" in body
    assert "Merge" in body
    assert "Reject" in body


def test_ui_server_candidates_page_renders_pagination_links():
    from ovp_pipeline.commands.ui_server import _render_candidates_page

    html = _render_candidates_page(
        {
            "query": "alpha",
            "requested_pack": "default-knowledge",
            "limit": 25,
            "offset": 25,
            "count": 80,
            "status_counts": {"candidate": 80},
            "items": [],
            "operator_rail": [],
        }
    )

    assert 'href="/candidates?q=alpha&amp;limit=25&amp;offset=0&amp;pack=default-knowledge"' in html
    assert 'href="/candidates?q=alpha&amp;limit=25&amp;offset=50&amp;pack=default-knowledge"' in html


def test_ui_server_candidate_item_keeps_zero_score_and_requires_low_score_merge_target():
    from ovp_pipeline.commands.ui_server import _render_candidate_items

    html = _render_candidate_items(
        {
            "requested_pack": "research-tech",
            "items": [
                {
                    "slug": "alpha-candidate",
                    "title": "Alpha Candidate",
                    "definition": "Candidate concept awaiting review.",
                    "source_count": 1,
                    "evidence_count": 1,
                    "similar_existing": [
                        {
                            "slug": "alpha-existing",
                            "title": "Alpha Existing",
                            "score": 0.0,
                            "path": "/object?id=alpha-existing",
                        }
                    ],
                }
            ],
        }
    )

    assert "<span class='pill'>0.0</span>" in html
    assert "name='target_slug' value=''" in html


def test_ui_server_candidates_page_renders_review_warning():
    from ovp_pipeline.commands.ui_server import _render_candidates_page

    html = _render_candidates_page(
        {
            "query": "",
            "requested_pack": "",
            "count": 0,
            "status_counts": {},
            "items": [],
            "candidate_warning": "rebuild failed",
        }
    )

    assert "Review Warning" in html
    assert "rebuild failed" in html


def test_ui_server_atlas_and_cluster_pages_truncate_large_member_lists():
    from ovp_pipeline.commands.ui_server import _render_atlas_page, _render_clusters_page

    members = [
        {
            "object_id": f"obj-{index}",
            "title": f"Object {index}",
            "object_path": f"/object?id=obj-{index}",
        }
        for index in range(20)
    ]
    atlas = _render_atlas_page(
        {
            "requested_pack": "",
            "query": "",
            "limit": 20,
            "is_limited": True,
            "count": 1,
            "items": [
                {
                    "path": "00-Atlas/Demo.md",
                    "title": "Demo Atlas",
                    "member_count": 20,
                    "members": members,
                    "deep_dives": [],
                    "source_notes": [],
                    "preview_titles": [],
                }
            ],
        }
    )
    clusters = _render_clusters_page(
        {
            "requested_pack": "",
            "query": "",
            "limit": 20,
            "is_limited": True,
            "cluster_kind_counts": {"component": 1},
            "largest_cluster_size": 20,
            "model_notes": [],
            "count": 1,
            "items": [
                {
                    "detail_path": "/cluster?id=demo",
                    "display_title": "Demo Cluster",
                    "label": "Demo Cluster",
                    "cluster_kind": "component",
                    "priority_band": "active",
                    "member_count": 20,
                    "member_links": [
                        {"title": f"Object {index}", "path": f"/object?id=obj-{index}"}
                        for index in range(20)
                    ],
                    "center_object_path": "/object?id=obj-0",
                    "center_title": "Object 0",
                    "priority_reason": "demo",
                    "relation_pattern_preview": "",
                    "related_cluster_count": 0,
                    "related_cluster_preview": "",
                    "neighborhood_score": 0,
                    "next_read_title": "",
                    "top_reading_route_kind": "",
                    "reading_intent_count": 0,
                    "top_summary_bullet": "",
                }
            ],
        }
    )

    assert "Object 7" in atlas
    assert "Object 8" not in atlas
    assert "12 more" in atlas
    assert "Object 7" in clusters
    assert "Object 8" not in clusters
    assert "12 more" in clusters


def test_ui_server_can_promote_candidate_via_api(temp_vault):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.concept_registry import ConceptRegistry, STATUS_ACTIVE
    from ovp_pipeline.truth_api import list_review_actions

    _seed_candidate_for_ui(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode(
            {
                "slug": "alpha-candidate",
                "action": "promote",
                "note": "Promote from UI",
            }
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/candidates/review",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    promoted = ConceptRegistry(temp_vault).load().find_by_slug("alpha-candidate")
    assert response.status == 200
    assert payload["action"] == "promote"
    assert payload["mutation"]["action"] == "promote"
    assert payload["knowledge_index_rebuilt"] is True
    assert promoted.status == STATUS_ACTIVE
    audit = list_review_actions(temp_vault, limit=1)[0]
    assert audit["event_type"] == "ui_candidate_reviewed"
    assert audit["slug"] == "alpha-candidate"
    assert audit["status"] == "promoted"
    assert audit["note"] == "Promote from UI"


def test_ui_server_candidate_review_reports_rebuild_failure_without_disconnect(
    temp_vault, monkeypatch
):
    from ovp_pipeline.commands.ui_server import create_server
    from ovp_pipeline.concept_registry import ConceptRegistry, STATUS_ACTIVE

    _seed_candidate_for_ui(temp_vault)

    def fail_rebuild(vault_dir, *, pack_name=None):
        raise ValueError("rebuild failed")

    monkeypatch.setattr("ovp_pipeline.truth_api.rebuild_knowledge_index", fail_rebuild)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = urlencode(
            {
                "slug": "alpha-candidate",
                "action": "promote",
                "note": "Promote from UI",
            }
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/candidates/review",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            response = conn.getresponse()
        except RemoteDisconnected as exc:
            raise AssertionError("candidate review request disconnected") from exc
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    promoted = ConceptRegistry(temp_vault).load().find_by_slug("alpha-candidate")
    assert response.status == 200
    assert payload["partial_success"] is True
    assert payload["action"] == "promote"
    assert payload["mutation"]["action"] == "promote"
    assert payload["knowledge_index_rebuilt"] is False
    assert payload["knowledge_index_error"] == "rebuild failed"
    assert "candidate review applied" in payload["warning"]
    assert "candidate_warning=rebuild%20failed" in payload["next_path"]
    assert promoted.status == STATUS_ACTIVE
