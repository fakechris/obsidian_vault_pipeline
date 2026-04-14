from __future__ import annotations

import sqlite3
import threading
from http.client import HTTPConnection
from urllib.parse import quote

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

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
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
    assert '/topic?id=alpha' in object_body
    assert '/events?q=alpha' in object_body
    assert '/contradictions?q=alpha' in object_body
    assert 'href="#claims"' in object_body
    assert "Source Slug" in object_body
    assert "Evergreen Markdown" in object_body
    assert "10-Knowledge/Evergreen/Alpha.md" in object_body
    assert "/private/" not in object_body
    assert "Source Deep Dive" in object_body
    assert "Atlas Index" in object_body
    assert f"/note?path={quote('10-Knowledge/Evergreen/Alpha.md', safe='')}" in object_body
    assert f"/note?path={quote('20-Areas/Tools/Topics/2026-04/Source Deep Dive_深度解读.md', safe='')}" in object_body
    assert f"/note?path={quote('10-Knowledge/Atlas/Atlas-Index.md', safe='')}" in object_body

    assert topic_status == 200
    assert "Topic: Alpha" in topic_body
    assert "Neighbors" in topic_body
    assert '/object?id=alpha' in topic_body
    assert '/events?q=alpha' in topic_body
    assert "Center Summary" in topic_body
    assert "Atlas / MOC" in topic_body

    assert events_status == 200
    assert "Event Dossier" in events_body
    assert "2026-04-13" in events_body
    assert 'id="date-2026-04-13"' in events_body
    assert "timeline-oriented" in events_body
    assert "page_date -" not in events_body

    assert contradictions_status == 200
    assert "Contradictions" in contradictions_body
    assert "alpha" in contradictions_body
    assert '/object?id=alpha' in contradictions_body


def test_ui_note_page_renders_markdown_note(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

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

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    beta = temp_vault / "10-Knowledge" / "Evergreen" / "Beta.md"
    beta.write_text(
        """---
note_id: beta
title: Beta
type: evergreen
date: 2026-04-13
---

# Beta
""",
        encoding="utf-8",
    )
    note = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    note.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.

- First point
- Second point

## 关联概念

[[beta]]

📚 来源
[[Source Deep Dive_深度解读]]

| Name | Value |
| --- | --- |
| mode | local-first |

```text
raw block
```
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, f"/note?path={quote('10-Knowledge/Evergreen/Alpha.md', safe='')}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Markdown Note" in body
    assert "Frontmatter" in body
    assert "published" not in body
    assert "Alpha supports local-first execution." in body
    assert "<li>First point</li>" in body
    assert '/object?id=beta' in body
    assert f'/note?path={quote("20-Areas/Tools/Topics/2026-04/Source Deep Dive_深度解读.md", safe="")}' in body
    assert "<table>" in body
    assert "raw block" in body
    assert "language-text" in body


def test_ui_note_page_formats_fenced_yaml_frontmatter(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    note = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Fenced Source_深度解读.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """```yaml
---
title: "Fenced Source"
source: "https://example.com/post"
author: "@author"
date: "2026-01-28"
type: "tutorial"
tags: ["AI-Agent", "workflow"]
status: "published"
---
```

## Summary

Body paragraph.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, f"/note?path={quote('20-Areas/AI-Research/Topics/2026-04/Fenced Source_深度解读.md', safe='')}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Frontmatter" in body
    assert "https://example.com/post" in body
    assert 'href="https://example.com/post"' in body
    assert "published" in body
    assert "```yaml" not in body
    assert "Body paragraph." in body


def test_ui_note_page_normalizes_related_knowledge_links(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    prompt = temp_vault / "10-Knowledge" / "Evergreen" / "Prompt Engineering.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        """---
note_id: prompt-engineering
title: Prompt Engineering
type: evergreen
date: 2026-04-13
---

# Prompt Engineering
""",
        encoding="utf-8",
    )
    note = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Related Links_深度解读.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """## 关联知识

- [[ai-agent|AI-Agent]] — explanation
- Prompt-Engineering — explanation
- AI-Workflow — explanation
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, f"/note?path={quote('20-Areas/AI-Research/Topics/2026-04/Related Links_深度解读.md', safe='')}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "[[ai-agent|AI-Agent]]" not in body
    assert '🔍 AI-Agent' in body
    assert '🎯 Prompt-Engineering' in body
    assert '🔍 AI-Workflow' in body
    assert '/objects?q=ai-agent' in body
    assert '/object?id=prompt-engineering' in body
    assert '/objects?q=AI-Workflow' in body


def test_ui_note_page_smart_renders_reference_tables_and_keywords(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    note = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Repo Links_深度解读.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """```yaml
---
title: "Repo Links"
source: "https://github.com/example/repo"
github: "https://github.com/example/repo"
---
```

## 13. 页脚

```
┌────────────────────────────────────┐
│ 参考链接                           │
├────────────────────────────────────┤
│ GitHub 仓库 │ https://github.com/example/repo │
├────────────────────────────────────┤
│ 打包文档 │ docs/RELEASING.md │
└────────────────────────────────────┘
```

**关键词**：OpenCove, Claude Code
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, f"/note?path={quote('20-Areas/Tools/Topics/2026-04/Repo Links_深度解读.md', safe='')}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert '<table>' in body
    assert 'href="https://github.com/example/repo"' in body
    assert 'href="https://github.com/example/repo/blob/main/docs/RELEASING.md"' in body
    assert '/objects?q=OpenCove' in body
    assert '/objects?q=Claude%20Code' in body


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


def test_ui_contradictions_page_filters_by_query(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/contradictions?q=alp")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "alpha" in body


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


def test_ui_smoke_atlas_and_deep_dive_pages_render_bridge_views(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
date: 2026-04-13
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
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
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        atlas_status, atlas_body = _get(port, "/atlas")
        derivations_status, derivations_body = _get(port, "/deep-dives")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert atlas_status == 200
    assert "Atlas / MOC Browser" in atlas_body
    assert "Atlas Index" in atlas_body
    assert "Alpha" in atlas_body

    assert derivations_status == 200
    assert "Deep Dive Derivations" in derivations_body
    assert "Deep Dive" in derivations_body
    assert "Alpha" in derivations_body
