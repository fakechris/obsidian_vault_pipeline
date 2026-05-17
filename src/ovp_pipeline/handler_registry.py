from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .event_emitter import emit as _emit_event
from .execution_contract_registry import (
    resolve_focused_action_execution_contract,
    resolve_stage_execution_contract,
)
from .packs.base import BaseDomainPack, StageHandlerSpec
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .pack_resolution import coerce_pack, iter_compatible_packs, load_entrypoint

# BL-103b: generic in/out count extraction from a stage result.
# DAG-boundary telemetry is deliberately generic (the operator chose
# the low-blast-radius wrap over per-handler instrumentation); these
# key-preference lists cover the common handler result shapes.  None
# when nothing matched → the zero-reason layer treats that as
# ``telemetry_missing`` rather than a false ``ran_no_input``.
_INPUT_KEYS = (
    "input_count",
    "files_processed",
    "scanned",
    "processed",
    "considered",
    "eligible",
    "candidates_considered",
)
_OUTPUT_KEYS = (
    "output_count",
    "candidates_added",
    "concepts_created",
    "concepts_promoted",
    "created",
    "written",
    "migrated",
    "promoted",
    "added",
)
_SKIP_STATUSES = {"skipped", "noop", "no_qualified_files", "skip"}


def _coerce_result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    for attr in ("to_dict", "_asdict"):
        fn = getattr(result, attr, None)
        if callable(fn):
            try:
                d = fn()
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
    return {}


