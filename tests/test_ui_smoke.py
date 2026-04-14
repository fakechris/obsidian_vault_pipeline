from __future__ import annotations

import sqlite3
import threading
from http.client import HTTPConnection

from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
from openclaw_pipeline.runtime import VaultLayout


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


def _resolve_all_contradictions(temp_vault):
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE contradictions
            SET status = 'resolved',
                resolution_note = 'reviewed',
                resolved_at = '2026-04-14T00:00:00Z'
            """
        )
        conn.commit()
def _get(port: int, path: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    return response.status, response.read().decode("utf-8")


def test_ui_smoke_pages_render_truth_views(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        objects_status, objects_body = _get(port, "/objects")
        object_status, object_body = _get(port, "/object?id=alpha")
        topic_status, topic_body = _get(port, "/topic?id=alpha")
        events_status, events_body = _get(port, "/events")
        contradictions_status, contradictions_body = _get(port, "/contradictions")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert objects_status == 200
    assert "Objects" in objects_body
    assert "Alpha" in objects_body

    assert object_status == 200
    assert "Object: Alpha" in object_body
    assert "Relations" in object_body
    assert "Beta" in object_body

    assert topic_status == 200
    assert "Topic: Alpha" in topic_body
    assert "Neighbors" in topic_body

    assert events_status == 200
    assert "Event Dossier" in events_body
    assert "2026-04-13" in events_body

    assert contradictions_status == 200
    assert "Contradictions" in contradictions_body
    assert "alpha" in contradictions_body


def test_ui_root_dashboard_renders_db_summary(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root_status, root_body = _get(port, "/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert root_status == 200
    assert "Objects Indexed" in root_body
    assert "Contradictions Open" in root_body
    assert "Recent Events" in root_body
    assert "Alpha" in root_body


def test_ui_objects_page_filters_by_query(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/objects?q=bet")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Beta" in body
    assert "Alpha" not in body


def test_ui_contradictions_page_filters_by_status(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    _resolve_all_contradictions(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/contradictions?status=resolved")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "resolved" in body
    assert "<span class='pill'>resolved</span>" in body
    assert "<span class='pill'>open</span>" not in body


def test_ui_events_page_filters_by_query(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/events?q=beta")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Beta" in body
    assert "Alpha" not in body
