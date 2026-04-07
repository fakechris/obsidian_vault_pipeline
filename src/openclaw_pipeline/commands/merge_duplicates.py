#!/usr/bin/env python3
"""
merge_duplicates - 识别并合并重复的 Evergreen 概念

使用 trigram similarity 识别概念集群（多个 slug 指向同一概念），
建议合并方案：将重复项作为 canonical 的 alias。

Usage:
    ovp-merge-duplicates --scan
    ovp-merge-duplicates --dry-run
    ovp-merge-duplicates --write
    ovp-merge-duplicates --threshold 0.82
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

try:
    from ..runtime import resolve_vault_dir
except ImportError:
    from runtime import resolve_vault_dir  # type: ignore

try:
    from ..concept_registry import (
        ConceptRegistry, ConceptEntry, STATUS_ACTIVE,
        STATUS_CANDIDATE, normalize_surface, slug_to_surface
    )
    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False


def get_vault_dir() -> Path:
    """获取 Vault 目录"""
    return resolve_vault_dir()


def char_ngrams(s: str, n: int = 3) -> set[str]:
    """生成 character n-grams"""
    s = f"  {s}  "
    if len(s) < n:
        return {s}
    return {s[i:i+n] for i in range(len(s) - n + 1)}


def trigram_jaccard(a: str, b: str) -> float:
    """计算 trigram Jaccard similarity"""
    ga, gb = char_ngrams(a, 3), char_ngrams(b, 3)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def find_duplicate_clusters(
    slugs: list[str],
    threshold: float = 0.82,
) -> list[list[str]]:
    """使用 trigram similarity 找到重复概念集群

    Args:
        slugs: 所有 slug 列表
        threshold: 相似度阈值 (default: 0.82)

    Returns:
        重复概念集群列表，每个集群是一个 slug 列表
    """
    # 计算所有 pair 的相似度
    n = len(slugs)
    similarity: dict[tuple[str, str], float] = {}

    for i in range(n):
        for j in range(i + 1, n):
            s1, s2 = slugs[i], slugs[j]
            # 使用 normalized surface 计算
            surf1 = normalize_surface(s1)
            surf2 = normalize_surface(s2)
            sim = trigram_jaccard(surf1, surf2)
            if sim >= threshold:
                similarity[(s1, s2)] = sim
                similarity[(s2, s1)] = sim

    # 使用 Union-Find 聚类
    parent: dict[str, str] = {s: s for s in slugs}

    def find(x: str) -> str:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for (s1, s2), sim in similarity.items():
        if sim >= threshold:
            union(s1, s2)

    # 收集集群
    clusters: dict[str, list[str]] = defaultdict(list)
    for s in slugs:
        root = find(s)
        clusters[root].append(s)

    # 只返回有多于 1 个成员的集群
    return [members for members in clusters.values() if len(members) > 1]


def scan_evergreen_duplicates(
    vault_dir: Path,
    threshold: float = 0.82,
) -> dict:
    """扫描 Evergreen 目录，查找重复概念"""
    evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"

    if not evergreen_dir.exists():
        return {"error": "Evergreen directory not found", "clusters": []}

    # 收集所有 slug
    slugs = []
    slug_to_path = {}
    slug_to_title = {}

    for md_file in evergreen_dir.glob("*.md"):
        if md_file.name.startswith("."):
            continue

        slug = md_file.stem
        slugs.append(slug)
        slug_to_path[slug] = md_file

        # 读取 title
        try:
            content = md_file.read_text(encoding="utf-8")
            title_match = re.search(r'^title:\s*"(.+)"', content, re.MULTILINE)
            if title_match:
                slug_to_title[slug] = title_match.group(1)
            else:
                slug_to_title[slug] = slug.replace("-", " ").title()
        except Exception:
            slug_to_title[slug] = slug

    # 查找重复集群
    clusters = find_duplicate_clusters(slugs, threshold=threshold)

    # 构建结果
    result_clusters = []
    for cluster in clusters:
        # 按字符串顺序选择 canonical（最简单的 slug）
        canonical = min(cluster, key=lambda s: (len(s), s))

        cluster_info = {
            "canonical": canonical,
            "canonical_title": slug_to_title.get(canonical, canonical),
            "canonical_path": str(slug_to_path.get(canonical, "")),
            "duplicates": [],
        }

        for slug in sorted(cluster):
            if slug != canonical:
                cluster_info["duplicates"].append({
                    "slug": slug,
                    "title": slug_to_title.get(slug, slug),
                    "path": str(slug_to_path.get(slug, "")),
                })

        result_clusters.append(cluster_info)

    return {
        "total_evergreen": len(slugs),
        "clusters_found": len(clusters),
        "threshold": threshold,
        "clusters": result_clusters,
    }


def merge_duplicate_cluster(
    cluster: dict,
    vault_dir: Path,
    dry_run: bool = True,
) -> dict:
    """合并一个重复集群

    将所有 duplicate 作为 alias 添加到 canonical，
    然后删除 duplicate 文件。
    """
    result = {
        "canonical": cluster["canonical"],
        "merged": [],
        "failed": [],
    }

    canonical = cluster["canonical"]
    canonical_path = Path(cluster["canonical_path"])

    if not canonical_path.exists():
        return {"error": f"Canonical file not found: {canonical_path}"}

    for dup in cluster.get("duplicates", []):
        dup_slug = dup["slug"]
        dup_path = Path(dup["path"])

        if dry_run:
            print(f"  🔍 [DRY RUN] Would merge {dup_slug} -> {canonical}")
            result["merged"].append(dup_slug)
            continue

        # 1. 读取 canonical，添加 alias
        try:
            content = canonical_path.read_text(encoding="utf-8")

            # 检查是否已有这个 alias
            if f'"{dup_slug}"' in content or f"'{dup_slug}'" in content:
                print(f"  ⚠️  {dup_slug} already an alias of {canonical}, skipping")
                continue

            # 添加 alias 到 frontmatter
            if "aliases:" in content:
                # 在现有 aliases 行添加
                content = re.sub(
                    r'aliases:\s*\[(.*?)\]',
                    f'aliases: [\\1, "{dup_slug}"]',
                    content
                )
            else:
                # 在 frontmatter 第一行后添加
                content = re.sub(
                    r"^(---\n)",
                    r'\1aliases: ["' + dup_slug + '"]\n',
                    content
                )

            canonical_path.write_text(content, encoding="utf-8")
            print(f"  ✅ Added alias '{dup_slug}' to {canonical}")

        except Exception as e:
            print(f"  ❌ Error updating canonical: {e}", file=sys.stderr)
            result["failed"].append(dup_slug)
            continue

        # 2. 在所有文件中替换对 duplicate 的引用为 canonical
        if dup_path.exists():
            try:
                dup_content = dup_path.read_text(encoding="utf-8")

                # 扫描 vault 中所有 md 文件，替换 wikilink 引用
                replacements_made = 0
                for md_file in vault_dir.rglob("*.md"):
                    if md_file == dup_path:
                        continue
                    try:
                        file_content = md_file.read_text(encoding="utf-8")
                        # 匹配 [[dup_slug]] 或 [[dup_slug|display]] 或 [[anything|dup_slug]]
                        pattern = rf'\[\[{re.escape(dup_slug)}(\|[^\]]+)?\]\]'
                        new_content, count = re.subn(
                            pattern,
                            lambda m: f"[[{canonical}{m.group(1) or ''}]]",
                            file_content
                        )
                        if count > 0:
                            md_file.write_text(new_content, encoding="utf-8")
                            replacements_made += count
                    except Exception:
                        pass

                # 3. 删除 duplicate 文件
                dup_path.unlink()
                print(f"  ✅ Deleted duplicate file {dup_path.name} (replaced {replacements_made} links)")

                result["merged"].append(dup_slug)
            except Exception as e:
                print(f"  ❌ Error deleting duplicate: {e}", file=sys.stderr)
                result["failed"].append(dup_slug)

    return result


def print_report(result: dict):
    """打印报告"""
    print("\n" + "=" * 60)
    print("Duplicate Concepts Merge Report")
    print("=" * 60)

    if "error" in result:
        print(f"\n❌ Error: {result['error']}")
        return

    print(f"\nTotal Evergreen files: {result['total_evergreen']}")
    print(f"Similarity threshold: {result['threshold']}")
    print(f"Duplicate clusters found: {result['clusters_found']}")

    if not result["clusters"]:
        print("\n✅ No duplicate clusters found!")
        return

    for i, cluster in enumerate(result["clusters"], 1):
        print(f"\n--- Cluster {i} ---")
        print(f"  Canonical: {cluster['canonical']}")
        print(f"  Title: {cluster['canonical_title']}")
        print(f"  Duplicates ({len(cluster['duplicates'])}):")
        for dup in cluster["duplicates"]:
            print(f"    - {dup['slug']} ({dup['title']})")


def main():
    parser = argparse.ArgumentParser(description="Find and merge duplicate Evergreen concepts")
    parser.add_argument("--scan", action="store_true", help="扫描重复概念")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry run mode (default)")
    parser.add_argument("--write", action="store_true",
                        help="Actually merge duplicates")
    parser.add_argument("--threshold", type=float, default=0.82,
                        help="Trigram similarity threshold (default: 0.82)")
    parser.add_argument("--vault-dir", type=Path, default=None,
                        help="Vault directory")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--cluster", type=int, default=None,
                        help="Merge only this cluster number (1-based)")

    args = parser.parse_args()

    vault_dir = resolve_vault_dir(args.vault_dir or get_vault_dir())

    # --write implies not dry_run
    dry_run = not args.write

    print(f"Scanning vault: {vault_dir}")
    print(f"Similarity threshold: {args.threshold}")

    # 扫描重复
    result = scan_evergreen_duplicates(vault_dir, threshold=args.threshold)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print_report(result)

    if not result.get("clusters"):
        return 0

    # 执行合并
    if args.write or not dry_run:
        clusters_to_merge = result["clusters"]
        if args.cluster is not None:
            idx = args.cluster - 1
            if 0 <= idx < len(clusters_to_merge):
                clusters_to_merge = [clusters_to_merge[idx]]
            else:
                print(f"❌ Invalid cluster number: {args.cluster}")
                return 1

        total_merged = 0
        total_failed = 0

        for cluster in clusters_to_merge:
            print(f"\nMerging cluster: {cluster['canonical']}")
            merge_result = merge_duplicate_cluster(cluster, vault_dir, dry_run=dry_run)
            total_merged += len(merge_result.get("merged", []))
            total_failed += len(merge_result.get("failed", []))

        if not dry_run:
            print(f"\n✅ Merge complete: {total_merged} merged, {total_failed} failed")
        else:
            print(f"\n🔍 Dry run: would merge {total_merged} duplicates")

    return 0


if __name__ == "__main__":
    sys.exit(main())
