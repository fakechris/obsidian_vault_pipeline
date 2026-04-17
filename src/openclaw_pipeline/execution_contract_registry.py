from __future__ import annotations

from dataclasses import dataclass

from .pack_resolution import coerce_pack, iter_compatible_packs
from .packs.base import BaseDomainPack, ProcessorContractSpec, StageHandlerSpec
from .processor_contract_registry import resolve_processor_contract


def _processor_key(*, stage: str | None = None, action_kind: str | None = None) -> tuple[str, str]:
    return (str(stage or ""), str(action_kind or ""))


@dataclass(frozen=True)
class ExecutionContractSpec:
    handler_spec: StageHandlerSpec
    processor_contract: ProcessorContractSpec

    @property
    def stage(self) -> str | None:
        return self.handler_spec.stage or self.processor_contract.stage

    @property
    def action_kind(self) -> str | None:
        return self.handler_spec.action_kind or self.processor_contract.action_kind


def _resolve_stage_handler_spec(
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


def _resolve_focused_action_handler_spec(
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


def resolve_stage_execution_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    stage: str,
    runtime_adapter: str,
) -> ExecutionContractSpec:
    handler_spec = _resolve_stage_handler_spec(
        pack_name=pack_name,
        stage=stage,
        runtime_adapter=runtime_adapter,
    )
    processor_contract = resolve_processor_contract(
        pack_name=pack_name,
        stage=stage,
    )
    return ExecutionContractSpec(
        handler_spec=handler_spec,
        processor_contract=processor_contract,
    )


def resolve_focused_action_execution_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    action_kind: str,
) -> ExecutionContractSpec:
    handler_spec = _resolve_focused_action_handler_spec(
        pack_name=pack_name,
        action_kind=action_kind,
    )
    processor_contract = resolve_processor_contract(
        pack_name=pack_name,
        action_kind=action_kind,
    )
    return ExecutionContractSpec(
        handler_spec=handler_spec,
        processor_contract=processor_contract,
    )


def list_effective_execution_contracts(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[ExecutionContractSpec]:
    compatible_packs = iter_compatible_packs(pack_name)

    processor_by_key: dict[tuple[str, str], ProcessorContractSpec] = {}
    for pack in compatible_packs:
        for spec in pack.processor_contracts():
            key = _processor_key(stage=spec.stage, action_kind=spec.action_kind)
            processor_by_key.setdefault(key, spec)

    bundles: list[ExecutionContractSpec] = []
    seen_handler_keys: set[tuple[str, str, str, str]] = set()
    for pack in compatible_packs:
        for handler_spec in pack.stage_handlers():
            processor_key = _processor_key(
                stage=handler_spec.stage,
                action_kind=handler_spec.action_kind,
            )
            processor_contract = processor_by_key.get(processor_key)
            if processor_contract is None:
                continue
            handler_key = (
                str(handler_spec.runtime_adapter or ""),
                str(handler_spec.stage or ""),
                str(handler_spec.action_kind or ""),
                str(handler_spec.entrypoint or ""),
            )
            if handler_key in seen_handler_keys:
                continue
            seen_handler_keys.add(handler_key)
            bundles.append(
                ExecutionContractSpec(
                    handler_spec=handler_spec,
                    processor_contract=processor_contract,
                )
            )
    return bundles


def compute_declared_contract_integrity(
    *,
    pack_name: str | BaseDomainPack | None,
) -> dict[str, list[dict[str, object]]]:
    pack = coerce_pack(pack_name)

    handler_adapters_by_key: dict[tuple[str, str], set[str]] = {}
    for handler_spec in pack.stage_handlers():
        key = _processor_key(stage=handler_spec.stage, action_kind=handler_spec.action_kind)
        handler_adapters_by_key.setdefault(key, set()).add(str(handler_spec.runtime_adapter or ""))

    processor_by_key: dict[tuple[str, str], ProcessorContractSpec] = {}
    for processor_spec in pack.processor_contracts():
        key = _processor_key(stage=processor_spec.stage, action_kind=processor_spec.action_kind)
        processor_by_key[key] = processor_spec

    missing_processor_contracts: list[dict[str, object]] = []
    for stage_key, action_key in sorted(handler_adapters_by_key):
        key = (stage_key, action_key)
        if key in processor_by_key:
            continue
        missing_processor_contracts.append(
            {
                "stage": stage_key or None,
                "action_kind": action_key or None,
                "runtime_adapters": sorted(handler_adapters_by_key[key]),
            }
        )

    orphan_processor_contracts: list[dict[str, object]] = []
    for key, processor_spec in sorted(
        processor_by_key.items(),
        key=lambda item: (item[0][0], item[0][1], item[1].name),
    ):
        if key in handler_adapters_by_key:
            continue
        orphan_processor_contracts.append(
            {
                "stage": key[0] or None,
                "action_kind": key[1] or None,
                "mode": processor_spec.mode,
                "entrypoint": processor_spec.entrypoint,
            }
        )

    return {
        "missing_processor_contracts": missing_processor_contracts,
        "orphan_processor_contracts": orphan_processor_contracts,
    }
