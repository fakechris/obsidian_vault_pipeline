from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Literal

from .runtime import (
    VaultLayout,
    advisory_file_lock,
    append_jsonl,
    format_utc_iso,
    iter_jsonl_with_rotated,
    parse_utc_timestamp,
    resolve_vault_dir,
    utc_now,
)

ProjectionRepairKind = Literal["metadata_only", "full_rebuild", "semantic_reindex"]
ProjectionRepairStatus = Literal["open", "claimed", "closed", "superseded"]

_utc_now = utc_now
_format_dt = format_utc_iso
_parse_dt = parse_utc_timestamp


def _marker_log_path(vault_dir: Path | str) -> Path:
    layout = VaultLayout.from_vault(resolve_vault_dir(vault_dir))
    return layout.logs_dir / "projection-repair.jsonl"


def _marker_log_lock_path(vault_dir: Path | str) -> Path:
    return _marker_log_path(vault_dir).with_suffix(".jsonl.lock")


@contextmanager
def _marker_log_lock(vault_dir: Path | str) -> Iterator[None]:
    with advisory_file_lock(_marker_log_lock_path(vault_dir)):
        yield


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _marker_id(
    *,
    kind: str,
    scope: dict[str, object],
    reason: str,
    caused_by: str,
    created_at: datetime,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            [
                kind,
                _canonical_json(scope),
                reason,
                caused_by,
                _format_dt(created_at),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"prm_{digest}"


@dataclass(frozen=True)
class ProjectionRepairMarker:
    marker_id: str
    kind: ProjectionRepairKind
    scope: dict[str, object]
    reason: str
    caused_by: str
    created_at: datetime
    authority_schema_version: int
    projection_schema_version: int
    status: ProjectionRepairStatus = "open"
    superseded_by: str = ""
    claimed_by: str = ""
    claim_lease_until: datetime | None = None
    closed_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "marker_id": self.marker_id,
            "kind": self.kind,
            "scope": self.scope,
            "reason": self.reason,
            "caused_by": self.caused_by,
            "created_at": _format_dt(self.created_at),
            "authority_schema_version": self.authority_schema_version,
            "projection_schema_version": self.projection_schema_version,
            "status": self.status,
            "superseded_by": self.superseded_by,
            "claimed_by": self.claimed_by,
            "claim_lease_until": _format_dt(self.claim_lease_until),
            "closed_at": _format_dt(self.closed_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ProjectionRepairMarker | None":
        try:
            marker_id = str(payload.get("marker_id") or "").strip()
            if not marker_id:
                return None
            kind = str(payload.get("kind") or "metadata_only")
            if kind not in {"metadata_only", "full_rebuild", "semantic_reindex"}:
                return None
            status = str(payload.get("status") or "open")
            if status not in {"open", "claimed", "closed", "superseded"}:
                return None
            raw_scope = payload.get("scope")
            if raw_scope is None:
                scope: dict[str, object] = {}
            elif isinstance(raw_scope, dict):
                scope = dict(raw_scope)
            else:
                return None
            return cls(
                marker_id=marker_id,
                kind=kind,  # type: ignore[arg-type]
                scope=scope,
                reason=str(payload.get("reason") or ""),
                caused_by=str(payload.get("caused_by") or ""),
                created_at=_parse_dt(payload.get("created_at")) or _utc_now(),
                authority_schema_version=int(payload.get("authority_schema_version") or 0),
                projection_schema_version=int(payload.get("projection_schema_version") or 0),
                status=status,  # type: ignore[arg-type]
                superseded_by=str(payload.get("superseded_by") or ""),
                claimed_by=str(payload.get("claimed_by") or ""),
                claim_lease_until=_parse_dt(payload.get("claim_lease_until")),
                closed_at=_parse_dt(payload.get("closed_at")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _append_event(vault_dir: Path | str, event: dict[str, object]) -> None:
    path = _marker_log_path(vault_dir)
    append_jsonl(path, event)  # type: ignore[arg-type]


def _iter_events(vault_dir: Path | str) -> Iterator[dict[str, object]]:
    """Replay all marker events including rotated archive segments."""
    path = _marker_log_path(vault_dir)
    for obj in iter_jsonl_with_rotated(path):
        yield obj


def _is_broader_marker(new_marker: ProjectionRepairMarker, old_marker: ProjectionRepairMarker) -> bool:
    if old_marker.status not in {"open", "claimed"}:
        return False
    if new_marker.kind != "full_rebuild" or old_marker.kind == "full_rebuild":
        return False
    new_projection = str(new_marker.scope.get("projection_kind") or "")
    old_projection = str(old_marker.scope.get("projection_kind") or "")
    return bool(new_projection and new_projection == old_projection)


def _list_projection_repair_markers_unlocked(vault_dir: Path | str) -> list[ProjectionRepairMarker]:
    markers: dict[str, ProjectionRepairMarker] = {}
    order: list[str] = []
    for event in _iter_events(vault_dir):
        event_type = str(event.get("event_type") or "")
        marker_id = str(event.get("marker_id") or "")
        if event_type == "projection_repair_marker_written":
            raw_marker = event.get("marker")
            if not isinstance(raw_marker, dict):
                continue
            marker = ProjectionRepairMarker.from_dict(dict(raw_marker))
            if marker is None:
                continue
            markers[marker.marker_id] = marker
            if marker.marker_id not in order:
                order.append(marker.marker_id)
            continue
        if marker_id not in markers:
            continue
        marker = markers[marker_id]
        if event_type == "projection_repair_marker_superseded":
            markers[marker_id] = replace(
                marker,
                status="superseded",
                superseded_by=str(event.get("superseded_by") or ""),
            )
        elif event_type == "projection_repair_marker_claimed":
            markers[marker_id] = replace(
                marker,
                status="claimed",
                claimed_by=str(event.get("claimed_by") or ""),
                claim_lease_until=_parse_dt(event.get("claim_lease_until")),
            )
        elif event_type == "projection_repair_marker_closed":
            markers[marker_id] = replace(
                marker,
                status="closed",
                closed_at=_parse_dt(event.get("closed_at")),
            )
    return [markers[marker_id] for marker_id in order if marker_id in markers]


def list_projection_repair_markers(vault_dir: Path | str) -> list[ProjectionRepairMarker]:
    with _marker_log_lock(vault_dir):
        return _list_projection_repair_markers_unlocked(vault_dir)


def write_projection_repair_marker(
    vault_dir: Path | str,
    *,
    kind: ProjectionRepairKind,
    scope: dict[str, object],
    reason: str,
    caused_by: str,
    authority_schema_version: int,
    projection_schema_version: int,
    created_at: datetime | None = None,
) -> ProjectionRepairMarker:
    created = created_at or _utc_now()
    marker = ProjectionRepairMarker(
        marker_id=_marker_id(
            kind=kind,
            scope=scope,
            reason=reason,
            caused_by=caused_by,
            created_at=created,
        ),
        kind=kind,
        scope=dict(scope),
        reason=reason,
        caused_by=caused_by,
        created_at=created,
        authority_schema_version=int(authority_schema_version),
        projection_schema_version=int(projection_schema_version),
    )
    with _marker_log_lock(vault_dir):
        _append_event(
            vault_dir,
            {
                "event_type": "projection_repair_marker_written",
                "marker_id": marker.marker_id,
                "timestamp": _format_dt(created),
                "marker": marker.to_dict(),
            },
        )
        for existing in _list_projection_repair_markers_unlocked(vault_dir):
            if existing.marker_id == marker.marker_id:
                continue
            if _is_broader_marker(marker, existing):
                _append_event(
                    vault_dir,
                    {
                        "event_type": "projection_repair_marker_superseded",
                        "marker_id": existing.marker_id,
                        "superseded_by": marker.marker_id,
                        "timestamp": _format_dt(created),
                    },
                )
    return marker


def claim_projection_repair_marker(
    vault_dir: Path | str,
    marker_id: str,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> ProjectionRepairMarker | None:
    current_time = now or _utc_now()
    with _marker_log_lock(vault_dir):
        markers = {
            marker.marker_id: marker
            for marker in _list_projection_repair_markers_unlocked(vault_dir)
        }
        marker = markers.get(marker_id)
        if marker is None or marker.status not in {"open", "claimed"}:
            return None
        if marker.claimed_by and marker.claim_lease_until and marker.claim_lease_until > current_time:
            return None
        lease_until = current_time + timedelta(seconds=lease_seconds)
        _append_event(
            vault_dir,
            {
                "event_type": "projection_repair_marker_claimed",
                "marker_id": marker_id,
                "claimed_by": worker_id,
                "claim_lease_until": _format_dt(lease_until),
                "timestamp": _format_dt(current_time),
            },
        )
        return replace(
            marker,
            status="claimed",
            claimed_by=worker_id,
            claim_lease_until=lease_until,
        )


def close_projection_repair_marker(
    vault_dir: Path | str,
    marker_id: str,
    *,
    closed_at: datetime | None = None,
) -> ProjectionRepairMarker | None:
    current_time = closed_at or _utc_now()
    with _marker_log_lock(vault_dir):
        markers = {
            marker.marker_id: marker
            for marker in _list_projection_repair_markers_unlocked(vault_dir)
        }
        if marker_id not in markers:
            return None
        _append_event(
            vault_dir,
            {
                "event_type": "projection_repair_marker_closed",
                "marker_id": marker_id,
                "closed_at": _format_dt(current_time),
                "timestamp": _format_dt(current_time),
            },
        )
        return replace(markers[marker_id], status="closed", closed_at=current_time)


def ensure_projection_schema_current(
    vault_dir: Path | str,
    *,
    projection_kind: str,
    current_authority_schema_version: int,
    projection_schema_version: int,
    caused_by: str,
    now: datetime | None = None,
) -> ProjectionRepairMarker | None:
    if int(current_authority_schema_version) <= int(projection_schema_version):
        return None
    return write_projection_repair_marker(
        vault_dir,
        kind="full_rebuild",
        scope={"projection_kind": projection_kind},
        reason="authority_schema_version_newer_than_projection",
        caused_by=caused_by,
        authority_schema_version=int(current_authority_schema_version),
        projection_schema_version=int(projection_schema_version),
        created_at=now,
    )
