from __future__ import annotations

from importlib import import_module
from typing import Any, Callable

from .packs.base import BaseDomainPack
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME, load_pack


def coerce_pack(pack_name: str | BaseDomainPack | None) -> BaseDomainPack:
    if isinstance(pack_name, BaseDomainPack):
        return pack_name
    return load_pack(pack_name or DEFAULT_WORKFLOW_PACK_NAME)


def iter_compatible_packs(pack_name: str | BaseDomainPack | None) -> list[BaseDomainPack]:
    pack = coerce_pack(pack_name)
    packs: list[BaseDomainPack] = [pack]
    seen = {pack.name}
    current = pack
    while current.role == "compatibility" and current.compatibility_base:
        current = load_pack(current.compatibility_base)
        if current.name in seen:
            break
        seen.add(current.name)
        packs.append(current)
    return packs


def load_entrypoint(entrypoint: str) -> Callable[..., Any]:
    module_name, sep, attr_name = entrypoint.partition(":")
    if not sep or not module_name or not attr_name:
        raise ValueError(f"Invalid handler entrypoint: {entrypoint!r}")
    module = import_module(module_name)
    target = getattr(module, attr_name, None)
    if target is None or not callable(target):
        raise ValueError(f"Handler entrypoint is not callable: {entrypoint!r}")
    return target
