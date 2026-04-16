from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

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
def test_ui_server_root_serves_html_shell(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert "OpenClaw Truth UI" in body
    assert "/api/objects" in body


def test_ui_server_objects_endpoint_returns_json(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Agent Harness_深度解读.md"
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


def test_ui_server_object_endpoint_returns_detail_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_contradictions_endpoint_returns_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_signals_endpoint_returns_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_evolution_endpoint_returns_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_clusters_endpoint_returns_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_cluster_detail_endpoint_returns_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.truth_api import list_graph_clusters

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
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.truth_api import list_graph_clusters

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
    shared_source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
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


def test_ui_server_clusters_endpoint_includes_related_cluster_summary(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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
    shared_source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
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


def test_ui_server_can_accept_evolution_candidate_via_api(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.truth_api import list_evolution_candidates

    _seed_truth_store(temp_vault)
    candidate = next(item for item in list_evolution_candidates(temp_vault) if item["link_type"] == "challenges")
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
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.truth_api import record_review_action

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


def test_ui_server_can_enqueue_signal_action_via_api(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.truth_api import list_signals

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


def test_ui_server_can_run_next_action_via_api(temp_vault, monkeypatch):
    import openclaw_pipeline.commands.ui_server as ui_server
    from openclaw_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "run_next_action_queue_item",
        lambda vault_dir, *, safe_only=False: {
            "ran": True,
            "safe_only": safe_only,
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
    import openclaw_pipeline.commands.ui_server as ui_server
    from openclaw_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "run_action_queue",
        lambda vault_dir, *, limit, safe_only=False: {
            "ran_count": 2,
            "stopped_reason": "no_queued_actions",
            "results": [],
            "limit": limit,
            "safe_only": safe_only,
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
    import openclaw_pipeline.commands.ui_server as ui_server
    from openclaw_pipeline.commands.ui_server import create_server

    monkeypatch.setattr(
        ui_server,
        "run_action_queue",
        lambda vault_dir, *, limit, safe_only=False: {
            "ran_count": 1,
            "limit": limit,
            "safe_only": safe_only,
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
    import openclaw_pipeline.commands.ui_server as ui_server
    from openclaw_pipeline.commands.ui_server import create_server

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
    import openclaw_pipeline.commands.ui_server as ui_server
    from openclaw_pipeline.commands.ui_server import create_server

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
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.runtime import VaultLayout
    from openclaw_pipeline.truth_api import list_contradictions

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
    contradiction = next(item for item in list_contradictions(temp_vault, status="resolved") if item["contradiction_id"] == contradiction_id)
    assert contradiction["status"] == "resolved_keep_positive"
    assert contradiction["resolution_note"] == "Reviewed in UI"
    assert latest_review["event_type"] == "ui_contradictions_resolved"


def test_ui_server_can_bulk_resolve_contradictions_via_api(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.runtime import VaultLayout

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
        contradiction_ids = [row[0] for row in conn.execute("SELECT contradiction_id FROM contradictions ORDER BY contradiction_id").fetchall()]

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = "contradiction_id=" + "&contradiction_id=".join(contradiction_ids) + "&status=dismissed&note=Batch+reviewed"
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
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.runtime import VaultLayout

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
        VaultLayout.from_vault(temp_vault).logs_dir / "review-actions.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    latest_review = json.loads(review_log[-1])
    assert latest_review["event_type"] == "ui_summaries_rebuilt"


def test_ui_server_can_bulk_rebuild_summaries_via_api(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_topic_and_events_endpoints_return_payloads(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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


def test_ui_server_main_starts_server_with_requested_bind(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands.ui_server import main

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

    monkeypatch.setattr("openclaw_pipeline.commands.ui_server.create_server", fake_create_server)
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server.build_objects_index_payload",
        lambda vault_dir, *, limit, offset: {"items": []},
    )
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server.ensure_signal_ledger_synced",
        lambda vault_dir: {"signal_count": 0, "type_counts": {}},
    )
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server._start_ui_prewarm",
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


def test_ui_server_main_can_spawn_detached_action_worker_when_enabled(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands.ui_server import main

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

    monkeypatch.setattr("openclaw_pipeline.commands.ui_server.create_server", fake_create_server)
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server.build_objects_index_payload",
        lambda vault_dir, *, limit, offset: {"items": []},
    )
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server.ensure_signal_ledger_synced",
        lambda vault_dir: {"signal_count": 0, "type_counts": {}},
    )
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server._start_ui_prewarm",
        lambda vault_dir: None,
    )
    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server.subprocess.Popen",
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
    assert calls["worker_process"]["cmd"][1:4] == ["-m", "openclaw_pipeline.commands.run_actions", "--vault-dir"]
    assert "--loop" in calls["worker_process"]["cmd"]
    assert calls["worker_process"]["kwargs"]["start_new_session"] is True


def test_ui_server_main_exits_nonzero_when_preflight_fails(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands.ui_server import main

    class FakeServer:
        def serve_forever(self):
            raise AssertionError("serve_forever should not run when preflight fails")

        def server_close(self):
            return None

    monkeypatch.setattr(
        "openclaw_pipeline.commands.ui_server.create_server",
        lambda vault_dir, *, host, port: FakeServer(),
    )

    def boom(vault_dir, *, limit, offset):
        raise ValueError("broken knowledge db")

    monkeypatch.setattr("openclaw_pipeline.commands.ui_server.build_objects_index_payload", boom)

    exit_code = main(["--vault-dir", str(temp_vault)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "broken knowledge db" in captured.err


def test_ui_server_module_compiles_on_python311():
    module_path = Path("src/openclaw_pipeline/commands/ui_server.py")
    result = subprocess.run(
        ["python3.11", "-m", "py_compile", str(module_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
