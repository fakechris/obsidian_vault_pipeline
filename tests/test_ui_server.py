from __future__ import annotations

import json
import threading
from contextlib import closing
from http.client import HTTPConnection
from socket import socket

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


def _free_port() -> int:
    with closing(socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_ui_server_root_serves_html_shell(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    port = _free_port()
    server = create_server(temp_vault, host="127.0.0.1", port=port)
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
    port = _free_port()
    server = create_server(temp_vault, host="127.0.0.1", port=port)
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


def test_ui_server_object_endpoint_returns_detail_payload(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    port = _free_port()
    server = create_server(temp_vault, host="127.0.0.1", port=port)
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
    port = _free_port()
    server = create_server(temp_vault, host="127.0.0.1", port=port)
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


def test_ui_server_topic_and_events_endpoints_return_payloads(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    port = _free_port()
    server = create_server(temp_vault, host="127.0.0.1", port=port)
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

    exit_code = main(["--vault-dir", str(temp_vault), "--host", "127.0.0.1", "--port", "9999"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {"host": "127.0.0.1", "port": 9999, "vault_dir": str(temp_vault)}
    assert calls == {
        "vault_dir": str(temp_vault),
        "host": "127.0.0.1",
        "port": 9999,
        "closed": True,
    }
