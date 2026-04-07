#!/usr/bin/env python3
"""
rebuild_registry - 重建 Registry 与文件系统的双向同步

扫描 Evergreen 目录，与 Registry 双向同步：
1. 文件存在但不在 Registry -> 补充 Registry 条目
2. Registry 有条目但文件不存在 -> 标记为 orphaned 或删除
3. 报告分叉状态

Usage:
    ovp-rebuild-registry --dry-run
    ovp-rebuild-registry --write
    ovp-rebuild-registry --vault-dir /path/to/vault
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from ..concept_registry import (
        ConceptRegistry, ConceptEntry, STATUS_ACTIVE,
        STATUS_CANDIDATE, STATUS_ALIAS, STATUS_DEPRECATED, STATUS_REJECTED
    )
except ImportError:
    print("⚠️ concept_registry not available, running in scan-only mode")
    ConceptRegistry = None
    ConceptEntry = None
    STATUS_ACTIVE = "active"
    STATUS_CANDIDATE = "candidate"


def get_vault_dir() -> Path:
    """获取 Vault 目录"""
    try:
        import subprocess
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True
        ).strip()
        return Path(git_root)
    except subprocess.CalledProcessError:
        return Path.cwd()


def scan_evergreen_files(evergreen_dir: Path) -> dict[str, dict]:
    """扫描 Evergreen 目录，返回 {slug: {path, frontmatter}}"""
    files = {}

    if not evergreen_dir.exists():
        return files

    for md_file in evergreen_dir.glob("*.md"):
        if md_file.name.startswith("."):
            continue

        slug = md_file.stem  # filename without extension = slug

        # 读取 frontmatter
        frontmatter = {}
        try:
            content = md_file.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if fm_match:
                for line in fm_match.group(1).split("\n"):
                    if ":" in line:
                        key, _, value = line.partition(":")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        frontmatter[key] = value
        except Exception:
            pass

        files[slug] = {
            "path": md_file,
            "title": frontmatter.get("title", slug.replace("-", " ").title()),
            "aliases": [],
            "type": frontmatter.get("type", "evergreen"),
        }

    return files


def rebuild_registry(vault_dir: Path, dry_run: bool = True, write: bool = False) -> dict:
    """重建 Registry 与文件系统同步"""
    evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"

    # 扫描文件系统
    fs_files = scan_evergreen_files(evergreen_dir)
    fs_slugs = set(fs_files.keys())

    result = {
        "dry_run": dry_run,
        "write": write,
        "fs_file_count": len(fs_files),
        "fs_slugs": sorted(fs_slugs),
        "not_in_registry": [],  # 文件存在但不在 registry
        "not_in_filesystem": [],  # registry 有但文件不存在
        "in_sync": [],  # 两者都有
        "orphan_registry_entries": [],
    }

    if ConceptRegistry is None:
        result["error"] = "concept_registry not available"
        return result

    # 加载 Registry
    registry = ConceptRegistry(vault_dir)
    registry.load()
    registry_slugs = {e.slug for e in registry.entries}
    registry_slug_to_entry = {e.slug: e for e in registry.entries}

    # 比较
    result["registry_entry_count"] = len(registry_slugs)

    for slug in sorted(fs_slugs):
        if slug not in registry_slugs:
            result["not_in_registry"].append({
                "slug": slug,
                "path": str(fs_files[slug]["path"]),
                "title": fs_files[slug]["title"],
            })
        else:
            result["in_sync"].append(slug)

    for slug in sorted(registry_slugs):
        if slug not in fs_slugs:
            entry = registry_slug_to_entry[slug]
            result["not_in_filesystem"].append({
                "slug": slug,
                "title": entry.title,
                "status": entry.status,
                "kind": getattr(entry, "kind", "unknown"),
            })

    # 执行写入（非 dry_run 模式）
    if write and not dry_run:
        # 为不在 registry 的文件创建条目
        for item in result["not_in_registry"]:
            slug = item["slug"]
            try:
                entry = ConceptEntry(
                    slug=slug,
                    title=item["title"],
                    aliases=[],
                    definition=f"Auto-imported from {item['path']}",
                    area="general",
                    status=STATUS_ACTIVE,
                )
                registry.add_entry(entry)
                print(f"✅ Added to registry: {slug}")
            except ValueError as e:
                print(f"⚠️  {slug}: {e}")

        # 保存
        registry.save()
        print(f"\n✅ Registry rebuilt. Added {len(result['not_in_registry'])} entries.")
    else:
        if result["not_in_registry"]:
            print(f"\n📋 Would add {len(result['not_in_registry'])} files to registry:")
            for item in result["not_in_registry"][:10]:
                print(f"   - {item['slug']} ({item['title']})")
            if len(result["not_in_registry"]) > 10:
                print(f"   ... and {len(result['not_in_registry']) - 10} more")

        if result["not_in_filesystem"]:
            print(f"\n📋 {len(result['not_in_filesystem'])} registry entries have no file:")
            for item in result["not_in_filesystem"][:10]:
                print(f"   - {item['slug']} ({item['title']}, status={item['status']})")
            if len(result["not_in_filesystem"]) > 10:
                print(f"   ... and {len(result['not_in_filesystem']) - 10} more")

    return result


def print_report(result: dict):
    """打印报告"""
    print("\n" + "=" * 60)
    print("Registry Rebuild Report")
    print("=" * 60)

    print(f"\nFilesystem Evergreen files: {result['fs_file_count']}")
    print(f"Registry entries: {result.get('registry_entry_count', 'N/A')}")

    sync_count = len(result["in_sync"])
    not_in_reg = len(result["not_in_registry"])
    not_in_fs = len(result["not_in_filesystem"])

    print(f"\nSync status:")
    print(f"  ✅ In sync: {sync_count}")
    print(f"  ⚠️  Not in registry: {not_in_reg}")
    print(f"  ⚠️  Not in filesystem: {not_in_fs}")

    if result.get("error"):
        print(f"\n❌ Error: {result['error']}")
        return

    if not result["not_in_registry"] and not result["not_in_filesystem"]:
        print("\n✅ Registry and filesystem are fully synchronized!")
    else:
        pct_in_sync = sync_count / max(sync_count + not_in_reg, 1) * 100
        print(f"\n📊 Sync rate: {pct_in_sync:.1f}%")

    if result["dry_run"] and (not_in_reg > 0 or not_in_fs > 0):
        print("\n💡 Run with --write to apply fixes")


def main():
    parser = argparse.ArgumentParser(description="Rebuild Registry from Evergreen files")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry run mode (default)")
    parser.add_argument("--write", action="store_true",
                        help="Actually write changes")
    parser.add_argument("--vault-dir", type=Path, default=None,
                        help="Vault directory")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    vault_dir = args.vault_dir or get_vault_dir()

    # --write implies not dry_run
    dry_run = not args.write

    result = rebuild_registry(vault_dir, dry_run=dry_run, write=args.write)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)

    if not result.get("error"):
        if result["not_in_registry"] or result["not_in_filesystem"]:
            sys.exit(1)  # Indicate drift detected
        sys.exit(0)
    else:
        sys.exit(2)


if __name__ == "__main__":
    sys.exit(main())
