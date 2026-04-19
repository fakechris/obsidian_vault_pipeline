from __future__ import annotations

from pathlib import Path

from .pack_resolution import coerce_pack, iter_compatible_packs, load_entrypoint
from .packs.base import BaseDomainPack, TruthProjectionSpec
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .truth_store import TruthStoreProjection


def resolve_truth_projection_builder(
    *, pack_name: str | BaseDomainPack | None
) -> TruthProjectionSpec:
    for pack in iter_compatible_packs(pack_name):
        spec = pack.truth_projection()
        if spec is not None:
            return spec
    resolved = coerce_pack(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    raise ValueError(f"Unknown truth projection builder for pack '{resolved.name}'")


def execute_truth_projection_builder(
    *,
    vault_dir: Path,
    page_rows: list[tuple[str, str, str, str, str, str, str]],
    link_rows: list[tuple[str, str, str, str, int]],
    pack_name: str | BaseDomainPack | None = None,
) -> tuple[TruthProjectionSpec, TruthStoreProjection]:
    spec = resolve_truth_projection_builder(pack_name=pack_name)
    builder = load_entrypoint(spec.entrypoint)
    projection = builder(
        vault_dir=vault_dir,
        page_rows=page_rows,
        link_rows=link_rows,
        pack_name=str(getattr(coerce_pack(pack_name), "name", "")),
        spec=spec,
    )
    return spec, projection
