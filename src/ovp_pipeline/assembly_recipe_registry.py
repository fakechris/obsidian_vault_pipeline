from __future__ import annotations

from .pack_resolution import coerce_pack, iter_compatible_packs
from .packs.base import AssemblyRecipeSpec, BaseDomainPack


def resolve_assembly_recipe_spec(
    *,
    pack_name: str | BaseDomainPack | None,
    recipe_name: str,
) -> AssemblyRecipeSpec:
    pack = coerce_pack(pack_name)
    for pack_candidate in iter_compatible_packs(pack):
        for spec in pack_candidate.assembly_recipes():
            if spec.name == recipe_name:
                return spec
    raise ValueError(f"Unknown assembly recipe '{recipe_name}' for pack '{pack.name}'")


def list_effective_assembly_recipes(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[AssemblyRecipeSpec]:
    seen_recipe_names: set[str] = set()
    recipes: list[AssemblyRecipeSpec] = []
    for pack in iter_compatible_packs(pack_name):
        for spec in pack.assembly_recipes():
            if spec.name in seen_recipe_names:
                continue
            seen_recipe_names.add(spec.name)
            recipes.append(spec)
    return recipes


def resolve_assembly_source_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    recipe: AssemblyRecipeSpec,
) -> dict[str, str]:
    pack = coerce_pack(pack_name)
    source_kind = recipe.source_contract_kind
    source_name = recipe.source_contract_name
    if source_kind == "wiki_view":
        for candidate in iter_compatible_packs(pack):
            try:
                view = candidate.wiki_view(source_name)
            except ValueError:
                continue
            return {
                "source_provider_pack": candidate.name,
                "source_provider_name": getattr(view, "name", source_name),
                "source_status": "declared" if candidate.name == pack.name else "inherited",
            }
    if source_kind == "observation_surface":
        for candidate in iter_compatible_packs(pack):
            for spec in candidate.observation_surfaces():
                if spec.surface_kind != source_name:
                    continue
                return {
                    "source_provider_pack": candidate.name,
                    "source_provider_name": spec.name,
                    "source_status": "declared" if candidate.name == pack.name else "inherited",
                }
    return {
        "source_provider_pack": "",
        "source_provider_name": "",
        "source_status": "missing",
    }


def describe_assembly_recipe_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    recipe_name: str,
) -> dict[str, str]:
    try:
        pack = coerce_pack(pack_name)
    except ValueError:
        return {
            "recipe_name": recipe_name,
            "requested_pack": str(pack_name or ""),
            "status": "missing",
            "provider_pack": "",
            "provider_name": "",
            "recipe_kind": "",
            "description": "",
            "source_contract_kind": "",
            "source_contract_name": "",
            "source_provider_pack": "",
            "source_provider_name": "",
            "source_status": "missing",
            "publish_target": "",
            "output_mode": "",
        }
    declared = next((spec for spec in pack.assembly_recipes() if spec.name == recipe_name), None)
    if declared is not None:
        source = resolve_assembly_source_contract(pack_name=pack, recipe=declared)
        return {
            "recipe_name": recipe_name,
            "requested_pack": pack.name,
            "status": "declared",
            "provider_pack": declared.pack,
            "provider_name": declared.name,
            "recipe_kind": declared.recipe_kind,
            "description": declared.description,
            "source_contract_kind": declared.source_contract_kind,
            "source_contract_name": declared.source_contract_name,
            **source,
            "publish_target": declared.output.publish_target,
            "output_mode": declared.output.output_mode,
        }
    effective = next(
        (spec for spec in list_effective_assembly_recipes(pack_name=pack) if spec.name == recipe_name),
        None,
    )
    if effective is not None:
        source = resolve_assembly_source_contract(pack_name=pack, recipe=effective)
        return {
            "recipe_name": recipe_name,
            "requested_pack": pack.name,
            "status": "inherited",
            "provider_pack": effective.pack,
            "provider_name": effective.name,
            "recipe_kind": effective.recipe_kind,
            "description": effective.description,
            "source_contract_kind": effective.source_contract_kind,
            "source_contract_name": effective.source_contract_name,
            **source,
            "publish_target": effective.output.publish_target,
            "output_mode": effective.output.output_mode,
        }
    return {
        "recipe_name": recipe_name,
        "requested_pack": pack.name,
        "status": "missing",
        "provider_pack": "",
        "provider_name": "",
        "recipe_kind": "",
        "description": "",
        "source_contract_kind": "",
        "source_contract_name": "",
        "source_provider_pack": "",
        "source_provider_name": "",
        "source_status": "missing",
        "publish_target": "",
        "output_mode": "",
    }
