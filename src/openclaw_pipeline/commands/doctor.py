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
            f"pinboard={vault['pinboard_count']} processing={vault['processing_count']} "
            f"processed={vault['processed_count']} "
            f"evergreen={vault['evergreen_count']} knowledge_db_exists={vault['knowledge_db_exists']}"
        )
    return 0
