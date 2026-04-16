from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..pack_resolution import iter_compatible_packs
from ..packs.loader import (
    DEFAULT_PACK_NAME,
    DEFAULT_WORKFLOW_PACK_NAME,
    PRIMARY_PACK_NAME,
    load_pack,
)
from ..runtime import VaultLayout, iter_markdown_files, resolve_vault_dir
from ..truth_projection_registry import resolve_truth_projection_builder


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in [current.parent, *current.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current.parents[3]


def _doc_stem(pack_name: str, suffix: str) -> str:
    normalized = pack_name.replace("-", "_").upper()
    return f"{normalized}_{suffix}"


def _count_markdown(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for _ in iter_markdown_files(directory))


def _docs_payload(repo_root: Path, *, pack_name: str) -> dict[str, object]:
    pack_slug = pack_name.replace("_", "-")
    docs_root = repo_root / "docs"
    pack_docs_root = docs_root / pack_slug
    recipes_root = docs_root / "recipes" / pack_slug

    skillpack = pack_docs_root / f"{_doc_stem(pack_name, 'SKILLPACK')}.md"
    verify = pack_docs_root / f"{_doc_stem(pack_name, 'VERIFY')}.md"
    recipe_files = sorted(recipes_root.glob("*.md")) if recipes_root.exists() else []
    return {
        "skillpack": {"path": str(skillpack), "exists": skillpack.exists()},
        "verify": {"path": str(verify), "exists": verify.exists()},
        "recipes": {
            "path": str(recipes_root),
            "exists": recipes_root.exists(),
            "count": len(recipe_files),
            "files": [item.name for item in recipe_files],
        },
    }


def _vault_payload(vault_dir: Path | None) -> dict[str, object] | None:
    if vault_dir is None:
        return None
    layout = VaultLayout.from_vault(vault_dir)
    return {
        "vault_dir": str(layout.vault_dir),
        "raw_count": _count_markdown(layout.raw_dir),
        "clippings_count": _count_markdown(layout.clippings_dir),
        "pinboard_count": _count_markdown(layout.pinboard_dir),
        "processing_count": _count_markdown(layout.processing_dir),
        "processed_count": _count_markdown(layout.processed_dir),
        "evergreen_count": _count_markdown(layout.evergreen_dir),
        "knowledge_db_exists": layout.knowledge_db.exists(),
    }


def _stage_handler_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "handler_kind": getattr(spec, "handler_kind", ""),
        "runtime_adapter": getattr(spec, "runtime_adapter", ""),
        "stage": getattr(spec, "stage", None),
        "action_kind": getattr(spec, "action_kind", None),
        "target_mode": getattr(spec, "target_mode", ""),
        "supports_autopilot": bool(getattr(spec, "supports_autopilot", False)),
        "safe_to_run": bool(getattr(spec, "safe_to_run", False)),
        "requires_truth_refresh": bool(getattr(spec, "requires_truth_refresh", False)),
        "requires_signal_resync": bool(getattr(spec, "requires_signal_resync", False)),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _truth_projection_payload(spec: object | None) -> dict[str, object] | None:
    if spec is None:
        return None
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _observation_surface_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "surface_kind": getattr(spec, "surface_kind", ""),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _contracts_payload(pack_name: str) -> dict[str, object]:
    pack = load_pack(pack_name)
    compatible_packs = iter_compatible_packs(pack)
    declared_stage_handlers = [_stage_handler_payload(spec) for spec in pack.stage_handlers()]
    declared_truth_projection = _truth_projection_payload(pack.truth_projection())
    declared_surfaces = [_observation_surface_payload(spec) for spec in pack.observation_surfaces()]

    effective_stage_handlers: list[dict[str, object]] = []
    seen_stage_keys: set[tuple[str, str, str]] = set()
    for compatible_pack in compatible_packs:
        for spec in compatible_pack.stage_handlers():
            key = (
                str(getattr(spec, "handler_kind", "")),
                str(getattr(spec, "runtime_adapter", "")),
                str(getattr(spec, "stage", "") or getattr(spec, "action_kind", "")),
            )
            if key in seen_stage_keys:
                continue
            seen_stage_keys.add(key)
            effective_stage_handlers.append(_stage_handler_payload(spec))

    effective_surfaces: list[dict[str, object]] = []
    seen_surface_kinds: set[str] = set()
    for compatible_pack in compatible_packs:
        for spec in compatible_pack.observation_surfaces():
            surface_kind = str(getattr(spec, "surface_kind", ""))
            if surface_kind in seen_surface_kinds:
                continue
            seen_surface_kinds.add(surface_kind)
            effective_surfaces.append(_observation_surface_payload(spec))

    return {
        "declared": {
            "stage_handlers": declared_stage_handlers,
            "truth_projection": declared_truth_projection,
            "observation_surfaces": declared_surfaces,
        },
        "effective": {
            "stage_handlers": effective_stage_handlers,
            "truth_projection": _truth_projection_payload(
                resolve_truth_projection_builder(pack_name=pack_name)
            ),
            "observation_surfaces": effective_surfaces,
        },
        "contract_notes": {
            "compatibility_behavior": (
                "Compatibility packs inherit stage handlers, truth projection, and observation "
                "surfaces from compatibility_base only when they do not declare their own contract."
            ),
            "media_pack_guidance": (
                "External packs should implement stage handlers, a truth projection builder, and "
                "observation surfaces inside the pack rather than patching core runtime modules."
            ),
        },
    }


def _payload(pack_name: str, vault_dir: Path | None) -> dict[str, object]:
    repo_root = _repo_root()
    pack = load_pack(pack_name)
    return {
        "defaults": {
            "workflow_pack": DEFAULT_WORKFLOW_PACK_NAME,
            "compatibility_pack": DEFAULT_PACK_NAME,
            "primary_pack": PRIMARY_PACK_NAME,
        },
        "pack": {
            "name": pack.name,
            "role": getattr(pack, "role", "domain"),
            "compatibility_base": getattr(pack, "compatibility_base", None),
            "workflow_profiles": [profile.name for profile in pack.workflow_profiles()],
            "extraction_profiles": [profile.name for profile in pack.extraction_profiles()],
            "operation_profiles": [profile.name for profile in pack.operation_profiles()],
            "wiki_views": [view.name for view in pack.wiki_views()],
        },
        "storage": {
            "selected_engine": "sqlite",
            "pglite_migration": "defer",
            "reason": (
                "knowledge.db is currently a Python-native derived/truth-aware store; "
                "PGlite should only be revisited if browser/JS-native or remote-Postgres "
                "parity becomes a hard requirement."
            ),
        },
        "docs": _docs_payload(repo_root, pack_name=pack_name),
        "vault": _vault_payload(vault_dir),
        "contracts": _contracts_payload(pack_name),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect pack/runtime operational health and explain why SQLite remains the "
            "selected engine while PGlite migration is deferred."
        )
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Optional vault directory for health checks")
    parser.add_argument(
        "--pack",
        default=PRIMARY_PACK_NAME,
        help=(
            f"Pack name to inspect (primary pack: {PRIMARY_PACK_NAME}; "
            f"compatibility pack: {DEFAULT_PACK_NAME})"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    args = parser.parse_args(argv)

    payload = _payload(args.pack, resolve_vault_dir(args.vault_dir) if args.vault_dir else None)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(f"Pack: {payload['pack']['name']} [{payload['pack']['role']}]")
    print(f"Defaults: workflow={DEFAULT_WORKFLOW_PACK_NAME} compatibility={DEFAULT_PACK_NAME}")
    print("Storage: sqlite (PGlite migration deferred)")
    print(f"Skillpack doc: {payload['docs']['skillpack']['path']}")
    print(f"Verify doc: {payload['docs']['verify']['path']}")
    if payload["vault"]:
        vault = payload["vault"]
        print(
            "Vault: "
            f"raw={vault['raw_count']} clippings={vault['clippings_count']} "
            f"pinboard={vault['pinboard_count']} processing={vault['processing_count']} "
            f"processed={vault['processed_count']} "
            f"evergreen={vault['evergreen_count']} knowledge_db_exists={vault['knowledge_db_exists']}"
        )
    return 0
