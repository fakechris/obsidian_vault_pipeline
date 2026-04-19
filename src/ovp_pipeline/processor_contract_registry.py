from __future__ import annotations

from .pack_resolution import coerce_pack, iter_compatible_packs
from .packs.base import BaseDomainPack, ProcessorContractSpec


def resolve_processor_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    stage: str | None = None,
    action_kind: str | None = None,
) -> ProcessorContractSpec:
    if bool(stage) == bool(action_kind):
        raise ValueError("Resolve exactly one processor contract key: stage or action_kind")

    compatible_packs = iter_compatible_packs(pack_name)
    resolved = compatible_packs[0]
    for pack in compatible_packs:
        for spec in pack.processor_contracts():
            if stage is not None and spec.stage == stage:
                return spec
            if action_kind is not None and spec.action_kind == action_kind:
                return spec
    if stage is not None:
        raise ValueError(f"Unknown processor contract for stage '{stage}' in pack '{resolved.name}'")
    raise ValueError(f"Unknown processor contract for action '{action_kind}' in pack '{resolved.name}'")


def list_effective_processor_contracts(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[ProcessorContractSpec]:
    compatible_packs = iter_compatible_packs(pack_name)
    seen_keys: set[tuple[str, str]] = set()
    items: list[ProcessorContractSpec] = []
    for pack in compatible_packs:
        for spec in pack.processor_contracts():
            key = (str(spec.stage or ""), str(spec.action_kind or ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append(spec)
    return items
