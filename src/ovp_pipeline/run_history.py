from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PRODUCED_RE = re.compile(r"\bProduced\s+(\d+)\s+items?\b", re.IGNORECASE)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if remaining_seconds == 0 else f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if remaining_minutes == 0 else f"{hours}h {remaining_minutes}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d" if remaining_hours == 0 else f"{days}d {remaining_hours}h"


def _produced_count(output: object) -> int | None:
    if not isinstance(output, str):
        return None
    match = _PRODUCED_RE.search(output)
    if not match:
        return None
    return int(match.group(1))


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _step_summaries(steps: object) -> list[dict[str, Any]]:
    if not isinstance(steps, dict):
        return []
    summaries: list[dict[str, Any]] = []
    for step_name, raw_step in steps.items():
        step = raw_step if isinstance(raw_step, dict) else {}
        output = str(step.get("output") or "")
        summaries.append(
            {
                "step_name": str(step_name),
                "status": str(step.get("status") or ""),
                "output": output,
                "updated_at": str(step.get("updated_at") or ""),
                "produced_count": _produced_count(output),
                "cache_hit": bool(step.get("cache_hit")),
                "skipped": bool(step.get("skipped")),
                "blocked_reason": str(step.get("blocked_reason") or ""),
                "stage_fingerprint": str(step.get("stage_fingerprint") or ""),
                "stage_artifact": str(step.get("stage_artifact") or ""),
            }
        )
    return summaries


def _content_summary(
    *,
    produced_total: int,
    progress_summary: str,
    cache_hit_count: int = 0,
    skipped_count: int = 0,
    blocked_reasons: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if produced_total:
        parts.append(f"Produced {produced_total} items")
    if cache_hit_count:
        parts.append(f"Cache hits: {cache_hit_count}")
    if skipped_count:
        parts.append(f"skipped: {skipped_count}")
    for reason in blocked_reasons or []:
        parts.append(f"blocked: {reason}")
    if progress_summary:
        parts.append(progress_summary)
    return "; ".join(parts) if parts else "No counted work recorded."


def summarize_run(payload: dict[str, Any], *, now_iso: str | None = None) -> dict[str, Any]:
    ledger = payload.get("run_ledger") if isinstance(payload.get("run_ledger"), dict) else {}
    current = ledger.get("current_step") if isinstance(ledger.get("current_step"), dict) else {}
    status = str(payload.get("status") or ledger.get("run_state") or "")
    is_active = status in {"", "in_progress", "pending", "running"}
    started_at = (
        _parse_timestamp(ledger.get("started_at"))
        or _parse_timestamp(payload.get("start_time"))
        or _parse_timestamp(payload.get("last_updated"))
    )
    finished_at = (
        _parse_timestamp(payload.get("completed_at"))
        or _parse_timestamp(payload.get("failed_at"))
        or (None if is_active else _parse_timestamp(ledger.get("updated_at")))
        or (None if is_active else _parse_timestamp(payload.get("last_updated")))
    )
    now = _parse_timestamp(now_iso) or datetime.now(timezone.utc)
    duration_end = finished_at or now
    duration_seconds = None
    if started_at is not None:
        duration_seconds = max(0, int((duration_end - started_at).total_seconds()))

    raw_planned_steps = ledger.get("planned_steps")
    planned_steps = [str(item) for item in raw_planned_steps if item] if isinstance(raw_planned_steps, list) else []
    if not planned_steps and isinstance(payload.get("steps"), dict):
        planned_steps = [str(item) for item in payload["steps"].keys()]
    step_summaries = _step_summaries(payload.get("steps"))
    produced_total = sum(item["produced_count"] or 0 for item in step_summaries)
    cache_hit_count = sum(1 for item in step_summaries if item["cache_hit"])
    skipped_count = sum(1 for item in step_summaries if item["skipped"])
    blocked_reasons = [item["blocked_reason"] for item in step_summaries if item["blocked_reason"]]
    completed_steps = sum(1 for item in step_summaries if item["status"] == "completed")
    failed_step = next((item["step_name"] for item in step_summaries if item["status"] == "failed"), "")
    progress_summary = str(current.get("progress_summary") or "").strip()

    return {
        "run_id": str(payload.get("id") or ledger.get("run_id") or ""),
        "workflow_type": str(payload.get("type") or ""),
        "status": status,
        "run_state": str(ledger.get("run_state") or status),
        "checkpoint": str(payload.get("checkpoint") or ledger.get("current_step_name") or ""),
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if started_at else "",
        "finished_at": finished_at.strftime("%Y-%m-%dT%H:%M:%SZ") if finished_at else "",
        "updated_at": str(ledger.get("updated_at") or payload.get("last_updated") or ""),
        "duration_seconds": duration_seconds,
        "duration_summary": _format_duration(duration_seconds) if duration_seconds is not None else "",
        "workflow_profile": str(ledger.get("workflow_profile") or ""),
        "pack_name": str(ledger.get("pack_name") or ""),
        "planned_steps": planned_steps,
        "scope_summary": " → ".join(planned_steps),
        "completed_steps": completed_steps,
        "total_steps": len(planned_steps) or len(step_summaries),
        "failed_step": failed_step,
        "produced_total": produced_total,
        "work_units_done": _coerce_int(current.get("work_units_done")),
        "work_units_total": _coerce_int(current.get("work_units_total")),
        "work_units_failed": _coerce_int(current.get("work_units_failed")) or 0,
        "progress_summary": progress_summary,
        "content_summary": _content_summary(
            produced_total=produced_total,
            progress_summary=progress_summary,
            cache_hit_count=cache_hit_count,
            skipped_count=skipped_count,
            blocked_reasons=blocked_reasons,
        ),
        "step_summaries": step_summaries,
    }


def list_run_history(transactions_dir: Path, *, now_iso: str | None = None, limit: int = 10) -> dict[str, Any]:
    if not transactions_dir.exists():
        return {"total_count": 0, "items": []}
    limit_count = max(limit, 0)
    txn_files = list(transactions_dir.glob("*.json"))
    total_count = len(txn_files)
    if limit_count == 0:
        return {"total_count": total_count, "items": []}

    def file_sort_key(path: Path) -> tuple[int, str]:
        try:
            return (path.stat().st_mtime_ns, path.name)
        except OSError:
            return (0, path.name)

    items: list[dict[str, Any]] = []
    for txn_file in sorted(txn_files, key=file_sort_key, reverse=True)[:limit_count]:
        try:
            payload = json.loads(txn_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        summary = summarize_run(payload, now_iso=now_iso)
        if summary["run_id"]:
            items.append(summary)

    def sort_key(item: dict[str, Any]) -> tuple[str, str]:
        return (str(item.get("started_at") or ""), str(item.get("updated_at") or ""))

    items.sort(key=sort_key, reverse=True)
    return {"total_count": total_count, "items": items}
