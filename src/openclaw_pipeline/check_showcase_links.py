#!/usr/bin/env python3
"""
Showcase Link Checker - 链接稳定性检查器
检查所有 wikilink 是否指向存在的文件
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from collections import defaultdict

VAULT_DIR = Path(__file__).parent.parent.parent.parent / "openclaw-showcase"

def extract_wikilinks(content: str) -> list[str]:
    """提取所有 wikilink [[...]]"""
    # 匹配 [[link]] 或 [[link|alias]]
    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    return re.findall(pattern, content)

def get_all_md_files() -> set[str]:
    """获取所有 Markdown 文件名（不含扩展名）"""
    files = set()
    if not VAULT_DIR.exists():
        print(f"✗ Vault 目录不存在: {VAULT_DIR}")
        return files

    for md_file in VAULT_DIR.rglob("*.md"):
        # 文件名（不含扩展名）
        files.add(md_file.stem)
        # 带路径的文件名
        rel_path = md_file.relative_to(VAULT_DIR)
        files.add(str(rel_path.with_suffix('')))

    return files

def check_links():
    """检查所有链接"""
    print("="*70)
    print("Showcase Link Checker")
    print("="*70)
    print(f"Vault: {VAULT_DIR}")
    print()

    if not VAULT_DIR.exists():
        print(f"✗ 错误: Vault 目录不存在")
        return 1

    # 获取所有文件
    all_files = get_all_md_files()
    print(f"✓ 发现 {len(all_files)} 个 Markdown 文件")
    print()

    # 统计
    broken_links = defaultdict(list)  # file -> [broken_links]
    all_links = []  # (file, link) 列表

    # 遍历所有文件检查链接
    for md_file in VAULT_DIR.rglob("*.md"):
        rel_path = md_file.relative_to(VAULT_DIR)
        try:
            content = md_file.read_text(encoding='utf-8')
            links = extract_wikilinks(content)

            for link in links:
                all_links.append((str(rel_path), link))

                # 检查链接目标是否存在
                # 清理链接（移除锚点 #...）
                clean_link = link.split('#')[0].strip()

                # 检查文件名匹配
                if clean_link not in all_files:
                    # 尝试不同路径格式
                    found = False
                    for f in all_files:
                        if f.endswith(clean_link) or f == clean_link:
                            found = True
                            break
                    if not found:
                        broken_links[str(rel_path)].append(link)

        except Exception as e:
            print(f"✗ 读取文件失败: {rel_path} - {e}")

    # 报告
    total_links = len(all_links)
    broken_count = sum(len(links) for links in broken_links.values())

    print(f"总链接数: {total_links}")
    print(f"断裂链接: {broken_count}")
    print(f"健康率: {(total_links - broken_count) / total_links * 100:.1f}%" if total_links > 0 else "N/A")
    print()

    # 详细报告
    if broken_links:
        print("="*70)
        print("断裂链接详情")
        print("="*70)
        for file, links in sorted(broken_links.items()):
            print(f"\n📄 {file}")
            for link in links:
                print(f"   ✗ [[{link}]]")
        print()
    else:
        print("✓ 所有链接都有效！")
        print()

    # 返回状态码
    return 0 if not broken_links else 1

def check_data_quality():
    """检查数据质量"""
    print("="*70)
    print("数据质量检查")
    print("="*70)
    print()

    issues = []

    for md_file in VAULT_DIR.rglob("*.md"):
        rel_path = md_file.relative_to(VAULT_DIR)
        try:
            content = md_file.read_text(encoding='utf-8')

            # 检查 frontmatter
            if not content.startswith('---'):
                issues.append(f"{rel_path}: 缺少 frontmatter")

            # 检查内容长度
            lines = content.split('\n')
            content_lines = [l for l in lines if l.strip() and not l.strip().startswith('#')]
            if len(content_lines) < 5:
                issues.append(f"{rel_path}: 内容过短 ({len(content_lines)} 行)")

            # 检查是否有内容
            if len(content.strip()) < 100:
                issues.append(f"{rel_path}: 文件内容过少")

        except Exception as e:
            issues.append(f"{rel_path}: 读取错误 - {e}")

    if issues:
        print(f"⚠ 发现 {len(issues)} 个问题:")
        for issue in issues[:20]:  # 最多显示20个
            print(f"  - {issue}")
        if len(issues) > 20:
            print(f"  ... 还有 {len(issues) - 20} 个问题")
        return 1
    else:
        print("✓ 数据质量检查通过")
        return 0

if __name__ == "__main__":
    link_status = check_links()
    print()
    quality_status = check_data_quality()

    print()
    print("="*70)
    if link_status == 0 and quality_status == 0:
        print("✓ 所有检查通过")
        sys.exit(0)
    else:
        print("⚠ 部分检查失败，请查看详情")
        sys.exit(1)
