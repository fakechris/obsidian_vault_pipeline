#!/usr/bin/env python3
"""
migrate_broken_links - 扫描并修复 Markdown 中的断裂 wikilink

扫描所有 md 文件，检测断裂链接并尝试自动修复：
1. 空格转连字符: [[Agent Harness]] -> [[agent-harness]]
2. 大小写规范化: [[MCP Protocol]] -> [[mcp-protocol]]
3. Registry 模糊匹配: 尝试在 registry 中查找最接近的 slug

Usage:
    ovp-migrate-links --scan
    ovp-migrate-links --dry-run
    ovp-migrate-links --write
    ovp-migrate-links --vault-dir /path/to/vault
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from ..concept_registry import ConceptRegistry
    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False


WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')


@dataclass
class BrokenLink:
    """断裂链接信息"""
    source_file: Path
    source_stem: str
    target_raw: str  # 原始 wikilink 目标
    target_normalized: str  # 规范化后的目标
    display_text: str
    line_number: int
    line_content: str
    suggested_fix: str | None = None
    fix_confidence: float = 0.0

    def normalized_link_target(self) -> str:
        """返回规范化后的链接目标（不含 display）"""
        return self.target_normalized


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


def normalize_slug(slug: str) -> str:
    """将 wikilink surface 规范化为 slug"""
    # 去除 heading 和 query
    clean = re.sub(r'[#?].*$', '', slug).strip()
    # 空格/下划线转连字符
    clean = re.sub(r'[\s_]+', '-', clean)
    # 移除非法字符（保留连字符）
    clean = re.sub(r'[^\w\-]', '', clean)
    # 合并连续连字符
    clean = re.sub(r'-+', '-', clean)
    # 小写化
    return clean.lower().strip('-')


def scan_directory(vault_dir: Path, recursive: bool = True) -> list[BrokenLink]:
    """扫描目录下所有 md 文件的 wikilink"""
    broken_links: list[BrokenLink] = []

    # 确定扫描范围
    scan_dirs = [
        vault_dir / "10-Knowledge" / "Evergreen",
        vault_dir / "20-Areas",
    ]

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue

        pattern = "**/*.md" if recursive else "*.md"
        for md_file in scan_dir.glob(pattern):
            if any(part.startswith('.') for part in md_file.parts):
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
                lines = content.split('\n')

                for line_num, line in enumerate(lines, 1):
                    for match in WIKILINK_PATTERN.finditer(line):
                        target_raw = match.group(1).strip()
                        display = match.group(2).strip() if match.group(2) else target_raw

                        # 规范化 target
                        target_norm = normalize_slug(target_raw)

                        # 检查目标文件是否存在
                        # 检查多种可能的路径格式
                        possible_paths = [
                            vault_dir / "10-Knowledge" / "Evergreen" / f"{target_norm}.md",
                            vault_dir / "10-Knowledge" / "Evergreen" / f"{target_raw}.md",
                        ]

                        target_exists = any(p.exists() for p in possible_paths)

                        if not target_exists:
                            broken = BrokenLink(
                                source_file=md_file,
                                source_stem=md_file.stem,
                                target_raw=target_raw,
                                target_normalized=target_norm,
                                display_text=display,
                                line_number=line_num,
                                line_content=line.strip()[:100],
                            )
                            broken_links.append(broken)

            except Exception as e:
                print(f"⚠️  Error scanning {md_file}: {e}", file=sys.stderr)

    return broken_links


def suggest_fixes(broken_links: list[BrokenLink], vault_dir: Path) -> list[BrokenLink]:
    """为每个断裂链接建议修复方案"""
    if not HAS_REGISTRY:
        return broken_links

    registry = ConceptRegistry(vault_dir)
    registry.load()

    for broken in broken_links:
        # 1. 空格转连字符修复
        if ' ' in broken.target_raw or '_' in broken.target_raw:
            broken.suggested_fix = f"[[{broken.target_normalized}|{broken.display_text}]]"
            broken.fix_confidence = 0.95

        # 2. 尝试在 registry 中查找
        result = registry.resolve_mention(broken.target_raw)
        if result.action.value == "link_existing" and result.entry:
            fix = f"[[{result.entry.slug}|{broken.display_text}]]"
            if fix != f"[[{broken.target_raw}|{broken.display_text}]]":
                broken.suggested_fix = fix
                broken.fix_confidence = max(broken.fix_confidence, result.confidence)

    return broken_links


def apply_fixes(broken_links: list[BrokenLink], dry_run: bool = True) -> dict:
    """应用修复"""
    results = {
        "total_broken": len(broken_links),
        "fixed": 0,
        "failed": 0,
        "skipped": 0,
        "by_file": {},
    }

    # 按文件分组
    by_source: dict[Path, list[BrokenLink]] = {}
    for broken in broken_links:
        if broken.source_file not in by_source:
            by_source[broken.source_file] = []
        by_source[broken.source_file].append(broken)

    for source_file, links in by_source.items():
        if dry_run:
            print(f"\n🔍 [DRY RUN] Would fix {len(links)} links in {source_file.name}")
            for link in links[:5]:
                if link.suggested_fix:
                    print(f"   '{link.target_raw}' -> '{link.normalized_link_target()}'")
            if len(links) > 5:
                print(f"   ... and {len(links) - 5} more")
            results["skipped"] += len(links)
            continue

        # 读取文件内容
        try:
            content = source_file.read_text(encoding="utf-8")
            lines = content.split('\n')

            # 按行号倒序修改（避免行号偏移）
            links_sorted = sorted(links, key=lambda x: x.line_number, reverse=True)

            for link in links_sorted:
                if not link.suggested_fix:
                    continue

                # 构造旧 wikilink 和新 wikilink
                old_pattern = f"[[{link.target_raw}|{link.display_text}]]"
                if f"[[{link.target_raw}]]" == link.line_content.strip():
                    # 无 display text 的情况
                    old_pattern = f"[[{link.target_raw}]]"
                    new_link_text = f"[[{link.target_normalized}]]"
                else:
                    new_link_text = link.suggested_fix

                # 替换
                if old_pattern in lines[link.line_number - 1]:
                    lines[link.line_number - 1] = lines[link.line_number - 1].replace(
                        old_pattern, new_link_text
                    )
                    results["fixed"] += 1
                else:
                    results["failed"] += 1

            # 写回文件
            source_file.write_text('\n'.join(lines), encoding="utf-8")
            print(f"✅ Fixed {results['fixed']} links in {source_file.name}")

        except Exception as e:
            print(f"❌ Error fixing {source_file}: {e}", file=sys.stderr)
            results["failed"] += len(links)

        results["by_file"][str(source_file)] = {
            "total": len(links),
            "fixed": results["fixed"],
        }

    return results


def print_report(broken_links: list[BrokenLink], results: dict | None = None):
    """打印报告"""
    print("\n" + "=" * 60)
    print("Broken Links Scan Report")
    print("=" * 60)

    if not broken_links:
        print("\n✅ No broken links found!")
        return

    print(f"\nTotal broken links: {len(broken_links)}")

    # 按 source 文件分组
    by_source: dict[str, list[BrokenLink]] = {}
    for link in broken_links:
        key = str(link.source_file)
        if key not in by_source:
            by_source[key] = []
        by_source[key].append(link)

    print(f"Affected files: {len(by_source)}")

    # Top 断裂目标
    target_counts: dict[str, int] = {}
    for link in broken_links:
        target_counts[link.target_raw] = target_counts.get(link.target_raw, 0) + 1

    print("\nTop broken link targets:")
    for target, count in sorted(target_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {count:3d}x [[{target}]]")

    if results:
        print(f"\nFix results:")
        print(f"  Fixed: {results.get('fixed', 0)}")
        print(f"  Failed: {results.get('failed', 0)}")
        print(f"  Skipped: {results.get('skipped', 0)}")


def main():
    parser = argparse.ArgumentParser(description="Scan and fix broken wikilinks")
    parser.add_argument("--scan", action="store_true", help="扫描断裂链接")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry run mode (default)")
    parser.add_argument("--write", action="store_true",
                        help="Actually write fixes")
    parser.add_argument("--vault-dir", type=Path, default=None,
                        help="Vault directory")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--no-registry", action="store_true",
                        help="Disable registry-based suggestions")

    args = parser.parse_args()

    vault_dir = args.vault_dir or get_vault_dir()

    # --write implies not dry_run
    dry_run = not args.write

    print(f"Scanning vault: {vault_dir}")

    # 扫描断裂链接
    broken_links = scan_directory(vault_dir)

    if not broken_links:
        print_report(broken_links)
        return 0

    # 建议修复方案
    if not args.no_registry and HAS_REGISTRY:
        broken_links = suggest_fixes(broken_links, vault_dir)

    # 应用修复
    if args.write or not dry_run:
        results = apply_fixes(broken_links, dry_run=dry_run)
    else:
        results = {"fixed": 0, "failed": 0, "skipped": len(broken_links)}

    if args.json:
        output = {
            "broken_links": [
                {
                    "source": str(b.source_file),
                    "target_raw": b.target_raw,
                    "target_normalized": b.target_normalized,
                    "line": b.line_number,
                    "suggested_fix": b.suggested_fix,
                    "confidence": b.fix_confidence,
                }
                for b in broken_links
            ],
            "results": results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_report(broken_links, results)

    if not dry_run:
        if results.get("fixed", 0) > 0:
            print(f"\n✅ Fixed {results['fixed']} broken links")
        if results.get("failed", 0) > 0:
            print(f"❌ Failed to fix {results['failed']} links")

    return 0


if __name__ == "__main__":
    sys.exit(main())
