"""Phase 38 Stage C — /explore reviewer surface + agent-decisions SSE."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from ovp_pipeline.commands.ui_server import (
    _render_explore_fragment,
    _render_explore_page,
    create_server,
)
from ovp_pipeline.runtime import VaultLayout

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# Pure renderer tests
# ---------------------------------------------------------------------------


def test_render_explore_page_includes_three_panes() -> None:
    html = _render_explore_page(object_id="alpha")
    assert "id='pane-canvas'" in html
    assert "id='pane-synth'" in html
    assert "agent-feed" in html  # the timeline pane is the SSE fragment


def test_render_explore_page_threads_object_id_into_canvas_and_synthesis() -> None:
    html = _render_explore_page(object_id="alpha")
    assert "/object/fragment?id=alpha" in html
    # Both canvas and synthesis iframes resolve to the object fragment.
    assert html.count("/object/fragment?id=alpha") >= 2


def test_render_explore_page_falls_back_when_no_object_selected() -> None:
    html = _render_explore_page(object_id="")
    # Without an object id, both iframes default to /objects.
    assert "id='pane-canvas' src='/objects'" in html
    assert "id='pane-synth' src='/objects'" in html


def test_render_explore_fragment_targets_explore_stream() -> None:
    fragment = _render_explore_fragment("alpha")
    # The SSE source must include the object_id query so the server can
    # later (Phase 38+) scope decisions; today it just round-trips.
    assert "EventSource('/explore/stream?object_id=alpha')" in fragment
    assert "agent_decision" in fragment


# ---------------------------------------------------------------------------
# Live HTTP route + SSE round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
def running_server(temp_vault: Path):
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _server_url(server, path: str) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}{path}"


def _fetch(server, path: str) -> str:
    with _NO_PROXY_OPENER.open(_server_url(server, path), timeout=5) as resp:
        assert resp.status == 200
        return resp.read().decode("utf-8")


def test_explore_route_returns_three_pane_html(running_server) -> None:
    body = _fetch(running_server, "/explore?object_id=alpha")
    assert "<title>Explore</title>" in body
    assert "id='pane-canvas'" in body
    assert "id='pane-synth'" in body
    assert "agent-feed" in body


def test_explore_navbar_link_present(running_server) -> None:
    home = _fetch(running_server, "/")
    assert 'href="/map"' in home


def test_explore_stream_round_trips_one_synthetic_event(temp_vault: Path, running_server) -> None:
    """The SSE handler captures byte offsets at connect time, then any new
    line appended to ``agent-decisions.jsonl`` becomes one ``agent_decision``
    SSE frame on the next poll."""
    url = _server_url(running_server, "/explore/stream?max_polls=8&poll_interval=0.05")
    body_holder: dict[str, str] = {}

    def consume() -> None:
        with _NO_PROXY_OPENER.open(url, timeout=10) as response:
            body_holder["body"] = response.read().decode("utf-8")

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()

    time.sleep(0.15)
    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    decision_log = layout.logs_dir / "agent-decisions.jsonl"
    decision_log.write_text(
        json.dumps(
            {
                "ts": "2026-04-24T12:00:00Z",
                "tool": "graph_neighborhood",
                "arguments": {"object_id": "alpha", "hop": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    consumer.join(timeout=5)
    body = body_holder.get("body", "")
    assert "event: agent_decision" in body
    assert "graph_neighborhood" in body
