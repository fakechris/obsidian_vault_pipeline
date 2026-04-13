from __future__ import annotations

from pathlib import Path

from .packs.loader import load_pack
from .runtime import VaultLayout, resolve_vault_dir

TEXT_SUFFIXES = {".json", ".jsonl", ".md"}


def default_target_pack(from_pack: str) -> str:
    pack = load_pack(from_pack)
    compatibility_base = getattr(pack, "compatibility_base", None)
    if compatibility_base:
        return str(compatibility_base)
    return from_pack


def iter_provenance_files(layout: VaultLayout) -> list[Path]:
    if not layout.logs_dir.exists():
        return []
    return sorted(
        path
        for path in layout.logs_dir.rglob("*")
        if path.is_file() and path.suffix in TEXT_SUFFIXES
    )


def migrate_pack_provenance(
    vault_dir: Path | str | None = None,
    *,
    from_pack: str,
    to_pack: str | None = None,
    write: bool = False,
) -> dict[str, object]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    target_pack = to_pack or default_target_pack(from_pack)

    files_scanned = 0
    files_changed = 0
    replacements = 0
    changed_paths: list[str] = []

    if from_pack == target_pack:
        return {
            "vault_dir": str(resolved_vault),
            "logs_dir": str(layout.logs_dir),
            "from_pack": from_pack,
            "to_pack": target_pack,
            "files_scanned": 0,
            "files_changed": 0,
            "replacements": 0,
            "changed_paths": [],
            "write": write,
        }

    for path in iter_provenance_files(layout):
        files_scanned += 1
        original = path.read_text(encoding="utf-8")
        count = original.count(from_pack)
        if count <= 0:
            continue
        updated = original.replace(from_pack, target_pack)
        files_changed += 1
        replacements += count
        changed_paths.append(str(path))
        if write:
            path.write_text(updated, encoding="utf-8")

    return {
        "vault_dir": str(resolved_vault),
        "logs_dir": str(layout.logs_dir),
        "from_pack": from_pack,
        "to_pack": target_pack,
        "files_scanned": files_scanned,
        "files_changed": files_changed,
        "replacements": replacements,
        "changed_paths": changed_paths,
        "write": write,
    }
