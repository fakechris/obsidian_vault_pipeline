"""
Append-only JSONL event emitter shared by reuse_events, audit_events, and
future Phase 32-37 streams.

JSONL is the truth — SQLite tables in knowledge.db are derived/rebuildable.
This module owns the contract:

  60-Logs/<log_name>       — one JSON object per line, atomic O_APPEND writes
  knowledge.db <table>     — re-derived from JSONL by rebuild_knowledge_index

Closed `event_type` vocabulary (forward-compat for Phase 37 Pulse feed):

  trusted_reuse_event      — Phase 32. Canonical object consumed by a surface
                             (query|briefing|writing_prompt|compiled_view|
                              export|truth_api|prompt).
  promotion                — Phase 32/34/35. State boundary crossed
                             (candidate→canonical, draft→accepted,
                              relation_candidate→relation_row).
  evidence_reverified      — Phase 33. claim_evidence row re-hashed.
  zone_violation           — Phase 34. Accepted-zone file mutated outside
                             promotion command.
  feedback_yield           — Phase 36. ovp-query produced a downstream
                             candidate / question / writing prompt.

Adding a new event_type requires: (a) appending here, (b) updating any
collector(s) in knowledge_index.py, (c) updating ingest tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import uuid
from typing import Any, Iterable

from .runtime import VaultLayout


_DEFAULT_SESSION_ID: str | None = None


def default_session_id() -> str:
    """Return a process-wide session id, creating one on first call."""
    global _DEFAULT_SESSION_ID
    if _DEFAULT_SESSION_ID is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        _DEFAULT_SESSION_ID = f"{stamp}-{os.urandom(4).hex()}"
    return _DEFAULT_SESSION_ID


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(
    vault_dir: Path | str,
    log_name: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
    pack: str | None = None,
) -> dict[str, Any]:
    """Append a single event to ``60-Logs/<log_name>`` and return it.

    The returned dict includes the auto-stamped fields (``event_id``, ``ts``,
    ``session_id``, ``pack``, ``event_type``) so callers can reference them.

    Atomicity: ``O_APPEND`` guarantees the kernel positions the write at the
    current end of file, so concurrent writers cannot interleave each other's
    bytes within a single ``write()`` call. Each event is serialized into one
    bytes buffer and emitted with a single ``os.write()``, which Linux/macOS
    deliver as one atomic write for typical line sizes (well under the
    page-aligned chunking threshold). Lock-free on the producer side.
    """
    layout = VaultLayout.from_vault(vault_dir)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = layout.logs_dir / log_name

    event = {
        "event_id": uuid.uuid4().hex,
        "ts": _utc_now_text(),
        "session_id": session_id or default_session_id(),
        "pack": pack or "",
        "event_type": event_type,
        **payload,
    }

    line = json.dumps(event, ensure_ascii=False) + "\n"
    encoded = line.encode("utf-8")
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)

    return event


def collect_for_index(layout: VaultLayout, log_name: str) -> list[dict[str, Any]]:
    """Read every JSONL event from ``60-Logs/<log_name>``.

    Mirrors the ``_collect_audit_rows`` pattern in knowledge_index.py; used by
    rebuild_knowledge_index to materialize a derived SQLite table.
    """
    log_path = layout.logs_dir / log_name
    if not log_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def iter_for_index(layout: VaultLayout, log_name: str) -> Iterable[dict[str, Any]]:
    """Streaming variant of :func:`collect_for_index` (line-by-line)."""
    log_path = layout.logs_dir / log_name
    if not log_path.exists():
        return iter(())

    def _generator() -> Iterable[dict[str, Any]]:
        with log_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj

    return _generator()
