from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ..assembly_recipe_registry import resolve_assembly_recipe_spec, resolve_assembly_source_contract
from ..observation_surface_registry import execute_observation_surface_builder
from ..packs.loader import PRIMARY_PACK_NAME, load_pack
from ..runtime import resolve_vault_dir
from ..wiki_views.runtime import build_view


TARGET_TO_RECIPE = {
    "orientation-brief": "orientation_brief",
    "object-page": "object_brief",
    "topic-overview": "topic_overview",
    "event-dossier": "event_dossier",
    "contradictions": "contradiction_view",
}


def _resolve_export_recipe(pack, target: str) -> tuple[object, object]:
    recipe_name = TARGET_TO_RECIPE[target]
    recipe = resolve_assembly_recipe_spec(pack_name=pack, recipe_name=recipe_name)
    return load_pack(recipe.pack), recipe


def _resolve_export_view(pack, recipe_provider_pack, recipe) -> tuple[object, object]:
    if getattr(recipe, "source_contract_kind", "") != "wiki_view":
        raise ValueError(
            f"assembly recipe '{recipe.name}' for pack '{recipe_provider_pack.name}' "
            f"is not exportable via wiki views"
        )
    source = resolve_assembly_source_contract(pack_name=pack, recipe=recipe)
    provider_pack_name = str(source.get("source_provider_pack") or "")
    view_name = str(source.get("source_provider_name") or getattr(recipe, "source_contract_name", ""))
    if not provider_pack_name:
        raise ValueError(
            f"assembly recipe '{recipe.name}' for pack '{recipe_provider_pack.name}' "
            f"has no resolved wiki-view provider"
        )
    provider_pack = load_pack(provider_pack_name)
    return provider_pack, provider_pack.wiki_view(view_name)


def _write_json_export(output_path: Path, payload: object) -> None:
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export pack-backed compiled artifacts to an explicit output path."
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--pack", default=PRIMARY_PACK_NAME, help=f"Pack name (default: {PRIMARY_PACK_NAME})")
    parser.add_argument("--target", required=True, choices=sorted(TARGET_TO_RECIPE), help="Export target")
    parser.add_argument("--object-id", help="Required for object-page exports")
    parser.add_argument("--output-path", type=Path, required=True, help="Where to write the exported artifact")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    try:
        recipe_provider_pack, recipe = _resolve_export_recipe(pack, args.target)
    except Exception as exc:
        parser.error(f"failed to resolve export recipe for target '{args.target}' and pack '{pack.name}': {exc}")
    if args.target == "object-page" and not args.object_id:
        parser.error("the --object-id argument is required for object-page exports")

    output_path = args.output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source = resolve_assembly_source_contract(pack_name=pack, recipe=recipe)
    if getattr(recipe, "source_contract_kind", "") == "wiki_view":
        try:
            view_provider_pack, view = _resolve_export_view(pack, recipe_provider_pack, recipe)
        except Exception as exc:
            parser.error(
                f"failed to resolve export view '{getattr(recipe, 'source_contract_name', None)}' "
                f"for target '{args.target}' and pack '{pack.name}': {exc}"
            )
        try:
            source_path = build_view(vault_dir, view, object_id=args.object_id)
        except Exception as exc:
            parser.error(
                f"failed to build export target '{args.target}' for view '{getattr(view, 'name', None)}' "
                f"and object_id={args.object_id!r}: {exc}"
            )
        shutil.copyfile(source_path, output_path)
        print(
            json.dumps(
                {
                    "target": args.target,
                    "pack": pack.name,
                    "recipe_name": getattr(recipe, "name", ""),
                    "recipe_provider_pack": getattr(recipe_provider_pack, "name", ""),
                    "view_name": getattr(view, "name", ""),
                    "view_provider_pack": getattr(view_provider_pack, "name", ""),
                    "source_path": str(source_path),
                    "output_path": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if getattr(recipe, "source_contract_kind", "") == "observation_surface":
        try:
            spec, payload = execute_observation_surface_builder(
                surface_kind=str(getattr(recipe, "source_contract_name", "")),
                vault_dir=vault_dir,
                pack_name=pack.name,
            )
        except Exception as exc:
            parser.error(
                f"failed to build export target '{args.target}' for observation surface "
                f"'{getattr(recipe, 'source_contract_name', None)}': {exc}"
            )
        if getattr(recipe, "name", "") == "orientation_brief":
            from ..ui.view_models import build_briefing_payload

            payload = build_briefing_payload(vault_dir, pack_name=pack.name)
        _write_json_export(output_path, payload)
        print(
            json.dumps(
                {
                    "target": args.target,
                    "pack": pack.name,
                    "recipe_name": getattr(recipe, "name", ""),
                    "recipe_provider_pack": getattr(recipe_provider_pack, "name", ""),
                    "source_name": getattr(recipe, "source_contract_name", ""),
                    "source_provider_pack": str(source.get("source_provider_pack") or getattr(spec, "pack", "")),
                    "source_provider_name": str(source.get("source_provider_name") or getattr(spec, "name", "")),
                    "output_path": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    parser.error(
        f"assembly recipe '{getattr(recipe, 'name', '')}' uses unsupported source contract kind "
        f"'{getattr(recipe, 'source_contract_kind', '')}'"
    )
    return 0
