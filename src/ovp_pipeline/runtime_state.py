from __future__ import annotations

import heapq
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .projection_labels import frontmatter_projection_fields
from .projection_lifecycle import ProjectionRepairMarker, list_projection_repair_markers
from .runtime import (
    VaultLayout,
    format_utc_timestamp,
    parse_utc_timestamp,
    resolve_vault_dir,
    utc_now,
)


DEFAULT_RECENT_LIMIT = 20
DEFAULT_ACTION_DISPLAY_LIMIT = 200
RUNTIME_STATE_DIR = ("60-Logs", "runtime-state")
ACTION_RUNNING_STALE_AFTER_SECONDS = 3600

_utc_now = utc_now
_format_dt = format_utc_timestamp
_parse_dt = parse_utc_timestamp


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def _count_jsonl_lines(path: Path) -> int:
    """Count non-empty lines in a JSONL file without parsing JSON."""
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _event_time(event: dict[str, Any]) -> datetime | None:
    return _parse_dt(event.get("ts") or event.get("timestamp"))


def _event_time_text(event: dict[str, Any]) -> str:
    parsed = _event_time(event)
    return _format_dt(parsed) if parsed else str(event.get("ts") or event.get("timestamp") or "")


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _int_value(value: object, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _action_queue_path(layout: VaultLayout) -> Path:
    return layout.actions_log


def _action_worker_state_path(layout: VaultLayout) -> Path:
    return layout.action_worker_state


def _action_row(action: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    status = str(action.get("status") or "")
    started_at = _parse_dt(action.get("started_at") or action.get("created_at"))
    running_age_seconds = (
        max(0, int((now - started_at).total_seconds()))
        if status == "running" and started_at is not None
        else None
    )
    stale_running = bool(
        status == "running"
        and running_age_seconds is not None
        and running_age_seconds > ACTION_RUNNING_STALE_AFTER_SECONDS
    )
    return {
        "action_id": str(action.get("action_id") or ""),
        "action_kind": str(action.get("action_kind") or ""),
        "pack": str(action.get("pack") or ""),
        "source_signal_id": str(action.get("source_signal_id") or ""),
        "title": str(action.get("title") or ""),
        "target_ref": str(action.get("target_ref") or ""),
        "status": status,
        "safe_to_run": _truthy(action.get("safe_to_run")),
        "created_at": str(action.get("created_at") or ""),
        "started_at": str(action.get("started_at") or ""),
        "finished_at": str(action.get("finished_at") or ""),
        "failure_bucket": str(action.get("failure_bucket") or ""),
        "retry_count": _int_value(action.get("retry_count")),
        "running_age_seconds": running_age_seconds,
        "stale_running": stale_running,
    }


def _action_worker_state(layout: VaultLayout, *, now: datetime) -> dict[str, Any]:
    path = _action_worker_state_path(layout)
    if not path.exists():
        return {
            "active": False,
            "state": "stopped",
            "worker_id": "",
            "heartbeat_at": "",
            "heartbeat_age_seconds": None,
            "current_action": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    heartbeat_at = _parse_dt(payload.get("heartbeat_at"))
    heartbeat_age_seconds = (
        max(0, int((now - heartbeat_at).total_seconds())) if heartbeat_at is not None else None
    )
    state = str(payload.get("state") or "stopped")
    active = state in {"running", "idle"} and (
        heartbeat_age_seconds is None
        or heartbeat_age_seconds <= ACTION_RUNNING_STALE_AFTER_SECONDS
    )
    return {
        "active": active,
        "state": state,
        "worker_id": str(payload.get("worker_id") or ""),
        "pid": _int_value(payload.get("pid")),
        "mode": str(payload.get("mode") or ""),
        "safe_only": _truthy(payload.get("safe_only")),
        "heartbeat_at": str(payload.get("heartbeat_at") or ""),
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "current_action": payload.get("current_action")
        if isinstance(payload.get("current_action"), dict)
        else {},
        "last_result": payload.get("last_result")
        if isinstance(payload.get("last_result"), dict)
        else {},
    }


def _event_sort_time(event: dict[str, Any]) -> datetime:
    return _event_time(event) or datetime.min.replace(tzinfo=timezone.utc)


def _recent_events(events: Iterable[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    ordered = sorted(heapq.nlargest(limit, events, key=_event_sort_time), key=_event_sort_time)
    return [
        {
            "event_type": str(event.get("event_type") or ""),
            "ts": _event_time_text(event),
            "session_id": str(event.get("session_id") or ""),
            "pack": str(event.get("pack") or ""),
        }
        for event in ordered[-limit:]
    ]


@dataclass(frozen=True)
class EventLogSummary:
    total: int
    counts: Counter[str]
    recent: list[dict[str, Any]]


@dataclass(frozen=True)
class RuntimeStatePaths:
    json_path: Path
    markdown_path: Path


def _summarize_event_log(path: Path, *, recent_limit: int) -> EventLogSummary:
    total = 0
    counts: Counter[str] = Counter()
    recent_heap: list[tuple[datetime, int, dict[str, Any]]] = []
    for event in _iter_jsonl(path):
        total += 1
        counts[str(event.get("event_type") or "(unknown)")] += 1
        if recent_limit <= 0:
            continue
        entry = (_event_sort_time(event), total, event)
        if len(recent_heap) < recent_limit:
            heapq.heappush(recent_heap, entry)
        else:
            heapq.heappushpop(recent_heap, entry)
    recent_events = [event for _event_time, _seq, event in sorted(recent_heap)]
    return EventLogSummary(total=total, counts=counts, recent=_recent_events(recent_events, limit=recent_limit))


@dataclass(frozen=True)
class ReuseEventSummary:
    total: int
    trusted: int
    reuse_by_surface: dict[str, dict[str, Any]]
    recent: list[dict[str, Any]]


@dataclass(frozen=True)
class ActionQueueSummary:
    total: int
    counts: Counter[str]
    rows: list[dict[str, Any]]
    stale_running_actions: list[dict[str, Any]]
    failed_actions: list[dict[str, Any]]
    blocked_actions: list[dict[str, Any]]


def _summarize_reuse_event_log(path: Path, *, recent_limit: int) -> ReuseEventSummary:
    total = 0
    trusted = 0
    recent_heap: list[tuple[datetime, int, dict[str, Any]]] = []
    surface_last_event: dict[str, datetime] = {}
    reuse_by_surface: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"surface": "", "events": 0, "trusted": 0, "untrusted": 0, "last_event_at": ""}
    )
    for event in _iter_jsonl(path):
        total += 1
        if recent_limit > 0:
            entry = (_event_sort_time(event), total, event)
            if len(recent_heap) < recent_limit:
                heapq.heappush(recent_heap, entry)
            else:
                heapq.heappushpop(recent_heap, entry)

        surface = str(event.get("surface") or "(unknown)")
        row = reuse_by_surface[surface]
        row["surface"] = surface
        row["events"] += 1
        if _truthy(event.get("trusted")):
            row["trusted"] += 1
            trusted += 1
        else:
            row["untrusted"] += 1

        event_time = _event_time(event)
        if event_time is not None and event_time >= surface_last_event.get(
            surface,
            datetime.min.replace(tzinfo=timezone.utc),
        ):
            surface_last_event[surface] = event_time
            row["last_event_at"] = _format_dt(event_time)

    recent_events = [event for _event_time, _seq, event in sorted(recent_heap)]
    return ReuseEventSummary(
        total=total,
        trusted=trusted,
        reuse_by_surface=dict(reuse_by_surface),
        recent=_recent_events(recent_events, limit=recent_limit),
    )


def _summarize_action_queue(
    path: Path,
    *,
    now: datetime,
    row_limit: int = DEFAULT_ACTION_DISPLAY_LIMIT,
) -> ActionQueueSummary:
    total = 0
    counts: Counter[str] = Counter()
    important_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    stale_running_actions: list[dict[str, Any]] = []
    failed_actions: list[dict[str, Any]] = []
    blocked_actions: list[dict[str, Any]] = []
    for action in _iter_jsonl(path):
        total += 1
        row = _action_row(action, now=now)
        status = str(row.get("status") or "(unknown)")
        counts[status] += 1

        if row["stale_running"] and len(stale_running_actions) < 5:
            stale_running_actions.append(row)
        if status == "failed" and len(failed_actions) < 5:
            failed_actions.append(row)
        if status == "blocked" and len(blocked_actions) < 5:
            blocked_actions.append(row)

        is_active_or_attention = status in {"queued", "running", "failed", "blocked"}
        if is_active_or_attention and len(important_rows) < row_limit:
            important_rows.append(row)
        elif not important_rows and len(fallback_rows) < row_limit:
            fallback_rows.append(row)

    rows = important_rows if important_rows else fallback_rows
    return ActionQueueSummary(
        total=total,
        counts=counts,
        rows=rows,
        stale_running_actions=stale_running_actions,
        failed_actions=failed_actions,
        blocked_actions=blocked_actions,
    )


def _marker_projection_kind(marker: ProjectionRepairMarker) -> str:
    return str(marker.scope.get("projection_kind") or "unknown")


def _marker_row(marker: ProjectionRepairMarker, *, now: datetime) -> dict[str, Any]:
    lease_expired = bool(
        marker.status == "claimed"
        and marker.claim_lease_until is not None
        and marker.claim_lease_until <= now
    )
    return {**marker.to_dict(), "lease_expired": lease_expired}


def build_runtime_state(
    vault_dir: Path | str,
    *,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    current_time = now or _utc_now()

    repair_event_count = _count_jsonl_lines(layout.logs_dir / "projection-repair.jsonl")
    repair_markers = list_projection_repair_markers(resolved_vault)
    repair_rows = [_marker_row(marker, now=current_time) for marker in repair_markers]
    open_markers = [marker for marker in repair_markers if marker.status == "open"]
    claimed_markers = [marker for marker in repair_markers if marker.status == "claimed"]
    expired_claims = [
        marker
        for marker in claimed_markers
        if marker.claim_lease_until is not None and marker.claim_lease_until <= current_time
    ]

    pipeline_summary = _summarize_event_log(layout.pipeline_log, recent_limit=recent_limit)
    reuse_summary = _summarize_reuse_event_log(
        layout.logs_dir / "reuse-events.jsonl",
        recent_limit=recent_limit,
    )
    action_summary = _summarize_action_queue(_action_queue_path(layout), now=current_time)
    action_rows = action_summary.rows
    action_counts = action_summary.counts
    stale_running_actions = action_summary.stale_running_actions
    failed_actions = action_summary.failed_actions
    blocked_actions = action_summary.blocked_actions
    worker_state = _action_worker_state(layout, now=current_time)

    pipeline_counts = pipeline_summary.counts
    reuse_by_surface = reuse_summary.reuse_by_surface

    attention: list[dict[str, Any]] = []
    for marker in open_markers:
        attention.append(
            {
                "kind": "open_projection_repair_marker",
                "severity": "warning",
                "ref": marker.marker_id,
                "message": f"{marker.kind} repair marker is open for {_marker_projection_kind(marker)}",
            }
        )
    for marker in expired_claims:
        attention.append(
            {
                "kind": "expired_projection_repair_lease",
                "severity": "warning",
                "ref": marker.marker_id,
                "message": f"repair marker lease expired for worker {marker.claimed_by}",
            }
        )
    for action in stale_running_actions:
        attention.append(
            {
                "kind": "stale_running_action",
                "severity": "warning",
                "ref": action["action_id"],
                "message": f"{action['action_kind']} action has been running for more than one hour",
            }
        )
    for action in failed_actions[:5]:
        attention.append(
            {
                "kind": "failed_action",
                "severity": "warning",
                "ref": action["action_id"],
                "message": f"{action['action_kind']} action failed in {action['failure_bucket'] or 'unknown'}",
            }
        )

    nodes: list[dict[str, Any]] = [
        {
            "id": "runtime",
            "kind": "runtime",
            "label": "Operational Runtime",
            "status": "attention_required" if attention else "ok",
        },
        {
            "id": "log:pipeline",
            "kind": "event_log",
            "label": "pipeline.jsonl",
            "events": pipeline_summary.total,
        },
        {
            "id": "log:reuse-events",
            "kind": "event_log",
            "label": "reuse-events.jsonl",
            "events": reuse_summary.total,
        },
        {
            "id": "log:projection-repair",
            "kind": "event_log",
            "label": "projection-repair.jsonl",
            "events": repair_event_count,
        },
        {
            "id": "log:actions",
            "kind": "event_log",
            "label": "actions.jsonl",
            "events": action_summary.total,
        },
    ]
    edges: list[dict[str, str]] = [
        {"source": "runtime", "target": "log:pipeline", "kind": "reads"},
        {"source": "runtime", "target": "log:reuse-events", "kind": "reads"},
        {"source": "runtime", "target": "log:projection-repair", "kind": "reads"},
        {"source": "runtime", "target": "log:actions", "kind": "reads"},
    ]

    seen_projection_nodes: set[str] = set()
    seen_worker_nodes: set[str] = set()
    for marker in repair_markers:
        projection_kind = _marker_projection_kind(marker)
        projection_node = f"projection:{projection_kind}"
        marker_node = f"marker:{marker.marker_id}"
        if projection_node not in seen_projection_nodes:
            nodes.append(
                {
                    "id": projection_node,
                    "kind": "projection",
                    "label": projection_kind,
                }
            )
            seen_projection_nodes.add(projection_node)
        nodes.append(
            {
                "id": marker_node,
                "kind": "projection_repair_marker",
                "label": marker.marker_id,
                "status": marker.status,
                "repair_kind": marker.kind,
            }
        )
        edges.append({"source": marker_node, "target": projection_node, "kind": "repairs"})
        edges.append({"source": "runtime", "target": marker_node, "kind": "tracks"})
        if marker.claimed_by:
            worker_node = f"worker:{marker.claimed_by}"
            if worker_node not in seen_worker_nodes:
                nodes.append(
                    {
                        "id": worker_node,
                        "kind": "worker",
                        "label": marker.claimed_by,
                    }
                )
                seen_worker_nodes.add(worker_node)
            edges.append({"source": marker_node, "target": worker_node, "kind": "claimed_by"})

    for surface, row in sorted(reuse_by_surface.items()):
        surface_node = f"surface:{surface}"
        nodes.append(
            {
                "id": surface_node,
                "kind": "reuse_surface",
                "label": surface,
                "events": row["events"],
                "trusted": row["trusted"],
                "untrusted": row["untrusted"],
            }
        )
        edges.append({"source": surface_node, "target": "log:reuse-events", "kind": "emits"})

    seen_signal_nodes: set[str] = set()
    for action in action_rows:
        if not action["action_id"]:
            continue
        action_node = f"action:{action['action_id']}"
        nodes.append(
            {
                "id": action_node,
                "kind": "workflow_action",
                "label": action["action_kind"] or action["action_id"],
                "status": action["status"],
                "stale_running": action["stale_running"],
            }
        )
        edges.append({"source": action_node, "target": "log:actions", "kind": "recorded_in"})
        if action["source_signal_id"]:
            signal_node = f"signal:{action['source_signal_id']}"
            if signal_node not in seen_signal_nodes:
                nodes.append(
                    {
                        "id": signal_node,
                        "kind": "signal",
                        "label": action["source_signal_id"],
                    }
                )
                seen_signal_nodes.add(signal_node)
            edges.append({"source": action_node, "target": signal_node, "kind": "responds_to"})

    worker_id = str(worker_state.get("worker_id") or "")
    if worker_id:
        worker_node = f"action-worker:{worker_id}"
        nodes.append(
            {
                "id": worker_node,
                "kind": "action_worker",
                "label": worker_id,
                "status": str(worker_state.get("state") or ""),
                "active": bool(worker_state.get("active")),
            }
        )
        edges.append({"source": "runtime", "target": worker_node, "kind": "tracks"})
        current_action = worker_state.get("current_action")
        if isinstance(current_action, dict) and current_action.get("action_id"):
            edges.append(
                {
                    "source": worker_node,
                    "target": f"action:{current_action['action_id']}",
                    "kind": "working_on",
                }
            )

    metrics = {
        "projection_repair_markers": len(repair_markers),
        "projection_repair_events": repair_event_count,
        "open_projection_repair_markers": len(open_markers),
        "claimed_projection_repair_markers": len(claimed_markers),
        "expired_projection_repair_leases": len(expired_claims),
        "action_queue_items": action_summary.total,
        "queued_actions": action_counts.get("queued", 0),
        "running_actions": action_counts.get("running", 0),
        "stale_running_actions": len(stale_running_actions),
        "failed_actions": len(failed_actions),
        "blocked_actions": len(blocked_actions),
        "obsolete_actions": action_counts.get("obsolete", 0),
        "dismissed_actions": action_counts.get("dismissed", 0),
        "succeeded_actions": action_counts.get("succeeded", 0),
        "pipeline_events": pipeline_summary.total,
        "reuse_events": reuse_summary.total,
        "trusted_reuse_events": reuse_summary.trusted,
        "reuse_surfaces": len(reuse_by_surface),
    }

    return {
        "type": "operational_runtime_state",
        "generated_at": _format_dt(current_time),
        "status": "attention_required" if attention else "ok",
        "metrics": metrics,
        "attention": attention,
        "projection_repair_markers": repair_rows,
        "workflow_actions": action_rows,
        "action_worker": worker_state,
        "action_status_counts": dict(sorted(action_counts.items())),
        "reuse_surfaces": sorted(reuse_by_surface.values(), key=lambda row: str(row["surface"])),
        "pipeline_event_counts": dict(sorted(pipeline_counts.items())),
        "recent_pipeline_events": pipeline_summary.recent,
        "recent_reuse_events": reuse_summary.recent,
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
    }


def runtime_state_paths(vault_dir: Path | str) -> RuntimeStatePaths:
    resolved_vault = resolve_vault_dir(vault_dir)
    output_dir = resolved_vault.joinpath(*RUNTIME_STATE_DIR)
    return RuntimeStatePaths(
        json_path=output_dir / "current.json",
        markdown_path=output_dir / "current.md",
    )


def read_runtime_state(vault_dir: Path | str) -> dict[str, Any] | None:
    paths = runtime_state_paths(vault_dir)
    if not paths.json_path.exists():
        return None
    try:
        payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def render_runtime_state_markdown(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    attention = state.get("attention") if isinstance(state.get("attention"), list) else []
    reuse_surfaces = state.get("reuse_surfaces") if isinstance(state.get("reuse_surfaces"), list) else []
    marker_rows = (
        state.get("projection_repair_markers")
        if isinstance(state.get("projection_repair_markers"), list)
        else []
    )
    action_rows = (
        state.get("workflow_actions")
        if isinstance(state.get("workflow_actions"), list)
        else []
    )

    frontmatter = (
        "---\n"
        "type: operational_runtime_state\n"
        f"generated_at: {state.get('generated_at') or ''}\n"
        + "\n".join(
            frontmatter_projection_fields(
                surface="runtime_state",
                projection_kind="operational_runtime_projection",
                owner_pack="research-tech",
                generated_by="build_runtime_state",
                derived_from=("projection-repair.jsonl", "pipeline.jsonl", "reuse-events.jsonl"),
                rebuild_policy="on_runtime_state_refresh",
            )
        )
        + "\n---\n\n"
    )

    lines = [
        "# Operational Runtime State",
        "",
        f"- Generated at: {state.get('generated_at') or ''}",
        f"- Status: {state.get('status') or 'unknown'}",
        f"- Open repair markers: {metrics.get('open_projection_repair_markers', 0)}",
        f"- Expired repair leases: {metrics.get('expired_projection_repair_leases', 0)}",
        f"- Queued actions: {metrics.get('queued_actions', 0)}",
        f"- Stale running actions: {metrics.get('stale_running_actions', 0)}",
        f"- Failed actions: {metrics.get('failed_actions', 0)}",
        f"- Pipeline events: {metrics.get('pipeline_events', 0)}",
        f"- Reuse events: {metrics.get('reuse_events', 0)}",
        "",
        "## Attention",
        "",
    ]
    if attention:
        for item in attention:
            lines.append(f"- {item.get('severity', 'info')}: {item.get('message', '')}")
    else:
        lines.append("- (none)")

    lines.extend(["", "## Projection Repair Markers", ""])
    if marker_rows:
        lines.extend(["| marker_id | kind | status | projection | claimed_by | lease_expired |", "|---|---|---|---|---|---|"])
        for marker in marker_rows:
            scope = marker.get("scope") if isinstance(marker.get("scope"), dict) else {}
            lines.append(
                "| {marker_id} | {kind} | {status} | {projection} | {claimed_by} | {lease_expired} |".format(
                    marker_id=marker.get("marker_id", ""),
                    kind=marker.get("kind", ""),
                    status=marker.get("status", ""),
                    projection=scope.get("projection_kind", ""),
                    claimed_by=marker.get("claimed_by", ""),
                    lease_expired=marker.get("lease_expired", False),
                )
            )
    else:
        lines.append("- (none)")

    lines.extend(["", "## Workflow Actions", ""])
    if action_rows:
        lines.extend(["| action_id | kind | status | stale_running | retry_count | target |", "|---|---|---|---|---:|---|"])
        for action in action_rows:
            lines.append(
                "| {action_id} | {kind} | {status} | {stale_running} | {retry_count} | {target} |".format(
                    action_id=action.get("action_id", ""),
                    kind=action.get("action_kind", ""),
                    status=action.get("status", ""),
                    stale_running=action.get("stale_running", False),
                    retry_count=action.get("retry_count", 0),
                    target=action.get("target_ref", ""),
                )
            )
    else:
        lines.append("- (none)")

    lines.extend(["", "## Reuse Surfaces", ""])
    if reuse_surfaces:
        lines.extend(["| surface | events | trusted | untrusted | last_event_at |", "|---|---:|---:|---:|---|"])
        for row in reuse_surfaces:
            lines.append(
                f"| {row.get('surface', '')} | {row.get('events', 0)} | {row.get('trusted', 0)} | "
                f"{row.get('untrusted', 0)} | {row.get('last_event_at', '')} |"
            )
    else:
        lines.append("- (none)")

    return frontmatter + "\n".join(lines).rstrip() + "\n"


def write_runtime_state(
    vault_dir: Path | str,
    state: dict[str, Any],
) -> RuntimeStatePaths:
    paths = runtime_state_paths(vault_dir)
    paths.json_path.parent.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths.markdown_path.write_text(render_runtime_state_markdown(state), encoding="utf-8")
    return paths
