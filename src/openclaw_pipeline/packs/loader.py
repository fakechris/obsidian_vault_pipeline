from __future__ import annotations

import os
from pathlib import Path

from .base import BaseDomainPack, WorkflowProfile


DEFAULT_PACK_NAME = "default-knowledge"


def load_default_pack() -> BaseDomainPack:
    from .default_knowledge import get_pack

    pack = get_pack()
    if not isinstance(pack, BaseDomainPack):
        raise TypeError("default pack entrypoint did not return a BaseDomainPack")
    return pack


def load_pack(name: str) -> BaseDomainPack:
    if name == DEFAULT_PACK_NAME:
        return load_default_pack()
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
