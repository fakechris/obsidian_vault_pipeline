from __future__ import annotations

from .pack_resolution import coerce_pack, iter_compatible_packs
from .packs.base import ArtifactSpec, BaseDomainPack


def resolve_artifact_spec(
    *,
    pack_name: str | BaseDomainPack | None,
    artifact_name: str,
) -> ArtifactSpec:
    pack = coerce_pack(pack_name)
    declared = next((spec for spec in pack.artifact_specs() if spec.name == artifact_name), None)
    if declared is not None:
        return declared
    effective = next(
        (spec for spec in list_effective_artifact_specs(pack_name=pack) if spec.name == artifact_name),
        None,
    )
    if effective is not None:
        return effective
    raise ValueError(f"Unknown artifact spec '{artifact_name}' for pack '{pack.name}'")


def list_effective_artifact_specs(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[ArtifactSpec]:
    seen_names: set[str] = set()
    specs: list[ArtifactSpec] = []
    for candidate in iter_compatible_packs(pack_name):
        for spec in candidate.artifact_specs():
            if spec.name in seen_names:
                continue
            seen_names.add(spec.name)
            specs.append(spec)
    return specs
