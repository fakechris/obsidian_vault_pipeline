"""
ovp-query-to-wiki - Query-to-Wiki 归档器

Karpathy LLM Wiki 模式: 好答案应该归档回知识库

Usage:
    # 归档当前查询的问答结果
    ovp-query-to-wiki --title "问答主题" --definition "一句话定义"

    # 创建新 Evergreen
    ovp-query-to-wiki --create-evergreen "新概念名" --definition "一句话定义"

Features:
    - 从对话/问答提取结构化知识
    - 自动生成 Evergreen 格式
    - Git 提交（与 WIGS 流程集成）
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def get_vault_dir() -> Path:
    """获取 Vault 目录"""
    try:
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True
        ).strip()
        return Path(git_root)
    except subprocess.CalledProcessError:
        return Path.cwd()


def create_evergreen(title: str, definition: str, content: str = "", sources: list[str] = None) -> Path:
    """创建新的 Evergreen 笔记"""
    vault_dir = get_vault_dir()

    # 标准化文件名
    safe_title = title.replace(" ", "-").replace("/", "-")
    date_str = datetime.now().strftime("%Y-%m-%d")

    evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"
    evergreen_dir.mkdir(parents=True, exist_ok=True)
    evergreen_path = evergreen_dir / f"{safe_title}.md"

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

"""

    if sources:
        md_content += "\n## 来源引用\n\n"
        for source in sources:
            md_content += f"- {source}\n"

    md_content += f"""
---

*创建于 {date_str} — 从问答自动归档*
"""

    evergreen_path.write_text(md_content, encoding="utf-8")
    print(f"✅ 创建 Evergreen: {evergreen_path}")

    return evergreen_path


def update_atlas_index(title: str) -> None:
    """更新 Atlas/MOC 索引"""
    vault_dir = get_vault_dir()
    index_path = vault_dir / "10-Knowledge" / "Atlas" / "MOC-Index.md"

    if not index_path.exists():
        print(f"⚠️ MOC-Index.md 不存在: {index_path}")
        return

    content = index_path.read_text(encoding="utf-8")
    safe_title = title.replace(" ", "-")

    # 检查是否已存在
    if f"[[{safe_title}]]" in content or f"[[{safe_title}.md]]" in content:
        print(f"ℹ️ {title} 已在索引中")
        return

    # 在 "## Concepts" 部分添加
    marker = "## Concepts"
    new_entry = f"- [[{safe_title}]]"

    if marker in content:
        content = content.replace(marker, f"{marker}\n{new_entry}")
        index_path.write_text(content, encoding="utf-8")
        print(f"✅ 更新索引: {title}")
    else:
        print(f"⚠️ 未找到插入点，手动添加: {new_entry}")


def append_log(operation: str, subject: str, details: list[str]) -> None:
    """追加到 Log.md 时间线"""
    vault_dir = get_vault_dir()
    log_path = vault_dir / "Log.md"

    if not log_path.exists():
        print(f"⚠️ Log.md 不存在: {log_path}")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    log_entry = f"\n## [{date_str}] {operation} | {subject}\n"
    for detail in details:
        log_entry += f"- {detail}\n"

    content = log_path.read_text(encoding="utf-8")
    marker = "## 最近操作\n"

    if marker in content:
        content = content.replace(marker, f"{marker}{log_entry}")
        log_path.write_text(content, encoding="utf-8")
        print(f"✅ 追加 Log: {operation} | {subject}")


def auto_git_commit(files: list[Path], message: str) -> bool:
    """自动 Git 提交"""
    try:
        vault_dir = get_vault_dir()

        for f in files:
            subprocess.run(
                ["git", "add", str(f)],
                capture_output=True,
                cwd=str(vault_dir)
            )

        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=str(vault_dir)
        )

        if result.returncode == 0:
            print(f"✅ Git 提交: {message}")
            return True
        else:
            print(f"⚠️ Git 提交失败: {result.stderr}")
            return False

    except Exception as e:
        print(f"❌ Git 提交异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Query-to-Wiki 归档器")
    parser.add_argument("--title", help="问答主题/标题")
    parser.add_argument("--definition", help="一句话定义")
    parser.add_argument("--content", default="", help="详细内容")
    parser.add_argument("--sources", help="来源文档，逗号分隔")
    parser.add_argument("--create-evergreen", metavar="TITLE", help="创建新 Evergreen")
    parser.add_argument("--no-commit", action="store_true", help="跳过 Git 提交")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault 目录")

    args = parser.parse_args()

    title = args.create_evergreen or args.title
    if not title:
        print("❌ 需要提供 --title 或 --create-evergreen")
        sys.exit(1)

    definition = args.definition or "从问答提取的核心洞察（待精炼）"
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None

    # 创建 Evergreen
    evergreen_path = create_evergreen(title, definition, args.content, sources)

    # 更新索引
    safe_title = title.replace(" ", "-")
    update_atlas_index(safe_title)

    # 追加日志
    details = [
        f"创建 Evergreen: {title}",
        f"定义: {definition[:50]}..."
    ]
    if sources:
        details.append(f"来源: {', '.join(sources[:3])}")

    append_log("query-to-wiki", title, details)

    # Git 提交
    if not args.no_commit:
        vault_dir = args.vault_dir or get_vault_dir()
        files_to_commit = [evergreen_path]
        mocs = list((vault_dir / "10-Knowledge" / "Atlas").glob("*.md"))
        files_to_commit.extend(mocs)

        commit_msg = f"query-to-wiki: {title} (auto-generated from discussion)"
        auto_git_commit(files_to_commit, commit_msg)
    else:
        print("\n💡 手动提交命令:")
        print(f"  git add {evergreen_path}")
        print(f"  git commit -m 'query-to-wiki: {title}'")

    print("\n" + "=" * 60)
    print("✅ 问答归档完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
