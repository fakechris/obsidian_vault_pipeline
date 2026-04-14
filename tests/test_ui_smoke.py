from __future__ import annotations

import sqlite3
import threading
from http.client import HTTPConnection
from urllib.parse import quote, urlencode

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


def _post(port: int, path: str, fields: dict[str, str]) -> tuple[int, str, dict[str, str]]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = urlencode(fields)
    conn.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response = conn.getresponse()
    headers = {key.lower(): value for key, value in response.getheaders()}
    return response.status, response.read().decode("utf-8"), headers


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
    assert "Review Context" in object_body
    assert "Open contradictions" in object_body
    assert "/summaries?q=alpha" in object_body
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
    assert "Review Context" in topic_body
    assert "/summaries?q=alpha" in topic_body

    assert events_status == 200
    assert "Event Dossier" in events_body
    assert "2026-04-13" in events_body
    assert 'id="date-2026-04-13"' in events_body
    assert "timeline-oriented" in events_body
    assert "Dated Note" in events_body
    assert "Model Notes" in events_body
    assert "Review Context" in events_body
    assert "/summaries?q=alpha" in events_body
    assert "page_date -" not in events_body
    assert "Source Deep Dive" in events_body
    assert "Atlas Index" in events_body
    assert f"/note?path={quote('20-Areas/Tools/Topics/2026-04/Source Deep Dive_深度解读.md', safe='')}" in events_body
    assert f"/note?path={quote('10-Knowledge/Atlas/Atlas-Index.md', safe='')}" in events_body

    assert contradictions_status == 200
    assert "Contradictions" in contradictions_body
    assert "alpha" in contradictions_body
    assert '/object?id=alpha' in contradictions_body
    assert "Resolve Selected" in contradictions_body
    assert "Source Deep Dive" in contradictions_body
    assert "Atlas Index" in contradictions_body
    assert "Detection Notes" in contradictions_body


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
    assert '/search?q=ai-agent' in body
    assert '/object?id=prompt-engineering' in body
    assert '/search?q=AI-Workflow' in body


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
    assert '/search?q=OpenCove' in body
    assert '/search?q=Claude%20Code' in body


