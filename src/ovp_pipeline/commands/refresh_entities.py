"""ovp-refresh-entities — Auto-refresh Entity .md files + data cleaning.

Three responsibilities:
1. Refresh: Sync Entity .md frontmatter with current EntityRegistry metadata
   (aliases, entity_type, tags) — body content is preserved.
2. Orphan detection: Find Entity .md files with no matching registry entry.
3. Consistency: Find registry entries with no .md file on disk.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ENTITY_DIR = Path("10-Knowledge/Entity")
CANDIDATES_DIR = Path("10-Knowledge/Entity/_Candidates")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split markdown into (frontmatter_dict, body). Returns ({}, text) if no frontmatter."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2]


def _rebuild_frontmatter(fm: dict[str, Any]) -> str:
    import json as _json
    key_order = [
        "note_id", "title", "type", "entity_type",
        "date", "tags", "aliases",
    ]
    lines = ["---"]
    done: set[str] = set()
    for key in key_order:
        if key in fm:
            done.add(key)
            val = fm[key]
            if key == "title":
                lines.append(f"title: {_json.dumps(val, ensure_ascii=False)}")
            elif isinstance(val, list):
                items = ", ".join(
                    _json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v)
                    for v in val
                )
                lines.append(f"{key}: [{items}]")
            else:
                lines.append(f"{key}: {val}")
    for key, val in fm.items():
        if key not in done:
            if isinstance(val, str):
                lines.append(f'{key}: "{val}"')
            else:
                lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def run(
    vault_dir: Path,
    *,
    dry_run: bool = False,
    fix: bool = False,
) -> dict[str, Any]:
    from ..entity_registry import EntityRegistry
    from ..promote_entities import write_entity_file

    entity_dir = vault_dir / ENTITY_DIR
    candidates_dir = vault_dir / CANDIDATES_DIR

    if not entity_dir.is_dir():
        print(f"Entity directory not found: {entity_dir}")
        return {"error": "directory_not_found"}

    registry = EntityRegistry(vault_dir).load()

    active_map = {
        e.slug: e for e in registry.all_entries() if e.status == "active"
    }
    candidate_map = {
        e.slug: e for e in registry.all_entries() if e.status == "candidate"
    }

    refreshed: list[str] = []
    orphan_files: list[str] = []
    missing_files: list[str] = []
    type_mismatches: list[dict[str, str]] = []

    for md_file in sorted(entity_dir.glob("*.md")):
        slug = md_file.stem
        if slug.startswith("_"):
            continue

        text = md_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)

        entry = active_map.get(slug)
        if entry is None:
            orphan_files.append(slug)
            continue

        needs_update = False
        file_type = fm.get("entity_type", "")
        if file_type != entry.entity_type:
            type_mismatches.append({
                "slug": slug,
                "file_type": file_type,
                "registry_type": entry.entity_type,
            })
            needs_update = True

        registry_aliases = set(a for a in entry.aliases if a)
        file_aliases = set(fm.get("aliases", []) or [])
        if registry_aliases != file_aliases:
            needs_update = True

        file_title = fm.get("title", "").strip('"')
        if file_title != entry.title:
            needs_update = True

        if needs_update:
            refreshed.append(slug)
            if not dry_run and fix:
                fm["note_id"] = entry.slug
                fm["title"] = entry.title
                fm["type"] = "entity"
                fm["entity_type"] = entry.entity_type
                fm["tags"] = ["entity", entry.entity_type]
                fm["aliases"] = list(dict.fromkeys(a for a in entry.aliases if a))

                new_fm = _rebuild_frontmatter(fm)
                new_content = f"{new_fm}\n{body}"
                md_file.write_text(new_content, encoding="utf-8")

    if candidates_dir.is_dir():
        for md_file in sorted(candidates_dir.glob("*.md")):
            slug = md_file.stem
            entry = candidate_map.get(slug)
            if entry is None and slug not in active_map:
                orphan_files.append(f"_Candidates/{slug}")

    for slug in active_map:
        entity_path = entity_dir / f"{slug}.md"
        if not entity_path.exists():
            missing_files.append(slug)
            if not dry_run and fix:
                write_entity_file(vault_dir, active_map[slug], dry_run=False)

    summary = {
        "registry_active": len(active_map),
        "registry_candidates": len(candidate_map),
        "refreshed": refreshed,
        "refreshed_count": len(refreshed),
        "orphan_files": orphan_files,
        "orphan_count": len(orphan_files),
        "missing_files": missing_files,
        "missing_count": len(missing_files),
        "type_mismatches": type_mismatches,
        "dry_run": dry_run,
        "fix_applied": fix and not dry_run,
    }

    print(f"Entity Refresh Report:")
    print(f"  Active entities in registry: {len(active_map)}")
    print(f"  Candidate entities: {len(candidate_map)}")
    print(f"  Files needing refresh: {len(refreshed)}")
    if refreshed:
        for s in refreshed[:10]:
            print(f"    - {s}")
        if len(refreshed) > 10:
            print(f"    ... and {len(refreshed) - 10} more")
    print(f"  Orphan .md files (no registry entry): {len(orphan_files)}")
    if orphan_files:
        for s in orphan_files[:10]:
            print(f"    - {s}")
    print(f"  Missing .md files (registry but no file): {len(missing_files)}")
    if missing_files:
        for s in missing_files[:10]:
            print(f"    - {s}")
    print(f"  Type mismatches: {len(type_mismatches)}")

    if fix and not dry_run:
        print("\n  Fixes applied.")
    elif dry_run:
        print("\n  [dry-run] No changes made. Use --fix to apply.")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh Entity .md files and detect data inconsistencies"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path.cwd(),
        help="Vault root directory (default: cwd)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (refresh + generate missing)")
    args = parser.parse_args()
    result = run(args.vault_dir, dry_run=args.dry_run, fix=args.fix)
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
