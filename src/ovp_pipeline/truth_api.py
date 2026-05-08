from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import Counter
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ._truth_helpers import (  # noqa: F401 — re-exported public constants
    CONTRADICTION_STATUS_EXPLANATIONS,
    EVOLUTION_LINK_EXPLANATIONS,
    LOGGER,
    MAX_PAGE_SIZE,
    SIGNAL_TYPE_EXPLANATIONS,
    _ACTION_LOG_NAME,
    _ACTION_RUNNING_STALE_AFTER_SECONDS,
    _ASCII_TOKEN_RE,
    _BRIEFING_EVOLUTION_PRIORITY,
    _BRIEFING_SIGNAL_PRIORITY,
    _CANDIDATE_SENSITIVE_TERMS,
    _CANDIDATE_STRONG_EVIDENCE_COUNT,
    _CANDIDATE_STRONG_SOURCE_COUNT,
    _CJK_RE,
    _EVOLUTION_CANDIDATE_CACHE,
    _FENCED_FRONTMATTER_RE,
    _LEGACY_AUTO_QUEUE_SIGNAL_TYPES,
    _NOTE_CAPTURE_EVENT_TYPES,
    _PIPELINE_LOG_INDEX_CACHE,
    _REVIEW_AUDIT_LOG_NAME,
    _SIGNAL_LEDGER_SYNC_CACHE,
    _SIGNAL_LOG_NAME,
    _SOURCE_NOTE_INDEX_CACHE,
    _action_queue_path,
    _append_jsonl,
    _briefing_evolution_score,
    _briefing_priority_score,
    _build_fts_match,
    _coerce_float,
    _coerce_int,
    _db_path,
    _escape_like,
    _format_duration,
    _format_utc_timestamp,
    _materialized_truth_packs,
    _note_date_sort_key,
    _note_date_text,
    _parse_frontmatter,
    _parse_iso_datetime,
    _path_signature,
    _read_jsonl_items,
    _read_note_frontmatter,
    _read_note_text,
    _rewrite_jsonl,
    _search_root_signatures,
    _signal_dependency_signature,
    _signal_ledger_path,
    _tokenize_for_search,
    _truth_pack_candidates,
    _truth_pack_name,
    _utc_now_text,
    _validate_page_args,
    _vault_relative_path,
)
from .execution_contract_registry import resolve_focused_action_execution_contract
from .governance_registry import (
    describe_resolver_rule_contract,
    describe_signal_rule_contract,
    list_effective_governance_specs,
)
from .handler_registry import execute_focused_action_handler
from .identity import canonicalize_note_id
from .knowledge_index import rebuild_knowledge_index
from .observation_surface_registry import execute_observation_surface_builder
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .concept_registry import ConceptRegistry, ResolutionAction, STATUS_ACTIVE
from .promote_candidates import (
    candidate_file_path,
    merge_candidate,
    promote_candidate,
    reject_candidate,
)
from .runtime import (
    VaultLayout,
    action_queue_write_lock,
    knowledge_db_write_lock,  # noqa: F401 - kept as a monkeypatch seam for lock-safety tests
    resolve_vault_dir,
    signal_ledger_write_lock,
)
from .runtime_processes import detect_runtime_processes
from .runtime_state import build_runtime_state, read_runtime_state, write_runtime_state
from .run_history import list_run_history
from .txn import classify_run_ledgers


