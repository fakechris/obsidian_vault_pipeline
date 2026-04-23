"""
Phase 32 prelude §2.3 — single-emit point for state-boundary crossings.

Phases 32, 34, and 35 all need to record one fact: an artifact crossed a
``state:`` boundary (concept candidate → canonical, draft → accepted-state
file, relation candidate → relations row, …). The doctor's "unreviewed
canonical mutation" count and the lint ``ZONE_BOUNDARY_VIOLATION`` rule both
read from the same audit stream, so it must have a single source.

Wraps :func:`event_emitter.emit` against ``60-Logs/pipeline.jsonl`` with
``event_type='promotion'``. The closed event-type vocabulary is documented in
``event_emitter`` for forward-compat with the Phase 37 Pulse feed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .event_emitter import emit
from .state_lifecycle import State


_RESERVED_BOUNDARY_KEYS = frozenset(
    {"from_state", "to_state", "target_path", "actor", "reason"}
)


def emit_promotion(
    vault_dir: Path | str,
    *,
    pack: str,
    from_state: State | str,
    to_state: State | str,
    target_path: Path | str,
    actor: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Append one ``promotion`` event to ``60-Logs/pipeline.jsonl``.

    ``actor`` should identify the caller in a stable way (e.g. command name
    ``promote_candidates.review``, autopilot worker id, or human approver
    handle). ``payload`` is folded into the JSON line and is the place to
    record extra context such as object_id, source_slug, or candidate_id.
    Reserved boundary keys (``from_state``, ``to_state``, ``target_path``,
    ``actor``, ``reason``) cannot be clobbered by ``payload``.
    """
    body: dict[str, Any] = {
        "from_state": _state_value(from_state),
        "to_state": _state_value(to_state),
        "target_path": _vault_relative(vault_dir, target_path),
        "actor": actor,
    }
    if reason:
        body["reason"] = reason
    if payload:
        for key, value in payload.items():
            if key in _RESERVED_BOUNDARY_KEYS:
                continue
            body[key] = value
    return emit(
        vault_dir,
        "pipeline.jsonl",
        "promotion",
        body,
        session_id=session_id,
        pack=pack,
    )


def _vault_relative(vault_dir: Path | str, target_path: Path | str) -> str:
    raw = Path(target_path)
    try:
        base = Path(vault_dir).resolve()
        return str(raw.resolve().relative_to(base))
    except (OSError, ValueError):
        return str(raw)


def emit_zone_violation(
    vault_dir: Path | str,
    *,
    pack: str,
    target_path: Path | str,
    actor: str,
    reason: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Record an attempted accepted-zone write that did NOT route through promotion.

    The lint rule reads this against file mtimes; the count surfaces in the
    doctor's "unreviewed canonical mutation" panel.
    """
    return emit(
        vault_dir,
        "pipeline.jsonl",
        "zone_violation",
        {
            "target_path": str(target_path),
            "actor": actor,
            "reason": reason,
        },
        session_id=session_id,
        pack=pack,
    )


def _state_value(state: State | str) -> str:
    if isinstance(state, State):
        return state.value
    return str(state or "")
