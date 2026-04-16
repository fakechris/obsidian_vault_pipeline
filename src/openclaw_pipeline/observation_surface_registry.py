from __future__ import annotations

from pathlib import Path
from typing import Any

from .pack_resolution import coerce_pack, iter_compatible_packs, load_entrypoint
from .packs.base import BaseDomainPack, ObservationSurfaceSpec

UI_SHELL_SURFACE_KINDS = ("signals", "briefing", "production_chains")


def resolve_observation_surface_builder(
    *,
    pack_name: str | BaseDomainPack | None,
    surface_kind: str,
) -> ObservationSurfaceSpec:
    compatible_packs = iter_compatible_packs(pack_name)
    resolved = compatible_packs[0]
    for pack in compatible_packs:
        for spec in pack.observation_surfaces():
            if spec.surface_kind == surface_kind:
                return spec
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


def list_effective_observation_surfaces(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[ObservationSurfaceSpec]:
    seen_surface_kinds: set[str] = set()
    surfaces: list[ObservationSurfaceSpec] = []
    for pack in iter_compatible_packs(pack_name):
        for spec in pack.observation_surfaces():
            if spec.surface_kind in seen_surface_kinds:
                continue
            seen_surface_kinds.add(spec.surface_kind)
            surfaces.append(spec)
    return surfaces


def compute_declared_observation_surface_integrity(
    *,
    pack_name: str | BaseDomainPack | None,
) -> dict[str, object]:
    pack = coerce_pack(pack_name)
    declared_surface_map: dict[str, list[ObservationSurfaceSpec]] = {}
    for spec in pack.observation_surfaces():
        declared_surface_map.setdefault(spec.surface_kind, []).append(spec)

    duplicate_declared_surface_kinds = [
        {
            "surface_kind": surface_kind,
            "declared_names": [item.name for item in specs],
        }
        for surface_kind, specs in sorted(declared_surface_map.items())
        if len(specs) > 1
    ]

    effective_surfaces = {
        spec.surface_kind: spec for spec in list_effective_observation_surfaces(pack_name=pack_name)
    }
    shell_surface_support = []
    missing_shell_surface_kinds: list[str] = []
    for surface_kind in UI_SHELL_SURFACE_KINDS:
        declared = declared_surface_map.get(surface_kind, [])
        effective = effective_surfaces.get(surface_kind)
        if declared:
            shell_surface_support.append(
                {
                    "surface_kind": surface_kind,
                    "status": "declared",
                    "provider_pack": declared[0].pack,
                    "provider_name": declared[0].name,
                }
            )
            continue
        if effective is not None:
            shell_surface_support.append(
                {
                    "surface_kind": surface_kind,
                    "status": "inherited",
                    "provider_pack": effective.pack,
                    "provider_name": effective.name,
                }
            )
            continue
        missing_shell_surface_kinds.append(surface_kind)
        shell_surface_support.append(
            {
                "surface_kind": surface_kind,
                "status": "missing",
                "provider_pack": "",
                "provider_name": "",
            }
        )

    return {
        "duplicate_declared_surface_kinds": duplicate_declared_surface_kinds,
        "missing_shell_surface_kinds": missing_shell_surface_kinds,
        "shell_surface_support": shell_surface_support,
    }