def get_runtime_status(
    vault_dir: Path | str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    classified = classify_run_ledgers(layout.transactions_dir, now_iso=now_iso)
    generated_at = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    active_run = dict(classified["active"][0]) if classified["active"] else None
    if active_run is not None:
        active_run["runtime_progress"] = _build_runtime_progress(active_run, now_iso=generated_at)
    runtime_process_items = detect_runtime_processes(resolved_vault)
    run_history = list_run_history(layout.transactions_dir, now_iso=generated_at, limit=10)
    action_worker = get_action_worker_status(
        resolved_vault, now_iso=generated_at, processes=runtime_process_items
    )
    return {
        "generated_at": generated_at,
        "active_count": len(classified["active"]),
        "stale_count": len(classified["stale"]),
        "active_run": active_run,
        "stale_runs": classified["stale"],
        "runtime_processes": {
            "active_count": len(runtime_process_items),
            "items": runtime_process_items,
        },
        "action_worker": action_worker,
        "run_history": run_history,
    }


def get_operational_runtime_state(
    vault_dir: Path | str,
    *,
    recent_limit: int = 20,
    write_projection: bool = False,
    prefer_materialized: bool = True,
) -> dict[str, Any]:
    """Return the derived operational runtime state projection.

    This is the stable provider-facing read API for BL-014. It deliberately
    returns a rebuildable projection over JSONL event streams and repair
    markers; callers must not treat the payload itself as Authority.
    """
    if write_projection:
        state = build_runtime_state(vault_dir, recent_limit=recent_limit)
    elif prefer_materialized:
        state = read_runtime_state(vault_dir)
        if state is None:
            state = build_runtime_state(vault_dir, recent_limit=recent_limit)
    else:
        state = build_runtime_state(vault_dir, recent_limit=recent_limit)
    if write_projection:
        paths = write_runtime_state(vault_dir, state)
        state = {
            **state,
            "paths": {
                "json": str(paths.json_path),
                "markdown": str(paths.markdown_path),
            },
        }
    return state


def _worker_identity_from_processes(processes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for process in processes:
        if str(process.get("process_kind") or "") == "action_worker":
            return process
    return None


def get_action_worker_status(
    vault_dir: Path | str,
    *,
    now_iso: str | None = None,
    processes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    now = _parse_iso_datetime(now_iso) or datetime.now(timezone.utc)
    state: dict[str, Any] = {}
    if layout.action_worker_state.exists():
        try:
            parsed = json.loads(layout.action_worker_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            state = parsed
    worker_process = _worker_identity_from_processes(
        processes if processes is not None else detect_runtime_processes(resolved_vault)
    )
    started_at = _parse_iso_datetime(state.get("started_at"))
    heartbeat_at = _parse_iso_datetime(state.get("heartbeat_at"))
    elapsed_seconds = max(0, int((now - started_at).total_seconds())) if started_at else None
    heartbeat_age_seconds = (
        max(0, int((now - heartbeat_at).total_seconds())) if heartbeat_at else None
    )
    state_name = str(state.get("state") or "stopped")
    active = state_name in {"running", "idle"} and (
        heartbeat_age_seconds is None
        or heartbeat_age_seconds <= _ACTION_RUNNING_STALE_AFTER_SECONDS
    )
    if worker_process is not None:
        active = True
    return {
        "active": active,
        "worker_id": str(state.get("worker_id") or ""),
        "pid": int(
            worker_process.get("pid")
            if worker_process and worker_process.get("pid")
            else state.get("pid") or 0
        ),
        "state": state_name,
        "mode": str(state.get("mode") or ""),
        "safe_only": bool(state.get("safe_only", False)),
        "started_at": str(state.get("started_at") or ""),
        "heartbeat_at": str(state.get("heartbeat_at") or ""),
        "elapsed_seconds": elapsed_seconds,
        "elapsed_summary": _format_duration(elapsed_seconds) if elapsed_seconds is not None else "",
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_age_summary": _format_duration(heartbeat_age_seconds)
        if heartbeat_age_seconds is not None
        else "",
        "current_action": state.get("current_action")
        if isinstance(state.get("current_action"), dict)
        else {},
        "last_result": state.get("last_result")
        if isinstance(state.get("last_result"), dict)
        else {},
        "process": worker_process or {},
    }


def record_action_worker_state(
    vault_dir: Path | str,
    *,
    worker_id: str,
    pid: int,
    state: str,
    mode: str,
    safe_only: bool,
    current_action: dict[str, Any] | None = None,
    last_result: dict[str, Any] | None = None,
    interval_seconds: float | None = None,
    max_runs: int | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    existing: dict[str, Any] = {}
    if layout.action_worker_state.exists():
        try:
            parsed = json.loads(layout.action_worker_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            existing = parsed
    timestamp = _utc_now_text()
    payload = {
        "worker_id": worker_id,
        "pid": pid,
        "state": state,
        "mode": mode,
        "safe_only": safe_only,
        "started_at": started_at or str(existing.get("started_at") or timestamp),
        "heartbeat_at": timestamp,
        "current_action": current_action or {},
        "last_result": last_result or existing.get("last_result") or {},
    }
    if interval_seconds is not None:
        payload["interval_seconds"] = max(0.0, float(interval_seconds))
    if max_runs is not None:
        payload["max_runs"] = max_runs
    layout.action_worker_state.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = layout.action_worker_state.with_name(
        f"{layout.action_worker_state.name}.{worker_id}.{pid}.tmp"
    )
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, layout.action_worker_state)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return payload


def _runtime_planned_steps(
    active_run: dict[str, Any], ledger: dict[str, Any], current_step_name: str
) -> list[str]:
    raw_planned = ledger.get("planned_steps")
    planned = [str(item) for item in raw_planned if item] if isinstance(raw_planned, list) else []
    if planned:
        return planned
    raw_steps = active_run.get("steps")
    if isinstance(raw_steps, dict):
        planned = [str(item) for item in raw_steps.keys() if item]
    if current_step_name and current_step_name not in planned:
        planned.append(current_step_name)
    return planned


def _build_runtime_progress(active_run: dict[str, Any], *, now_iso: str) -> dict[str, Any]:
    ledger = active_run.get("run_ledger") if isinstance(active_run.get("run_ledger"), dict) else {}
    current = ledger.get("current_step") if isinstance(ledger.get("current_step"), dict) else {}
    current_step_name = str(
        current.get("step_name")
        or ledger.get("current_step_name")
        or active_run.get("checkpoint")
        or ""
    )
    planned_steps = _runtime_planned_steps(active_run, ledger, current_step_name)
    current_index: int | None = None
    if current_step_name and current_step_name in planned_steps:
        current_index = planned_steps.index(current_step_name) + 1
    stage_total = len(planned_steps) or None
    if current_index is not None and stage_total is not None:
        stage_summary = f"Stage {current_index}/{stage_total}: {current_step_name}"
    elif current_step_name:
        stage_summary = f"Stage unknown: {current_step_name}"
    else:
        stage_summary = "Stage unknown"

    done = _coerce_int(current.get("work_units_done"))
    total = _coerce_int(current.get("work_units_total"))
    failed = _coerce_int(current.get("work_units_failed")) or 0
    percent = _coerce_float(current.get("progress_percent"))
    if percent is None and done is not None and total and total > 0:
        percent = round((done / total) * 100.0, 1)
    if percent is not None:
        percent = round(percent, 1)
    progress_summary = str(current.get("progress_summary") or "").strip()
    if not progress_summary:
        if total is not None and done is not None:
            progress_summary = f"{done}/{total} work units completed"
        else:
            progress_summary = "Progress is currently indeterminate."

    now = _parse_iso_datetime(now_iso) or datetime.now(timezone.utc)
    started_at = (
        _parse_iso_datetime(current.get("step_started_at"))
        or _parse_iso_datetime(ledger.get("started_at"))
        or _parse_iso_datetime(active_run.get("start_time"))
    )
    elapsed_seconds: int | None = None
    items_per_second: float | None = None
    items_per_minute: float | None = None
    eta_seconds: int | None = None
    eta_at: str | None = None
    rate_summary = "Speed unavailable until counted progress starts."
    eta_summary = "ETA unavailable until counted progress starts."
    if started_at is not None:
        elapsed_seconds = max(0, int((now - started_at).total_seconds()))
    if (
        done is not None
        and total is not None
        and total > 0
        and done > 0
        and elapsed_seconds
        and elapsed_seconds > 0
    ):
        raw_items_per_second = done / elapsed_seconds
        items_per_second = round(raw_items_per_second, 6)
        items_per_minute = round(raw_items_per_second * 60.0, 2)
        rate_summary = f"{raw_items_per_second:.4f} files/s ({items_per_minute:.2f} files/min)"
        remaining = max(total - done, 0)
        if remaining:
            eta_seconds = int(round(remaining / raw_items_per_second))
            eta_at = _format_utc_timestamp(now + timedelta(seconds=eta_seconds))
            eta_summary = f"~{_format_duration(eta_seconds)} remaining, ETA {eta_at}"
        else:
            eta_seconds = 0
            eta_at = _format_utc_timestamp(now)
            eta_summary = f"Current stage complete as of {eta_at}"

    return {
        "stage": {
            "current_index": current_index,
            "total": stage_total,
            "current_name": current_step_name,
            "planned_steps": planned_steps,
            "remaining_steps": planned_steps[current_index:] if current_index is not None else [],
            "summary": stage_summary,
        },
        "work": {
            "mode": str(current.get("progress_mode") or "indeterminate"),
            "done": done,
            "total": total,
            "failed": failed,
            "percent": percent,
            "current_item": current.get("current_item"),
            "summary": progress_summary,
        },
        "performance": {
            "elapsed_seconds": elapsed_seconds,
            "elapsed_summary": _format_duration(elapsed_seconds)
            if elapsed_seconds is not None
            else "",
            "items_per_second": items_per_second,
            "items_per_minute": items_per_minute,
            "rate_summary": rate_summary,
            "eta_seconds": eta_seconds,
            "eta_at": eta_at,
            "eta_summary": eta_summary,
        },
    }


def _evolution_dependency_signature(vault_dir: Path) -> tuple[tuple[str, int, int], ...]:
    return _signal_dependency_signature(vault_dir)


def record_review_action(
    vault_dir: Path | str,
    *,
    event_type: str,
    payload: dict[str, Any],
    slug: str = "",
    session_id: str = "ovp-ui",
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    timestamp = _utc_now_text()
    event = {
        "timestamp": timestamp,
        "session_id": session_id,
        "event_type": event_type,
        "slug": slug,
        **payload,
    }
    _append_jsonl(layout.logs_dir / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl", event)
    return event


def _candidate_review_suggestion(
    registry: ConceptRegistry, entry: Any
) -> tuple[str, list[tuple[Any, float]]]:
    similar: list[tuple[Any, float]] = []
    seen_slugs: set[str] = set()
    found_ambiguous_active = False
    resolution = registry.resolve_mention(
        entry.title,
        area=entry.area or None,
        include_related_context=False,
    )
    if resolution.action == ResolutionAction.LINK_EXISTING and resolution.entry:
        candidate = registry.find_by_slug(resolution.entry.slug)
        if (
            candidate
            and candidate.slug != entry.slug
            and candidate.status == STATUS_ACTIVE
        ):
            similar.append((candidate, resolution.confidence))
            seen_slugs.add(candidate.slug)
    elif resolution.action == ResolutionAction.REVIEW_AMBIGUOUS:
        for ambiguous in resolution.ambiguous_entries:
            candidate = registry.find_by_slug(ambiguous.slug)
            if (
                candidate
                and candidate.slug != entry.slug
                and candidate.status == STATUS_ACTIVE
                and candidate.slug not in seen_slugs
            ):
                similar.append((candidate, resolution.confidence))
                seen_slugs.add(candidate.slug)
                found_ambiguous_active = True

    if not similar:
        for near in registry._safe_near_candidates(entry.title, area=entry.area or None, topk=10):
            candidate = registry.find_by_slug(near.record.entry.slug)
            if (
                candidate
                and candidate.slug != entry.slug
                and candidate.status == STATUS_ACTIVE
                and candidate.slug not in seen_slugs
            ):
                similar.append((candidate, near.score))
                seen_slugs.add(candidate.slug)
                if len(similar) >= 5:
                    break

    similar = sorted(similar, key=lambda item: item[1], reverse=True)[:5]
    action = "keep_as_candidate"
    if found_ambiguous_active:
        action = "keep_as_candidate"
    elif (
        entry.source_count >= _CANDIDATE_STRONG_SOURCE_COUNT
        or entry.evidence_count >= _CANDIDATE_STRONG_EVIDENCE_COUNT
    ):
        if similar and similar[0][1] >= 0.7:
            action = "merge_as_alias"
        else:
            action = "promote_to_active"
    elif similar and similar[0][1] >= 0.8:
        action = "merge_as_alias"
    return action, similar


def _candidate_risk_layer(
    entry: Any,
    *,
    suggested_action: str,
    similar_existing: list[tuple[Any, float]],
) -> dict[str, Any]:
    source_count = int(getattr(entry, "source_count", 0) or 0)
    evidence_count = int(getattr(entry, "evidence_count", 0) or 0)
    if (
        evidence_count >= _CANDIDATE_STRONG_EVIDENCE_COUNT
        or source_count >= _CANDIDATE_STRONG_SOURCE_COUNT
    ):
        evidence_strength = "strong"
    elif evidence_count > 0 or source_count > 0:
        evidence_strength = "partial"
    else:
        evidence_strength = "weak"

    identity_ambiguity = "clear"
    if len(similar_existing) >= 2 and suggested_action == "keep_as_candidate":
        identity_ambiguity = "ambiguous"
    elif similar_existing:
        identity_ambiguity = "possible_duplicate"

    sensitivity_text = " ".join(
        [
            str(getattr(entry, "title", "") or ""),
            str(getattr(entry, "definition", "") or ""),
            str(getattr(entry, "area", "") or ""),
        ]
    ).lower()
    sensitivity = "sensitive" if any(term in sensitivity_text for term in _CANDIDATE_SENSITIVE_TERMS) else "normal"
    impact = {
        "promote_to_active": "canonical_write",
        "merge_as_alias": "identity_merge",
        "keep_as_candidate": "review_only",
    }.get(suggested_action, "review_only")

    reasons: list[str] = []
    if evidence_strength == "weak":
        reasons.append("weak_evidence")
    elif evidence_strength == "partial":
        reasons.append("partial_evidence")
    if identity_ambiguity == "ambiguous":
        reasons.append("identity_ambiguous")
    elif identity_ambiguity == "possible_duplicate":
        reasons.append("possible_duplicate")
    if sensitivity == "sensitive":
        reasons.append("sensitive_subject")
    if impact in {"canonical_write", "identity_merge"}:
        reasons.append("canonical_impact")

    tier = "low"
    if identity_ambiguity == "ambiguous" or (sensitivity == "sensitive" and impact != "review_only"):
        tier = "high"
    elif (
        evidence_strength in {"weak", "partial"}
        or identity_ambiguity == "possible_duplicate"
        or impact in {"canonical_write", "identity_merge"}
    ):
        tier = "medium"

    return {
        "tier": tier,
        "reasons": reasons,
        "factors": {
            "evidence_strength": evidence_strength,
            "identity_ambiguity": identity_ambiguity,
            "sensitivity": sensitivity,
            "impact": impact,
        },
    }


def list_candidate_concepts(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List candidate concepts from the registry with review suggestions."""
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    resolved_vault = resolve_vault_dir(vault_dir)
    registry = ConceptRegistry(resolved_vault).load()
    normalized_query = (query or "").strip().lower()
    filtered_candidates = []
    for entry in registry.candidates:
        searchable = " ".join(
            [
                entry.slug,
                entry.title,
                entry.definition,
                entry.area,
                *entry.aliases,
            ]
        ).lower()
        if normalized_query and normalized_query not in searchable:
            continue
        filtered_candidates.append(entry)

    sorted_candidates = sorted(
        filtered_candidates, key=lambda item: (item.last_seen_at, item.slug), reverse=True
    )
    page_candidates = sorted_candidates[offset : offset + limit]
    items: list[dict[str, Any]] = []

    for entry in page_candidates:
        suggested_action, similar_existing = _candidate_review_suggestion(registry, entry)
        risk = _candidate_risk_layer(
            entry,
            suggested_action=suggested_action,
            similar_existing=similar_existing,
        )
        candidate_path = candidate_file_path(resolved_vault, entry.slug)
        candidate_rel = ""
        candidate_note_path = ""
        if candidate_path.exists():
            candidate_rel = str(candidate_path.relative_to(resolved_vault))
            candidate_note_path = f"/note?path={quote(candidate_rel, safe='')}"

        items.append(
            {
                "slug": entry.slug,
                "title": entry.title,
                "aliases": list(entry.aliases),
                "definition": entry.definition,
                "area": entry.area,
                "status": entry.status,
                "review_state": entry.review_state,
                "source_count": entry.source_count,
                "evidence_count": entry.evidence_count,
                "last_seen_at": entry.last_seen_at,
                "candidate_path": candidate_rel,
                "candidate_note_path": candidate_note_path,
                "suggested_action": suggested_action,
                "risk": risk,
                "similar_existing": [
                    {
                        "slug": similar.slug,
                        "title": similar.title,
                        "score": score,
                        "path": f"/object?id={quote(similar.slug, safe='')}",
                    }
                    for similar, score in similar_existing
                ],
            }
        )

    risk_counts = {"low": 0, "medium": 0, "high": 0}
    for item in items:
        tier = str(item.get("risk", {}).get("tier") or "low")
        if tier not in risk_counts:
            continue
        risk_counts[tier] += 1

    return {
        "screen": "candidates/browser",
        "query": query or "",
        "limit": limit,
        "offset": offset,
        "count": len(filtered_candidates),
        "status_counts": {"candidate": len(filtered_candidates)},
        "risk_counts": risk_counts,
        "items": items,
    }


def _emit_extract_provenance(
    vault_dir: Path,
    *,
    pack_name: str,
    target_slug: str,
) -> None:
    """BL-056: write one ``stage='extract'`` row backdated to the
    candidate's extraction time.

    Reads ``absorbed_at`` + ``extraction_prompt_version`` from the
    promoted evergreen's frontmatter (the candidate file moved into
    ``10-Knowledge/Evergreen/`` carries those fields verbatim from
    ``auto_evergreen_extractor``).  When the frontmatter doesn't
    carry ``absorbed_at`` (legacy candidates, hand-edited evergreens),
    the row is skipped — emitting at ``now`` would lie about the
    chain timestamp and break the audit guarantee.

    Best-effort like ``_emit_promote_provenance``: provenance failure
    must not abort the review action's primary commit.
    """
    import sqlite3

    from .provenance import upsert_provenance
    from .runtime import VaultLayout

    layout = VaultLayout.from_vault(vault_dir)
    if not layout.knowledge_db.exists():
        return
    with sqlite3.connect(layout.knowledge_db) as conn:
        row = conn.execute(
            "SELECT canonical_path, source_url FROM objects WHERE pack=? AND object_id=?",
            (pack_name, target_slug),
        ).fetchone()
        if not row:
            return
        canonical_path, source_url = row[0] or "", row[1] or ""
        if not canonical_path:
            return
        # Try the absolute path first (common since rebuild stores
        # absolute canonical_path); fall back to vault-relative.
        from pathlib import Path as _Path

        abs_path = _Path(canonical_path)
        if not abs_path.is_absolute():
            abs_path = resolve_vault_dir(vault_dir) / canonical_path
        if not abs_path.is_file():
            return
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            return
        frontmatter = _parse_frontmatter(text)
        absorbed_at = str(frontmatter.get("absorbed_at") or "").strip()
        if not absorbed_at:
            return
        prompt_version = str(frontmatter.get("extraction_prompt_version") or "").strip()
        metadata: dict[str, Any] = {"via": "auto_evergreen_extractor"}
        if prompt_version:
            metadata["prompt_version"] = prompt_version
        upsert_provenance(
            conn,
            pack=pack_name,
            object_id=target_slug,
            derived_via_stage="extract",
            source_url=source_url,
            metadata=metadata,
            derived_at=absorbed_at,
        )
        conn.commit()


def _emit_promote_provenance(
    vault_dir: Path,
    *,
    pack_name: str,
    target_slug: str,
    lifecycle_action: str,
    source_slug: str,
    note: str = "",
) -> None:
    """BL-056: write one ``stage='promote'`` (or ``'merge'``) row to
    the provenance audit log.  Reads the freshly-rebuilt
    ``objects.source_url`` for ``target_slug`` so the promote row is
    consistent with the ingest row written by the rebuild.

    Best-effort.  ``provenance.upsert_provenance`` itself swallows
    schema-not-present errors; this helper only protects against
    the DB connect failing entirely (e.g. file permission flakes).
    """
    import sqlite3

    from .provenance import upsert_provenance
    from .runtime import VaultLayout

    layout = VaultLayout.from_vault(vault_dir)
    if not layout.knowledge_db.exists():
        return
    with sqlite3.connect(layout.knowledge_db) as conn:
        row = conn.execute(
            "SELECT source_url FROM objects WHERE pack=? AND object_id=?",
            (pack_name, target_slug),
        ).fetchone()
        source_url = (row or ("",))[0] or ""
        metadata: dict[str, Any] = {
            "lifecycle_action": lifecycle_action,
            "candidate_slug": source_slug,
        }
        if note:
            metadata["note"] = note
        upsert_provenance(
            conn,
            pack=pack_name,
            object_id=target_slug,
            derived_via_stage="promote",
            source_url=source_url,
            parent_object_id=source_slug if source_slug != target_slug else None,
            metadata=metadata,
        )
        conn.commit()


def review_candidate_concept(
    vault_dir: Path | str,
    *,
    slug: str,
    action: str,
    target_slug: str | None = None,
    note: str = "",
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Apply a candidate review action through the existing lifecycle helpers."""
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_slug = slug.strip()
    normalized_action = action.strip()
    normalized_target = (target_slug or "").strip()
    if not normalized_slug:
        raise ValueError("missing candidate slug")

    action_aliases = {
        "promote": "promote",
        "promote_to_active": "promote",
        "merge": "merge",
        "merge_as_alias": "merge",
        "reject": "reject",
    }
    lifecycle_action = action_aliases.get(normalized_action)
    if lifecycle_action is None:
        raise ValueError("invalid candidate action")

    if lifecycle_action == "promote":
        mutation = promote_candidate(resolved_vault, normalized_slug, dry_run=False)
    elif lifecycle_action == "merge":
        if not normalized_target:
            raise ValueError("missing target_slug for merge")
        mutation = merge_candidate(
            resolved_vault, normalized_slug, normalized_target, dry_run=False
        )
    else:
        mutation = reject_candidate(resolved_vault, normalized_slug, dry_run=False)

    knowledge_index_rebuilt = False
    knowledge_index_result: dict[str, Any] = {}
    knowledge_index_error = ""
    rebuild_exception: Exception | None = None
    if lifecycle_action in {"promote", "merge"}:
        try:
            knowledge_index_result = rebuild_knowledge_index(resolved_vault, pack_name=pack_name)
            knowledge_index_rebuilt = True
        except Exception as exc:
            knowledge_index_error = str(exc)
            rebuild_exception = exc

    # BL-056: emit ``stage='extract'`` + ``stage='promote'`` (or
    # ``'merge'``) provenance rows for the resulting evergreen, in
    # addition to the ``stage='ingest'`` row the rebuild already
    # wrote.  Two writes, two stage labels, one event:
    #
    #   - ``extract`` is backdated to the candidate's
    #     ``absorbed_at`` so the chain timestamp reflects when
    #     ``auto_evergreen_extractor`` produced the candidate, not
    #     when the human reviewed it.
    #   - ``promote`` carries the lifecycle action + reviewer note
    #     at the current time.
    #
    # Best-effort: provenance failure must not abort the review
    # action's primary commit.  Logged at WARN so the operator
    # knows the audit row is missing.
    if knowledge_index_rebuilt and lifecycle_action in {"promote", "merge"}:
        target_slug_value = mutation.target_slug or normalized_slug
        truth_pack = _truth_pack_name(pack_name)
        try:
            _emit_extract_provenance(
                resolved_vault,
                pack_name=truth_pack,
                target_slug=target_slug_value,
            )
        except Exception as exc:  # noqa: BLE001 — never block the review path
            import logging
            logging.getLogger(__name__).warning(
                "provenance emit for extract failed: %s", exc,
            )
        try:
            _emit_promote_provenance(
                resolved_vault,
                pack_name=truth_pack,
                target_slug=target_slug_value,
                lifecycle_action=lifecycle_action,
                source_slug=normalized_slug,
                note=note,
            )
        except Exception as exc:  # noqa: BLE001 — never block the review path
            import logging
            logging.getLogger(__name__).warning(
                "provenance emit for promote/merge failed: %s", exc,
            )

    status_by_action = {
        "promote": "promoted",
        "merge": "merged",
        "reject": "rejected",
    }
    mutation_payload = mutation.to_dict()
    event = record_review_action(
        resolved_vault,
        event_type="ui_candidate_reviewed",
        slug=normalized_slug,
        payload={
            "candidate_slug": normalized_slug,
            "target_slug": mutation.target_slug or normalized_target,
            "action": lifecycle_action,
            "status": status_by_action[lifecycle_action],
            "note": note,
            "pack": _truth_pack_name(pack_name),
            "mutation": mutation_payload,
            "knowledge_index_rebuilt": knowledge_index_rebuilt,
            "knowledge_index_result": knowledge_index_result,
            "knowledge_index_error": knowledge_index_error,
        },
    )
    if rebuild_exception is not None:
        raise RuntimeError(
            f"candidate review applied but knowledge index rebuild failed: {knowledge_index_error}"
        ) from rebuild_exception
    return {
        "action": lifecycle_action,
        "slug": normalized_slug,
        "target_slug": mutation.target_slug or normalized_target,
        "status": status_by_action[lifecycle_action],
        "note": note,
        "mutation": mutation_payload,
        "knowledge_index_rebuilt": knowledge_index_rebuilt,
        "knowledge_index_result": knowledge_index_result,
        "knowledge_index_error": knowledge_index_error,
        "audit_event": event,
    }


def _is_moc_row(note_type: str, path: str) -> bool:
    return note_type == "moc" or "/10-Knowledge/Atlas/" in path or Path(path).name.startswith("MOC")


def _batch_object_rows(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not object_ids:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    placeholders = ",".join("?" for _ in object_ids)
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT pack, object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE pack IN ({pack_placeholders}) AND object_id IN ({placeholders})
            ORDER BY CASE pack
              {"".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))}
              ELSE {len(pack_candidates)}
            END, object_id
            """,
            (*pack_candidates, *object_ids, *pack_candidates),
        ).fetchall()
    items: dict[str, dict[str, Any]] = {}
    for pack, object_id, object_kind, title, canonical_path, source_slug in rows:
        if object_id in items:
            continue
        items[object_id] = {
            "object_id": object_id,
            "object_kind": object_kind,
            "title": title,
            "canonical_path": _vault_relative_path(resolved_vault, canonical_path),
            "source_slug": source_slug,
            "pack": pack,
        }
    return items


def get_object_provenance_map(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not object_ids:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    ordered_object_ids = list(dict.fromkeys(object_ids))
    object_rows = _batch_object_rows(vault_dir, ordered_object_ids, pack_name=pack_name)
    placeholders = ",".join("?" for _ in ordered_object_ids)
    truth_pack = _truth_pack_name(pack_name)
    with sqlite3.connect(db_path) as conn:
        mention_rows = conn.execute(
            f"""
            SELECT page_links.target_slug, pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path,
                   objects.object_kind
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            LEFT JOIN objects ON objects.object_id = pages_index.slug AND objects.pack = ?
            WHERE page_links.target_slug IN ({placeholders})
              AND pages_index.slug != page_links.target_slug
            ORDER BY page_links.target_slug, pages_index.slug
            """,
            (truth_pack, *ordered_object_ids),
        ).fetchall()

    provenance = {
        object_id: {
            "title": object_rows.get(object_id, {}).get("title", object_id),
            "evergreen_path": object_rows.get(object_id, {}).get("canonical_path", ""),
            "source_notes": [],
            "mocs": [],
        }
        for object_id in ordered_object_ids
    }
    for target_slug, slug, title, note_type, path, object_kind in mention_rows:
        item = {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": _vault_relative_path(resolved_vault, path),
            "object_kind": object_kind or "",
        }
        if _is_moc_row(note_type, path):
            provenance[target_slug]["mocs"].append(item)
        elif note_type != "evergreen":
            provenance[target_slug]["source_notes"].append(item)
    return provenance


def get_review_context(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {
            "object_count": 0,
            "source_note_count": 0,
            "moc_count": 0,
            "contradiction_count": 0,
            "open_contradiction_count": 0,
            "stale_summary_count": 0,
            "latest_event_date": "",
            "source_notes": [],
            "mocs": [],
            "stale_summary_object_ids": [],
            "contradiction_object_ids": [],
            "recent_review_actions": [],
        }

    provenance_map = get_object_provenance_map(
        vault_dir, normalized_object_ids, pack_name=pack_name
    )
    source_notes: dict[str, dict[str, Any]] = {}
    mocs: dict[str, dict[str, Any]] = {}
    for provenance in provenance_map.values():
        for note in provenance["source_notes"]:
            source_notes.setdefault(note["slug"], note)
        for moc in provenance["mocs"]:
            mocs.setdefault(moc["slug"], moc)

    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    with sqlite3.connect(db_path) as conn:
        stale_rows = conn.execute(
            f"""
            SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
                   COALESCE(rel.outgoing_count, 0) AS outgoing_count
            FROM objects
            LEFT JOIN compiled_summaries
              ON compiled_summaries.pack = objects.pack
             AND compiled_summaries.object_id = objects.object_id
            LEFT JOIN (
                SELECT pack, source_object_id, COUNT(*) AS outgoing_count
                FROM relations
                GROUP BY pack, source_object_id
            ) AS rel ON rel.pack = objects.pack AND rel.source_object_id = objects.object_id
            WHERE objects.object_id IN ({placeholders})
              AND objects.pack IN ({pack_placeholders})
            ORDER BY CASE objects.pack
              {"".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))}
              ELSE {len(pack_candidates)}
            END, objects.object_id
            """,
            tuple([*normalized_object_ids, *pack_candidates, *pack_candidates]),
        ).fetchall()
        event_row = conn.execute(
            f"""
            SELECT MAX(event_date)
            FROM timeline_events
            WHERE slug IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchone()
        contradiction_rows = conn.execute(
            f"""
            SELECT contradiction_id, positive_claim_ids_json, negative_claim_ids_json, status
            FROM contradictions
            WHERE pack IN ({pack_placeholders})
            ORDER BY CASE pack
              {"".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))}
              ELSE {len(pack_candidates)}
            END, contradiction_id
            """,
            tuple([*pack_candidates, *pack_candidates]),
        ).fetchall()

    stale_summaries: list[dict[str, Any]] = []
    seen_stale_object_ids: set[str] = set()
    for object_id, title, summary_text, outgoing_count in stale_rows:
        if str(object_id) in seen_stale_object_ids:
            continue
        seen_stale_object_ids.add(str(object_id))
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        stale_summaries.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "object_path": f"/object?id={object_id}",
            }
        )
    stale_summary_object_ids = [item["object_id"] for item in stale_summaries]

    contradiction_ids: list[str] = []
    open_contradiction_ids: list[str] = []
    contradiction_object_ids: set[str] = set()
    object_id_set = set(normalized_object_ids)
    for contradiction_id, positive_json, negative_json, status in contradiction_rows:
        claim_ids = json.loads(positive_json) + json.loads(negative_json)
        matched_object_ids = {
            claim_id.split("::", 1)[0]
            for claim_id in claim_ids
            if claim_id.split("::", 1)[0] in object_id_set
        }
        if not matched_object_ids:
            continue
        contradiction_ids.append(str(contradiction_id))
        contradiction_object_ids.update(matched_object_ids)
        if status == "open":
            open_contradiction_ids.append(str(contradiction_id))

    return {
        "object_count": len(normalized_object_ids),
        "source_note_count": len(source_notes),
        "moc_count": len(mocs),
        "contradiction_count": len(contradiction_ids),
        "open_contradiction_count": len(open_contradiction_ids),
        "stale_summary_count": len(stale_summaries),
        "latest_event_date": str(event_row[0] or ""),
        "source_notes": list(source_notes.values()),
        "mocs": list(mocs.values()),
        "stale_summary_object_ids": stale_summary_object_ids,
        "contradiction_object_ids": sorted(contradiction_object_ids),
        "recent_review_actions": list_review_actions(
            vault_dir, object_ids=normalized_object_ids, limit=5
        ),
    }


def _claim_details_map(vault_dir: Path | str, claim_ids: list[str]) -> dict[str, dict[str, Any]]:
    normalized_claim_ids = list(dict.fromkeys(claim_id for claim_id in claim_ids if claim_id))
    if not normalized_claim_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_claim_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT claims.claim_id, claims.object_id, objects.title, claims.claim_kind, claims.claim_text, claims.confidence
            FROM claims
            JOIN objects ON objects.object_id = claims.object_id
            WHERE claims.claim_id IN ({placeholders})
            ORDER BY claims.claim_id
            """,
            tuple(normalized_claim_ids),
        ).fetchall()
    return {
        row[0]: {
            "claim_id": row[0],
            "object_id": row[1],
            "object_title": row[2],
            "claim_kind": row[3],
            "claim_text": row[4],
            "confidence": row[5],
        }
        for row in rows
    }


def _claim_evidence_map(
    vault_dir: Path | str, claim_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    normalized_claim_ids = list(dict.fromkeys(claim_id for claim_id in claim_ids if claim_id))
    if not normalized_claim_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_claim_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT claim_id, source_slug, evidence_kind, quote_text,
                   quote_start_line, quote_end_line, quote_start_char, quote_end_char
            FROM claim_evidence
            WHERE claim_id IN ({placeholders})
            ORDER BY claim_id, source_slug, evidence_kind
            """,
            tuple(normalized_claim_ids),
        ).fetchall()
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        claim_id, source_slug, evidence_kind, quote_text = row[:4]
        evidence_map.setdefault(claim_id, []).append(
            {
                "source_slug": source_slug,
                "evidence_kind": evidence_kind,
                "quote_text": quote_text or "",
                "quote_start_line": int(row[4] or 0),
                "quote_end_line": int(row[5] or 0),
                "quote_start_char": int(row[6] or 0),
                "quote_end_char": int(row[7] or 0),
            }
        )
    return evidence_map


def _rank_contradiction_evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    rank = 1
    for polarity, claims in (
        ("positive", item["positive_claims"]),
        ("negative", item["negative_claims"]),
    ):
        for claim in claims:
            for evidence in claim["evidence"]:
                ranked.append(
                    {
                        "rank": rank,
                        "polarity": polarity,
                        "claim_id": claim["claim_id"],
                        "object_id": claim["object_id"],
                        "object_title": claim["object_title"],
                        "evidence_kind": evidence["evidence_kind"],
                        "quote_text": evidence["quote_text"],
                        "source_slug": evidence["source_slug"],
                    }
                )
                rank += 1
    return ranked


def _candidate_evolution_id(
    *,
    link_type: str,
    subject_kind: str,
    subject_id: str,
    earlier_ref: str,
    later_ref: str,
) -> str:
    fingerprint = "::".join([link_type, subject_kind, subject_id, earlier_ref, later_ref])
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _page_paths_for_slugs(vault_dir: Path | str, slugs: list[str]) -> dict[str, str]:
    normalized_slugs = list(dict.fromkeys(slug for slug in slugs if slug))
    if not normalized_slugs:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    placeholders = ",".join("?" for _ in normalized_slugs)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT slug, path
            FROM pages_index
            WHERE slug IN ({placeholders})
            """,
            tuple(normalized_slugs),
        ).fetchall()
    return {str(slug): _vault_relative_path(resolved_vault, path) for slug, path in rows}


_SUPERSESSION_CUE_RE = re.compile(
    r"\b(supersed(?:e|es|ed|ing)|replace(?:s|d|ment|ments)?|obsolete|deprecated|no longer|instead)\b",
    re.IGNORECASE,
)
_CONFIRMATION_CUE_RE = re.compile(
    r"\b(confirm(?:s|ed|ing)?|corroborat(?:e|es|ed|ing)|validated?|agrees?\s+with|supports?)\b",
    re.IGNORECASE,
)


def _has_cue(
    vault_dir: Path | str,
    note_path: str,
    pattern: re.Pattern[str],
) -> bool:
    text = _read_note_text(vault_dir, note_path).lower()
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 24) : match.start()]
        if re.search(r"(?:\bnot\s+|\bwithout\s+|n't\s+)$", prefix):
            continue
        return True
    return False


def _has_supersession_cue(vault_dir: Path | str, note_path: str) -> bool:
    return _has_cue(vault_dir, note_path, _SUPERSESSION_CUE_RE)


def _has_confirmation_cue(vault_dir: Path | str, note_path: str) -> bool:
    return _has_cue(vault_dir, note_path, _CONFIRMATION_CUE_RE)


def _evolution_candidate_matches_query(item: dict[str, Any], normalized_query: str) -> bool:
    haystacks = [
        str(item.get("link_type") or "").lower(),
        str(item.get("subject_kind") or "").lower(),
        str(item.get("subject_id") or "").lower(),
        str(item.get("earlier_ref") or "").lower(),
        str(item.get("later_ref") or "").lower(),
        *(str(path).lower() for path in item.get("source_paths", [])),
        *(str(code).lower() for code in item.get("reason_codes", [])),
        *(
            str(entry.get("source_slug") or entry.get("path") or entry.get("title") or "").lower()
            for entry in item.get("evidence", [])
            if isinstance(entry, dict)
        ),
    ]
    return any(normalized_query in haystack for haystack in haystacks if haystack)


_OBJECTS_INDEX_SORTS = ("alpha", "most_linked")


def list_objects(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    object_kind: str | None = None,
    pack_name: str | None = None,
    sort: str = "alpha",
) -> list[dict[str, Any]]:
    if sort not in _OBJECTS_INDEX_SORTS:
        sort = "alpha"
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    normalized_query = _escape_like(query.strip().lower()) if query else ""

    pack_placeholders = ",".join("?" for _ in pack_candidates)

    # Build a column-prefix-aware WHERE clause; ``alpha`` queries the
    # ``objects`` table directly while ``most_linked`` aliases it as ``o``
    # so it can LEFT JOIN against a backlink-count subquery.
    col_prefix = "o." if sort == "most_linked" else ""
    pack_order = " ".join(
        f"WHEN ? THEN {index}" for index, _ in enumerate(pack_candidates)
    )
    fallback_order = len(pack_candidates)

    inner_params: list[Any] = [*pack_candidates]
    where_parts = [f"{col_prefix}pack IN ({pack_placeholders})"]
    if object_kind:
        from .object_kinds import normalize_kind

        where_parts.append(f"{col_prefix}object_kind = ?")
        inner_params.append(normalize_kind(object_kind))
    if normalized_query:
        where_parts.append(
            f"({col_prefix}object_id LIKE ? ESCAPE '\\'"
            f" OR {col_prefix}title LIKE ? ESCAPE '\\'"
            f" OR {col_prefix}source_slug LIKE ? ESCAPE '\\')"
        )
        inner_params.extend([
            f"%{normalized_query}%",
            f"%{normalized_query}%",
            f"%{normalized_query}%",
        ])
    where_clause = " AND ".join(where_parts)

    if sort == "most_linked":
        # Rank by incoming-relation count (backlinks).  The LEFT JOIN
        # keeps zero-link objects so the result count matches the
        # ``count_objects`` total used for pagination.  Tie-break by
        # ``object_id`` to keep ordering deterministic.
        #
        # The relations sub-query intentionally groups by
        # ``target_object_id`` only (no pack join): an object's
        # popularity should reflect inbound links from every active
        # pack in the vault, not just the pack the row happens to
        # live in.  Without this, a "Core" object referenced by many
        # "User"-pack rows would look unlinked.
        select_clause = (
            "SELECT pack, object_id, object_kind, title, canonical_path,"
            " source_slug, backlink_count"
        )
        inner_select = (
            "SELECT o.pack AS pack, o.object_id AS object_id,"
            " o.object_kind AS object_kind, o.title AS title,"
            " o.canonical_path AS canonical_path, o.source_slug AS source_slug,"
            " COALESCE(r.backlinks, 0) AS backlink_count,"
            " ROW_NUMBER() OVER ("
            "PARTITION BY o.object_id"
            f" ORDER BY CASE o.pack {pack_order} ELSE {fallback_order} END"  # noqa: S608
            ") AS rn"
            " FROM objects o"
            " LEFT JOIN ("
            "SELECT target_object_id, COUNT(*) AS backlinks"
            " FROM relations GROUP BY target_object_id"
            ") r ON r.target_object_id = o.object_id"
            f" WHERE {where_clause}"  # noqa: S608
        )
        order_clause = "ORDER BY backlink_count DESC, object_id"
    else:  # alpha (default)
        select_clause = (
            "SELECT pack, object_id, object_kind, title, canonical_path, source_slug"
        )
        inner_select = (
            "SELECT pack, object_id, object_kind, title, canonical_path, source_slug,"
            " ROW_NUMBER() OVER ("
            "PARTITION BY object_id"
            f" ORDER BY CASE pack {pack_order} ELSE {fallback_order} END"  # noqa: S608
            ") AS rn"
            f" FROM objects WHERE {where_clause}"  # noqa: S608
        )
        order_clause = "ORDER BY object_id"

    sql = f"""
        {select_clause}
        FROM (
            {inner_select}
        )
        WHERE rn = 1
        {order_clause}
        LIMIT ? OFFSET ?
    """
    params: list[Any] = [*pack_candidates, *inner_params, limit, offset]

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    if sort == "most_linked":
        return [
            {
                "object_id": object_id,
                "object_kind": object_kind,
                "title": title,
                "canonical_path": _vault_relative_path(resolved_vault, canonical_path),
                "source_slug": source_slug,
                "pack": pack,
                "backlink_count": backlink_count,
            }
            for (
                pack,
                object_id,
                object_kind,
                title,
                canonical_path,
                source_slug,
                backlink_count,
            ) in rows
        ]
    return [
        {
            "object_id": object_id,
            "object_kind": object_kind,
            "title": title,
            "canonical_path": _vault_relative_path(resolved_vault, canonical_path),
            "source_slug": source_slug,
            "pack": pack,
        }
        for pack, object_id, object_kind, title, canonical_path, source_slug in rows
    ]


def list_object_kind_stats(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-kind counts for all objects in the truth store."""
    from .object_kinds import display_label

    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    pack_order = " ".join(
        f"WHEN ? THEN {index}" for index, _ in enumerate(pack_candidates)
    )
    fallback_order = len(pack_candidates)

    sql = f"""
        SELECT object_kind, COUNT(*) as cnt
        FROM (
            SELECT object_id, object_kind,
                   ROW_NUMBER() OVER (
                       PARTITION BY object_id
                       ORDER BY CASE pack {pack_order} ELSE {fallback_order} END
                   ) AS rn
            FROM objects
            WHERE pack IN ({pack_placeholders})
        )
        WHERE rn = 1
        GROUP BY object_kind
        ORDER BY cnt DESC
    """
    params: list[Any] = [*pack_candidates, *pack_candidates]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {"object_kind": kind, "label": display_label(kind), "count": cnt}
        for kind, cnt in rows
    ]


