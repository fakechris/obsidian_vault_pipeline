"""Phase 37 — tests for the Pulse JSONL tail and the SSE endpoint."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from ovp_pipeline.commands.ui_server import create_server
from ovp_pipeline.event_emitter import emit
from ovp_pipeline.pulse import (
    DEFAULT_LOGS,
    initial_positions,
    tail_events,
)
from ovp_pipeline.runtime import VaultLayout


# Tests must talk to a localhost server without going through any HTTP_PROXY
# the developer environment may export — build an opener with an empty proxy
# map and use it for every request below.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _make_layout(vault: Path) -> VaultLayout:
    return VaultLayout.from_vault(vault)


def test_tail_events_empty_layout_returns_no_events(temp_vault: Path) -> None:
    layout = _make_layout(temp_vault)
    events, positions = tail_events(layout)
    assert events == []
    assert all(positions[name] == 0 for name in DEFAULT_LOGS)


def test_tail_events_picks_up_emitted_events_then_drains(temp_vault: Path) -> None:
    layout = _make_layout(temp_vault)
    positions = initial_positions(layout)

    emit(temp_vault, "pipeline.jsonl", "promotion", {"slug": "alpha"}, pack="default-knowledge")
    emit(
        temp_vault,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        {"object_id": "alpha"},
        pack="default-knowledge",
    )

    events, positions = tail_events(layout, since_position=positions)
    assert [e["event_type"] for e in events] == sorted(
        ["promotion", "trusted_reuse_event"], key=lambda et: et
    ) or [e["event_type"] for e in events] in (
        ["promotion", "trusted_reuse_event"],
        ["trusted_reuse_event", "promotion"],
    )
    # Second poll with no new events returns nothing.
    drain, positions = tail_events(layout, since_position=positions)
    assert drain == []


def test_tail_events_chronological_across_files(temp_vault: Path) -> None:
    layout = _make_layout(temp_vault)
    positions = initial_positions(layout)

    # Emit interleaved across two files; the merged batch must be sorted by ts.
    emit(temp_vault, "pipeline.jsonl", "promotion", {"n": 1}, pack="p")
    emit(temp_vault, "reuse-events.jsonl", "trusted_reuse_event", {"n": 2}, pack="p")
    emit(temp_vault, "pipeline.jsonl", "promotion", {"n": 3}, pack="p")

    events, _ = tail_events(layout, since_position=positions)
    timestamps = [str(e["ts"]) for e in events]
    assert timestamps == sorted(timestamps)


def test_tail_events_picks_up_new_file_mid_session(temp_vault: Path) -> None:
    layout = _make_layout(temp_vault)
    positions = initial_positions(layout)
    # `evidence-verifications.jsonl` doesn't exist yet — its position is 0.
    assert positions["evidence-verifications.jsonl"] == 0

    emit(
        temp_vault,
        "evidence-verifications.jsonl",
        "evidence_verified",
        {"table": "claim_evidence", "key": {}, "locator": "", "content_hash": "",
         "retrieval_context": "", "status": "verified", "verified_at": "now"},
        pack="p",
    )

    events, _ = tail_events(layout, since_position=positions)
    assert any(e["event_type"] == "evidence_verified" for e in events)


def test_tail_events_skips_unterminated_trailing_line(temp_vault: Path) -> None:
    """Partial-write safety: a poll that races a writer must not advance the
    offset past an incomplete tail line; otherwise the next poll skips the
    line entirely once the writer flushes the trailing newline.

    Setup: emit one complete event, then write a half-line directly to the
    file (no newline). After the first tail call we expect the complete event
    AND the offset to point at the start of the partial tail. After we then
    flush a newline + the rest of the line, the next tail call must surface it.
    """
    layout = _make_layout(temp_vault)
    log = layout.logs_dir / "pipeline.jsonl"

    # One complete event followed by a half-written second line.
    emit(temp_vault, "pipeline.jsonl", "promotion", {"n": 1}, pack="p")
    with log.open("ab") as handle:
        handle.write(b'{"event_id":"y","ts":"2099-01-02T00:00:00Z","session_id":"s",')
        # No newline yet — this is a partial write the next poll must NOT eat.

    events, positions = tail_events(layout)
    # We see the complete first event but NOT a corrupted parse of the second.
    assert len(events) == 1
    assert events[0]["n"] == 1

    # Now finish writing the partial line + add the terminator.
    with log.open("ab") as handle:
        handle.write(b'"pack":"p","event_type":"promotion","n":2}\n')

    events, _ = tail_events(layout, since_position=positions)
    # The previously-partial line is now complete and surfaces in this poll.
    assert len(events) == 1
    assert events[0]["n"] == 2


def test_tail_events_handles_truncation(temp_vault: Path) -> None:
    layout = _make_layout(temp_vault)
    log = layout.logs_dir / "pipeline.jsonl"
    emit(temp_vault, "pipeline.jsonl", "promotion", {"n": 1}, pack="p")
    _, positions = tail_events(layout)

    # Replace the file with a single fresh line — total size shrinks.
    log.write_text(
        json.dumps(
            {
                "event_id": "x",
                "ts": "2099-01-01T00:00:00Z",
                "session_id": "s",
                "pack": "p",
                "event_type": "promotion",
                "n": 99,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    events, _ = tail_events(layout, since_position=positions)
    assert len(events) == 1
    assert events[0]["n"] == 99


# ---------------------------------------------------------------------------
# /pulse/stream SSE round-trip
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


def test_pulse_stream_round_trip(temp_vault: Path, running_server) -> None:
    # Open the SSE stream first (so initial_positions captures end-of-file at
    # this moment), then emit an event from another thread. The next poll
    # inside the SSE loop must pick it up and ship a frame back. max_polls
    # bounds the loop so urlopen.read() returns instead of hanging forever.
    url = _server_url(running_server, "/ops/pulse/stream?max_polls=8&poll_interval=0.05")

    body_holder: dict[str, str] = {}

    def consume() -> None:
        with _NO_PROXY_OPENER.open(url, timeout=10) as response:
            body_holder["body"] = response.read().decode("utf-8")

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()

    # Wait briefly for the consumer to connect & capture initial positions,
    # then emit the event we expect to see streamed.
    import time as _time

    _time.sleep(0.15)
    emit(
        temp_vault,
        "pipeline.jsonl",
        "promotion",
        {"slug": "alpha"},
        pack="default-knowledge",
    )

    consumer.join(timeout=5)
    body = body_holder.get("body", "")

    assert "event: promotion" in body
    assert '"slug": "alpha"' in body or '"slug":"alpha"' in body


def test_pulse_fragment_returns_html(temp_vault: Path, running_server) -> None:
    url = _server_url(running_server, "/ops/pulse/fragment")
    with _NO_PROXY_OPENER.open(url, timeout=5) as response:
        body = response.read().decode("utf-8")
    assert "EventSource('/ops/pulse/stream')" in body
    # Post-PR#189 the pulse fragment renders through the
    # ``.live-feed`` kit primitive (ovp-pages.css) instead of a
    # ``.pulse`` class — same content, kit-faithful CSS.
    assert "<section class='live-feed'>" in body


def test_pulse_page_renders(temp_vault: Path, running_server) -> None:
    url = _server_url(running_server, "/ops/pulse")
    with _NO_PROXY_OPENER.open(url, timeout=5) as response:
        body = response.read().decode("utf-8")
    assert "<title>Pulse</title>" in body
    # Post-PR#189 the pulse fragment renders through the
    # ``.live-feed`` kit primitive (ovp-pages.css) instead of a
    # ``.pulse`` class — same content, kit-faithful CSS.
    assert "<section class='live-feed'>" in body