def _first_int(d: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    scopes: list[dict[str, Any]] = [d]
    summary = d.get("summary")
    if isinstance(summary, dict):
        scopes.append(summary)
    for scope in scopes:
        for k in keys:
            v = scope.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return int(v)
    return None


def _stage_io_counts(result: Any) -> tuple[int | None, int | None]:
    d = _coerce_result_dict(result)
    return _first_int(d, _INPUT_KEYS), _first_int(d, _OUTPUT_KEYS)


def _result_is_skip(result: Any) -> bool:
    d = _coerce_result_dict(result)
    if d.get("skipped") is True or d.get("skip_reason"):
        return True
    return str(d.get("status") or "").strip().lower() in _SKIP_STATUSES


def _emit_stage_event(
    vault_dir: Any,
    *,
    event_type: str,
    stage: str,
    run_id: str,
    session_id: str,
    pack: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort stage telemetry.  Telemetry must NEVER break the
    DAG — every failure here is swallowed."""
    if not vault_dir:
        return
    try:
        _emit_event(
            vault_dir,
            "pipeline.jsonl",
            event_type,
            {"stage": stage, "run_id": run_id, **(extra or {})},
            session_id=session_id or None,
            pack=pack or None,
        )
    except Exception:
        # Runtime emit failure (disk full, perms) must never break
        # the DAG — telemetry is best-effort.  NOT an import
        # fallback (the import is top-level).
        pass


def resolve_stage_handler(
    *,
    pack_name: str | BaseDomainPack | None,
    stage: str,
    runtime_adapter: str,
) -> StageHandlerSpec:
    for pack in iter_compatible_packs(pack_name):
        for spec in pack.stage_handlers():
            if (
                spec.handler_kind == "profile_stage"
                and spec.stage == stage
                and spec.runtime_adapter == runtime_adapter
            ):
                return spec
    resolved = coerce_pack(pack_name)
    raise ValueError(
        f"Unknown stage handler '{stage}' for pack '{resolved.name}' "
        f"(runtime_adapter={runtime_adapter})"
    )


def resolve_focused_action_handler(
    *,
    pack_name: str | BaseDomainPack | None,
    action_kind: str,
) -> StageHandlerSpec:
    for pack in iter_compatible_packs(pack_name):
        for spec in pack.stage_handlers():
            if (
                spec.handler_kind == "focused_action"
                and spec.action_kind == action_kind
                and spec.runtime_adapter == "focused_action"
            ):
                return spec
    resolved = coerce_pack(pack_name)
    raise ValueError(f"Unknown focused action handler '{action_kind}' for pack '{resolved.name}'")


def execute_profile_stage_handler(
    pipeline: Any,
    stage: str,
    *,
    pack_name: str | BaseDomainPack | None = None,
    batch_size: int | None = None,
    dry_run: bool = False,
    pinboard_days: int | None = None,
    pinboard_start: str | None = None,
    pinboard_end: str | None = None,
    results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = resolve_stage_execution_contract(
        pack_name=pack_name or getattr(pipeline, "workflow_pack_name", None),
        stage=stage,
        runtime_adapter="pipeline_step",
    )
    spec = contract.handler_spec
    handler = load_entrypoint(spec.entrypoint)

    vault_dir = getattr(pipeline, "vault_dir", None)
    session_id = str(getattr(pipeline, "session_id", "") or "")
    pack = str(pack_name or getattr(pipeline, "workflow_pack_name", "") or "")
    run_id = uuid.uuid4().hex
    _emit_stage_event(
        vault_dir,
        event_type="stage_started",
        stage=stage,
        run_id=run_id,
        session_id=session_id,
        pack=pack,
        extra={"dry_run": dry_run},
    )
    try:
        result: dict[str, Any] = handler(
            pipeline=pipeline,
            batch_size=batch_size,
            dry_run=dry_run,
            pinboard_days=pinboard_days,
            pinboard_start=pinboard_start,
            pinboard_end=pinboard_end,
            results=results if results is not None else {},
            spec=spec,
        )
    except Exception as exc:
        _emit_stage_event(
            vault_dir,
            event_type="stage_failed",
            stage=stage,
            run_id=run_id,
            session_id=session_id,
            pack=pack,
            extra={"error": type(exc).__name__},
        )
        raise
    in_n, out_n = _stage_io_counts(result)
    if _result_is_skip(result):
        _emit_stage_event(
            vault_dir,
            event_type="stage_skipped",
            stage=stage,
            run_id=run_id,
            session_id=session_id,
            pack=pack,
            extra={"input_count": in_n},
        )
    else:
        _emit_stage_event(
            vault_dir,
            event_type="stage_completed",
            stage=stage,
            run_id=run_id,
            session_id=session_id,
            pack=pack,
            extra={"input_count": in_n, "output_count": out_n},
        )
    return result


def execute_autopilot_stage_handler(
    daemon: Any,
    stage: str,
    *,
    task: Any | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = resolve_stage_execution_contract(
        pack_name=getattr(daemon, "pack", None),
        stage=stage,
        runtime_adapter="autopilot_stage",
    )
    spec = contract.handler_spec
    handler = load_entrypoint(spec.entrypoint)

    vault_dir = getattr(daemon, "vault_dir", None)
    session_id = str(getattr(daemon, "session_id", "") or "")
    pack = str(getattr(daemon, "pack", "") or "")
    run_id = uuid.uuid4().hex
    _emit_stage_event(
        vault_dir,
        event_type="stage_started",
        stage=stage,
        run_id=run_id,
        session_id=session_id,
        pack=pack,
    )
    try:
        handler_result: dict[str, Any] = handler(
            daemon=daemon,
            task=task,
            result=result if result is not None else {},
            spec=spec,
        )
    except Exception as exc:
        _emit_stage_event(
            vault_dir,
            event_type="stage_failed",
            stage=stage,
            run_id=run_id,
            session_id=session_id,
            pack=pack,
            extra={"error": type(exc).__name__},
        )
        raise
    in_n, out_n = _stage_io_counts(handler_result)
    if _result_is_skip(handler_result):
        _emit_stage_event(
            vault_dir,
            event_type="stage_skipped",
            stage=stage,
            run_id=run_id,
            session_id=session_id,
            pack=pack,
            extra={"input_count": in_n},
        )
    else:
        _emit_stage_event(
            vault_dir,
            event_type="stage_completed",
            stage=stage,
            run_id=run_id,
            session_id=session_id,
            pack=pack,
            extra={"input_count": in_n, "output_count": out_n},
        )
    return handler_result


def execute_focused_action_handler(
    vault_dir: Path | str,
    action: dict[str, Any],
    *,
    pack_name: str | BaseDomainPack | None = None,
) -> tuple[StageHandlerSpec, dict[str, Any]]:
    contract = resolve_focused_action_execution_contract(
        pack_name=pack_name or str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME),
        action_kind=str(action.get("action_kind") or ""),
    )
    spec = contract.handler_spec
    handler = load_entrypoint(spec.entrypoint)
    result = handler(vault_dir, action)
    return spec, result