def list_mention_kind_stats(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-kind counts of pages that mention (link to) *object_id*.

    Each result dict has ``object_kind`` (the kind of the mentioning page,
    or ``""`` for pages not in the objects table), ``label``, and ``count``.
    Results are ordered by count descending.
    """
    from .object_kinds import display_label

    db_path = _db_path(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(objects.object_kind, '') AS kind, COUNT(DISTINCT pages_index.slug) AS cnt
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            LEFT JOIN objects ON objects.object_id = pages_index.slug AND objects.pack = ?
            WHERE page_links.target_slug = ?
              AND pages_index.slug != ?
            GROUP BY kind
            ORDER BY cnt DESC
            """,
            (truth_pack, object_id, object_id),
        ).fetchall()
    return [
        {
            "object_kind": kind,
            "label": display_label(kind) if kind else "note",
            "count": cnt,
        }
        for kind, cnt in rows
    ]


def list_relation_kind_stats(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-kind counts of relation targets for *object_id*.

    Groups outgoing relations by the ``object_kind`` of the target object.
    Each result dict has ``object_kind``, ``label``, and ``count``.
    Results are ordered by count descending.
    """
    from .object_kinds import display_label

    db_path = _db_path(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(tgt.object_kind, '') AS kind, COUNT(*) AS cnt
            FROM relations r
            LEFT JOIN objects tgt ON tgt.object_id = r.target_object_id AND tgt.pack = r.pack
            WHERE r.pack = ? AND r.source_object_id = ?
            GROUP BY kind
            ORDER BY cnt DESC
            """,
            (truth_pack, object_id),
        ).fetchall()
    return [
        {
            "object_kind": kind,
            "label": display_label(kind) if kind else "unknown",
            "count": cnt,
        }
        for kind, cnt in rows
    ]


def search_vault_surface(
    vault_dir: Path | str,
    *,
    query: str,
    object_limit: int = 50,
    note_limit: int = 50,
    object_offset: int = 0,
    note_offset: int = 0,
    pack_name: str | None = None,
) -> dict[str, Any]:
    normalized_query = query.strip()
    object_limit, object_offset = _validate_page_args(limit=object_limit, offset=object_offset)
    note_limit, note_offset = _validate_page_args(limit=note_limit, offset=note_offset)
    requested_pack = _truth_pack_name(pack_name)
    if not normalized_query:
        return {
            "query": "",
            "objects": [],
            "notes": [],
            "object_total": 0,
            "note_total": 0,
            "object_offset": object_offset,
            "note_offset": note_offset,
            "object_limit": object_limit,
            "note_limit": note_limit,
        }
    tokens = _tokenize_for_search(normalized_query)
    if not tokens:
        return {
            "query": normalized_query,
            "objects": [],
            "notes": [],
            "object_total": 0,
            "note_total": 0,
            "object_offset": object_offset,
            "note_offset": note_offset,
            "object_limit": object_limit,
            "note_limit": note_limit,
        }
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )

    # Objects: per-token (field1 LIKE %tok% OR ...) joined by AND so a query
    # like "agent memory" matches a row mentioning the words anywhere, not
    # only as a contiguous substring. Then rank by a hand-rolled relevance
    # score (no FTS index on objects) so a title containing the literal
    # phrase "agent memory" beats one whose object_id just happens to come
    # first alphabetically.
    object_fields = (
        "objects.object_id",
        "objects.title",
        "objects.source_slug",
        "compiled_summaries.summary_text",
        "claims.claim_text",
    )
    token_clauses: list[str] = []
    base_object_filter_params: list[Any] = list(pack_candidates)
    for tok in tokens:
        like = f"%{_escape_like(tok)}%"
        token_clauses.append(
            "("
            + " OR ".join(f"lower({field}) LIKE ? ESCAPE '\\'" for field in object_fields)
            + ")"
        )
        base_object_filter_params.extend([like] * len(object_fields))
    object_join_where = f"""
        FROM objects
        LEFT JOIN compiled_summaries
          ON compiled_summaries.pack = objects.pack
         AND compiled_summaries.object_id = objects.object_id
        LEFT JOIN claims
          ON claims.pack = objects.pack
         AND claims.object_id = objects.object_id
        WHERE objects.pack IN ({",".join("?" for _ in pack_candidates)})
          AND {" AND ".join(token_clauses)}
    """
    object_count_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT objects.object_id
            {object_join_where}
        )
    """
    # Relevance: full phrase in title is the strongest signal; slug phrase
    # next; then per-token title/slug hits. Higher = better, so we ORDER DESC.
    title_lower_query = normalized_query.lower()
    per_token_title_bonus = " + ".join(
        "CASE WHEN instr(lower(objects.title), ?) > 0 THEN 10 ELSE 0 END" for _ in tokens
    )
    per_token_slug_bonus = " + ".join(
        "CASE WHEN instr(lower(objects.object_id), ?) > 0 THEN 5 ELSE 0 END" for _ in tokens
    )
    object_relevance_expr = (
        "CASE WHEN instr(lower(objects.title), ?) > 0 THEN 100 ELSE 0 END"
        " + CASE WHEN instr(lower(objects.object_id), ?) > 0 THEN 50 ELSE 0 END"
        f" + ({per_token_title_bonus})"
        f" + ({per_token_slug_bonus})"
    )
    object_relevance_params = (
        title_lower_query,
        title_lower_query,
        *tokens,  # per_token_title_bonus
        *tokens,  # per_token_slug_bonus
    )
    object_sql = f"""
        SELECT DISTINCT objects.object_id, objects.object_kind, objects.title,
               objects.canonical_path, objects.source_slug, objects.pack,
               {object_relevance_expr} AS relevance
        {object_join_where}
        ORDER BY relevance DESC, objects.object_id
        LIMIT ? OFFSET ?
    """

    # Notes: feed jieba-tokenized query into FTS5 (`page_fts`) and rank by
    # bm25 with the title column boosted, plus explicit title-substring
    # bonuses so a note titled "agent memory" beats a note that just
    # mentions "memory" many times in its body. Trigram tokenizer can't
    # index <3-char tokens, so when every token is too short (e.g. a bare
    # 2-char Chinese query like "记忆") we fall back to a per-token
    # body-LIKE scan. Note: bm25() weights are positional across ALL
    # declared columns including UNINDEXED ones, so the schema
    # (slug UNINDEXED, title, body) takes three weights:
    # (slug=ignored, title=5x, body=1x). The bonuses subtract from rank
    # because FTS5 bm25 is negative-better.
    fts_match = _build_fts_match(tokens)
    # Trigram FTS5 can't index <3-char tokens, so they were dropped from the
    # MATCH expression. Re-apply them as LIKE filters against pages_index so a
    # query like "AI agent" doesn't return every "agent" page regardless of "AI".
    short_tokens = [tok for tok in tokens if len(tok) < 3]
    if fts_match:
        title_lower_query = normalized_query.lower()
        # Per-token title bonus accumulates so multi-word out-of-order title
        # matches still rise above body-only ones.
        per_token_bonus_sql = " + ".join(
            "CASE WHEN instr(lower(pages_index.title), ?) > 0 THEN 2.0 ELSE 0.0 END"
            for _ in tokens
        )
        # Whole-query phrase bonus: a literal substring hit in the title is
        # the strongest signal a human would want surfaced first.
        rank_expr = (
            "bm25(page_fts, 1.0, 5.0, 1.0)"
            f" - ({per_token_bonus_sql})"
            " - CASE WHEN instr(lower(pages_index.title), ?) > 0 THEN 8.0 ELSE 0.0 END"
        )
        short_filter_sql = ""
        short_filter_params: list[Any] = []
        if short_tokens:
            short_clauses: list[str] = []
            for tok in short_tokens:
                like = f"%{_escape_like(tok)}%"
                short_clauses.append(
                    "(lower(pages_index.title) LIKE ? ESCAPE '\\' "
                    "OR lower(pages_index.body) LIKE ? ESCAPE '\\' "
                    "OR lower(pages_index.slug) LIKE ? ESCAPE '\\')"
                )
                short_filter_params.extend([like] * 3)
            short_filter_sql = " AND " + " AND ".join(short_clauses)
        note_count_sql = f"""
            SELECT COUNT(*) FROM page_fts
            JOIN pages_index ON pages_index.slug = page_fts.slug
            WHERE page_fts MATCH ?{short_filter_sql}
        """
        note_sql = f"""
            SELECT pages_index.slug, pages_index.title, pages_index.note_type,
                   pages_index.path, {rank_expr} AS rank
            FROM page_fts
            JOIN pages_index ON pages_index.slug = page_fts.slug
            WHERE page_fts MATCH ?{short_filter_sql}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        note_count_params: tuple[Any, ...] = (fts_match, *short_filter_params)
        note_params = (
            *tokens,
            title_lower_query,
            fts_match,
            *short_filter_params,
            note_limit,
            note_offset,
        )
    else:
        note_token_clauses: list[str] = []
        note_like_params: list[Any] = []
        for tok in tokens:
            like = f"%{_escape_like(tok)}%"
            note_token_clauses.append(
                "(lower(slug) LIKE ? ESCAPE '\\' OR lower(title) LIKE ? ESCAPE '\\' "
                "OR lower(path) LIKE ? ESCAPE '\\' OR lower(body) LIKE ? ESCAPE '\\')"
            )
            note_like_params.extend([like] * 4)
        note_where = f"WHERE {' AND '.join(note_token_clauses)}"
        note_count_sql = f"SELECT COUNT(*) FROM pages_index {note_where}"
        note_sql = f"""
            SELECT slug, title, note_type, path
            FROM pages_index
            {note_where}
            ORDER BY slug
            LIMIT ? OFFSET ?
        """
        note_count_params = tuple(note_like_params)
        note_params = (*note_like_params, note_limit, note_offset)
    with sqlite3.connect(db_path) as conn:
        object_total = conn.execute(
            object_count_sql, tuple(base_object_filter_params)
        ).fetchone()[0]
        object_rows = conn.execute(
            object_sql,
            (
                *object_relevance_params,
                *base_object_filter_params,
                object_limit,
                object_offset,
            ),
        ).fetchall()
        try:
            note_total = conn.execute(note_count_sql, note_count_params).fetchone()[0]
            note_rows = conn.execute(note_sql, note_params).fetchall()
        except sqlite3.OperationalError:
            # FTS query parser rejected the expression — fall back to empty.
            note_total = 0
            note_rows = []

    objects = [
        {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
            "pack": requested_pack,
            "row_pack": row[5],
        }
        for row in object_rows
    ]
    notes = [
        {
            "slug": row[0],
            "title": row[1],
            "note_type": row[2],
            "path": _vault_relative_path(resolved_vault, row[3]),
        }
        for row in note_rows
    ]
    return {
        "query": normalized_query,
        "objects": objects,
        "notes": notes,
        "object_total": int(object_total),
        "note_total": int(note_total),
        "object_offset": object_offset,
        "note_offset": note_offset,
        "object_limit": object_limit,
        "note_limit": note_limit,
    }


def count_objects(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    object_kind: str | None = None,
    pack_name: str | None = None,
) -> int:
    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    sql = f"SELECT COUNT(DISTINCT object_id) FROM objects WHERE pack IN ({','.join('?' for _ in pack_candidates)})"
    params: list[Any] = [*pack_candidates]
    if object_kind:
        from .object_kinds import normalize_kind

        sql += " AND object_kind = ?"
        params.append(normalize_kind(object_kind))
    if normalized_query:
        sql += """
            AND (
              lower(object_id) LIKE ? ESCAPE '\\'
              OR lower(title) LIKE ? ESCAPE '\\'
              OR lower(source_slug) LIKE ? ESCAPE '\\'
            )
        """
        params.extend([f"%{normalized_query}%"] * 3)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0


def _surface_page_query_clauses(*, note_type: str, normalized_query: str) -> tuple[str, list[Any]]:
    where = ["pages_index.note_type = ?"]
    params: list[Any] = [note_type]
    if normalized_query:
        where.append(
            """
            (
              lower(pages_index.slug) LIKE ? ESCAPE '\\'
              OR lower(pages_index.title) LIKE ? ESCAPE '\\'
              OR lower(objects.object_id) LIKE ? ESCAPE '\\'
              OR lower(objects.title) LIKE ? ESCAPE '\\'
            )
            """.strip()
        )
        params.extend([f"%{normalized_query}%"] * 4)
    return " AND ".join(where), params


