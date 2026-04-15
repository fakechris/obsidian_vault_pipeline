from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from .packs.base import BaseDomainPack, StageHandlerSpec
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME, load_pack
from .pack_resolution import coerce_pack, iter_compatible_packs, load_entrypoint


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
    spec = resolve_stage_handler(
        pack_name=pack_name or getattr(pipeline, "workflow_pack_name", None),
        stage=stage,
        runtime_adapter="pipeline_step",
    )
    handler = load_entrypoint(spec.entrypoint)
    return handler(
        pipeline=pipeline,
        batch_size=batch_size,
        dry_run=dry_run,
        pinboard_days=pinboard_days,
        pinboard_start=pinboard_start,
        pinboard_end=pinboard_end,
        results=results if results is not None else {},
        spec=spec,
    )


def execute_autopilot_stage_handler(
    daemon: Any,
    stage: str,
    *,
    task: Any | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = resolve_stage_handler(
        pack_name=getattr(daemon, "pack", None),
        stage=stage,
        runtime_adapter="autopilot_stage",
    )
    handler = load_entrypoint(spec.entrypoint)
    return handler(
        daemon=daemon,
        task=task,
        result=result if result is not None else {},
        spec=spec,
    )


def execute_focused_action_handler(
    vault_dir: Path | str,
    action: dict[str, Any],
    *,
    pack_name: str | BaseDomainPack | None = None,
) -> tuple[StageHandlerSpec, dict[str, Any]]:
    spec = resolve_focused_action_handler(
        pack_name=pack_name or str(action.get("pack") or DEFAULT_WORKFLOW_PACK_NAME),
        action_kind=str(action.get("action_kind") or ""),
    )
    handler = load_entrypoint(spec.entrypoint)
    result = handler(vault_dir, action)
    return spec, result
