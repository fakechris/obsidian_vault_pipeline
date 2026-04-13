from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..packs.loader import (
    DEFAULT_PACK_NAME,
    DEFAULT_WORKFLOW_PACK_NAME,
    PRIMARY_PACK_NAME,
    load_pack,
)
from ..runtime import VaultLayout, iter_markdown_files, resolve_vault_dir


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _docs_payload(repo_root: Path, *, pack_name: str) -> dict[str, object]:
    pack_slug = pack_name.replace("_", "-")
    docs_root = repo_root / "docs"
    research_root = docs_root / "research-tech"
    recipes_root = docs_root / "recipes" / pack_slug

    skillpack = research_root / "RESEARCH_TECH_SKILLPACK.md"
    verify = research_root / "RESEARCH_TECH_VERIFY.md"
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
        "raw_count": len(list(iter_markdown_files(layout.raw_dir))) if layout.raw_dir.exists() else 0,
        "clippings_count": len(list(iter_markdown_files(layout.clippings_dir))) if layout.clippings_dir.exists() else 0,
        "pinboard_count": len(list(iter_markdown_files(layout.pinboard_dir))) if layout.pinboard_dir.exists() else 0,
        "processing_count": len(list(iter_markdown_files(layout.processing_dir))) if layout.processing_dir.exists() else 0,
        "processed_count": len(list(iter_markdown_files(layout.processed_dir))) if layout.processed_dir.exists() else 0,
        "evergreen_count": len(list(iter_markdown_files(layout.evergreen_dir))) if layout.evergreen_dir.exists() else 0,
        "knowledge_db_exists": layout.knowledge_db.exists(),
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
            f"pinboard={vault['pinboard_count']} processed={vault['processed_count']} "
            f"evergreen={vault['evergreen_count']} knowledge_db_exists={vault['knowledge_db_exists']}"
        )
    return 0