def _list_surface_groups(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    note_type: str,
    query: str | None,
    limit: int,
    object_list_key: str,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    where_sql, base_params = _surface_page_query_clauses(
        note_type=note_type,
        normalized_query=normalized_query,
    )

    with sqlite3.connect(db_path) as conn:
        selected_rows = conn.execute(
            f"""
            SELECT DISTINCT pages_index.slug
            FROM pages_index
            JOIN page_links ON page_links.source_slug = pages_index.slug
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE objects.pack IN ({pack_placeholders}) AND {where_sql}
            ORDER BY pages_index.slug
            LIMIT ?
            """,
            tuple([*pack_candidates, *base_params, limit]),
        ).fetchall()
        selected_slugs = [row[0] for row in selected_rows]
        if not selected_slugs:
            return []
        placeholders = ",".join("?" for _ in selected_slugs)
        rows = conn.execute(
            f"""
            SELECT pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path, objects.pack, objects.object_id, objects.title
            FROM pages_index
            JOIN page_links ON page_links.source_slug = pages_index.slug
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE pages_index.slug IN ({placeholders})
              AND objects.pack IN ({pack_placeholders})
            ORDER BY pages_index.slug,
              CASE objects.pack
                {"".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))}
                ELSE {len(pack_candidates)}
              END,
              objects.object_id
            """,
            tuple([*selected_slugs, *pack_candidates, *pack_candidates]),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for slug, title, row_note_type, path, object_pack, object_id, object_title in rows:
        item = grouped.setdefault(
            slug,
            {
                "slug": slug,
                "title": title,
                "note_type": row_note_type,
                "path": _vault_relative_path(resolved_vault, path),
                object_list_key: [],
            },
        )
        if any(existing["object_id"] == object_id for existing in item[object_list_key]):
            continue
        item[object_list_key].append(
            {"object_id": object_id, "title": object_title, "pack": object_pack}
        )
    return list(grouped.values())


def get_object_detail(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    escaped = _escape_like(object_id)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )

    truth_pack = ""
    with sqlite3.connect(db_path) as conn:
        object_row = None
        for candidate_pack in pack_candidates:
            object_row = conn.execute(
                """
                SELECT object_id, object_kind, title, canonical_path,
                       source_slug, source_url
                FROM objects
                WHERE pack = ? AND object_id = ?
                """,
                (candidate_pack, object_id),
            ).fetchone()
            if object_row is not None:
                truth_pack = candidate_pack
                break
        if object_row is None:
            raise ValueError(f"Unknown object_id: {object_id}")

        summary_row = conn.execute(
            """
            SELECT object_id, summary_text, source_slug
            FROM compiled_summaries
            WHERE pack = ? AND object_id = ?
            """,
            (truth_pack, object_id),
        ).fetchone()
        claim_rows = conn.execute(
            """
            SELECT claim_id, claim_kind, claim_text, confidence
            FROM claims
            WHERE pack = ? AND object_id = ?
            ORDER BY claim_id
            """,
            (truth_pack, object_id),
        ).fetchall()
        evidence_rows = conn.execute(
            """
            SELECT claim_id, source_slug, evidence_kind, quote_text,
                   quote_start_line, quote_end_line, quote_start_char, quote_end_char
            FROM claim_evidence
            WHERE claim_id IN (
                SELECT claim_id FROM claims WHERE pack = ? AND object_id = ?
            )
            ORDER BY claim_id, evidence_kind
            """,
            (truth_pack, object_id),
        ).fetchall()
        relation_rows = conn.execute(
            """
            SELECT r.source_object_id, r.target_object_id, r.relation_type, r.evidence_source_slug,
                   COALESCE(src.object_kind, '') AS source_kind,
                   COALESCE(tgt.object_kind, '') AS target_kind
            FROM relations r
            LEFT JOIN objects src ON src.object_id = r.source_object_id AND src.pack = r.pack
            LEFT JOIN objects tgt ON tgt.object_id = r.target_object_id AND tgt.pack = r.pack
            WHERE r.pack = ? AND r.source_object_id = ?
            ORDER BY r.target_object_id
            """,
            (truth_pack, object_id),
        ).fetchall()
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
            FROM contradictions
            WHERE pack = ?
              AND (positive_claim_ids_json LIKE ? ESCAPE '\\' OR negative_claim_ids_json LIKE ? ESCAPE '\\')
            ORDER BY subject_key
            """,
            (truth_pack, f'%"{escaped}::%', f'%"{escaped}::%'),
        ).fetchall()
        mention_rows = conn.execute(
            """
            SELECT DISTINCT pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path,
                   objects.object_kind
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            LEFT JOIN objects ON objects.object_id = pages_index.slug AND objects.pack = ?
            WHERE page_links.target_slug = ?
              AND pages_index.slug != ?
            ORDER BY pages_index.slug
            """,
            (truth_pack, object_id, object_id),
        ).fetchall()

    mocs: list[dict[str, Any]] = []
    source_notes: list[dict[str, Any]] = []
    for slug, title, note_type, path, object_kind in mention_rows:
        item = {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": _vault_relative_path(resolved_vault, path),
            "object_kind": object_kind or "",
        }
        if _is_moc_row(note_type, path):
            mocs.append(item)
            continue
        if slug == object_id:
            continue
        if note_type != "evergreen":
            source_notes.append(item)

    contradiction_items = [
        {
            "contradiction_id": row[0],
            "subject_key": row[1],
            "positive_claim_ids": json.loads(row[2]),
            "negative_claim_ids": json.loads(row[3]),
            "status": row[4],
            "resolution_note": row[5] or "",
            "resolved_at": row[6] or "",
        }
        for row in contradiction_rows
    ]
    contradiction_overrides = _latest_contradiction_review_overrides(resolved_vault)
    for item in contradiction_items:
        override = contradiction_overrides.get(str(item["contradiction_id"]))
        if not override:
            continue
        item["status"] = override["status"]
        item["resolution_note"] = override["resolution_note"]
        item["resolved_at"] = override["resolved_at"]

    return {
        "object": {
            "object_id": object_row[0],
            "object_kind": object_row[1],
            "title": object_row[2],
            "canonical_path": _vault_relative_path(resolved_vault, object_row[3]),
            "source_slug": object_row[4],
            "source_url": object_row[5] or "",
            "pack": truth_pack,
        },
        "summary": (
            {
                "object_id": summary_row[0],
                "summary_text": summary_row[1],
                "source_slug": summary_row[2],
            }
            if summary_row
            else None
        ),
        "claims": [
            {
                "claim_id": row[0],
                "claim_kind": row[1],
                "claim_text": row[2],
                "confidence": row[3],
            }
            for row in claim_rows
        ],
        "evidence": [
            {
                "claim_id": row[0],
                "source_slug": row[1],
                "evidence_kind": row[2],
                "quote_text": row[3],
                "quote_start_line": int(row[4] or 0),
                "quote_end_line": int(row[5] or 0),
                "quote_start_char": int(row[6] or 0),
                "quote_end_char": int(row[7] or 0),
            }
            for row in evidence_rows
        ],
        "relations": [
            {
                "source_object_id": row[0],
                "target_object_id": row[1],
                "relation_type": row[2],
                "evidence_source_slug": row[3],
                "source_kind": row[4],
                "target_kind": row[5],
            }
            for row in relation_rows
        ],
        "contradictions": contradiction_items,
        "provenance": {
            "evergreen_path": _vault_relative_path(resolved_vault, object_row[3]),
            "source_notes": source_notes,
            "mocs": mocs,
        },
    }


def count_contradictions_by_status(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Lightweight overview probe for ``/ops/queue``.

    Returns ``{by_status: {status → count}, oldest_open: {...} | None,
    total: int}`` without paying the cost of ``list_contradictions``
    (claim-detail map + review-override JSONL replay + status text
    reconciliation per row).  ``GROUP BY status`` runs in O(rows) on
    the index, and the oldest-open probe is a single ``LIMIT 1``
    SELECT — total wire cost is ~2 ms even on a 10k-row table.

    Status here is the *raw* SQL value, not the override-reconciled
    one, so a contradiction whose review action overrode it from
    ``open`` → ``dismissed`` still counts as ``open`` in this
    aggregate.  The queue overview wants the work-pending signal,
    not the audit truth — leave override reconciliation to the
    detail page.
    """
    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="contradictions"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    by_status: dict[str, int] = {}
    oldest_open: dict[str, Any] | None = None
    try:
        with sqlite3.connect(db_path) as conn:
            count_sql = (
                "SELECT status, COUNT(*) FROM contradictions"
                f" WHERE pack IN ({pack_placeholders})"  # noqa: S608
                " GROUP BY status"
            )
            for status, n in conn.execute(count_sql, tuple(pack_candidates)).fetchall():
                by_status[str(status or "")] = int(n or 0)
            oldest_sql = (
                "SELECT contradiction_id, subject_key FROM contradictions"
                f" WHERE pack IN ({pack_placeholders}) AND status = 'open'"  # noqa: S608
                " ORDER BY contradiction_id LIMIT 1"
            )
            row = conn.execute(oldest_sql, tuple(pack_candidates)).fetchone()
            if row is not None:
                oldest_open = {
                    "contradiction_id": str(row[0] or ""),
                    "subject_key": str(row[1] or ""),
                }
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {"by_status": {}, "oldest_open": None, "total": 0}
        raise
    return {
        "by_status": by_status,
        "oldest_open": oldest_open,
        "total": sum(by_status.values()),
    }


def count_action_queue_by_status(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Lightweight overview probe for ``/ops/queue``.

    Reads the action-queue ledger once and returns
    ``{by_status: {status → count}, oldest_failed: {...} | None,
    total: int}`` without running ``_normalize_action_queue_item``
    on every row — that step pulls in resolver metadata, action
    contracts, and result-artifact summaries that the queue
    overview never reads.  Pre-fix the overview page paid that
    cost on up to 500 rows just to compute ``len()`` and a single
    oldest hint.
    """
    normalized_pack = str(pack_name or "").strip()
    by_status: dict[str, int] = {}
    oldest_failed: dict[str, Any] | None = None
    with action_queue_write_lock(vault_dir):
        for item in _read_action_queue_rows_unlocked(vault_dir):
            row_pack = str(item.get("pack") or DEFAULT_WORKFLOW_PACK_NAME)
            if normalized_pack and row_pack != normalized_pack:
                continue
            status = str(item.get("status") or "")
            by_status[status] = by_status.get(status, 0) + 1
            if status not in ("failed", "blocked"):
                continue
            # The action queue is persisted in *reverse* chronological
            # order (see ``enqueue_signal_action`` — newest first).
            # The first failed/blocked row encountered is therefore
            # the *newest* one; the queue overview wants the
            # *oldest* hint so the operator sees the row that has
            # been stuck longest.  Track explicitly by ``created_at``
            # and only swap when we find a strictly older row.
            created_at = str(item.get("created_at") or "")
            current_oldest = (
                str(oldest_failed.get("created_at") or "") if oldest_failed else ""
            )
            should_replace = oldest_failed is None or (
                created_at != ""
                and (current_oldest == "" or created_at < current_oldest)
            )
            if should_replace:
                oldest_failed = {
                    "action_id": str(item.get("action_id") or ""),
                    "title": str(item.get("title") or ""),
                    "created_at": created_at,
                    "status": status,
                }
    return {
        "by_status": by_status,
        "oldest_failed": oldest_failed,
        "total": sum(by_status.values()),
    }


def count_graph_clusters(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> int:
    """Total number of pack-scoped graph clusters matching ``query``.

    Used by ``/ops/clusters`` to render ``Showing N of TOTAL`` headers
    when the renderer applies a display ``limit``.
    """
    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="graph_clusters"
    )
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    sql = (
        "SELECT COUNT(DISTINCT cluster_id) FROM graph_clusters WHERE pack IN ("
        + ",".join("?" for _ in pack_candidates)  # noqa: S608
        + ")"
    )
    params: list[Any] = [*pack_candidates]
    if normalized_query:
        sql += (
            " AND ("
            " lower(pack) LIKE ? ESCAPE '\\'"
            " OR lower(cluster_kind) LIKE ? ESCAPE '\\'"
            " OR lower(label) LIKE ? ESCAPE '\\'"
            " OR lower(center_object_id) LIKE ? ESCAPE '\\'"
            " OR lower(member_object_ids_json) LIKE ? ESCAPE '\\'"
            ")"
        )
        params.extend([f"%{normalized_query}%"] * 5)
    with sqlite3.connect(db_path) as conn:
        ((count,),) = conn.execute(sql, tuple(params)).fetchall()
    return int(count or 0)


def list_graph_clusters(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List clusters scoped to ``pack_name`` with optional offset.

    The function does its own dedup-by-``cluster_id`` after fetching from
    SQL because the same id can appear under multiple packs (overlay shells
    materialise the parent's clusters).  Pagination therefore happens
    *after* dedup — we count distinct cluster_ids, skip the first
    ``offset``, then take ``limit``.  SQL ``LIMIT/OFFSET`` would count
    duplicates and produce off-by-N pages.
    """
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="graph_clusters"
    )
    requested_pack = pack_name or pack_candidates[0]
    normalized_query = _escape_like(query.strip().lower()) if query else ""

    sql = """
        SELECT pack, cluster_id, cluster_kind, label, center_object_id, member_object_ids_json, score
        FROM graph_clusters
        WHERE pack IN ({pack_placeholders})
    """
    sql = sql.format(pack_placeholders=",".join("?" for _ in pack_candidates))
    params: list[Any] = [*pack_candidates]
    if normalized_query:
        sql += """
          AND (
            lower(pack) LIKE ? ESCAPE '\\'
            OR
            lower(cluster_kind) LIKE ? ESCAPE '\\'
            OR lower(label) LIKE ? ESCAPE '\\'
            OR lower(center_object_id) LIKE ? ESCAPE '\\'
            OR lower(member_object_ids_json) LIKE ? ESCAPE '\\'
          )
        """
        params.extend([f"%{normalized_query}%"] * 5)
    sql += """
      ORDER BY CASE pack
        {pack_order}
        ELSE {fallback_order}
      END, score DESC, cluster_id
    """.format(
        pack_order=" ".join(f"WHEN ? THEN {index}" for index, _ in enumerate(pack_candidates)),
        fallback_order=len(pack_candidates),
    )
    params.extend(pack_candidates)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()

    all_member_ids = [
        object_id
        for _pack, _cluster_id, _cluster_kind, _label, _center_object_id, member_json, _score in rows
        for object_id in json.loads(member_json)
    ]
    object_rows = _batch_object_rows(vault_dir, all_member_ids, pack_name=pack_name)

    items: list[dict[str, Any]] = []
    seen_cluster_ids: set[str] = set()
    skipped = 0
    for cluster_pack, cluster_id, cluster_kind, label, center_object_id, member_json, score in rows:
        if cluster_id in seen_cluster_ids:
            continue
        seen_cluster_ids.add(cluster_id)
        # Skip the first ``offset`` distinct cluster_ids to support
        # paginated browsing.  Done after dedup so page boundaries
        # match what the operator sees in the rendered list.
        if skipped < offset:
            skipped += 1
            continue
        member_object_ids = json.loads(member_json)
        items.append(
            {
                "cluster_id": str(cluster_id),
                "cluster_kind": str(cluster_kind),
                "label": str(label),
                "center_object_id": str(center_object_id),
                "center_title": object_rows.get(str(center_object_id), {}).get(
                    "title", str(center_object_id)
                ),
                "member_object_ids": member_object_ids,
                "member_count": len(member_object_ids),
                "members": [
                    object_rows.get(
                        str(object_id),
                        {
                            "object_id": str(object_id),
                            "title": str(object_id),
                            "pack": cluster_pack,
                        },
                    )
                    for object_id in member_object_ids
                ],
                "score": float(score or 0.0),
                "pack": requested_pack,
                "row_pack": cluster_pack,
            }
        )
        if len(items) >= limit:
            break
    return items


def list_graph_edges_for_object_scope(
    vault_dir: Path | str,
    *,
    object_ids: list[str],
    pack_names: list[str] | None = None,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    normalized_object_ids = sorted({str(object_id) for object_id in object_ids if object_id})
    if not normalized_object_ids:
        return []
    if pack_names:
        candidate_packs = sorted({str(pack) for pack in pack_names if pack})
    else:
        candidate_packs = _materialized_truth_packs(
            vault_dir, pack_name=pack_name, table_name="graph_edges"
        )
    if not candidate_packs:
        return []

    db_path = _db_path(vault_dir)
    object_placeholders = ",".join("?" for _ in normalized_object_ids)
    pack_placeholders = ",".join("?" for _ in candidate_packs)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT ge.pack, ge.edge_id, ge.source_object_id, ge.target_object_id,
                   ge.edge_kind, ge.weight, ge.evidence_source_slug,
                   COALESCE(src.object_kind, '') AS source_kind,
                   COALESCE(tgt.object_kind, '') AS target_kind
            FROM graph_edges ge
            LEFT JOIN objects src ON src.object_id = ge.source_object_id AND src.pack = ge.pack
            LEFT JOIN objects tgt ON tgt.object_id = ge.target_object_id AND tgt.pack = ge.pack
            WHERE ge.pack IN ({pack_placeholders})
              AND ge.source_object_id IN ({object_placeholders})
              AND ge.target_object_id IN ({object_placeholders})
            ORDER BY ge.pack, ge.weight DESC, ge.edge_kind, ge.source_object_id, ge.target_object_id
            """,
            (*candidate_packs, *normalized_object_ids, *normalized_object_ids),
        ).fetchall()
    return [
        {
            "pack": str(row[0]),
            "edge_id": str(row[1]),
            "source_object_id": str(row[2]),
            "target_object_id": str(row[3]),
            "edge_kind": str(row[4]),
            "weight": float(row[5] or 0.0),
            "evidence_source_slug": str(row[6] or ""),
            "source_kind": str(row[7]),
            "target_kind": str(row[8]),
        }
        for row in rows
    ]


def get_graph_cluster_detail(
    vault_dir: Path | str,
    cluster_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="graph_clusters"
    )
    requested_pack = pack_name or pack_candidates[0]

    truth_pack = ""
    cluster_row = None
    with sqlite3.connect(db_path) as conn:
        for candidate_pack in pack_candidates:
            cluster_row = conn.execute(
                """
                SELECT cluster_id, cluster_kind, label, center_object_id, member_object_ids_json, score
                FROM graph_clusters
                WHERE pack = ? AND cluster_id = ?
                """,
                (candidate_pack, cluster_id),
            ).fetchone()
            if cluster_row is not None:
                truth_pack = candidate_pack
                break
        if cluster_row is None:
            raise ValueError(f"Unknown cluster_id: {cluster_id}")

        member_object_ids = [str(value) for value in json.loads(cluster_row[4])]
        if member_object_ids:
            placeholders = ",".join("?" for _ in member_object_ids)
            edge_rows = conn.execute(
                f"""
                SELECT edge_id, source_object_id, target_object_id, edge_kind, weight, evidence_source_slug
                FROM graph_edges
                WHERE pack = ?
                  AND source_object_id IN ({placeholders})
                  AND target_object_id IN ({placeholders})
                ORDER BY weight DESC, edge_kind, source_object_id, target_object_id
                """,
                (truth_pack, *member_object_ids, *member_object_ids),
            ).fetchall()
        else:
            edge_rows = []

    object_rows = _batch_object_rows(vault_dir, member_object_ids, pack_name=truth_pack)
    center_object_id = str(cluster_row[3])
    return {
        "cluster": {
            "cluster_id": str(cluster_row[0]),
            "cluster_kind": str(cluster_row[1]),
            "label": str(cluster_row[2]),
            "center_object_id": center_object_id,
            "center_title": object_rows.get(center_object_id, {}).get("title", center_object_id),
            "member_object_ids": member_object_ids,
            "member_count": len(member_object_ids),
            "members": [
                object_rows.get(
                    object_id,
                    {"object_id": object_id, "title": object_id, "pack": truth_pack},
                )
                for object_id in member_object_ids
            ],
            "score": float(cluster_row[5] or 0.0),
            "pack": requested_pack,
            "row_pack": truth_pack,
        },
        "edges": [
            {
                "edge_id": str(row[0]),
                "source_object_id": str(row[1]),
                "target_object_id": str(row[2]),
                "edge_kind": str(row[3]),
                "weight": float(row[4] or 0.0),
                "evidence_source_slug": str(row[5] or ""),
            }
            for row in edge_rows
        ],
    }


def _find_note_by_source(
    vault_dir: Path, *, source_url: str, exclude_path: str
) -> dict[str, str] | None:
    cache_key = (str(vault_dir.resolve()), _search_root_signatures(vault_dir))
    source_index = _SOURCE_NOTE_INDEX_CACHE.get(cache_key)
    if source_index is None:
        search_roots = [
            vault_dir / "50-Inbox" / "03-Processed",
            vault_dir / "50-Inbox" / "02-Processing",
            vault_dir / "50-Inbox" / "01-Raw",
        ]
        source_index = {}
        for root in search_roots:
            if not root.exists():
                continue
            for candidate in sorted(root.rglob("*.md")):
                frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
                candidate_source = str(frontmatter.get("source", "")).strip()
                if not candidate_source:
                    continue
                title = str(frontmatter.get("title") or candidate.stem).strip()
                source_index.setdefault(candidate_source, []).append(
                    {
                        "title": title,
                        "path": str(candidate.resolve().relative_to(vault_dir.resolve())),
                    }
                )
        _SOURCE_NOTE_INDEX_CACHE.clear()
        _SOURCE_NOTE_INDEX_CACHE[cache_key] = source_index

    resolved_exclude = str((vault_dir / exclude_path).resolve().relative_to(vault_dir.resolve()))
    for item in source_index.get(source_url, []):
        if item["path"] == resolved_exclude:
            continue
        return item
    return None


def _find_note_from_pipeline_log(vault_dir: Path, *, note_path: str) -> dict[str, str] | None:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    if not log_path.exists():
        return None
    index = _pipeline_log_index(vault_dir)
    return index["original_source_by_output"].get(
        str((vault_dir / note_path).resolve().relative_to(vault_dir.resolve()))
    )


def list_review_actions(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    normalized_object_ids = set(object_id for object_id in (object_ids or []) if object_id)
    resolved_vault = resolve_vault_dir(vault_dir)
    rows = [
        (
            _REVIEW_AUDIT_LOG_NAME,
            item.get("event_type", ""),
            item.get("slug", ""),
            item.get("session_id", ""),
            item.get("timestamp", ""),
            json.dumps(item, ensure_ascii=False),
        )
        for item in sorted(
            _read_jsonl_items(
                VaultLayout.from_vault(resolved_vault).logs_dir / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl"
            ),
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )[:200]
    ]
    return _review_action_items(rows, normalized_object_ids=normalized_object_ids, limit=limit)


def _review_action_items(
    rows: list[tuple[Any, ...]],
    *,
    normalized_object_ids: set[str],
    limit: int | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source_log, event_type, slug, session_id, timestamp, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        mutation_payload = payload.get("mutation")
        action_object_ids = [
            str(value)
            for value in payload.get("object_ids", [])
            if isinstance(value, str) and value
        ]
        if normalized_object_ids and not normalized_object_ids.intersection(action_object_ids):
            continue
        items.append(
            {
                "source_log": source_log,
                "event_type": event_type,
                "slug": slug,
                "session_id": session_id,
                "timestamp": timestamp,
                "object_ids": action_object_ids,
                "evolution_id": str(payload.get("evolution_id") or ""),
                "subject_kind": str(payload.get("subject_kind") or ""),
                "subject_id": str(payload.get("subject_id") or ""),
                "earlier_ref": str(payload.get("earlier_ref") or ""),
                "later_ref": str(payload.get("later_ref") or ""),
                "link_type": str(payload.get("link_type") or ""),
                "candidate_link_type": str(payload.get("candidate_link_type") or ""),
                "candidate_slug": str(payload.get("candidate_slug") or ""),
                "target_slug": str(payload.get("target_slug") or ""),
                "action": str(payload.get("action") or ""),
                "mutation": mutation_payload if isinstance(mutation_payload, dict) else {},
                "knowledge_index_rebuilt": bool(payload.get("knowledge_index_rebuilt")),
                "knowledge_index_error": str(payload.get("knowledge_index_error") or ""),
                "contradiction_ids": [
                    str(value)
                    for value in payload.get("contradiction_ids", [])
                    if isinstance(value, str) and value
                ],
                "status": str(payload.get("status") or ""),
                "note": str(payload.get("note") or ""),
                "rebuilt_object_ids": [
                    str(value)
                    for value in payload.get("rebuilt_object_ids", [])
                    if isinstance(value, str) and value
                ],
                "objects_rebuilt": int(payload.get("objects_rebuilt") or 0),
            }
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def _latest_contradiction_review_overrides(vault_dir: Path | str) -> dict[str, dict[str, str]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    items = sorted(
        [
            item
            for item in _read_jsonl_items(
                VaultLayout.from_vault(resolved_vault).logs_dir / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl"
            )
            if item.get("event_type") == "ui_contradictions_resolved"
        ],
        key=lambda item: str(item.get("timestamp") or ""),
    )
    overrides: dict[str, dict[str, str]] = {}
    for item in items:
        status = str(item.get("status") or "")
        note = str(item.get("note") or "")
        resolved_at = str(item.get("timestamp") or "")
        for contradiction_id in item.get("contradiction_ids", []) or []:
            contradiction_key = str(contradiction_id or "")
            if contradiction_key:
                overrides[contradiction_key] = {
                    "status": status,
                    "resolution_note": note,
                    "resolved_at": resolved_at,
                }
    return overrides


def list_evolution_review_actions(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    normalized_object_ids = set(object_id for object_id in (object_ids or []) if object_id)
    normalized_pack = _truth_pack_name(pack_name)
    resolved_vault = resolve_vault_dir(vault_dir)
    rows = [
        (
            _REVIEW_AUDIT_LOG_NAME,
            item.get("event_type", ""),
            item.get("slug", ""),
            item.get("session_id", ""),
            item.get("timestamp", ""),
            json.dumps(item, ensure_ascii=False),
        )
        for item in sorted(
            [
                item
                for item in _read_jsonl_items(
                    VaultLayout.from_vault(resolved_vault).logs_dir
                    / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl"
                )
                if item.get("event_type") == "ui_evolution_reviewed"
                and str(item.get("pack") or DEFAULT_WORKFLOW_PACK_NAME) == normalized_pack
            ],
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )
    ]
    return _review_action_items(rows, normalized_object_ids=normalized_object_ids, limit=None)


def _signal_id(signal_type: str, key: str) -> str:
    return f"{signal_type}::{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _action_id(
    signal_id: str,
    action_kind: str,
    target_ref: str,
    payload: dict[str, Any],
    *,
    pack_name: str,
) -> str:
    payload_key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    key = f"{pack_name}::{signal_id}::{action_kind}::{target_ref}::{payload_key}"
    return f"action::{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _recommended_action(*, kind: str, label: str, path: str, executable: bool) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "path": path,
        "executable": executable,
    }


def _focused_action_contract_metadata(
    action_kind: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    try:
        contract = resolve_focused_action_execution_contract(
            pack_name=pack_name or DEFAULT_WORKFLOW_PACK_NAME,
            action_kind=action_kind,
        )
    except ValueError:
        return {
            "safe_to_run": False,
            "handler_provider_pack": "",
            "handler_provider_name": "",
            "processor_provider_pack": "",
            "processor_provider_name": "",
            "processor_mode": "",
            "processor_inputs": [],
            "processor_outputs": [],
            "processor_quality_hooks": [],
        }
    return {
        "safe_to_run": bool(contract.handler_spec.safe_to_run),
        "handler_provider_pack": str(contract.handler_spec.pack or ""),
        "handler_provider_name": str(contract.handler_spec.name or ""),
        "processor_provider_pack": str(contract.processor_contract.pack or ""),
        "processor_provider_name": str(contract.processor_contract.name or ""),
        "processor_mode": str(contract.processor_contract.mode or ""),
        "processor_inputs": list(contract.processor_contract.inputs or ()),
        "processor_outputs": list(contract.processor_contract.outputs or ()),
        "processor_quality_hooks": list(contract.processor_contract.quality_hooks or ()),
    }


def _is_safe_action_kind(action_kind: str, *, pack_name: str | None = None) -> bool:
    metadata = _focused_action_contract_metadata(action_kind, pack_name=pack_name)
    return bool(metadata["safe_to_run"])


def _auto_queue_signal_types_for_pack(pack_name: str | None = None) -> set[str]:
    governance_specs = list_effective_governance_specs(
        pack_name=pack_name or DEFAULT_WORKFLOW_PACK_NAME,
    )
    if not governance_specs:
        return set(_LEGACY_AUTO_QUEUE_SIGNAL_TYPES)
    signal_types: set[str] = set()
    for governance_spec in governance_specs:
        for signal_rule in governance_spec.signal_rules:
            if bool(getattr(signal_rule, "auto_queue", False)):
                signal_type = str(getattr(signal_rule, "signal_type", "") or "").strip()
                if signal_type:
                    signal_types.add(signal_type)
    return signal_types


def _resolver_rule_metadata_for_action_kind(
    action_kind: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    normalized_action_kind = str(action_kind or "").strip()
    if not normalized_action_kind:
        return {}
    contract = describe_resolver_rule_contract(
        pack_name=pack_name or DEFAULT_WORKFLOW_PACK_NAME,
        rule_name=normalized_action_kind,
    )
    if str(contract.get("status") or "") != "missing":
        return {
            "resolution_kind": str(contract.get("resolution_kind") or ""),
            "dispatch_mode": str(contract.get("dispatch_mode") or ""),
            "executable": bool(contract.get("executable", False)),
            "safe_to_run": bool(contract.get("safe_to_run", False)),
            "governance_provider_pack": str(contract.get("provider_pack") or ""),
            "governance_provider_name": str(contract.get("provider_name") or ""),
            "governance_status": str(contract.get("status") or ""),
            "resolver_rule_name": str(contract.get("rule_name") or ""),
        }
    return {}


def _signal_rule_metadata_for_signal_type(
    signal_type: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    normalized_signal_type = str(signal_type or "").strip()
    if not normalized_signal_type:
        return {}
    contract = describe_signal_rule_contract(
        pack_name=pack_name or DEFAULT_WORKFLOW_PACK_NAME,
        signal_type=normalized_signal_type,
    )
    if str(contract.get("status") or "") != "missing":
        return {
            "governance_provider_pack": str(contract.get("provider_pack") or ""),
            "governance_provider_name": str(contract.get("provider_name") or ""),
            "governance_status": str(contract.get("status") or ""),
            "resolver_rule_name": str(contract.get("resolver_rule") or ""),
            "auto_queue": bool(contract.get("auto_queue", False)),
        }
    return {}


def _classify_action_error(error: str) -> str:
    normalized = (error or "").strip().lower()
    if not normalized:
        return ""
    if normalized.startswith("unsupported_action_kind:"):
        return "unsupported_action_kind"
    if "not found" in normalized or "missing" in normalized:
        return "missing_target"
    if "timed out" in normalized or "timeout" in normalized:
        return "timeout"
    if "integrity" in normalized or "database" in normalized or "sqlite" in normalized:
        return "storage_error"
    if "refresh" in normalized or "knowledge_index" in normalized:
        return "refresh_failed"
    return "workflow_failed"


def _read_action_queue_rows_unlocked(vault_dir: Path | str) -> list[dict[str, Any]]:
    return _read_jsonl_items(_action_queue_path(vault_dir))


def _write_action_queue_rows_unlocked(vault_dir: Path | str, actions: list[dict[str, Any]]) -> None:
    _rewrite_jsonl(_action_queue_path(vault_dir), actions)


def list_action_queue(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    normalized_query = (query or "").strip().lower()
    normalized_pack = str(pack_name or "").strip()
    raw_items: list[dict[str, Any]] = []
    with action_queue_write_lock(vault_dir):
        for item in _read_action_queue_rows_unlocked(vault_dir):
            if (
                normalized_pack
                and str(item.get("pack") or DEFAULT_WORKFLOW_PACK_NAME) != normalized_pack
            ):
                continue
            if status and item.get("status") != status:
                continue
            if normalized_query:
                haystacks = [
                    str(item.get("title") or "").lower(),
                    str(item.get("action_kind") or "").lower(),
                    str(item.get("target_ref") or "").lower(),
                ]
                if not any(normalized_query in haystack for haystack in haystacks):
                    continue
            raw_items.append(dict(item))
            if len(raw_items) >= limit:
                break
    return [_normalize_action_queue_item(item, vault_dir=vault_dir) for item in raw_items]


def _last_result_summary(action: dict[str, Any]) -> str:
    result = action.get("result")
    if not isinstance(result, dict) or not result:
        return "No execution result recorded yet."
    produced_count, produced_types = _result_artifact_summary(action)
    if produced_count:
        noun = "artifact" if produced_count == 1 else "artifacts"
        type_text = f" ({', '.join(produced_types)})" if produced_types else ""
        return f"Produced {produced_count} {noun}{type_text}."
    status = str(result.get("status") or result.get("ok") or "").strip()
    if status:
        return f"Last result: {status}."
    return "Execution completed without a tracked downstream artifact."


def _normalize_action_queue_item(
    item: dict[str, Any],
    *,
    vault_dir: Path | str | None = None,
) -> dict[str, Any]:
    normalized = dict(item)
    action_kind = str(normalized.get("action_kind") or "")
    pack_name = str(normalized.get("pack") or DEFAULT_WORKFLOW_PACK_NAME)
    resolver_metadata = _resolver_rule_metadata_for_action_kind(
        action_kind,
        pack_name=pack_name,
    )
    for key, value in resolver_metadata.items():
        current = normalized.get(key)
        if key not in normalized or current in (None, "", False):
            normalized[key] = value
    metadata = _focused_action_contract_metadata(
        action_kind,
        pack_name=pack_name,
    )
    for key, value in metadata.items():
        current = normalized.get(key)
        if key not in normalized or current in (None, "", []):
            normalized[key] = value
    if "precondition_status" not in normalized:
        normalized["precondition_status"] = ""
    if "blocked_reason" not in normalized:
        normalized["blocked_reason"] = ""
    if "obsolete_reason" not in normalized:
        normalized["obsolete_reason"] = ""
    if "retry_count" not in normalized:
        normalized["retry_count"] = 0
    if vault_dir is not None:
        signal_id = str(normalized.get("source_signal_id") or "")
        normalized["source_signal_active"] = bool(
            signal_id and _signal_by_id(vault_dir, signal_id, pack_name=pack_name) is not None
        )
    else:
        normalized.setdefault("source_signal_active", False)
    normalized["last_result_summary"] = _last_result_summary(normalized)
    normalized["impact_summary"] = _build_action_impact_summary(normalized)
    return normalized


def _count_nonempty_strings(value: Any) -> int:
    if isinstance(value, str):
        return 1 if value.strip() else 0
    if isinstance(value, (list, tuple, set)):
        return sum(1 for item in value if isinstance(item, str) and item.strip())
    return 0


def _count_sequence_items(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return sum(1 for item in value if item)
    return 0


def _result_artifact_summary(action: dict[str, Any]) -> tuple[int, list[str]]:
    result = action.get("result")
    if not isinstance(result, dict):
        return 0, []
    processor_outputs = {str(item) for item in action.get("processor_outputs", []) if item}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}

    note_output_count = max(
        _count_nonempty_strings(result.get("output_path")),
        _count_nonempty_strings(result.get("output")),
        _count_nonempty_strings(result.get("output_paths")),
    )
    object_output_count = max(
        _count_sequence_items(result.get("object_ids")),
        _count_sequence_items(result.get("created_object_ids")),
        _count_sequence_items(result.get("promoted_object_ids")),
        _count_sequence_items(result.get("rebuilt_object_ids")),
        int(summary.get("concepts_promoted") or 0) + int(summary.get("concepts_created") or 0),
        int(summary.get("objects_rebuilt") or 0),
    )

    artifact_types: list[str] = []
    if note_output_count > 0:
        artifact_types.append("deep_dive" if "deep_dive" in processor_outputs else "note_artifact")
    if object_output_count > 0:
        if "evergreen_object" in processor_outputs:
            artifact_types.append("evergreen_object")
        else:
            artifact_types.append("knowledge_artifact")
    return note_output_count + object_output_count, artifact_types


def _running_action_age_seconds(action: dict[str, Any]) -> float | None:
    started_at = _parse_iso_datetime(action.get("started_at") or action.get("created_at"))
    if started_at is None:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds()


def _build_action_impact_summary(action: dict[str, Any]) -> dict[str, Any]:
    action_status = str(action.get("status") or "")
    action_kind = str(action.get("action_kind") or "")
    produced_artifact_count, produced_artifact_types = _result_artifact_summary(action)
    base = {
        "impact_status": "unknown",
        "lifecycle_stage": action_status or "unknown",
        "action_kind": action_kind,
        "action_status": action_status,
        "impact_label": "Unknown execution state",
        "impact_detail": "The action queue item does not currently expose a recognized lifecycle state.",
        "produced_artifact_count": produced_artifact_count,
        "produced_artifact_types": produced_artifact_types,
    }
    if action_status == "queued":
        return {
            **base,
            "impact_status": "waiting",
            "lifecycle_stage": "queued",
            "impact_label": "Waiting on queue execution",
            "impact_detail": "A queueable action exists and is currently waiting to run.",
        }
    if action_status == "running":
        age_seconds = _running_action_age_seconds(action)
        if age_seconds is not None and age_seconds > _ACTION_RUNNING_STALE_AFTER_SECONDS:
            age_minutes = int(age_seconds // 60)
            return {
                **base,
                "impact_status": "stale",
                "lifecycle_stage": "stale_running",
                "impact_label": "Stale running action",
                "impact_detail": (
                    f"The queued action has been marked running for {age_minutes} minutes "
                    "without a finished_at timestamp."
                ),
            }
        return {
            **base,
            "impact_status": "running",
            "lifecycle_stage": "running",
            "impact_label": "Execution in progress",
            "impact_detail": "The queued action is currently running.",
        }
    if action_status == "failed":
        bucket = str(action.get("failure_bucket") or "")
        detail = "Execution failed."
        if bucket:
            detail = f"Execution failed in bucket '{bucket}'."
        return {
            **base,
            "impact_status": "failed",
            "lifecycle_stage": "failed",
            "impact_label": "Execution failed",
            "impact_detail": detail,
        }
    if action_status in {"dismissed", "obsolete", "blocked"}:
        detail = (
            "Execution was dismissed before completion."
            if action_status == "dismissed"
            else (
                "Execution became obsolete because the source signal disappeared."
                if action_status == "obsolete"
                else str(
                    action.get("blocked_reason")
                    or "Execution was blocked by a failed precondition."
                )
            )
        )
        return {
            **base,
            "impact_status": "stalled",
            "lifecycle_stage": action_status,
            "impact_label": "Execution stopped",
            "impact_detail": detail,
        }
    if action_status == "succeeded":
        if produced_artifact_count > 0:
            noun = "artifact" if produced_artifact_count == 1 else "artifacts"
            type_detail = (
                f" ({', '.join(str(item) for item in produced_artifact_types)})"
                if produced_artifact_types
                else ""
            )
            return {
                **base,
                "impact_status": "productive",
                "lifecycle_stage": "succeeded",
                "impact_label": "Produced downstream change",
                "impact_detail": (
                    f"Execution completed and produced {produced_artifact_count} tracked {noun}{type_detail}."
                ),
            }
        return {
            **base,
            "impact_status": "completed",
            "lifecycle_stage": "succeeded",
            "impact_label": "Execution completed",
            "impact_detail": "Execution completed without a tracked downstream artifact.",
        }
    return base


def _build_signal_impact_summary(
    signal: dict[str, Any],
    *,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recommended_action = signal.get("recommended_action")
    if not isinstance(recommended_action, dict) or not recommended_action.get("kind"):
        return {
            "impact_status": "review_only",
            "lifecycle_stage": "manual_review",
            "action_kind": "",
            "action_status": "",
            "impact_label": "Review-only signal",
            "impact_detail": "This signal currently has no execution path and remains an operator review item.",
            "produced_artifact_count": 0,
            "produced_artifact_types": [],
        }
    if action is not None:
        return _build_action_impact_summary(action)
    if bool(recommended_action.get("executable")):
        return {
            "impact_status": "ready",
            "lifecycle_stage": "recommendation_only",
            "action_kind": str(recommended_action.get("kind") or ""),
            "action_status": "",
            "impact_label": "Action available",
            "impact_detail": "An executable action exists for this signal, but nothing is currently queued.",
            "produced_artifact_count": 0,
            "produced_artifact_types": [],
        }
    return {
        "impact_status": "review_only",
        "lifecycle_stage": "manual_review",
        "action_kind": str(recommended_action.get("kind") or ""),
        "action_status": "",
        "impact_label": "Review-only signal",
        "impact_detail": "This signal currently routes to an operator review flow instead of queued execution.",
        "produced_artifact_count": 0,
        "produced_artifact_types": [],
    }


def _signal_by_id(
    vault_dir: Path | str,
    signal_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any] | None:
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    ledger_path = _signal_ledger_path(vault_dir, pack_name=normalized_pack)
    if not ledger_path.exists():
        ensure_signal_ledger_synced(vault_dir, pack_name=normalized_pack)
    with signal_ledger_write_lock(vault_dir):
        for item in _read_jsonl_items(ledger_path):
            if item.get("signal_id") == signal_id:
                return item
    return None


def enqueue_signal_action(
    vault_dir: Path | str,
    *,
    signal_id: str,
    pack_name: str | None = None,
    session_id: str = "ovp-ui",
) -> dict[str, Any]:
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    signal = _signal_by_id(vault_dir, signal_id, pack_name=normalized_pack)
    if signal is None:
        raise ValueError("unknown signal_id")
    with action_queue_write_lock(vault_dir):
        existing_actions = _read_action_queue_rows_unlocked(vault_dir)
        created, action = _enqueue_action_from_signal(
            signal,
            existing_actions=existing_actions,
            session_id=session_id,
            pack_name=normalized_pack,
        )
        if created:
            existing_actions.append(action)
            existing_actions.sort(
                key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))),
                reverse=True,
            )
            _write_action_queue_rows_unlocked(vault_dir, existing_actions)
    return {"created": created, "action": action}


def _enqueue_action_from_signal(
    signal: dict[str, Any],
    *,
    existing_actions: list[dict[str, Any]],
    session_id: str,
    pack_name: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    signal_id = str(signal.get("signal_id") or "")
    if not signal_id:
        raise ValueError("signal is missing signal_id")
    recommended_action = signal.get("recommended_action")
    if not isinstance(recommended_action, dict) or not recommended_action.get("kind"):
        raise ValueError("signal has no recommended action")
    target_ref = (
        next((path for path in signal.get("note_paths", []) if path), "")
        or next((object_id for object_id in signal.get("object_ids", []) if object_id), "")
        or str(signal.get("source_path") or "")
    )
    payload = {
        "recommended_action": recommended_action,
        "source_path": signal.get("source_path", ""),
        "note_paths": list(signal.get("note_paths", [])),
        "object_ids": list(signal.get("object_ids", [])),
    }
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    contract_metadata = _focused_action_contract_metadata(
        str(recommended_action["kind"]),
        pack_name=normalized_pack,
    )
    action_id = _action_id(
        signal_id,
        str(recommended_action["kind"]),
        target_ref,
        payload,
        pack_name=normalized_pack,
    )
    existing = next((item for item in existing_actions if item.get("action_id") == action_id), None)
    if existing is not None:
        return False, existing
    timestamp = _utc_now_text()
    action = {
        "action_id": action_id,
        "action_kind": str(recommended_action["kind"]),
        "pack": normalized_pack,
        "source_signal_id": signal_id,
        "title": str(recommended_action.get("label") or signal.get("title") or signal_id),
        "target_ref": target_ref,
        "object_ids": list(signal.get("object_ids", [])),
        "note_paths": list(signal.get("note_paths", [])),
        "status": "queued",
        "created_at": timestamp,
        "started_at": "",
        "finished_at": "",
        "error": "",
        "failure_bucket": "",
        "retry_count": 0,
        "payload": payload,
        "session_id": session_id,
        **contract_metadata,
    }
    return True, action


def _backfill_auto_queue_actions(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    auto_queue_signal_types = _auto_queue_signal_types_for_pack(pack_name)
    active_signals = (
        signals
        if signals is not None
        else list_signals(vault_dir, pack_name=pack_name, limit=MAX_PAGE_SIZE)
    )
    candidates = [
        item
        for item in active_signals
        if str(item.get("signal_type") or "") in auto_queue_signal_types
    ]
    if not candidates:
        return {"created_count": 0, "created_action_ids": []}
    with action_queue_write_lock(vault_dir):
        existing_actions = _read_action_queue_rows_unlocked(vault_dir)
        created_actions: list[dict[str, Any]] = []
        for item in candidates:
            created, action = _enqueue_action_from_signal(
                item,
                existing_actions=existing_actions,
                session_id="action-backfill",
                pack_name=pack_name,
            )
            if created:
                existing_actions.append(action)
                created_actions.append(action)
        if created_actions:
            existing_actions.sort(
                key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))),
                reverse=True,
            )
            _write_action_queue_rows_unlocked(vault_dir, existing_actions)
    return {
        "created_count": len(created_actions),
        "created_action_ids": [item["action_id"] for item in created_actions],
    }


def _replace_action_queue_item_unlocked(
    vault_dir: Path | str, action: dict[str, Any]
) -> dict[str, Any]:
    existing_actions = _read_action_queue_rows_unlocked(vault_dir)
    replaced = False
    for index, item in enumerate(existing_actions):
        if item.get("action_id") == action.get("action_id"):
            existing_actions[index] = action
            replaced = True
            break
    if not replaced:
        existing_actions.append(action)
    existing_actions.sort(
        key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))),
        reverse=True,
    )
    _write_action_queue_rows_unlocked(vault_dir, existing_actions)
    return action


def _action_by_id_unlocked(vault_dir: Path | str, action_id: str) -> dict[str, Any] | None:
    for item in _read_action_queue_rows_unlocked(vault_dir):
        if item.get("action_id") == action_id:
            return dict(item)
    return None


def _action_by_id(vault_dir: Path | str, action_id: str) -> dict[str, Any] | None:
    with action_queue_write_lock(vault_dir):
        return _action_by_id_unlocked(vault_dir, action_id)


def _looks_like_vault_note_path(value: str) -> bool:
    if not value:
        return False
    if "://" in value or value.startswith("/note?"):
        return False
    return (
        Path(value).is_absolute()
        or value.endswith(".md")
        or "/" in value
        or "\\" in value
        or value.startswith(".")
    )


def _focused_action_note_paths(action: dict[str, Any]) -> list[str]:
    note_paths = [
        str(path)
        for path in action.get("note_paths", [])
        if isinstance(path, str) and path.strip()
    ]
    target_ref = str(action.get("target_ref") or "").strip()
    if target_ref and _looks_like_vault_note_path(target_ref) and target_ref not in note_paths:
        note_paths.append(target_ref)
    return note_paths


def _focused_action_precondition(
    vault_dir: Path | str,
    action: dict[str, Any],
    *,
    safe_only: bool = False,
) -> dict[str, Any]:
    if safe_only and not bool(action.get("safe_to_run")):
        return {
            "status": "unsafe",
            "reason": "action_not_marked_safe_to_run",
        }
    signal_id = str(action.get("source_signal_id") or "")
    if (
        signal_id
        and _signal_by_id(
            vault_dir,
            signal_id,
            pack_name=str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME),
        )
        is None
    ):
        return {
            "status": "obsolete",
            "reason": "source_signal_inactive",
        }
    vault_root = resolve_vault_dir(vault_dir).resolve()
    for note_path in _focused_action_note_paths(action):
        if Path(note_path).is_absolute():
            return {
                "status": "blocked",
                "reason": f"invalid_note_path:{note_path}",
            }
        target = (vault_root / note_path).resolve()
        try:
            target.relative_to(vault_root)
        except ValueError:
            return {
                "status": "blocked",
                "reason": f"invalid_note_path:{note_path}",
            }
        if not target.is_file():
            return {
                "status": "blocked",
                "reason": f"missing_note_path:{note_path}",
            }
    backlink_precondition = _focused_action_backlink_precondition(vault_dir, action)
    if str(backlink_precondition.get("status") or "") != "ready":
        return backlink_precondition
    return {
        "status": "ready",
        "reason": "",
    }


def _focused_action_backlink_precondition(
    vault_dir: Path | str,
    action: dict[str, Any],
) -> dict[str, Any]:
    action_kind = str(action.get("action_kind") or "")
    if action_kind != "object_extraction_workflow":
        return {"status": "ready", "reason": ""}
    pack_name = str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME)
    note_paths = _focused_action_note_paths(action)
    if not note_paths:
        return {
            "status": "blocked",
            "reason": "backlink_expectation_unavailable:missing_note_path",
        }
    for note_path in note_paths:
        try:
            traceability = get_note_traceability(
                vault_dir,
                note_path=note_path,
                pack_name=pack_name,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            return {
                "status": "blocked",
                "reason": f"backlink_expectation_unavailable:{note_path}:{type(exc).__name__}",
            }
        expectation = traceability.get("backlink_expectation")
        if not isinstance(expectation, dict):
            return {
                "status": "blocked",
                "reason": f"backlink_expectation_unavailable:{note_path}:missing_payload",
            }
        expectation_status = str(expectation.get("status") or "")
        if expectation_status != "satisfied":
            return {
                "status": "blocked",
                "reason": f"backlink_expectation_failed:{expectation_status}:{note_path}",
            }
    return {"status": "ready", "reason": ""}


def _apply_failed_precondition(
    action: dict[str, Any], precondition: dict[str, Any]
) -> dict[str, Any]:
    status = str(precondition.get("status") or "blocked")
    reason = str(precondition.get("reason") or status)
    action["precondition_status"] = status
    action["finished_at"] = _utc_now_text()
    if status == "obsolete":
        action["status"] = "obsolete"
        action["obsolete_reason"] = reason
        action["failure_bucket"] = "obsolete_signal"
    elif status == "unsafe":
        action["status"] = "blocked"
        action["blocked_reason"] = reason
        action["failure_bucket"] = "unsafe_action"
    else:
        action["status"] = "blocked"
        action["blocked_reason"] = reason
        action["failure_bucket"] = "precondition_blocked"
    return action


def _next_queued_action_unlocked(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any] | None:
    normalized_pack = str(pack_name or "").strip()
    queued = [
        item
        for item in _read_action_queue_rows_unlocked(vault_dir)
        if item.get("status") == "queued"
        and (
            not normalized_pack
            or str(item.get("pack") or DEFAULT_WORKFLOW_PACK_NAME) == normalized_pack
        )
    ]
    if not queued:
        return None
    queued.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))))
    return dict(queued[0])


def _next_safe_queued_action_unlocked(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any] | None:
    normalized_pack = str(pack_name or "").strip()
    queued = [
        item
        for item in _read_action_queue_rows_unlocked(vault_dir)
        if item.get("status") == "queued"
        and bool(item.get("safe_to_run"))
        and (
            not normalized_pack
            or str(item.get("pack") or DEFAULT_WORKFLOW_PACK_NAME) == normalized_pack
        )
    ]
    if not queued:
        return None
    queued.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))))
    return dict(queued[0])


def retry_action_queue_item(vault_dir: Path | str, *, action_id: str) -> dict[str, Any]:
    with action_queue_write_lock(vault_dir):
        action = _action_by_id_unlocked(vault_dir, action_id)
        if action is None:
            raise ValueError("unknown action_id")
        if str(action.get("status") or "") not in {"failed", "blocked", "obsolete"}:
            raise ValueError("action is not retryable")
        action["status"] = "queued"
        action["started_at"] = ""
        action["finished_at"] = ""
        action["error"] = ""
        action["failure_bucket"] = ""
        action["precondition_status"] = ""
        action["blocked_reason"] = ""
        action["obsolete_reason"] = ""
        action["result"] = {}
        _replace_action_queue_item_unlocked(vault_dir, action)
    return {"retried": True, "action": action}


def dismiss_action_queue_item(vault_dir: Path | str, *, action_id: str) -> dict[str, Any]:
    with action_queue_write_lock(vault_dir):
        action = _action_by_id_unlocked(vault_dir, action_id)
        if action is None:
            raise ValueError("unknown action_id")
        if str(action.get("status") or "") in {"running", "succeeded", "dismissed"}:
            raise ValueError("action is not dismissible")
        action["status"] = "dismissed"
        action["finished_at"] = _utc_now_text()
        _replace_action_queue_item_unlocked(vault_dir, action)
    return {"dismissed": True, "action": action}


def _run_object_extraction_workflow_action(
    vault_dir: Path | str, action: dict[str, Any]
) -> dict[str, Any]:
    from .focused_actions import run_object_extraction_workflow_action

    return run_object_extraction_workflow_action(vault_dir=vault_dir, action=action)


def _refresh_truth_after_action(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    requires_truth_refresh: bool = False,
    requires_signal_resync: bool = False,
) -> None:
    from .knowledge_index import rebuild_knowledge_index

    if requires_truth_refresh:
        rebuild_knowledge_index(resolve_vault_dir(vault_dir), pack_name=pack_name)
    if requires_signal_resync:
        sync_signal_ledger(vault_dir, pack_name=pack_name)


def run_next_action_queue_item(
    vault_dir: Path | str,
    *,
    safe_only: bool = False,
    pack_name: str | None = None,
) -> dict[str, Any]:
    with action_queue_write_lock(vault_dir):
        action = _next_queued_action_unlocked(vault_dir, pack_name=pack_name)
        if action is None:
            return {
                "ran": False,
                "reason": "no_queued_actions",
                "safe_only": safe_only,
                "requested_pack": str(pack_name or ""),
            }

    try:
        contract = resolve_focused_action_execution_contract(
            pack_name=str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME),
            action_kind=str(action.get("action_kind") or ""),
        )
    except ValueError:
        with action_queue_write_lock(vault_dir):
            current = _action_by_id_unlocked(vault_dir, str(action.get("action_id") or ""))
            if current is None or str(current.get("status") or "") != "queued":
                return {
                    "ran": False,
                    "reason": "action_no_longer_queued",
                    "safe_only": safe_only,
                }
            current["status"] = "failed"
            current["error"] = f"unsupported_action_kind:{current.get('action_kind')}"
            current["failure_bucket"] = "unsupported_action_kind"
            current["retry_count"] = int(current.get("retry_count") or 0) + 1
            current["finished_at"] = _utc_now_text()
            _replace_action_queue_item_unlocked(vault_dir, current)
        return {
            "ran": False,
            "reason": "unsupported_action_kind",
            "action": current,
            "safe_only": safe_only,
        }

    precondition = _focused_action_precondition(vault_dir, action, safe_only=safe_only)
    with action_queue_write_lock(vault_dir):
        current = _action_by_id_unlocked(vault_dir, str(action.get("action_id") or ""))
        if current is None or str(current.get("status") or "") != "queued":
            return {
                "ran": False,
                "reason": "action_no_longer_queued",
                "safe_only": safe_only,
            }
        action = current
        if str(precondition.get("status") or "") != "ready":
            action = _apply_failed_precondition(action, precondition)
            _replace_action_queue_item_unlocked(vault_dir, action)
            reason = str(precondition.get("status") or "blocked")
            if reason == "obsolete":
                reason = "obsolete_signal"
            return {
                "ran": False,
                "reason": reason,
                "precondition": precondition,
                "action": action,
                "safe_only": safe_only,
            }

        started_at = _utc_now_text()
        action["status"] = "running"
        action["precondition_status"] = "ready"
        action["blocked_reason"] = ""
        action["obsolete_reason"] = ""
        action["started_at"] = started_at
        action["error"] = ""
        action["failure_bucket"] = ""
        _replace_action_queue_item_unlocked(vault_dir, action)

    try:
        _, result = execute_focused_action_handler(
            vault_dir,
            action,
            pack_name=str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME),
        )
        if getattr(contract.handler_spec, "requires_truth_refresh", False) or getattr(
            contract.handler_spec,
            "requires_signal_resync",
            False,
        ):
            _refresh_truth_after_action(
                vault_dir,
                pack_name=str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME),
                requires_truth_refresh=bool(
                    getattr(contract.handler_spec, "requires_truth_refresh", False)
                ),
                requires_signal_resync=bool(
                    getattr(contract.handler_spec, "requires_signal_resync", False)
                ),
            )
        with action_queue_write_lock(vault_dir):
            action["status"] = "succeeded"
            action["finished_at"] = _utc_now_text()
            action["result"] = result
            _replace_action_queue_item_unlocked(vault_dir, action)
        return {"ran": True, "action": action, "safe_only": safe_only}
    except Exception as exc:
        with action_queue_write_lock(vault_dir):
            action["status"] = "failed"
            action["error"] = str(exc)
            action["failure_bucket"] = _classify_action_error(str(exc))
            action["retry_count"] = int(action.get("retry_count") or 0) + 1
            action["finished_at"] = _utc_now_text()
            _replace_action_queue_item_unlocked(vault_dir, action)
        return {
            "ran": False,
            "reason": "execution_failed",
            "action": action,
            "safe_only": safe_only,
        }


def run_action_queue(
    vault_dir: Path | str,
    *,
    limit: int = 5,
    safe_only: bool = False,
    pack_name: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    results: list[dict[str, Any]] = []
    stopped_reason = "limit_reached"
    ran_count = 0
    for _ in range(MAX_PAGE_SIZE):
        if ran_count >= limit:
            break
        payload = run_next_action_queue_item(vault_dir, safe_only=safe_only, pack_name=pack_name)
        results.append(payload)
        if payload.get("ran"):
            ran_count += 1
            continue
        stopped_reason = str(payload.get("reason") or "stopped")
        if safe_only and stopped_reason == "unsafe":
            continue
        break
    else:
        if ran_count < limit:
            stopped_reason = "max_scan_reached"
    if ran_count >= limit:
        stopped_reason = "limit_reached"
    return {
        "limit": limit,
        "safe_only": safe_only,
        "requested_pack": str(pack_name or ""),
        "attempted_count": sum(1 for item in results if item.get("reason") != "no_queued_actions"),
        "ran_count": ran_count,
        "skipped_unsafe_count": sum(1 for item in results if item.get("reason") == "unsafe"),
        "obsolete_count": sum(1 for item in results if item.get("reason") == "obsolete_signal"),
        "failed_count": sum(
            1
            for item in results
            if item.get("reason") in {"execution_failed", "unsupported_action_kind"}
        ),
        "blocked_count": sum(1 for item in results if item.get("reason") == "blocked"),
        "stopped_reason": stopped_reason,
        "results": results,
    }


def _action_queue_state_map(vault_dir: Path | str) -> dict[str, dict[str, Any]]:
    state_map: dict[str, dict[str, Any]] = {}
    for item in list_action_queue(vault_dir, limit=MAX_PAGE_SIZE):
        signal_id = str(item.get("source_signal_id") or "")
        if signal_id and signal_id not in state_map:
            state_map[signal_id] = item
    return state_map


def _attach_action_queue_state(
    vault_dir: Path | str,
    items: list[dict[str, Any]],
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    queue_state = _action_queue_state_map(vault_dir)
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    capture_note_paths = list(
        dict.fromkeys(
            str(path)
            for item in items
            for path in item.get("note_paths", [])
            if isinstance(path, str) and path.strip()
        )
    )
    capture_summaries = _collect_capture_summaries_resilient(vault_dir, capture_note_paths)
    annotated: list[dict[str, Any]] = []
    for item in items:
        enriched = dict(item)
        signal_rule_metadata = _signal_rule_metadata_for_signal_type(
            str(item.get("signal_type") or ""),
            pack_name=normalized_pack,
        )
        for key, value in signal_rule_metadata.items():
            current = enriched.get(key)
            if key not in enriched or current in (None, "", False):
                enriched[key] = value
        recommended_action = item.get("recommended_action")
        matched_action: dict[str, Any] | None = None
        if isinstance(recommended_action, dict):
            action = queue_state.get(str(item.get("signal_id") or ""))
            matched_action = action
            recommended = dict(recommended_action)
            resolver_metadata = _resolver_rule_metadata_for_action_kind(
                str(recommended.get("kind") or ""),
                pack_name=normalized_pack,
            )
            for key, value in resolver_metadata.items():
                current = recommended.get(key)
                if key not in recommended or current in (None, "", False):
                    recommended[key] = value
            if action is not None:
                recommended["queue_status"] = action.get("status", "")
                recommended["action_id"] = action.get("action_id", "")
                recommended["precondition_status"] = action.get("precondition_status", "")
                recommended["blocked_reason"] = action.get("blocked_reason", "")
                recommended["obsolete_reason"] = action.get("obsolete_reason", "")
                recommended["handler_provider_pack"] = action.get("handler_provider_pack", "")
                recommended["handler_provider_name"] = action.get("handler_provider_name", "")
                recommended["processor_provider_pack"] = action.get("processor_provider_pack", "")
                recommended["processor_provider_name"] = action.get("processor_provider_name", "")
                action_pack = str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME)
                recommended["queue_path"] = (
                    "/actions"
                    if action_pack == DEFAULT_WORKFLOW_PACK_NAME
                    else f"/actions?pack={quote(action_pack, safe='')}"
                )
                recommended["safe_to_run"] = bool(action.get("safe_to_run"))
            enriched["recommended_action"] = recommended
        note_paths = [
            str(path)
            for path in enriched.get("note_paths", [])
            if isinstance(path, str) and path.strip()
        ]
        enriched["capture_summary"] = _capture_summary_from_map(note_paths, capture_summaries)
        enriched["impact_summary"] = _build_signal_impact_summary(enriched, action=matched_action)
        enriched["action_lifecycle"] = (
            {
                "queue_status": str(matched_action.get("status") or ""),
                "action_id": str(matched_action.get("action_id") or ""),
                "precondition_status": str(matched_action.get("precondition_status") or ""),
                "blocked_reason": str(matched_action.get("blocked_reason") or ""),
                "obsolete_reason": str(matched_action.get("obsolete_reason") or ""),
                "handler_provider_pack": str(matched_action.get("handler_provider_pack") or ""),
                "handler_provider_name": str(matched_action.get("handler_provider_name") or ""),
                "processor_provider_pack": str(matched_action.get("processor_provider_pack") or ""),
                "processor_provider_name": str(matched_action.get("processor_provider_name") or ""),
            }
            if matched_action is not None
            else {}
        )
        annotated.append(enriched)
    return annotated


_CAPTURE_SUMMARY_RECOVERABLE_ERRORS = (OSError, ValueError, sqlite3.Error)


def _collect_capture_summaries_resilient(
    vault_dir: Path | str,
    note_paths: list[str],
) -> dict[str, dict[str, Any]]:
    if not note_paths:
        return {}
    try:
        return _collect_note_capture_summaries(vault_dir, note_paths)
    except _CAPTURE_SUMMARY_RECOVERABLE_ERRORS:
        LOGGER.exception(
            "Failed to collect capture summaries for %d notes; falling back per note.",
            len(note_paths),
        )

    summaries: dict[str, dict[str, Any]] = {}
    for note_path in note_paths:
        try:
            summaries.update(_collect_note_capture_summaries(vault_dir, [note_path]))
        except _CAPTURE_SUMMARY_RECOVERABLE_ERRORS:
            LOGGER.exception("Failed to collect capture summary for %s.", note_path)
    return summaries


def list_production_gaps(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    candidate_limit = min(MAX_PAGE_SIZE, max(limit * 5, limit))
    items = list_production_chains(
        vault_dir, pack_name=pack_name, query=query, limit=candidate_limit
    )
    return _production_gap_items_from_chains(items, limit=limit)


def _production_gap_items_from_chains(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    weak_points: list[dict[str, Any]] = []
    for item in items:
        traceability = item["traceability"]
        missing: list[str] = []
        if item["stage_label"] == "source_note":
            if not traceability["objects"]:
                missing.append("objects")
            if not traceability["atlas_pages"]:
                missing.append("Atlas / MOC reach")
        else:
            if not traceability["source_notes"]:
                missing.append("source notes")
            if not traceability["objects"]:
                missing.append("objects")
            if not traceability["atlas_pages"]:
                missing.append("Atlas / MOC reach")
        if not missing:
            continue
        weak_points.append(
            {
                "signal_id": _signal_id("production_gap", item["path"]),
                "signal_type": "production_gap",
                "title": item["title"],
                "detail": ", ".join(missing),
                "stage_label": item["stage_label"],
                "note_path": item["path"],
                "missing": missing,
                "severity": len(missing),
                "traceability": item["traceability"],
            }
        )
    weak_points.sort(
        key=lambda item: (-item["severity"], item["stage_label"], item["title"].lower())
    )
    return weak_points[:limit]


def _compute_signal_entries(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    _, signals = execute_observation_surface_builder(
        surface_kind="signals",
        vault_dir=resolve_vault_dir(vault_dir),
        pack_name=pack_name,
    )
    return signals


def sync_signal_ledger(vault_dir: Path | str, *, pack_name: str | None = None) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    signals = _compute_signal_entries(resolved_vault, pack_name=normalized_pack)
    with signal_ledger_write_lock(resolved_vault):
        _rewrite_jsonl(_signal_ledger_path(resolved_vault, pack_name=normalized_pack), signals)
    type_counts = Counter(item["signal_type"] for item in signals)
    result = {
        "signal_count": len(signals),
        "type_counts": dict(type_counts),
        "pack": normalized_pack,
    }
    backfill = _backfill_auto_queue_actions(
        resolved_vault,
        pack_name=normalized_pack,
        signals=signals,
    )
    result["auto_queued_action_count"] = backfill["created_count"]
    cache_key = (
        str(resolved_vault.resolve()),
        normalized_pack,
        _signal_dependency_signature(resolved_vault),
    )
    _SIGNAL_LEDGER_SYNC_CACHE.clear()
    _SIGNAL_LEDGER_SYNC_CACHE[cache_key] = result
    return result


def ensure_signal_ledger_synced(
    vault_dir: Path | str, *, pack_name: str | None = None
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    cache_key = (
        str(resolved_vault.resolve()),
        normalized_pack,
        _signal_dependency_signature(resolved_vault),
    )
    cached = _SIGNAL_LEDGER_SYNC_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result = sync_signal_ledger(resolved_vault, pack_name=normalized_pack)
    _SIGNAL_LEDGER_SYNC_CACHE.clear()
    _SIGNAL_LEDGER_SYNC_CACHE[cache_key] = result
    return result


def list_signals(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    signal_type: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    ledger_path = _signal_ledger_path(resolved_vault, pack_name=normalized_pack)
    if not ledger_path.exists():
        ensure_signal_ledger_synced(resolved_vault, pack_name=normalized_pack)
    return _list_signals_from_ledger(
        resolved_vault,
        ledger_path=ledger_path,
        pack_name=normalized_pack,
        signal_type=signal_type,
        query=query,
        limit=limit,
    )


def _list_signals_from_ledger(
    vault_dir: Path | str,
    *,
    ledger_path: Path,
    pack_name: str | None = None,
    signal_type: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    normalized_query = (query or "").strip().lower()
    items: list[dict[str, Any]] = []
    with signal_ledger_write_lock(resolved_vault):
        for item in _read_jsonl_items(ledger_path):
            if signal_type and item.get("signal_type") != signal_type:
                continue
            if normalized_query:
                haystacks = [
                    str(item.get("title") or "").lower(),
                    str(item.get("detail") or "").lower(),
                    str(item.get("source_label") or "").lower(),
                ]
                if not any(normalized_query in haystack for haystack in haystacks):
                    continue
            items.append(item)
            if len(items) >= limit:
                break
    return _attach_action_queue_state(vault_dir, items, pack_name=normalized_pack)


def get_briefing_snapshot(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    resolved_vault = resolve_vault_dir(vault_dir)
    if not _signal_ledger_path(resolved_vault, pack_name=normalized_pack).exists():
        ensure_signal_ledger_synced(resolved_vault, pack_name=normalized_pack)
    _, payload = execute_observation_surface_builder(
        surface_kind="briefing",
        vault_dir=resolved_vault,
        pack_name=normalized_pack,
        limit=limit,
    )
    return payload


def get_note_provenance(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    frontmatter = _read_note_frontmatter(resolved_vault, note_path)
    source_url = str(frontmatter.get("source", "")).strip()
    original_source_note = None
    if source_url:
        original_source_note = _find_note_by_source(
            resolved_vault,
            source_url=source_url,
            exclude_path=note_path,
        )
    if original_source_note is None:
        original_source_note = _find_note_from_pipeline_log(resolved_vault, note_path=note_path)
    return {
        "note_path": note_path,
        "original_source_note": original_source_note,
    }


def _capture_summary_status(
    *,
    event_count: int,
    produced_artifact_count: int,
    error_count: int,
    skipped_count: int,
) -> str:
    if produced_artifact_count > 0:
        return "productive"
    if error_count > 0:
        return "failed"
    if skipped_count > 0:
        return "skipped"
    if event_count > 0:
        return "observed"
    return "missing"


def _capture_summary_text(
    *,
    status: str,
    event_count: int,
    produced_artifact_count: int,
    candidate_count: int,
    error_count: int,
) -> str:
    if status == "productive":
        artifact_noun = "artifact" if produced_artifact_count == 1 else "artifacts"
        candidate_text = (
            f" and surfaced {candidate_count} candidate" + ("" if candidate_count == 1 else "s")
            if candidate_count
            else ""
        )
        return (
            f"Captured {event_count} inbound events and produced "
            f"{produced_artifact_count} downstream {artifact_noun}{candidate_text}."
        )
    if status == "failed":
        issue_noun = "error" if error_count == 1 else "errors"
        return f"Observed {event_count} inbound capture events with {error_count} {issue_noun}."
    if status == "skipped":
        return f"Observed {event_count} inbound capture events, but the run stopped before downstream output."
    if status == "observed":
        noun = "event" if event_count == 1 else "events"
        return f"Observed {event_count} inbound capture {noun} but no downstream artifact yet."
    return "No inbound capture audit was found for this note yet."


def _note_capture_item(
    *,
    kind: str,
    label: str,
    timestamp: str,
    detail: str,
    path: str = "",
    produced_artifact_count: int = 0,
    candidate_count: int = 0,
    error_count: int = 0,
    skipped_count: int = 0,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "timestamp": timestamp,
        "detail": detail,
        "path": path,
        "produced_artifact_count": produced_artifact_count,
        "candidate_count": candidate_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
    }


def _note_capture_targets(vault_dir: Path | str, note_path: str) -> dict[str, Any]:
    relative_path = str(note_path)
    note_name = Path(relative_path).name
    frontmatter = _read_note_frontmatter(vault_dir, relative_path)
    note_slug = canonicalize_note_id(str(frontmatter.get("note_id") or Path(relative_path).stem))
    return {
        "note_path": relative_path,
        "note_name": note_name,
        "note_slug": note_slug,
    }


def _canonicalized_payload_targets(payload: dict[str, Any]) -> set[str]:
    targets = payload.get("targets", [])
    if not isinstance(targets, list):
        return set()
    return {
        canonicalize_note_id(str(item))
        for item in targets
        if isinstance(item, str) and item.strip()
    }


def _match_note_capture_event(
    vault_dir: Path | str,
    *,
    payload: dict[str, Any],
    targets: dict[str, Any],
    derived_note_names: set[str],
) -> dict[str, Any] | None:
    note_path = str(targets["note_path"])
    note_name = str(targets["note_name"])
    note_slug = str(targets["note_slug"])
    event_type = str(payload.get("event_type") or "").strip()
    if event_type not in _NOTE_CAPTURE_EVENT_TYPES:
        return None

    timestamp = str(payload.get("timestamp") or "")

    if event_type == "source_staged_for_processing":
        relative_source = _vault_relative_path(vault_dir, str(payload.get("source") or ""))
        relative_staged = _vault_relative_path(vault_dir, str(payload.get("staged") or ""))
        source_name = Path(str(payload.get("source") or "")).name
        staged_name = Path(relative_staged).name
        if not (
            relative_source == note_path
            or relative_staged == note_path
            or note_name in {source_name, staged_name}
        ):
            return None
        return _note_capture_item(
            kind="source_staged",
            label="Source staged",
            timestamp=timestamp,
            detail="Source note was staged for processing.",
        )
    if event_type == "source_archived_to_processed":
        relative_source = _vault_relative_path(vault_dir, str(payload.get("source") or ""))
        relative_archived = _vault_relative_path(vault_dir, str(payload.get("archived") or ""))
        source_name = Path(str(payload.get("source") or "")).name
        archived_name = Path(relative_archived).name
        if not (
            relative_source == note_path
            or relative_archived == note_path
            or note_name in {source_name, archived_name}
        ):
            return None
        return _note_capture_item(
            kind="processed_source",
            label="Source processed",
            timestamp=timestamp,
            detail="Source note was archived into the processed intake.",
        )
    if event_type == "source_restored_to_raw":
        relative_source = _vault_relative_path(vault_dir, str(payload.get("source") or ""))
        relative_restored = _vault_relative_path(vault_dir, str(payload.get("restored") or ""))
        source_name = Path(str(payload.get("source") or "")).name
        restored_name = Path(relative_restored).name
        if not (
            relative_source == note_path
            or relative_restored == note_path
            or note_name in {source_name, restored_name}
        ):
            return None
        return _note_capture_item(
            kind="source_restored",
            label="Source restored",
            timestamp=timestamp,
            detail="Source note was restored to raw intake before downstream output landed.",
            skipped_count=1,
        )
    # ``article_processed`` historically surfaced as "Deep dive
    # created" in the per-note capture timeline; BL-029 removed
    # the producer, but historical audit rows are still read here
    # so the inbound-capture status stays accurate against vaults
    # that pre-date the cleanup.  No new rows are emitted.
    if event_type == "article_processed":
        relative_output = _vault_relative_path(vault_dir, str(payload.get("output") or ""))
        file_name = Path(str(payload.get("file") or "")).name
        if relative_output != note_path and file_name != note_name:
            return None
        if relative_output:
            derived_note_names.add(Path(relative_output).name)
        return _note_capture_item(
            kind="deep_dive_created",
            label="Deep dive created",
            timestamp=timestamp,
            detail=(
                f"Created downstream deep dive at {relative_output}."
                if relative_output
                else "Created downstream deep dive."
            ),
            path=relative_output,
            produced_artifact_count=1,
        )
    if event_type == "article_abstained":
        file_name = Path(str(payload.get("file") or "")).name
        if file_name != note_name:
            return None
        return _note_capture_item(
            kind="capture_abstained",
            label="Capture abstained",
            timestamp=timestamp,
            detail=f"Interpretation abstained: {str(payload.get('reason') or 'unspecified')}.",
            skipped_count=1,
        )
    if event_type in {"article_error", "candidate_upsert_error", "evergreen_error"}:
        file_name = Path(str(payload.get("file") or "")).name
        if file_name != note_name:
            return None
        return _note_capture_item(
            kind="capture_error",
            label="Capture error",
            timestamp=timestamp,
            detail=str(payload.get("error") or "Capture error."),
            error_count=1,
        )
    if event_type == "candidates_upserted":
        file_name = Path(str(payload.get("file") or "")).name
        if file_name != note_name:
            return None
        candidates = [str(item) for item in payload.get("candidates", []) if str(item).strip()]
        return _note_capture_item(
            kind="candidate_upserted",
            label="Candidate surfaced",
            timestamp=timestamp,
            detail=(
                f"Surfaced {len(candidates)} candidate"
                + ("" if len(candidates) == 1 else "s")
                + (f": {', '.join(candidates)}." if candidates else ".")
            ),
            candidate_count=len(candidates),
        )
    if event_type in {"evergreen_auto_promoted", "evergreen_created"}:
        relative_path_field = _vault_relative_path(vault_dir, str(payload.get("path") or ""))
        source_name = Path(str(payload.get("source") or "")).name
        mutation = payload.get("mutation") if isinstance(payload.get("mutation"), dict) else {}
        target_slug = canonicalize_note_id(
            str(payload.get("concept") or mutation.get("target_slug") or "")
        )
        if not (
            relative_path_field == note_path
            or target_slug == note_slug
            or source_name == note_name
            or source_name in derived_note_names
        ):
            return None
        object_id = str(
            mutation.get("target_slug") or payload.get("concept") or ""
        ).strip()
        label = (
            "Evergreen promoted" if event_type == "evergreen_auto_promoted" else "Evergreen created"
        )
        detail = (
            f"Promoted evergreen object {object_id}."
            if event_type == "evergreen_auto_promoted" and object_id
            else (
                f"Created evergreen object {object_id}."
                if object_id
                else "Created evergreen output."
            )
        )
        return _note_capture_item(
            kind="evergreen_promoted"
            if event_type == "evergreen_auto_promoted"
            else "evergreen_created",
            label=label,
            timestamp=timestamp,
            detail=detail,
            path=relative_path_field,
            produced_artifact_count=1,
        )
    if event_type == "refine_mutation_applied":
        if note_slug in _canonicalized_payload_targets(payload):
            return _note_capture_item(
                kind="refine_mutation",
                label="Refine mutation",
                timestamp=timestamp,
                detail=f"Applied refine mutation in mode {str(payload.get('mode') or 'unknown')}.",
                produced_artifact_count=1,
            )
    return None


def _capture_summary_payload(
    note_path: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    items.sort(key=lambda item: (str(item.get("timestamp") or ""), str(item.get("kind") or "")))
    captured_event_count = len(items)
    produced_artifact_count = sum(int(item.get("produced_artifact_count") or 0) for item in items)
    candidate_count = sum(int(item.get("candidate_count") or 0) for item in items)
    error_count = sum(int(item.get("error_count") or 0) for item in items)
    skipped_count = sum(int(item.get("skipped_count") or 0) for item in items)
    status = _capture_summary_status(
        event_count=captured_event_count,
        produced_artifact_count=produced_artifact_count,
        error_count=error_count,
        skipped_count=skipped_count,
    )
    return {
        "note_path": str(note_path),
        "status": status,
        "captured_event_count": captured_event_count,
        "produced_artifact_count": produced_artifact_count,
        "candidate_count": candidate_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
        "latest_timestamp": str(items[-1]["timestamp"]) if items else "",
        "summary": _capture_summary_text(
            status=status,
            event_count=captured_event_count,
            produced_artifact_count=produced_artifact_count,
            candidate_count=candidate_count,
            error_count=error_count,
        ),
        "items": items[:8],
    }


def _collect_note_capture_summaries(
    vault_dir: Path | str,
    note_paths: list[str],
) -> dict[str, dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    target_state: dict[str, dict[str, Any]] = {}
    states_by_path: dict[str, list[dict[str, Any]]] = {}
    states_by_name: dict[str, list[dict[str, Any]]] = {}
    states_by_slug: dict[str, list[dict[str, Any]]] = {}

    def add_index(index: dict[str, list[dict[str, Any]]], key: str, state: dict[str, Any]) -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        bucket = index.setdefault(normalized_key, [])
        if not any(item["note_path"] == state["note_path"] for item in bucket):
            bucket.append(state)

    for note_path in note_paths:
        normalized_path = str(note_path)
        if not normalized_path.strip() or normalized_path in target_state:
            continue
        targets = _note_capture_targets(resolved_vault, normalized_path)
        state = {
            "note_path": normalized_path,
            "targets": targets,
            "derived_note_names": {str(targets["note_name"])},
            "items": [],
        }
        target_state[normalized_path] = state
        add_index(states_by_path, normalized_path, state)
        add_index(states_by_name, str(targets["note_name"]), state)
        add_index(states_by_slug, str(targets["note_slug"]), state)
    for log_path in (layout.pipeline_log, layout.logs_dir / "refine-mutations.jsonl"):
        if not log_path.exists():
            continue
        for payload in _read_jsonl_items(log_path):
            for state in _candidate_capture_states(
                resolved_vault,
                payload,
                states_by_path=states_by_path,
                states_by_name=states_by_name,
                states_by_slug=states_by_slug,
            ):
                previous_derived_names = set(state["derived_note_names"])
                item = _match_note_capture_event(
                    resolved_vault,
                    payload=payload,
                    targets=state["targets"],
                    derived_note_names=state["derived_note_names"],
                )
                if item is not None:
                    state["items"].append(item)
                    for derived_name in state["derived_note_names"] - previous_derived_names:
                        add_index(states_by_name, str(derived_name), state)
    return {
        note_path: _capture_summary_payload(note_path, list(state["items"]))
        for note_path, state in target_state.items()
    }


def _candidate_capture_states(
    vault_dir: Path | str,
    payload: dict[str, Any],
    *,
    states_by_path: dict[str, list[dict[str, Any]]],
    states_by_name: dict[str, list[dict[str, Any]]],
    states_by_slug: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    event_type = str(payload.get("event_type") or "").strip()
    if event_type not in _NOTE_CAPTURE_EVENT_TYPES:
        return []
    candidates: dict[str, dict[str, Any]] = {}

    def add_states(states: list[dict[str, Any]] | None) -> None:
        for state in states or []:
            candidates[str(state["note_path"])] = state

    def add_path(value: str) -> None:
        relative_path = _vault_relative_path(vault_dir, str(value or ""))
        add_states(states_by_path.get(relative_path))

    def add_name(value: str) -> None:
        name = Path(str(value or "")).name
        add_states(states_by_name.get(name))

    def add_slug(value: str) -> None:
        slug = canonicalize_note_id(str(value or ""))
        add_states(states_by_slug.get(slug))

    if event_type == "source_staged_for_processing":
        for key in ("source", "staged"):
            add_path(str(payload.get(key) or ""))
            add_name(str(payload.get(key) or ""))
    elif event_type == "source_archived_to_processed":
        for key in ("source", "archived"):
            add_path(str(payload.get(key) or ""))
            add_name(str(payload.get(key) or ""))
    elif event_type == "source_restored_to_raw":
        for key in ("source", "restored"):
            add_path(str(payload.get(key) or ""))
            add_name(str(payload.get(key) or ""))
    elif event_type == "article_processed":
        add_path(str(payload.get("output") or ""))
        add_name(str(payload.get("file") or ""))
    elif event_type in {
        "article_abstained",
        "article_error",
        "candidate_upsert_error",
        "evergreen_error",
        "candidates_upserted",
    }:
        add_name(str(payload.get("file") or ""))
    elif event_type in {"evergreen_auto_promoted", "evergreen_created"}:
        mutation = payload.get("mutation") if isinstance(payload.get("mutation"), dict) else {}
        add_path(str(payload.get("path") or ""))
        add_name(str(payload.get("source") or ""))
        add_slug(str(payload.get("concept") or mutation.get("target_slug") or ""))
    elif event_type == "refine_mutation_applied":
        for target in _canonicalized_payload_targets(payload):
            add_states(states_by_slug.get(target))
    return list(candidates.values())


def get_note_inbound_capture_summary(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    return _collect_note_capture_summaries(vault_dir, [str(note_path)]).get(
        str(note_path),
        _capture_summary_payload(str(note_path), []),
    )


def _aggregate_note_capture_summaries(
    vault_dir: Path | str,
    note_paths: list[str],
) -> dict[str, Any]:
    normalized_paths = [str(item) for item in note_paths if str(item).strip()]
    summary_map = _collect_note_capture_summaries(vault_dir, normalized_paths)
    return _capture_summary_from_map(normalized_paths, summary_map)


def _capture_summary_from_map(
    normalized_paths: list[str],
    summary_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized_paths = [str(item) for item in normalized_paths if str(item).strip()]
    if not normalized_paths:
        return _missing_capture_summary([])
    if len(normalized_paths) == 1:
        payload = dict(
            summary_map.get(normalized_paths[0])
            or _capture_summary_payload(normalized_paths[0], [])
        )
        payload["note_paths"] = normalized_paths
        return payload

    summaries = [
        summary_map.get(item) or _capture_summary_payload(item, [])
        for item in normalized_paths
    ]
    captured_event_count = sum(item["captured_event_count"] for item in summaries)
    produced_artifact_count = sum(item["produced_artifact_count"] for item in summaries)
    candidate_count = sum(item["candidate_count"] for item in summaries)
    error_count = sum(item["error_count"] for item in summaries)
    skipped_count = sum(item["skipped_count"] for item in summaries)
    latest_timestamp = max(
        (str(item["latest_timestamp"]) for item in summaries if item["latest_timestamp"]),
        default="",
    )
    status = _capture_summary_status(
        event_count=captured_event_count,
        produced_artifact_count=produced_artifact_count,
        error_count=error_count,
        skipped_count=skipped_count,
    )
    return {
        "status": status,
        "captured_event_count": captured_event_count,
        "produced_artifact_count": produced_artifact_count,
        "candidate_count": candidate_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
        "latest_timestamp": latest_timestamp,
        "summary": _capture_summary_text(
            status=status,
            event_count=captured_event_count,
            produced_artifact_count=produced_artifact_count,
            candidate_count=candidate_count,
            error_count=error_count,
        ),
        "items": [
            {
                "kind": "capture_note",
                "label": Path(item["note_path"]).name,
                "path": item["note_path"],
                "detail": item["summary"],
            }
            for item in summaries
        ][:8],
        "note_paths": normalized_paths,
    }


def _missing_capture_summary(note_paths: list[str]) -> dict[str, Any]:
    return {
        "status": "missing",
        "captured_event_count": 0,
        "produced_artifact_count": 0,
        "candidate_count": 0,
        "error_count": 0,
        "skipped_count": 0,
        "latest_timestamp": "",
        "summary": "No inbound capture audit was found for this note yet.",
        "items": [],
        "note_paths": list(note_paths),
    }


def _page_row_by_path(vault_dir: Path | str, note_path: str) -> dict[str, str]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE path = ?
            LIMIT 1
            """,
            (str((resolved_vault / note_path).resolve()),),
        ).fetchone()
    if row:
        return {
            "slug": row[0],
            "title": row[1],
            "note_type": row[2],
            "path": _vault_relative_path(resolved_vault, row[3]),
        }
    return {
        "slug": Path(note_path).stem,
        "title": Path(note_path).stem,
        "note_type": "note",
        "path": note_path,
    }


def _linked_existing_objects_for_note_path(
    vault_dir: Path | str,
    note_path: str,
    *,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    absolute_path = str((resolved_vault / note_path).resolve())
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    if not pack_candidates:
        return []
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT objects.pack, objects.object_id, objects.object_kind, objects.title,
                   objects.canonical_path, page_links.target_raw, page_links.line_number
            FROM page_links
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE page_links.source_slug = (
                SELECT slug
                FROM pages_index
                WHERE path = ?
                LIMIT 1
            )
              AND objects.pack IN ({pack_placeholders})
            ORDER BY CASE objects.pack
              {"".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))}
              ELSE {len(pack_candidates)}
            END, page_links.line_number, objects.object_id
            """,
            (absolute_path, *pack_candidates, *pack_candidates),
        ).fetchall()

    items: dict[str, dict[str, Any]] = {}
    for pack, object_id, object_kind, title, canonical_path, target_raw, line_number in rows:
        if object_id in items:
            continue
        items[str(object_id)] = {
            "object_id": str(object_id),
            "object_kind": str(object_kind),
            "title": str(title),
            "canonical_path": _vault_relative_path(resolved_vault, str(canonical_path)),
            "target_raw": str(target_raw or ""),
            "line_number": int(line_number or 0),
            "pack": str(pack),
        }
    return list(items.values())


def _brain_first_lookup_payload(
    *,
    stage_label: str,
    canonical_objects: list[dict[str, Any]],
    linked_existing_objects: list[dict[str, Any]],
) -> dict[str, Any]:
    if canonical_objects:
        return {
            "status": "canonical_objects_present",
            "decision": "skip_existing",
            "existing_object_count": len(canonical_objects),
            "existing_objects": canonical_objects,
            "lookup_source": "promoted_traceability",
            "summary": f"{len(canonical_objects)} canonical objects are already attached to this chain.",
        }
    if linked_existing_objects:
        return {
            "status": "existing_links_found",
            "decision": "reuse_existing",
            "existing_object_count": len(linked_existing_objects),
            "existing_objects": linked_existing_objects,
            "lookup_source": "page_links",
            "summary": (
                f"Brain-first lookup found {len(linked_existing_objects)} existing object links; "
                "reuse or reconcile before creating new candidates."
            ),
        }
    if stage_label == "source_note":
        return {
            "status": "no_existing_objects",
            "decision": "create_candidate",
            "existing_object_count": 0,
            "existing_objects": [],
            "lookup_source": "page_links",
            "summary": "No existing object links were found; candidate creation is allowed if extraction finds a stable concept.",
        }
    return {
        "status": "not_applicable",
        "decision": "inspect",
        "existing_object_count": 0,
        "existing_objects": [],
        "lookup_source": "page_links",
        "summary": "Brain-first lookup is not required for this note stage.",
    }


def _note_backlink_expectation_payload(
    *,
    note_path: str,
    stage_label: str,
    source_notes: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    atlas_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    source_note_paths = [str(item.get("path") or "") for item in source_notes if item.get("path")]
    object_ids = [
        str(item.get("object_id") or item.get("slug") or "")
        for item in objects
        if item.get("object_id") or item.get("slug")
    ]
    atlas_paths = [str(item.get("path") or "") for item in atlas_pages if item.get("path")]

    if stage_label == "source_note":
        status = (
            "satisfied"
            if object_ids or atlas_paths
            else "missing_downstream_links"
        )
    elif stage_label in {"evergreen_note", "evergreen_object"}:
        status = "satisfied" if source_note_paths else "missing_source_backlink"
    else:
        status = "inspect"

    return {
        "status": status,
        "note_path": str(note_path),
        "source_note_paths": source_note_paths,
        "object_ids": object_ids,
        "atlas_paths": atlas_paths,
        "summary": (
            f"{len(source_note_paths)} source notes, "
            f"{len(object_ids)} objects, {len(atlas_paths)} atlas pages linked."
        ),
    }


def _atlas_pages_for_object_ids(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> list[dict[str, str]]:
    atlas_pages: dict[str, dict[str, str]] = {}
    for provenance in get_object_provenance_map(
        vault_dir, object_ids, pack_name=pack_name
    ).values():
        for item in provenance["mocs"]:
            atlas_pages.setdefault(item["slug"], item)
    return list(atlas_pages.values())


def _pipeline_log_index(vault_dir: Path) -> dict[str, Any]:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    cache_key = (str(vault_dir.resolve()), *(_path_signature(log_path)[1:]))
    cached = _PIPELINE_LOG_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    article_outputs: dict[str, str] = {}
    derived_by_source_file: dict[str, list[dict[str, str]]] = {}
    archived_by_article_file: dict[str, str] = {}
    if log_path.exists():
        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("event_type") == "article_processed":
                file_name = str(event.get("file", "")).strip()
                output = str(event.get("output", "")).strip()
                if not file_name or not output:
                    continue
                candidate = Path(output)
                if not candidate.is_absolute():
                    candidate = (vault_dir / output).resolve()
                if not candidate.is_file():
                    continue
                relative_path = str(candidate.resolve().relative_to(vault_dir.resolve()))
                article_outputs[relative_path] = file_name
                frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
                derived_by_source_file.setdefault(file_name, [])
                if not any(
                    item["path"] == relative_path for item in derived_by_source_file[file_name]
                ):
                    derived_by_source_file[file_name].append(
                        {
                            "title": str(frontmatter.get("title") or candidate.stem).strip(),
                            "path": relative_path,
                        }
                    )
            elif event.get("event_type") == "source_archived_to_processed":
                archived = str(event.get("archived", "")).strip()
                source = str(event.get("source", "")).strip()
                if not archived and not source:
                    continue
                target = archived or source
                article_file = Path(target).name
                candidate = Path(target)
                if not candidate.is_absolute():
                    candidate = (vault_dir / target).resolve()
                if candidate.is_file():
                    archived_by_article_file[article_file] = str(
                        candidate.resolve().relative_to(vault_dir.resolve())
                    )

    original_source_by_output: dict[str, dict[str, str]] = {}
    for output_path, article_file in article_outputs.items():
        archived_path = archived_by_article_file.get(article_file)
        if not archived_path:
            continue
        candidate = (vault_dir / archived_path).resolve()
        if not candidate.is_file():
            continue
        frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
        original_source_by_output[output_path] = {
            "title": str(frontmatter.get("title") or candidate.stem).strip(),
            "path": archived_path,
        }

    result = {
        "original_source_by_output": original_source_by_output,
        "derived_by_source_file": derived_by_source_file,
    }
    _PIPELINE_LOG_INDEX_CACHE.clear()
    _PIPELINE_LOG_INDEX_CACHE[cache_key] = result
    return result


def get_note_traceability(
    vault_dir: Path | str,
    *,
    note_path: str,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Trace one note through the post-BL-029 chain.

    Stages: ``source_note`` → ``evergreen_note`` (objects) → atlas
    adjacency.  The legacy intermediate ``deep_dive`` stage was
    removed by BL-029; this function used to surface a
    ``deep_dives`` slot which is now omitted from the payload.
    """
    note = _page_row_by_path(vault_dir, note_path)
    provenance = get_note_provenance(vault_dir, note_path=note_path)
    source_notes: list[dict[str, str]] = []
    objects: list[dict[str, str]] = []
    atlas_pages: list[dict[str, str]] = []

    if note["note_type"] == "evergreen":
        object_traceability = get_object_traceability(vault_dir, note["slug"], pack_name=pack_name)
        source_notes = object_traceability["source_notes"]
        objects = [
            {
                "object_id": object_traceability["object"]["object_id"],
                "title": object_traceability["object"]["title"],
            }
        ]
        atlas_pages = object_traceability["atlas_pages"]
    elif provenance["original_source_note"]:
        source_notes = [provenance["original_source_note"]]

    if not atlas_pages:
        atlas_pages = _atlas_pages_for_object_ids(
            vault_dir,
            [item["object_id"] for item in objects],
            pack_name=pack_name,
        )
    note_type = str(note.get("note_type") or "")
    if note_type == "evergreen":
        stage_label = "evergreen_note"
        stage_presence = {
            "source_notes": bool(source_notes),
            "objects": True,
            "atlas_pages": bool(atlas_pages),
        }
        chain_summary = (
            f"Evergreen note currently traces to {len(source_notes)} source notes, "
            f"{len(objects)} objects, {len(atlas_pages)} atlas pages."
        )
    else:
        stage_label = "source_note"
        stage_presence = {
            "source_notes": True,
            "objects": bool(objects),
            "atlas_pages": bool(atlas_pages),
        }
        chain_summary = (
            f"Source note currently traces to {len(objects)} objects, "
            f"{len(atlas_pages)} atlas pages."
        )
    linked_existing_objects = []
    if not objects:
        linked_existing_objects = _linked_existing_objects_for_note_path(
            vault_dir,
            note_path,
            pack_name=pack_name,
        )
    brain_first_lookup = _brain_first_lookup_payload(
        stage_label=stage_label,
        canonical_objects=objects,
        linked_existing_objects=linked_existing_objects,
    )
    backlink_expectation = _note_backlink_expectation_payload(
        note_path=note_path,
        stage_label=stage_label,
        source_notes=source_notes,
        objects=objects,
        atlas_pages=atlas_pages,
    )
    missing_stages = [stage for stage, present in stage_presence.items() if not present]
    chain_status = "complete" if not missing_stages else "partial"
    return {
        "note": note,
        "stage_label": stage_label,
        "source_notes": source_notes,
        "objects": objects,
        "atlas_pages": atlas_pages,
        "stage_presence": stage_presence,
        "missing_stages": missing_stages,
        "chain_status": chain_status,
        "chain_summary": chain_summary,
        "brain_first_lookup": brain_first_lookup,
        "backlink_expectation": backlink_expectation,
        "counts": {
            "source_notes": len(source_notes),
            "objects": len(objects),
            "atlas_pages": len(atlas_pages),
        },
    }


def get_object_traceability(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Trace one object back to its source notes + atlas adjacency.

    Post-BL-029 chain: source_note → evergreen_object → atlas.  The
    legacy deep-dive intermediate stage was removed; ``source_notes``
    now comes directly from ``get_object_detail.provenance``
    (page_links → non-evergreen non-atlas backlinks).
    """
    detail = get_object_detail(vault_dir, object_id, pack_name=pack_name)
    source_note_map: dict[str, dict[str, str]] = {
        item["path"]: item for item in detail["provenance"]["source_notes"]
    }
    stage_presence = {
        "source_notes": bool(source_note_map),
        "atlas_pages": bool(detail["provenance"]["mocs"]),
    }
    missing_stages = [stage for stage, present in stage_presence.items() if not present]
    chain_status = "complete" if not missing_stages else "partial"
    object_as_link = {
        "object_id": detail["object"]["object_id"],
        "object_kind": detail["object"].get("object_kind", "evergreen"),
        "title": detail["object"]["title"],
        "canonical_path": detail["object"].get("canonical_path", ""),
        "pack": detail["object"].get("pack", ""),
    }
    brain_first_lookup = _brain_first_lookup_payload(
        stage_label="evergreen_object",
        canonical_objects=[object_as_link],
        linked_existing_objects=[],
    )
    backlink_expectation = _note_backlink_expectation_payload(
        note_path=str(detail["provenance"]["evergreen_path"]),
        stage_label="evergreen_object",
        source_notes=list(source_note_map.values()),
        objects=[object_as_link],
        atlas_pages=detail["provenance"]["mocs"],
    )
    return {
        "object": detail["object"],
        "stage_label": "evergreen_object",
        "evergreen_note": {
            "title": detail["object"]["title"],
            "path": detail["provenance"]["evergreen_path"],
        },
        "source_notes": list(source_note_map.values()),
        "atlas_pages": detail["provenance"]["mocs"],
        "stage_presence": stage_presence,
        "missing_stages": missing_stages,
        "chain_status": chain_status,
        "chain_summary": (
            f"Object currently traces to {len(source_note_map)} source notes, "
            f"{len(detail['provenance']['mocs'])} atlas pages."
        ),
        "brain_first_lookup": brain_first_lookup,
        "backlink_expectation": backlink_expectation,
        "counts": {
            "source_notes": len(source_note_map),
            "atlas_pages": len(detail["provenance"]["mocs"]),
        },
    }


# Legacy archive filename pattern produced by the pre-BL-029
# auto_article_processor.  The producer is gone, but legacy vaults
# still hold these files until ``absorb v2`` re-promotes them.
# We surface a warning chip when an evergreen object's
# ``canonical_path`` still points at one.
_LEGACY_DEEP_DIVE_SUFFIX = "_深度解读.md"


def get_object_source_chain(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Resolve the post-BL-029 source chain for one object.

    The chain (and the ``/object`` page that surfaces it) is::

        Source URL  →  Source File (active staging)
                    →  Pipeline Stages (provenance rows)
                    →  Evergreen Markdown (canonical object file)

    ``source_url`` lives on ``objects`` (BL-054).  Per-stage rows
    live in ``provenance`` (BL-055; today only ``stage='ingest'`` is
    written, future BL-056 fills ``extract``/``promote``/...).
    Source-file resolution reuses ``source_dedup.find_existing_by_url``
    so it stays in sync with the intake gate.

    Returns a structurally-stable dict — every field is always
    present, even when the underlying signal is missing — so the
    renderer never has to ``.get(...)`` defensively.
    """
    from .source_dedup import find_existing_by_url

    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )

    truth_pack = ""
    source_url = ""
    canonical_path = ""
    with sqlite3.connect(db_path) as conn:
        for candidate_pack in pack_candidates:
            row = conn.execute(
                """
                SELECT canonical_path, source_url
                FROM objects
                WHERE pack = ? AND object_id = ?
                """,
                (candidate_pack, object_id),
            ).fetchone()
            if row is not None:
                truth_pack = candidate_pack
                canonical_path = row[0] or ""
                source_url = row[1] or ""
                break
        provenance_rows: list[tuple[str, str, str, str]] = []
        if truth_pack:
            provenance_rows = conn.execute(
                """
                SELECT derived_via_stage, derived_at, source_url, metadata_json
                FROM provenance
                WHERE pack = ? AND object_id = ?
                ORDER BY derived_at, derived_via_stage
                """,
                (truth_pack, object_id),
            ).fetchall()

    source_url_domain = ""
    if source_url:
        try:
            parsed = urlparse(source_url)
            source_url_domain = (parsed.netloc or "").lower()
        except ValueError:
            source_url_domain = ""

    source_file_relative = ""
    if source_url:
        try:
            staging_path = find_existing_by_url(resolved_vault, source_url)
        except Exception:  # noqa: BLE001 — index lookup is best-effort
            staging_path = None
        if staging_path is not None:
            source_file_relative = _vault_relative_path(
                resolved_vault, str(staging_path),
            )

    provenance_stages: list[dict[str, Any]] = []
    for stage, derived_at, prov_source_url, metadata_json in provenance_rows:
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except (TypeError, ValueError):
            metadata = {}
        provenance_stages.append({
            "stage": stage or "",
            "derived_at": derived_at or "",
            "source_url": prov_source_url or "",
            "metadata": metadata if isinstance(metadata, dict) else {},
        })

    evergreen_path = _vault_relative_path(resolved_vault, canonical_path) if canonical_path else ""
    evergreen_path_legacy = bool(
        evergreen_path and evergreen_path.endswith(_LEGACY_DEEP_DIVE_SUFFIX)
    )

    return {
        "object_id": object_id,
        "pack": truth_pack,
        "source_url": source_url,
        "source_url_domain": source_url_domain,
        "source_file_path": source_file_relative,
        "provenance_stages": provenance_stages,
        "evergreen_path": evergreen_path,
        "evergreen_path_legacy": evergreen_path_legacy,
    }


def list_production_chains(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _, items = execute_observation_surface_builder(
        surface_kind="production_chains",
        vault_dir=resolve_vault_dir(vault_dir),
        pack_name=pack_name,
        query=query,
        limit=limit,
    )
    return items


def list_contradictions(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    limit: int = 100,
    status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="contradictions"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    sql = """
        SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
        FROM contradictions
    """
    params: list[Any] = [*pack_candidates]
    where_clauses: list[str] = [f"pack IN ({pack_placeholders})"]
    if normalized_query:
        where_clauses.append("lower(subject_key) LIKE ? ESCAPE '\\'")
        params.append(f"%{normalized_query}%")
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += (
        " ORDER BY CASE pack "
        + "".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))
        + f"ELSE {len(pack_candidates)} END, subject_key"
    )
    params.extend(pack_candidates)
    if status is None:
        sql += " LIMIT ?"
        params.append(limit)

    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise

    items = [
        {
            "contradiction_id": row[0],
            "subject_key": row[1],
            "positive_claim_ids": json.loads(row[2]),
            "negative_claim_ids": json.loads(row[3]),
            "status": row[4],
            "resolution_note": row[5] or "",
            "resolved_at": row[6] or "",
        }
        for row in rows
    ]
    contradiction_overrides = _latest_contradiction_review_overrides(vault_dir)
    for item in items:
        override = contradiction_overrides.get(str(item["contradiction_id"]))
        if not override:
            continue
        item["status"] = override["status"]
        item["resolution_note"] = override["resolution_note"]
        item["resolved_at"] = override["resolved_at"]
    if status:
        if status == "resolved":
            items = [item for item in items if item["status"] != "open"]
        else:
            items = [item for item in items if item["status"] == status]
    items = items[:limit]
    claim_map = _claim_details_map(
        vault_dir,
        [
            claim_id
            for item in items
            for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
        ],
    )
    evidence_map = _claim_evidence_map(
        vault_dir,
        [
            claim_id
            for item in items
            for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
        ],
    )
    for item in items:
        object_ids = list(
            dict.fromkeys(
                claim_id.split("::", 1)[0]
                for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
            )
        )
        item["positive_claims"] = [
            {
                **claim_map[claim_id],
                "evidence": evidence_map.get(claim_id, []),
            }
            for claim_id in item["positive_claim_ids"]
            if claim_id in claim_map
        ]
        item["negative_claims"] = [
            {
                **claim_map[claim_id],
                "evidence": evidence_map.get(claim_id, []),
            }
            for claim_id in item["negative_claim_ids"]
            if claim_id in claim_map
        ]
        item["detection_model"] = "page_summary_polarity"
        item["detection_confidence"] = "heuristic"
        item["status_bucket"] = "open" if item["status"] == "open" else "reviewed"
        item["status_explanation"] = CONTRADICTION_STATUS_EXPLANATIONS.get(
            item["status"],
            "Reviewed contradiction state.",
        )
        source_note_count = len(
            {
                evidence["source_slug"]
                for claim in item["positive_claims"] + item["negative_claims"]
                for evidence in claim["evidence"]
            }
        )
        quote_count = sum(
            1
            for claim in item["positive_claims"] + item["negative_claims"]
            for evidence in claim["evidence"]
            if str(evidence.get("quote_text") or "").strip()
        )
        item["scope_summary"] = {
            "object_count": len(object_ids),
            "positive_claim_count": len(item["positive_claims"]),
            "negative_claim_count": len(item["negative_claims"]),
            "source_note_count": source_note_count,
        }
        item["ranked_evidence"] = _rank_contradiction_evidence(item)
        item["polarity_summary"] = {
            "positive_claim_count": len(item["positive_claims"]),
            "negative_claim_count": len(item["negative_claims"]),
            "object_count": len(object_ids),
        }
        item["evidence_summary"] = {
            "ranked_evidence_count": len(item["ranked_evidence"]),
            "source_note_count": source_note_count,
            "quote_count": quote_count,
        }
        item["tension_summary"] = (
            f"{len(item['positive_claims'])} positive claims vs "
            f"{len(item['negative_claims'])} negative claims across {len(object_ids)} objects."
        )
        item["review_history"] = list_review_actions(vault_dir, object_ids=object_ids, limit=5)
    return items


def _eligible_evolution_object_ids(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> list[str]:
    """Object ids in scope for evolution-candidate scoring.

    Pre-BL-029 this was the intersection of "objects in
    ``objects`` table" and "objects produced by a deep-dive
    promotion" — the deep-dive map is gone post-BL-029, and the
    evergreen-promotion path now writes directly into ``objects``.
    The simpler scope is "every object in the pack-scoped truth
    store"; evolution candidate scoring already filters by other
    signals (claims/relations recency).

    Goes straight to SQL (``SELECT DISTINCT object_id``) instead of
    routing through ``list_objects``: the latter caps results at
    ``MAX_PAGE_SIZE`` and would silently drop the tail of any pack
    with more than one page of objects.
    """
    db_path = _db_path(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    if not pack_candidates:
        return []
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT DISTINCT object_id FROM objects "
            f"WHERE pack IN ({pack_placeholders})",
            tuple(pack_candidates),
        ).fetchall()
    return sorted(str(row[0]) for row in rows if row[0])


def _compute_evolution_candidates(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    normalized_object_ids = list(
        dict.fromkeys(object_id for object_id in (object_ids or []) if object_id)
    )
    scoped_object_id_set = set(normalized_object_ids)

    open_contradictions = list_contradictions(
        vault_dir,
        pack_name=pack_name,
        limit=MAX_PAGE_SIZE,
        status="open",
    )
    if scoped_object_id_set:
        open_contradictions = [
            item
            for item in open_contradictions
            if scoped_object_id_set.intersection(
                claim["object_id"] for claim in (item["positive_claims"] + item["negative_claims"])
            )
        ]
    contradiction_object_ids = sorted(
        {
            claim["object_id"]
            for item in open_contradictions
            for claim in (item["positive_claims"] + item["negative_claims"])
        }
    )
    contradiction_object_paths = _batch_object_rows(
        vault_dir,
        contradiction_object_ids,
        pack_name=pack_name,
    )
    contradiction_source_paths = _page_paths_for_slugs(
        vault_dir,
        [
            evidence["source_slug"]
            for item in open_contradictions
            for claim in (item["positive_claims"] + item["negative_claims"])
            for evidence in claim["evidence"]
            if evidence.get("source_slug")
        ],
    )

    for item in open_contradictions:
        positive_claims = list(item["positive_claims"])
        negative_claims = list(item["negative_claims"])
        if not positive_claims or not negative_claims:
            continue
        contradiction_item_object_ids = sorted(
            {claim["object_id"] for claim in (positive_claims + negative_claims)}
        )

        claim_dates: dict[str, tuple[tuple[int, float, str], str]] = {}
        for claim in positive_claims + negative_claims:
            canonical_path = contradiction_object_paths.get(claim["object_id"], {}).get(
                "canonical_path", ""
            )
            date_text = _note_date_text(vault_dir, canonical_path) if canonical_path else ""
            claim_dates[claim["claim_id"]] = (_note_date_sort_key(date_text), date_text)

        positive_claim = positive_claims[0]
        negative_claim = negative_claims[0]
        best_pair_score: tuple[int, float, str, str] | None = None
        for positive_candidate in positive_claims:
            positive_key, _positive_date = claim_dates[positive_candidate["claim_id"]]
            for negative_candidate in negative_claims:
                negative_key, _negative_date = claim_dates[negative_candidate["claim_id"]]
                both_valid = 1 if positive_key[0] and negative_key[0] else 0
                distance = abs(positive_key[1] - negative_key[1]) if both_valid else 0.0
                pair_score = (
                    both_valid,
                    distance,
                    positive_candidate["claim_id"],
                    negative_candidate["claim_id"],
                )
                if best_pair_score is None or pair_score > best_pair_score:
                    best_pair_score = pair_score
                    positive_claim = positive_candidate
                    negative_claim = negative_candidate

        positive_key, positive_date = claim_dates[positive_claim["claim_id"]]
        negative_key, negative_date = claim_dates[negative_claim["claim_id"]]
        if positive_key > negative_key:
            earlier_claim, later_claim = negative_claim, positive_claim
            earlier_date, later_date = negative_date, positive_date
        else:
            earlier_claim, later_claim = positive_claim, negative_claim
            earlier_date, later_date = positive_date, negative_date

        record = {
            "evolution_id": _candidate_evolution_id(
                link_type="challenges",
                subject_kind="topic",
                subject_id=item["subject_key"],
                earlier_ref=f"claim://{earlier_claim['claim_id']}",
                later_ref=f"claim://{later_claim['claim_id']}",
            ),
            "status": "candidate",
            "link_type": "challenges",
            "subject_kind": "topic",
            "subject_id": item["subject_key"],
            "object_ids": contradiction_item_object_ids,
            "earlier_ref": f"claim://{earlier_claim['claim_id']}",
            "later_ref": f"claim://{later_claim['claim_id']}",
            "earlier_date": earlier_date,
            "later_date": later_date,
            "reason_codes": ["open_contradiction", "claim_polarity_divergence"],
            "confidence": 0.9,
            "evidence": item["ranked_evidence"][:4],
            "source_paths": [
                path
                for path in dict.fromkeys(
                    [
                        *(
                            contradiction_source_paths.get(evidence["source_slug"], "")
                            for claim in (item["positive_claims"] + item["negative_claims"])
                            for evidence in claim["evidence"]
                            if evidence.get("source_slug")
                        ),
                        *(
                            contradiction_object_paths.get(object_id, {}).get("canonical_path", "")
                            for object_id in contradiction_item_object_ids
                        ),
                    ]
                )
                if path
            ],
        }
        candidates.append(record)

    stale_summaries = list_stale_summaries(
        vault_dir,
        pack_name=pack_name,
        object_ids=normalized_object_ids or None,
        limit=MAX_PAGE_SIZE,
    )
    for item in stale_summaries:
        traceability = get_object_traceability(vault_dir, item["object_id"], pack_name=pack_name)
        earlier_path = traceability["object"]["canonical_path"]
        earlier_date = _note_date_text(vault_dir, earlier_path)
        earlier_key = _note_date_sort_key(earlier_date)
        later_choice: dict[str, str] | None = None
        later_choice_key: tuple[int, float, str] | None = None
        for note in traceability["source_notes"]:
            if not _has_supersession_cue(vault_dir, note["path"]):
                continue
            candidate_date = _note_date_text(vault_dir, note["path"])
            candidate_key = _note_date_sort_key(candidate_date)
            if candidate_key <= earlier_key:
                continue
            if later_choice_key is None or candidate_key > later_choice_key:
                later_choice = note
                later_choice_key = candidate_key
        if later_choice is None:
            continue
        later_ref = f"{later_choice.get('note_type') or 'note'}://{later_choice['path']}"
        later_date = _note_date_text(vault_dir, later_choice["path"])
        record = {
            "evolution_id": _candidate_evolution_id(
                link_type="replaces",
                subject_kind="object",
                subject_id=item["object_id"],
                earlier_ref=f"object://{item['object_id']}",
                later_ref=later_ref,
            ),
            "status": "candidate",
            "link_type": "replaces",
            "subject_kind": "object",
            "subject_id": item["object_id"],
            "object_ids": [item["object_id"]],
            "earlier_ref": f"object://{item['object_id']}",
            "later_ref": later_ref,
            "earlier_date": earlier_date,
            "later_date": later_date,
            "reason_codes": ["stale_summary", "later_traceability_neighbor"],
            "confidence": 0.8,
            "evidence": [
                *[
                    {"kind": "stale_summary_reason", "code": code, "text": text}
                    for code, text in zip(item["reason_codes"], item["reason_texts"], strict=False)
                ],
                {
                    "kind": "later_traceability_neighbor",
                    "title": later_choice["title"],
                    "path": later_choice["path"],
                    "date": later_date,
                },
            ],
            "source_paths": [
                path
                for path in dict.fromkeys(
                    [
                        earlier_path,
                        *[note["path"] for note in traceability["source_notes"]],
                    ]
                )
                if path
            ],
        }
        candidates.append(record)

    candidate_object_ids = normalized_object_ids or _eligible_evolution_object_ids(
        vault_dir,
        pack_name=pack_name,
    )
    for object_id in candidate_object_ids:
        traceability = get_object_traceability(vault_dir, object_id, pack_name=pack_name)
        earlier_path = traceability["object"]["canonical_path"]
        earlier_date = _note_date_text(vault_dir, earlier_path)
        earlier_key = _note_date_sort_key(earlier_date)
        for note in traceability["source_notes"]:
            later_date = _note_date_text(vault_dir, note["path"])
            later_key = _note_date_sort_key(later_date)
            if later_key <= earlier_key:
                continue
            if _has_supersession_cue(vault_dir, note["path"]):
                continue
            inferred_link_type = (
                "confirms" if _has_confirmation_cue(vault_dir, note["path"]) else "enriches"
            )
            record = {
                "evolution_id": _candidate_evolution_id(
                    link_type=inferred_link_type,
                    subject_kind="object",
                    subject_id=object_id,
                    earlier_ref=f"object://{object_id}",
                    later_ref=f"{note.get('note_type') or 'note'}://{note['path']}",
                ),
                "status": "candidate",
                "link_type": inferred_link_type,
                "subject_kind": "object",
                "subject_id": object_id,
                "object_ids": [object_id],
                "earlier_ref": f"object://{object_id}",
                "later_ref": f"{note.get('note_type') or 'note'}://{note['path']}",
                "earlier_date": earlier_date,
                "later_date": later_date,
                "reason_codes": [
                    "later_traceability_neighbor",
                    f"lexical_{inferred_link_type}"
                    if inferred_link_type == "confirms"
                    else "later_context",
                ],
                "confidence": 0.7 if inferred_link_type == "confirms" else 0.6,
                "evidence": [
                    {
                        "kind": "later_traceability_neighbor",
                        "title": note["title"],
                        "path": note["path"],
                        "date": later_date,
                    }
                ],
                "source_paths": [
                    path for path in dict.fromkeys([earlier_path, note["path"]]) if path
                ],
            }
            candidates.append(record)

    candidates.sort(
        key=lambda item: (
            str(item["later_date"]),
            str(item["subject_id"]),
            str(item["link_type"]),
            str(item["evolution_id"]),
        ),
        reverse=True,
    )
    unique_candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in candidates:
        if item["evolution_id"] in seen_ids:
            continue
        seen_ids.add(item["evolution_id"])
        unique_candidates.append(item)
    return unique_candidates


def _all_evolution_candidates(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    pack_name: str | None = None,
) -> list[dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_object_ids = tuple(
        dict.fromkeys(object_id for object_id in (object_ids or []) if object_id)
    )
    cache_key = (
        str(resolved_vault.resolve()),
        _evolution_dependency_signature(resolved_vault),
        _truth_pack_name(pack_name),
        normalized_object_ids,
    )
    cached = _EVOLUTION_CANDIDATE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result = _compute_evolution_candidates(
        resolved_vault,
        object_ids=list(normalized_object_ids),
        pack_name=pack_name,
    )
    _EVOLUTION_CANDIDATE_CACHE[cache_key] = result
    return result


def list_evolution_candidates(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    pack_name: str | None = None,
    query: str | None = None,
    link_type: str | None = None,
    status: str = "candidate",
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    if status != "candidate":
        return []
    normalized_query = (query or "").strip().lower()
    unique_candidates = [
        item
        for item in _all_evolution_candidates(vault_dir, object_ids=object_ids, pack_name=pack_name)
        if (not link_type or item["link_type"] == link_type)
        and (not normalized_query or _evolution_candidate_matches_query(item, normalized_query))
    ]
    return unique_candidates[offset : offset + limit]


def list_evolution_links(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    pack_name: str | None = None,
    query: str | None = None,
    link_type: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    normalized_query = (query or "").strip().lower()
    latest_by_evolution_id: dict[str, dict[str, Any]] = {}
    for action in list_evolution_review_actions(
        vault_dir, object_ids=object_ids, pack_name=pack_name
    ):
        evolution_id = str(action.get("evolution_id") or "")
        if not evolution_id or evolution_id in latest_by_evolution_id:
            continue
        latest_by_evolution_id[evolution_id] = action
    items = list(latest_by_evolution_id.values())
    filtered: list[dict[str, Any]] = []
    for item in items:
        if status and item.get("status") != status:
            continue
        if link_type and item.get("link_type") != link_type:
            continue
        if normalized_query and not _evolution_candidate_matches_query(item, normalized_query):
            continue
        filtered.append(item)
    if limit is not None:
        limit, _ = _validate_page_args(limit=limit, offset=0)
        return filtered[:limit]
    return filtered


def review_evolution_candidate(
    vault_dir: Path | str,
    *,
    evolution_id: str,
    status: str,
    pack_name: str | None = None,
    note: str = "",
    link_type: str | None = None,
) -> dict[str, Any]:
    if not evolution_id:
        raise ValueError("missing evolution_id")
    if status not in {"accepted", "rejected"}:
        raise ValueError("invalid evolution status")
    if link_type and link_type not in {"replaces", "enriches", "confirms", "challenges"}:
        raise ValueError("invalid evolution link_type")
    candidate = next(
        (
            item
            for item in _all_evolution_candidates(vault_dir, pack_name=pack_name)
            if item["evolution_id"] == evolution_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("unknown evolution candidate")
    final_link_type = link_type or str(candidate["link_type"])
    payload = {
        "object_ids": list(candidate.get("object_ids", [])),
        "evolution_id": candidate["evolution_id"],
        "status": status,
        "note": note,
        "link_type": final_link_type,
        "candidate_link_type": candidate["link_type"],
        "subject_kind": candidate["subject_kind"],
        "subject_id": candidate["subject_id"],
        "earlier_ref": candidate["earlier_ref"],
        "later_ref": candidate["later_ref"],
        "pack": _truth_pack_name(pack_name),
    }
    record_review_action(
        vault_dir,
        event_type="ui_evolution_reviewed",
        slug=str(candidate["subject_id"]),
        payload=payload,
    )
    return {
        "reviewed_count": 1,
        "accepted_count": 1 if status == "accepted" else 0,
        "rejected_count": 1 if status == "rejected" else 0,
        "evolution_ids": [candidate["evolution_id"]],
        "candidate_count": 1,
        "status": status,
    }


def get_topic_neighborhood(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
    depth: int = 1,
) -> dict[str, Any]:
    if depth != 1:
        raise ValueError("Only depth=1 is currently supported")

    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    pack_order = "".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))
    with sqlite3.connect(db_path) as conn:
        center = conn.execute(
            f"""
            SELECT pack, object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE pack IN ({",".join("?" for _ in pack_candidates)}) AND object_id = ?
            ORDER BY CASE pack {pack_order}ELSE {len(pack_candidates)} END
            LIMIT 1
            """,
            (*pack_candidates, object_id, *pack_candidates),
        ).fetchone()
        if center is None:
            raise ValueError(f"Unknown object_id: {object_id}")
        truth_pack = str(center[0])

        edge_rows = conn.execute(
            """
            SELECT r.source_object_id, r.target_object_id, r.relation_type, r.evidence_source_slug,
                   COALESCE(src.object_kind, '') AS source_kind,
                   COALESCE(tgt.object_kind, '') AS target_kind
            FROM relations r
            LEFT JOIN objects src ON src.object_id = r.source_object_id AND src.pack = r.pack
            LEFT JOIN objects tgt ON tgt.object_id = r.target_object_id AND tgt.pack = r.pack
            WHERE r.pack = ? AND r.source_object_id = ?
            ORDER BY r.target_object_id
            """,
            (truth_pack, object_id),
        ).fetchall()
        neighbor_ids = [row[1] for row in edge_rows]
        if neighbor_ids:
            placeholders = ",".join("?" for _ in neighbor_ids)
            neighbor_rows = conn.execute(
                f"""
                SELECT object_id, object_kind, title, canonical_path, source_slug
                FROM objects
                WHERE pack = ? AND object_id IN ({placeholders})
                ORDER BY object_id
                """,
                (truth_pack, *neighbor_ids),
            ).fetchall()
        else:
            neighbor_rows = []

    return {
        "center": {
            "object_id": center[1],
            "object_kind": center[2],
            "title": center[3],
            "canonical_path": _vault_relative_path(resolved_vault, center[4]),
            "source_slug": center[5],
            "row_pack": truth_pack,
        },
        "neighbors": [
            {
                "object_id": row[0],
                "object_kind": row[1],
                "title": row[2],
                "canonical_path": _vault_relative_path(resolved_vault, row[3]),
                "source_slug": row[4],
                "row_pack": truth_pack,
            }
            for row in neighbor_rows
        ],
        "edges": [
            {
                "source_object_id": row[0],
                "target_object_id": row[1],
                "relation_type": row[2],
                "evidence_source_slug": row[3],
                "source_kind": row[4],
                "target_kind": row[5],
            }
            for row in edge_rows
        ],
    }


def list_atlas_memberships(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items = _list_surface_groups(
        vault_dir,
        pack_name=pack_name,
        note_type="moc",
        query=query,
        limit=limit,
        object_list_key="members",
    )
    return [
        {
            "slug": item["slug"],
            "title": item["title"],
            "path": item["path"],
            "members": item["members"],
        }
        for item in items
    ]


def list_timeline_events(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 100,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """List timeline events.

    ``from_date`` / ``to_date`` accept ``YYYY-MM-DD`` strings.  Both are
    inclusive.  Pass the same value for both to fetch a single day.
    """
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    candidate_limit = min(MAX_PAGE_SIZE, max(limit * max(1, len(pack_candidates)), limit))
    sql = f"""
        SELECT timeline_events.event_date, timeline_events.event_type, timeline_events.heading,
               timeline_events.payload_json, objects.pack, objects.object_id, objects.title, compiled_summaries.summary_text
        FROM timeline_events
        JOIN objects ON objects.object_id = timeline_events.slug
        LEFT JOIN compiled_summaries
          ON compiled_summaries.object_id = objects.object_id
         AND compiled_summaries.pack = objects.pack
        WHERE objects.pack IN ({pack_placeholders})
    """
    params: list[Any] = [*pack_candidates]
    if from_date:
        sql += "\n            AND timeline_events.event_date >= ?\n        "
        params.append(from_date)
    if to_date:
        sql += "\n            AND timeline_events.event_date <= ?\n        "
        params.append(to_date)
    if normalized_query:
        sql += """
            AND (
                lower(objects.object_id) LIKE ? ESCAPE '\\'
                OR lower(objects.title) LIKE ? ESCAPE '\\'
                OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
            )
        """
        params.extend([f"%{normalized_query}%"] * 3)
    sql += f"""
        ORDER BY timeline_events.event_date DESC,
          CASE objects.pack
            {"".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))}
            ELSE {len(pack_candidates)}
          END,
          objects.object_id
        LIMIT ?
    """
    params.extend([*pack_candidates, candidate_limit])
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for (
        event_date,
        event_type,
        heading,
        payload_json,
        row_pack,
        object_id,
        title,
        summary_text,
    ) in rows:
        key = (str(event_date or ""), str(event_type or ""), str(object_id))
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "event_date": str(event_date or ""),
                "event_type": str(event_type or ""),
                "heading": str(heading or ""),
                "payload_json": str(payload_json or ""),
                "row_pack": str(row_pack or ""),
                "object_id": str(object_id),
                "title": str(title or object_id),
                "summary_text": str(summary_text or ""),
            }
        )
        if len(items) >= limit:
            break
    return items


def list_stale_summaries(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    object_ids: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    pack_candidates = _materialized_truth_packs(
        vault_dir, pack_name=pack_name, table_name="objects"
    )
    pack_placeholders = ",".join("?" for _ in pack_candidates)
    sql = """
        SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
               COALESCE(rel.outgoing_count, 0) AS outgoing_count
        FROM objects
        LEFT JOIN compiled_summaries
          ON compiled_summaries.pack = objects.pack
         AND compiled_summaries.object_id = objects.object_id
        LEFT JOIN (
            SELECT pack, source_object_id, COUNT(*) AS outgoing_count
            FROM relations
            GROUP BY pack, source_object_id
        ) AS rel ON rel.pack = objects.pack AND rel.source_object_id = objects.object_id
    """
    params: list[Any] = [*pack_candidates]
    where_clauses: list[str] = [f"objects.pack IN ({pack_placeholders})"]
    if object_ids:
        normalized_object_ids = list(
            dict.fromkeys(object_id for object_id in object_ids if object_id)
        )
        if not normalized_object_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_object_ids)
        where_clauses.append(f"objects.object_id IN ({placeholders})")
        params.extend(normalized_object_ids)
    if normalized_query:
        where_clauses.append(
            """
            (
                lower(objects.object_id) LIKE ? ESCAPE '\\'
                OR lower(objects.title) LIKE ? ESCAPE '\\'
                OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
            )
            """.strip()
        )
        params.extend([f"%{normalized_query}%"] * 3)
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += (
        " ORDER BY CASE objects.pack "
        + "".join(f"WHEN ? THEN {index} " for index, _ in enumerate(pack_candidates))
        + f"ELSE {len(pack_candidates)} END, objects.object_id LIMIT ?"
    )
    params.extend(pack_candidates)
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        latest_event_rows = conn.execute(
            """
            SELECT slug, MAX(event_date)
            FROM timeline_events
            GROUP BY slug
            """
        ).fetchall()
    latest_event_map = {str(slug): str(event_date or "") for slug, event_date in latest_event_rows}

    items: list[dict[str, Any]] = []
    seen_object_ids: set[str] = set()
    for object_id, title, summary_text, outgoing_count in rows:
        if str(object_id) in seen_object_ids:
            continue
        seen_object_ids.add(str(object_id))
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        reason_codes: list[str] = ["no_outgoing_relations"]
        reason_texts: list[str] = ["No outgoing relations currently support this summary."]
        if not summary:
            reason_codes.append("summary_missing")
            reason_texts.append("Compiled summary is empty.")
        elif len(summary) < 40:
            reason_codes.append("summary_too_short")
            reason_texts.append("Compiled summary is too short to stand on its own.")
        if summary and summary.lower() == str(title).strip().lower():
            reason_codes.append("summary_repeats_title")
            reason_texts.append("Compiled summary repeats the title instead of adding substance.")
        items.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "object_path": f"/object?id={object_id}",
                "reason_codes": reason_codes,
                "reason_texts": reason_texts,
                "review_history": list_review_actions(
                    vault_dir, object_ids=[str(object_id)], limit=5
                ),
                "latest_event_date": latest_event_map.get(str(object_id), ""),
            }
        )
    return items
