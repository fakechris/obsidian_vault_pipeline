#!/usr/bin/env python3
"""
Query-to-Wiki 归档器

Karpathy LLM Wiki模式: 好答案应该归档回知识库

Usage:
    # 归档当前查询的问答结果
    python3 query_to_wiki.py --title "问答主题" --sources "source1.md,source2.md"

    # 手动创建新Evergreen
    python3 query_to_wiki.py --create-evergreen "新概念名" --definition "一句话定义"

Features:
    - 从对话/问答提取结构化知识
    - 自动生成Evergreen格式
    - 更新Index和Log
    - Git提交（与WIGS流程集成）
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def get_vault_dir() -> Path:
    """获取Vault目录"""
    try:
        git_root = subprocess.check_output(
            ["git", "rev-parse", "show-toplevel"],
            text=True
        ).strip()
        return Path(git_root)
    except:
        return Path.cwd()


def create_evergreen(title: str, definition: str, content: str = "") -> Path:
    """创建新的Evergreen笔记"""
    vault_dir = get_vault_dir()

    # 标准化文件名
    safe_title = title.replace(" ", "-").replace("/", "-")
    date_str = datetime.now().strftime("%Y-%m-%d")

    evergreen_path = vault_dir / "10-Knowledge" / "Evergreen" / f"{safe_title}.md"

    # 构建内容
    md_content = f"""---
title: "{title}"
type: evergreen
date: {date_str}
tags: [evergreen, auto-generated]
aliases: [{safe_title}]
---

# {title}

> **一句话定义**: {definition}

---

## 详细内容

{content if content else "📝 详细解释（待补充）"}

---

## 关联概念

- 返回 [[../../10-Knowledge/Index\|Index 知识目录]]
- 相关合集: 查看其他 [[./\|Evergreen 合集]]

---

*创建于 {date_str} — 从问答自动归档*
"""

    evergreen_path.write_text(md_content, encoding="utf-8")
    print(f"✅ 创建 Evergreen: {evergreen_path}")

    return evergreen_path


def update_index(title: str) -> None:
    """更新Index.md添加新Evergreen链接"""
    vault_dir = get_vault_dir()
    index_path = vault_dir / "Index.md"

    if not index_path.exists():
        print(f"⚠️ Index.md 不存在: {index_path}")
        return

    # 读取当前内容
    content = index_path.read_text(encoding="utf-8")

    # 检查是否已存在
    if f"[[Evergreen/{title}" in content:
        print(f"ℹ️ {title} 已在Index中")
        return

    # 在"重构新增概念"部分添加
    marker = "### 📦 重构新增概念"
    new_entry = f"- [[Evergreen/{title}|{title}]] — 从问答自动归档"

    if marker in content:
        content = content.replace(
            marker,
            f"{marker}\n{new_entry}"
        )
        index_path.write_text(content, encoding="utf-8")
        print(f"✅ 更新 Index.md: {title}")
    else:
        print(f"⚠️ 未找到插入点，手动添加: {new_entry}")


def append_log(operation: str, subject: str, details: list[str]) -> None:
    """追加到Log.md时间线"""
    vault_dir = get_vault_dir()
    log_path = vault_dir / "Log.md"

    if not log_path.exists():
        print(f"⚠️ Log.md 不存在: {log_path}")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    log_entry = f"\n## [{date_str}] {operation} | {subject}\n"
    for detail in details:
        log_entry += f"- {detail}\n"

    # 在"最近操作"后插入
    content = log_path.read_text(encoding="utf-8")
    marker = "## 最近操作\n"

    if marker in content:
        content = content.replace(
            marker,
            f"{marker}{log_entry}"
        )
        log_path.write_text(content, encoding="utf-8")
        print(f"✅ 追加 Log.md: {operation} | {subject}")


def auto_git_commit(files: list[Path], message: str) -> bool:
    """自动Git提交"""
    try:
        vault_dir = get_vault_dir()

        # 添加文件
        for f in files:
            subprocess.run(
                ["git", "add", str(f)],
                capture_output=True,
                cwd=str(vault_dir)
            )

        # 提交
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=str(vault_dir)
        )

        if result.returncode == 0:
            print(f"✅ Git提交: {message}")
            return True
        else:
            print(f"⚠️ Git提交失败: {result.stderr}")
            return False

    except Exception as e:
        print(f"❌ Git提交异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Query-to-Wiki 归档器 — 将问答归档回知识库"
    )
    parser.add_argument(
        "--title",
        help="问答主题/标题"
    )
    parser.add_argument(
        "--definition",
        help="一句话定义（核心洞察）"
    )
    parser.add_argument(
        "--content",
        default="",
        help="详细内容（可后续编辑）"
    )
    parser.add_argument(
        "--sources",
        help="来源文档，逗号分隔（用于引用）"
    )
    parser.add_argument(
        "--create-evergreen",
        metavar="TITLE",
        help="创建新Evergreen的标题"
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="跳过Git提交（用于审核）"
    )

    args = parser.parse_args()

    # 参数处理
    title = args.create_evergreen or args.title
    if not title:
        print("❌ 需要提供 --title 或 --create-evergreen")
        sys.exit(1)

    definition = args.definition or "从问答提取的核心洞察（待精炼）"

    # 创建Evergreen
    evergreen_path = create_evergreen(title, definition, args.content)

    # 更新索引
    update_index(title.replace(" ", "-").replace("/", "-"))

    # 追加日志
    details = [
        f"创建 Evergreen: {title}",
        f"定义: {definition[:50]}..."
    ]
    if args.sources:
        details.append(f"来源: {args.sources}")

    append_log("query-to-wiki", title, details)

    # Git提交
    if not args.no_commit:
        files_to_commit = [
            evergreen_path,
            get_vault_dir() / "Index.md",
            get_vault_dir() / "Log.md"
        ]
        commit_msg = f"query-to-wiki: {title} (auto-generated from discussion)"
        auto_git_commit(files_to_commit, commit_msg)
    else:
        print("\n💡 手动提交命令:")
        print(f"  git add {evergreen_path} Index.md Log.md")
        print(f'  git commit -m "query-to-wiki: {title}"')

    print("\n" + "="*60)
    print("✅ 问答归档完成")
    print("="*60)


if __name__ == "__main__":
    main()
