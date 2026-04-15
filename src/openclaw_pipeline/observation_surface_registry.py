from __future__ import annotations

from pathlib import Path
from typing import Any

from .pack_resolution import coerce_pack, iter_compatible_packs, load_entrypoint
from .packs.base import BaseDomainPack, ObservationSurfaceSpec


def resolve_observation_surface_builder(
    *,
    pack_name: str | BaseDomainPack | None,
    surface_kind: str,
) -> ObservationSurfaceSpec:
    for pack in iter_compatible_packs(pack_name):
        for spec in pack.observation_surfaces():
            if spec.surface_kind == surface_kind:
                return spec
    resolved = coerce_pack(pack_name)
    raise ValueError(
        f"Unknown observation surface builder '{surface_kind}' for pack '{resolved.name}'"
    )


def execute_observation_surface_builder(
    *,
    surface_kind: str,
    vault_dir: Path,
    pack_name: str | BaseDomainPack | None = None,
    **kwargs: Any,
) -> tuple[ObservationSurfaceSpec, Any]:
    spec = resolve_observation_surface_builder(pack_name=pack_name, surface_kind=surface_kind)
    builder = load_entrypoint(spec.entrypoint)
    result = builder(vault_dir=vault_dir, pack_name=pack_name, spec=spec, **kwargs)
    return spec, result
