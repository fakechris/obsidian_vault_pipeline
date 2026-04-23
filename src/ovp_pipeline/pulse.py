"""Phase 37 — Pulse: poll-based tail of the JSONL event logs.

Phases 32–36 already declared a closed ``event_type`` vocabulary in
:mod:`event_emitter`; Pulse is its first real-time consumer. The Workbench
shell embeds Pulse in its bottom pane and any future UI (or external watcher)
can consume the same stream over SSE.

Design decisions:

* **Polling, not inotify.** Human-scale traffic; zero new deps; no platform
  forks. The SSE handler in ``ui_server`` sleeps ~1s between polls.
* **Byte offsets per file**, not (file, line_no), so a file that is rewritten
  out-of-band (e.g. log rotation in the future) is detected by truncation
  rather than line drift.
* **Open-by-name on every poll.** A new log file appearing mid-session
  (``evidence-verifications.jsonl`` after the first ``ovp-evidence verify``)
  enters the position dict at offset 0 on its first sighting and is read in
  full from then on.
"""

from __future__ import annotations

import json
from typing import Iterable

from .runtime import VaultLayout


DEFAULT_LOGS: tuple[str, ...] = (
    "pipeline.jsonl",
    "reuse-events.jsonl",
    "evidence-verifications.jsonl",
    "open-questions.jsonl",
)


def tail_events(
    layout: VaultLayout,
    *,
    since_position: dict[str, int] | None = None,
    logs: Iterable[str] = DEFAULT_LOGS,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Read all new lines across ``logs`` since the per-file byte offsets in
    ``since_position`` and return ``(chronological_events, new_positions)``.

    Iteration order across files: events from each file are appended, then the
    full batch is sorted by ``ts`` so the SSE consumer sees a strict
    chronological feed even when two files were appended to between polls.
    Lines that fail to parse are silently skipped — JSONL is a log, not an
    authoritative store.
    """
    positions: dict[str, int] = dict(since_position or {})
    new_events: list[dict[str, object]] = []

    for log_name in logs:
        log_path = layout.logs_dir / log_name
        if not log_path.exists():
            # Reset to 0 so the file is read in full on its first appearance.
            positions[log_name] = 0
            continue

        size = log_path.stat().st_size
        offset = positions.get(log_name, 0)
        if offset > size:
            # File was truncated/rotated under us — start fresh.
            offset = 0

        if offset == size:
            positions[log_name] = size
            continue

        with log_path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(size - offset)
            new_offset = handle.tell()

        for raw_line in chunk.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                new_events.append(obj)

        positions[log_name] = new_offset

    new_events.sort(key=lambda event: str(event.get("ts") or ""))
    return new_events, positions


def initial_positions(
    layout: VaultLayout, *, logs: Iterable[str] = DEFAULT_LOGS
) -> dict[str, int]:
    """Return the *current* end-of-file offset per log without emitting events.

    Used by the SSE handler when a client connects without a ``Last-Event-ID``
    header — we want only events from this point forward, not a replay of
    everything ever logged.
    """
    positions: dict[str, int] = {}
    for log_name in logs:
        log_path = layout.logs_dir / log_name
        positions[log_name] = log_path.stat().st_size if log_path.exists() else 0
    return positions
