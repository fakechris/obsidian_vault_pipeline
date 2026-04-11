from __future__ import annotations

import os
from pathlib import Path

from .base import BaseDomainPack, WorkflowProfile


DEFAULT_PACK_NAME = "default-knowledge"
PRIMARY_PACK_NAME = "research-tech"
BUILTIN_PACK_LOADERS = {
    "default-knowledge": ("openclaw_pipeline.packs.default_knowledge", "get_pack"),
    "research-tech": ("openclaw_pipeline.packs.research_tech", "get_pack"),
}


def load_default_pack() -> BaseDomainPack:
    return load_builtin_pack(DEFAULT_PACK_NAME)


def load_primary_pack() -> BaseDomainPack:
    return load_builtin_pack(PRIMARY_PACK_NAME)


def list_builtin_packs() -> list[BaseDomainPack]:
    return [load_builtin_pack(name) for name in BUILTIN_PACK_LOADERS]


def load_builtin_pack(name: str) -> BaseDomainPack:
    module_name, factory_name = BUILTIN_PACK_LOADERS[name]
    module = __import__(module_name, fromlist=[factory_name])
    pack = getattr(module, factory_name)()
    if not isinstance(pack, BaseDomainPack):
        raise TypeError(f"builtin pack '{name}' did not return a BaseDomainPack")
    return pack


def load_pack(name: str) -> BaseDomainPack:
    if name in BUILTIN_PACK_LOADERS:
        return load_builtin_pack(name)
    from ..plugins import discover_entrypoint_packs, discover_plugin_manifests, load_manifest_pack

    entrypoint_packs = discover_entrypoint_packs()
    if name in entrypoint_packs:
        return entrypoint_packs[name]

    manifest_env = os.environ.get("OPENCLAW_PACK_MANIFESTS", "")
    manifest_paths = [Path(item) for item in manifest_env.split(":") if item]
    if manifest_paths:
        manifests = discover_plugin_manifests(manifest_paths)
        if name in manifests:
            return load_manifest_pack(manifests[name])
    raise ValueError(f"Unknown pack: {name}")


def resolve_workflow_profile(
    *,
    pack_name: str | None = None,
    profile_name: str | None = None,
    default_profile: str,
    require_autopilot: bool = False,
) -> tuple[BaseDomainPack, WorkflowProfile]:
    """Resolve a pack/profile pair with stable defaults."""

    resolved_pack_name = pack_name or DEFAULT_PACK_NAME
    resolved_profile_name = profile_name or default_profile

    pack = load_pack(resolved_pack_name)
    profile = pack.profile(resolved_profile_name)

    if require_autopilot and not profile.supports_autopilot:
        raise ValueError(
            f"Workflow profile '{resolved_profile_name}' for pack '{resolved_pack_name}' "
            "does not support autopilot"
        )

    return pack, profile
