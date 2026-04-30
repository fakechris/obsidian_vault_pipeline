"""
Pytest fixtures for ovp_pipeline tests.
"""

import pytest
import tempfile
import json
import threading
from pathlib import Path
from http.client import HTTPConnection
from datetime import datetime


@pytest.fixture
def temp_vault(tmp_path):
    """Create a temporary vault structure for testing."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Create standard directory structure
    (vault / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault / "10-Knowledge" / "Evergreen" / "_Candidates").mkdir(parents=True)
    (vault / "20-Areas" / "AI-Research" / "Topics").mkdir(parents=True)
    (vault / "60-Logs" / "link-resolution").mkdir(parents=True)
    (vault / "60-Logs" / "migration-reports").mkdir(parents=True)

    return vault


@pytest.fixture
def fetch_ui():
    def _fetch(temp_vault, path: str) -> tuple[int, str, str]:
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
            content_type = response.getheader("Content-Type") or ""
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        return response.status, body, content_type

    return _fetch


@pytest.fixture
def post_ui():
    def _post(temp_vault, path: str, body: str) -> tuple[int, str]:
        from ovp_pipeline.commands.ui_server import create_server

        server = create_server(temp_vault, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response = conn.getresponse()
            payload = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        return response.status, payload

    return _post


@pytest.fixture
def seed_hot_path_vault():
    def _seed(temp_vault) -> None:
        from ovp_pipeline.knowledge_index import rebuild_knowledge_index

        note = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
        note.write_text(
            """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-30
---

# Alpha

Alpha supports reader-first local knowledge reuse.
""",
            encoding="utf-8",
        )
        raw_dir = temp_vault / "00-Capture" / "Raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "heavy.pdf").write_bytes(b"%PDF-1.4 sentinel")
        (raw_dir / "heavy.docx").write_bytes(b"PK sentinel")
        rebuild_knowledge_index(temp_vault)

    return _seed


@pytest.fixture
def sample_evergreen_files(temp_vault):
    """Create sample Evergreen files for testing."""
    evergreen_dir = temp_vault / "10-Knowledge" / "Evergreen"

    files = [
        {
            "name": "DCF-Valuation.md",
            "content": '''---
title: "DCF Valuation"
type: evergreen
date: 2026-01-01
tags: [evergreen, investing]
aliases: ["DCF估值", "折现现金流估值"]
---

# DCF Valuation

> **定义**: 基于未来现金流贴现估算企业内在价值的方法。
'''
        },
        {
            "name": "WACC.md",
            "content": '''---
title: "WACC"
type: evergreen
date: 2026-01-01
tags: [evergreen, investing]
aliases: ["加权平均资本成本"]
---

# WACC

> **定义**: 加权平均资本成本。
'''
        },
        {
            "name": "AI-Agent.md",
            "content": '''---
title: "AI Agent"
type: evergreen
date: 2026-01-01
tags: [evergreen, AI]
aliases: ["AI-Agent", "Agent"]
---

# AI Agent

> **定义**: 能够感知环境、自主决策并采取行动的AI系统。
'''
        },
    ]

    for f in files:
        (evergreen_dir / f["name"]).write_text(f["content"], encoding="utf-8")

    return evergreen_dir


@pytest.fixture
def sample_article(temp_vault):
    """Create a sample article for testing."""
    article_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics"
    article_content = '''---
title: "Sample Article"
type: article
date: 2026-04-01
author: Test
source: test
---

# Sample Article

这是一篇关于DCF估值的深度解读。

文章中提到了 [[DCF估值]] 和 [[WACC]] 的概念。

还提到了 [[AI-Agent]] 和 [[Some-New-Concept]]。
'''

    article_path = article_dir / "2026-04-01_Sample_深度解读.md"
    article_path.parent.mkdir(parents=True, exist_ok=True)
    article_path.write_text(article_content, encoding="utf-8")

    return article_path