def test_ui_note_page_shows_original_source_note_for_deep_dive(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "2026-04-01_The_Harness_Wars_Begin.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
---

Processed source note.
""",
        encoding="utf-8",
    )
    note = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "2026-04-09_The Harness Wars Begin_深度解读.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """```yaml
---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
date: "2026-04-09"
type: "ai"
---
```

# One-liner
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '\n'.join(
            [
                '{"event_type":"article_processed","file":"2026-04-01_The_Harness_Wars_Begin.md","output":"'
                + str(note)
                + '"}',
                '{"event_type":"source_archived_to_processed","source":"'
                + str(temp_vault / "50-Inbox" / "02-Processing" / "2026-04-01_The_Harness_Wars_Begin.md")
                + '","archived":"'
                + str(processed)
                + '"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(
            port,
            f"/note?path={quote('20-Areas/AI-Research/Topics/2026-04/2026-04-09_The Harness Wars Begin_深度解读.md', safe='')}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Original Source Note" in body
    assert f'/note?path={quote("50-Inbox/03-Processed/2026-04/2026-04-01_The_Harness_Wars_Begin.md", safe="")}' in body


def test_ui_note_page_shows_derived_deep_dive_for_processed_source(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "2026-04-01_The_Harness_Wars_Begin.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
---

Processed source note.
""",
        encoding="utf-8",
    )
    note = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "2026-04-09_The Harness Wars Begin_深度解读.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """```yaml
---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
date: "2026-04-09"
type: "ai"
---
```

# One-liner
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '{"event_type":"article_processed","file":"2026-04-01_The_Harness_Wars_Begin.md","output":"'
        + str(note)
        + '"}\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(
            port,
            f"/note?path={quote('50-Inbox/03-Processed/2026-04/2026-04-01_The_Harness_Wars_Begin.md', safe='')}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Derived Deep Dives" in body
    assert f'/note?path={quote("20-Areas/AI-Research/Topics/2026-04/2026-04-09_The Harness Wars Begin_深度解读.md", safe="")}' in body


def test_ui_note_page_rewrites_local_images_to_asset_route(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    asset = temp_vault / "50-Inbox" / "01-Raw" / "attachments" / "2026-04" / "sample.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(
        bytes.fromhex(
            "89504E470D0A1A0A0000000D4948445200000001000000010802000000907753DE0000000C49444154789C6360000000020001E221BC330000000049454E44AE426082"
        )
    )
    note = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Images.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "![Image](50-Inbox/01-Raw/attachments/2026-04/sample.png)\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(
            port,
            f"/note?path={quote('50-Inbox/03-Processed/2026-04/Images.md', safe='')}",
        )
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            f"/asset?path={quote('50-Inbox/01-Raw/attachments/2026-04/sample.png', safe='')}",
        )
        response = conn.getresponse()
        asset_status = response.status
        asset_body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "/asset?path=50-Inbox%2F01-Raw%2Fattachments%2F2026-04%2Fsample.png" in body
    assert asset_status == 200
    assert asset_body.startswith(b"\x89PNG")


def test_ui_deep_dive_browser_uses_promoted_objects_not_incidental_mentions(temp_vault):
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
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Weekly Build_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: weekly-build
title: Weekly Build
type: deep_dive
date: 2026-04-13
---

# Weekly Build

- [[prompt-engineering]] — mentioned as related knowledge
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/deep-dives")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Weekly Build" in body
    assert "0 derived objects" in body
    assert "Prompt Engineering" not in body


def test_ui_search_page_combines_objects_and_notes(temp_vault):
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
        status, body = _get(port, "/search?q=alpha")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Search" in body
    assert "Objects" in body
    assert "Notes" in body
    assert "/object?id=alpha" in body
    assert f'/note?path={quote("20-Areas/AI-Research/Topics/2026-04/Agent Harness_深度解读.md", safe="")}' in body


def test_ui_root_dashboard_renders_db_summary(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    thin = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    thin.write_text(
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
        root_status, root_body = _get(port, "/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert root_status == 200
    assert "Objects Indexed" in root_body
    assert "Contradictions Open" in root_body
    assert "Recent Events" in root_body
    assert "Stale Summaries" in root_body
    assert "Alpha" in root_body
    assert "Thin Note" in root_body
    assert "/summaries" in root_body


def test_ui_contradictions_and_summaries_support_batch_actions(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server

    _seed_truth_store(temp_vault)
    thin = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    thin.write_text(
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
        contradictions_status, contradictions_body = _get(port, "/contradictions")
        summaries_status, summaries_body = _get(port, "/summaries")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert contradictions_status == 200
    assert "Resolve Selected" in contradictions_body
    assert "name='contradiction_id'" in contradictions_body
    assert summaries_status == 200
    assert "Rebuild Selected" in summaries_body
    assert "name='object_id'" in summaries_body


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


def test_ui_contradictions_empty_state_explains_heuristic_limit(temp_vault):
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
    rebuild_knowledge_index(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/contradictions")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Zero results usually means the current heuristic did not detect a conflict" in body


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


def test_ui_contradictions_page_can_resolve_item(temp_vault):
    from openclaw_pipeline.commands.ui_server import create_server
    from openclaw_pipeline.runtime import VaultLayout

    _seed_truth_store(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    with sqlite3.connect(layout.knowledge_db) as conn:
        contradiction_id = conn.execute("SELECT contradiction_id FROM contradictions").fetchone()[0]

    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _body, headers = _post(
            port,
            "/contradictions/resolve",
            {
                "contradiction_id": contradiction_id,
                "status": "resolved_keep_positive",
                "note": "Reviewed in browser",
                "rebuild_summaries": "1",
            },
        )
        page_status, page_body = _get(port, "/contradictions?status=resolved")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 303
    assert headers["location"] == "/contradictions?status=resolved"
    assert page_status == 200
    assert "resolved_keep_positive" in page_body
    assert "Reviewed in browser" in page_body


def test_ui_summaries_page_can_rebuild_item(temp_vault):
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
        status, before_body = _get(port, "/summaries")
        rebuild_status, _body, headers = _post(
            port,
            "/summaries/rebuild",
            {"object_id": "thin-note"},
        )
        after_status, after_body = _get(port, "/summaries")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert "Stale Summaries" in before_body
    assert "thin-note" in before_body
    assert rebuild_status == 303
    assert headers["location"] == "/summaries"
    assert after_status == 200
    assert "Thin note." in after_body


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
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"alpha"}}\n',
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
    assert "1 objects" in atlas_body
    assert f"/note?path={quote('10-Knowledge/Atlas/Atlas-Index.md', safe='')}" in atlas_body

    assert derivations_status == 200
    assert "Deep Dive Derivations" in derivations_body
    assert "Deep Dive" in derivations_body
    assert "Alpha" in derivations_body
    assert "1 derived objects" in derivations_body
    assert (
        f"/note?path={quote('20-Areas/Tools/Topics/2026-04/Deep Dive_深度解读.md', safe='')}"
        in derivations_body
    )
