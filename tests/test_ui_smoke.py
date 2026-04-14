from __future__ import annotations

import threading
from http.client import HTTPConnection

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
