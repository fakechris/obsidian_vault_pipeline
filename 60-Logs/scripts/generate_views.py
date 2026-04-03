#!/usr/bin/env python3
"""
Views Generator - 视图生成器
自动生成 80-Views/ 目录下的索引文件
纯 Markdown 输出，无需 Dataview 插件

Usage:
    python3 generate_views.py --period 1      # 生成最近一天视图
    python3 generate_views.py --period 7    # 生成最近一周视图
    python3 generate_views.py --type evergreen  # 生成 Evergreen 索引
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 自动加载 .env
VAULT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = VAULT_DIR / ".env"
if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_FILE, override=True)
    except ImportError:
        pass

VIEWS_DIR = VAULT_DIR / "80-Views"
EVERGREEN_DIR = VAULT_DIR / "10-Knowledge" / "Evergreen"
AREAS_DIR = VAULT_DIR / "20-Areas"
INBOX_DIR = VAULT_DIR / "50-Inbox" / "01-Raw"


def get_recent_files(period_days: int = 1) -> dict[str, list[dict]]:
    """获取最近创建的文件"""
    cutoff = datetime.now() - timedelta(days=period_days)
    results = {
        "areas": [],
        "evergreen": [],
        "inbox": [],
    }

    # Areas 目录
    if AREAS_DIR.exists():
        for f in AREAS_DIR.rglob("*.md"):
            try:
                stat = f.stat()
                ctime = datetime.fromtimestamp(stat.st_ctime)
                if ctime >= cutoff:
                    results["areas"].append({
                        "name": f.stem,
                        "path": str(f.relative_to(VAULT_DIR)),
                        "ctime": ctime,
                        "size": stat.st_size,
                    })
            except OSError:
                pass

    # Evergreen 目录
    if EVERGREEN_DIR.exists():
        for f in EVERGREEN_DIR.glob("*.md"):
            try:
                stat = f.stat()
                ctime = datetime.fromtimestamp(stat.st_ctime)
                if ctime >= cutoff:
                    results["evergreen"].append({
                        "name": f.stem,
                        "path": str(f.relative_to(VAULT_DIR)),
                        "ctime": ctime,
                        "size": stat.st_size,
                    })
            except OSError:
                pass

    # Inbox 目录
    if INBOX_DIR.exists():
        for f in INBOX_DIR.glob("*.md"):
            try:
                stat = f.stat()
                ctime = datetime.fromtimestamp(stat.st_ctime)
                if ctime >= cutoff:
                    results["inbox"].append({
                        "name": f.stem,
                        "path": str(f.relative_to(VAULT_DIR)),
                        "ctime": ctime,
                        "size": stat.st_size,
                    })
            except OSError:
                pass

    # 按时间排序
    for key in results:
        results[key].sort(key=lambda x: x["ctime"], reverse=True)

    return results


def get_evergreen_index() -> list[dict]:
    """获取所有 Evergreen 笔记索引"""
    results = []
    if not EVERGREEN_DIR.exists():
        return results

    for f in EVERGREEN_DIR.glob("*.md"):
        try:
            stat = f.stat()
            # 简单读取 frontmatter
            content = f.read_text(encoding="utf-8", errors="ignore")
            title = f.stem
            description = ""

            # 提取 title 和 description
            if content.startswith("---"):
                try:
                    end = content.index("---", 3)
                    frontmatter = content[3:end].strip()
                    for line in frontmatter.split("\n"):
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip('"')
                except ValueError:
                    pass

            results.append({
                "name": f.stem,
                "title": title,
                "description": description,
                "path": str(f.relative_to(VAULT_DIR)),
                "ctime": datetime.fromtimestamp(stat.st_ctime),
            })
        except OSError:
            pass

    results.sort(key=lambda x: x["name"])
    return results


def update_view_file(view_name: str, new_content: str) -> bool:
    """更新视图文件中的自动生成部分"""
    view_file = VIEWS_DIR / f"{view_name}.md"
    if not view_file.exists():
        print(f"✗ 视图文件不存在: {view_file}")
        return False

    try:
        content = view_file.read_text(encoding="utf-8")

        # 查找 AUTO-GENERATED 标记之间的内容并替换
        pattern = r'(<!-- AUTO-GENERATED-CONTENT-BELOW -->\n)(.*?)(\n<!-- AUTO-GENERATED-CONTENT-ABOVE -->)'
        replacement = r'\1\n' + new_content + r'\3'

        new_content_full = re.sub(pattern, replacement, content, flags=re.DOTALL)

        if new_content_full == content:
            # 没有变化，尝试直接插入
            print(f"⚠ 未找到生成标记，可能需要手动更新: {view_name}")
            return False

        view_file.write_text(new_content_full, encoding="utf-8")
        print(f"✓ 已更新: {view_file}")
        return True

    except Exception as e:
        print(f"✗ 更新失败: {e}")
        return False


def generate_recent_view(period_days: int = 1) -> str:
    """生成最近新增视图内容"""
    files = get_recent_files(period_days)

    lines = []
    lines.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")

    # Areas
    if files["areas"]:
        lines.append(f"### 深度解读文章 ({len(files['areas'])} 个)")
        lines.append("")
        for f in files["areas"][:20]:  # 最多显示20个
            lines.append(f"- [[{f['name']}|{f['name']}]] - {f['ctime'].strftime('%m-%d %H:%M')}")
        lines.append("")
    else:
        lines.append("### 深度解读文章")
        lines.append("_暂无新增_")
        lines.append("")

    # Evergreen
    if files["evergreen"]:
        lines.append(f"### Evergreen 笔记 ({len(files['evergreen'])} 个)")
        lines.append("")
        for f in files["evergreen"][:20]:
            lines.append(f"- [[{f['name']}|{f['name']}]] - {f['ctime'].strftime('%m-%d %H:%M')}")
        lines.append("")
    else:
        lines.append("### Evergreen 笔记")
        lines.append("_暂无新增_")
        lines.append("")

    # Inbox
    if files["inbox"]:
        lines.append(f"### 收件箱 ({len(files['inbox'])} 个)")
        lines.append("")
        for f in files["inbox"][:10]:
            lines.append(f"- [[{f['name']}|{f['name']}]] - {f['ctime'].strftime('%m-%d %H:%M')}")
        lines.append("")
    else:
        lines.append("### 收件箱")
        lines.append("_暂无新增_")
        lines.append("")

    return "\n".join(lines)


def generate_evergreen_view() -> str:
    """生成 Evergreen 索引视图内容"""
    notes = get_evergreen_index()

    lines = []
    lines.append(f"*总计: {len(notes)} 个 Evergreen 笔记 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")

    if not notes:
        lines.append("_暂无 Evergreen 笔记_")
        return "\n".join(lines)

    lines.append("| 概念 | 描述 | 创建时间 |")
    lines.append("|------|------|----------|")

    for note in notes:
        desc = note["description"] if note["description"] else "_"
        # 截断过长的描述
        if len(desc) > 50:
            desc = desc[:47] + "..."
        ctime = note["ctime"].strftime('%Y-%m-%d')
        lines.append(f"| [[{note['name']}|{note['title']}]] | {desc} | {ctime} |")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成 Obsidian 视图文件")
    parser.add_argument("--period", type=int, default=1, help="时间范围（天）")
    parser.add_argument("--type", choices=["recent", "evergreen", "all"], default="all", help="视图类型")
    args = parser.parse_args()

    print("="*60)
    print("Views Generator")
    print("="*60)
    print(f"Vault: {VAULT_DIR}")
    print()

    success = True

    if args.type in ("recent", "all"):
        print(f"生成最近 {args.period} 天视图...")
        content = generate_recent_view(args.period)
        if args.period == 1:
            if not update_view_file("最近新增", content):
                success = False
        else:
            # 保存到另一个文件
            pass
        print()

    if args.type in ("evergreen", "all"):
        print("生成 Evergreen 索引...")
        content = generate_evergreen_view()
        if not update_view_file("Evergreen索引", content):
            success = False
        print()

    print("="*60)
    if success:
        print("✓ 视图生成完成")
        return 0
    else:
        print("⚠ 部分视图生成失败，请检查文件标记")
        return 1


if __name__ == "__main__":
    sys.exit(main())
