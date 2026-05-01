from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .projection_labels import frontmatter_projection_fields
from .projection_lifecycle import ProjectionRepairMarker, list_projection_repair_markers
from .runtime import VaultLayout, resolve_vault_dir


DEFAULT_RECENT_LIMIT = 20
RUNTIME_STATE_DIR = ("60-Logs", "runtime-state")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _event_time(event: dict[str, Any]) -> datetime | None:
    return _parse_dt(event.get("ts") or event.get("timestamp"))


def _event_time_text(event: dict[str, Any]) -> str:
    parsed = _event_time(event)
    return _format_dt(parsed) if parsed else str(event.get("ts") or event.get("timestamp") or "")


def _last_event_time(events: Iterable[dict[str, Any]]) -> str:
    parsed = [_event_time(event) for event in events]
    present = [value for value in parsed if value is not None]
    if not present:
        return ""
    return _format_dt(max(present))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _recent_events(events: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda event: _event_time(event) or datetime.min.replace(tzinfo=timezone.utc))
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
class RuntimeStatePaths:
    json_path: Path
    markdown_path: Path


def _marker_projection_kind(marker: ProjectionRepairMarker) -> str:
    return str(marker.scope.get("projection_kind") or "unknown")


def _marker_row(marker: ProjectionRepairMarker, *, now: datetime) -> dict[str, Any]:
    lease_expired = bool(
        marker.status == "claimed"
        and marker.claim_lease_until is not None
        and marker.claim_lease_until <= now
    )
    row = marker.to_dict()
    row["lease_expired"] = lease_expired
    return row


def build_runtime_state(
    vault_dir: Path | str,
    *,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    current_time = now or _utc_now()

    repair_events = _read_jsonl(layout.logs_dir / "projection-repair.jsonl")
    repair_markers = list_projection_repair_markers(resolved_vault)
    repair_rows = [_marker_row(marker, now=current_time) for marker in repair_markers]
    open_markers = [marker for marker in repair_markers if marker.status == "open"]
    claimed_markers = [marker for marker in repair_markers if marker.status == "claimed"]
    expired_claims = [
        marker
        for marker in claimed_markers
        if marker.claim_lease_until is not None and marker.claim_lease_until <= current_time
    ]

    pipeline_events = _read_jsonl(layout.pipeline_log)
    reuse_events = _read_jsonl(layout.logs_dir / "reuse-events.jsonl")

    pipeline_counts = Counter(str(event.get("event_type") or "(unknown)") for event in pipeline_events)
    reuse_by_surface: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"surface": "", "events": 0, "trusted": 0, "untrusted": 0, "last_event_at": ""}
    )
    surface_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in reuse_events:
        surface = str(event.get("surface") or "(unknown)")
        surface_events[surface].append(event)
        row = reuse_by_surface[surface]
        row["surface"] = surface
        row["events"] += 1
        if _truthy(event.get("trusted")):
            row["trusted"] += 1
        else:
            row["untrusted"] += 1

    for surface, events in surface_events.items():
        reuse_by_surface[surface]["last_event_at"] = _last_event_time(events)

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
            "events": len(pipeline_events),
        },
        {
            "id": "log:reuse-events",
            "kind": "event_log",
            "label": "reuse-events.jsonl",
            "events": len(reuse_events),
        },
        {
            "id": "log:projection-repair",
            "kind": "event_log",
            "label": "projection-repair.jsonl",
            "events": len(repair_events),
        },
    ]
    edges: list[dict[str, str]] = [
        {"source": "runtime", "target": "log:pipeline", "kind": "reads"},
        {"source": "runtime", "target": "log:reuse-events", "kind": "reads"},
        {"source": "runtime", "target": "log:projection-repair", "kind": "reads"},
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

    metrics = {
        "projection_repair_markers": len(repair_markers),
        "projection_repair_events": len(repair_events),
        "open_projection_repair_markers": len(open_markers),
        "claimed_projection_repair_markers": len(claimed_markers),
        "expired_projection_repair_leases": len(expired_claims),
        "pipeline_events": len(pipeline_events),
        "reuse_events": len(reuse_events),
        "trusted_reuse_events": sum(1 for event in reuse_events if _truthy(event.get("trusted"))),
        "reuse_surfaces": len(reuse_by_surface),
    }

    return {
        "type": "operational_runtime_state",
        "generated_at": _format_dt(current_time),
        "status": "attention_required" if attention else "ok",
        "metrics": metrics,
        "attention": attention,
        "projection_repair_markers": repair_rows,
        "reuse_surfaces": sorted(reuse_by_surface.values(), key=lambda row: str(row["surface"])),
        "pipeline_event_counts": dict(sorted(pipeline_counts.items())),
        "recent_pipeline_events": _recent_events(pipeline_events, limit=recent_limit),
        "recent_reuse_events": _recent_events(reuse_events, limit=recent_limit),
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


def render_runtime_state_markdown(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    attention = state.get("attention") if isinstance(state.get("attention"), list) else []
    reuse_surfaces = state.get("reuse_surfaces") if isinstance(state.get("reuse_surfaces"), list) else []
    marker_rows = (
        state.get("projection_repair_markers")
        if isinstance(state.get("projection_repair_markers"), list)
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
