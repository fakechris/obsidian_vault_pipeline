from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from ..derived.paths import review_queue_path
from ..runtime import VaultLayout, iter_markdown_files, read_markdown_frontmatter, resolve_vault_dir
from .specs import OperationProfileSpec


def _frontmatter_audit_items(vault_dir: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for md_file in iter_markdown_files(vault_dir):
        frontmatter = read_markdown_frontmatter(md_file)
        if frontmatter.get("title"):
            continue
        items.append(
            {
                "queue_name": "frontmatter",
                "issue_type": "missing-title",
                "file": str(md_file.relative_to(vault_dir)),
                "message": "Missing title field in frontmatter",
                "review_required": True,
            }
        )
    return items


def _noop_items(_: Path) -> list[dict[str, object]]:
    return []


_OPERATION_BUILDERS: dict[str, Callable[[Path], list[dict[str, object]]]] = {
    "vault/frontmatter_audit": _frontmatter_audit_items,
    "vault/review_queue": _noop_items,
    "vault/bridge_recommendations": _noop_items,
}


def run_operation_profile(vault_dir: Path, profile: OperationProfileSpec) -> list[Path]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    builder = _OPERATION_BUILDERS.get(profile.name, _noop_items)
    items = builder(resolved_vault)

    written: list[Path] = []
    for item in items:
        artifact_path = review_queue_path(
            layout,
            queue_name=str(item["queue_name"]),
            subject=Path(str(item["file"])).stem,
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(artifact_path)
    return written
