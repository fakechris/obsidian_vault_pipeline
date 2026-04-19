"""
ovp-query-to-wiki - Query-to-Wiki 归档器

Karpathy LLM Wiki 模式: 好答案应该归档回知识库。

当前实现遵守统一契约：
- Evergreen 文件写入 10-Knowledge/Evergreen
- registry 是概念真相源
- Atlas 通过 registry-aware writer 刷新
- pipeline 事件写入 60-Logs/pipeline.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from .auto_moc_updater import MOCUpdater, PipelineLogger
    from .concept_registry import ConceptEntry, ConceptRegistry, STATUS_CANDIDATE
    from .identity import canonicalize_note_id
    from .runtime import VaultLayout, resolve_vault_dir
except ImportError:
    from auto_moc_updater import MOCUpdater, PipelineLogger  # type: ignore
    from concept_registry import ConceptEntry, ConceptRegistry, STATUS_CANDIDATE  # type: ignore
    from identity import canonicalize_note_id  # type: ignore
    from runtime import VaultLayout, resolve_vault_dir  # type: ignore


def create_evergreen(
    vault_dir: Path,
    title: str,
    definition: str,
    content: str = "",
    sources: list[str] | None = None,
) -> tuple[Path, str]:
    """Create or overwrite an evergreen note using the canonical identity."""
    layout = VaultLayout.from_vault(vault_dir)
    slug = canonicalize_note_id(title)
    date_str = datetime.now().strftime("%Y-%m-%d")
    evergreen_path = layout.evergreen_dir / f"{slug}.md"
    layout.evergreen_dir.mkdir(parents=True, exist_ok=True)

    md_content = f"""---
title: "{title}"
note_id: "{slug}"
type: evergreen
date: {date_str}
tags: [evergreen, auto-generated]
aliases: ["{title}", "{slug}"]
"""
    if sources:
        md_content += "sources:\n"
        for source in sources:
            md_content += f'  - "{source}"\n'
    md_content += f"""---

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
    return evergreen_path, slug


def sync_registry(vault_dir: Path, slug: str, title: str, definition: str) -> ConceptRegistry:
    """Register the evergreen in the canonical concept registry."""
    registry = ConceptRegistry(vault_dir).load()
    entry = registry.find_by_slug(slug)

    if entry:
        entry.title = title
        entry.definition = definition
        if title not in entry.aliases:
            entry.aliases.append(title)
        if entry.status == STATUS_CANDIDATE:
            entry.status = "active"
            entry.review_state = "promoted"
        registry.upsert_entry(entry)
    else:
        registry.upsert_entry(ConceptEntry(
            slug=slug,
            title=title,
            aliases=[title],
            definition=definition,
            area="general",
            status="active",
        ))

    registry.save()
    print(f"✅ 同步 registry: {registry.registry_path}")
    return registry


def refresh_atlas(vault_dir: Path) -> Path:
    """Refresh Atlas through the registry-aware MOC writer."""
    layout = VaultLayout.from_vault(vault_dir)
    updater = MOCUpdater(layout.vault_dir, PipelineLogger(layout.pipeline_log))
    updater.update_atlas_from_registry(dry_run=False)
    atlas_path = layout.atlas_dir / "Atlas-Index.md"
    print(f"✅ 更新 Atlas: {atlas_path}")
    return atlas_path


def append_pipeline_log(vault_dir: Path, operation: str, subject: str, details: list[str]) -> Path:
    """Append a structured event to the shared pipeline log."""
    layout = VaultLayout.from_vault(vault_dir)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": operation,
        "subject": subject,
        "details": details,
    }
    layout.pipeline_log.parent.mkdir(parents=True, exist_ok=True)
    with open(layout.pipeline_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"✅ 追加 pipeline 日志: {operation} | {subject}")
    return layout.pipeline_log


def auto_git_commit(vault_dir: Path, files: list[Path], message: str) -> bool:
    """Commit touched files without reaching outside the resolved vault."""
    try:
        for f in files:
            subprocess.run(["git", "add", str(f)], capture_output=True, cwd=str(vault_dir))

        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=str(vault_dir),
        )
        if result.returncode == 0:
            print(f"✅ Git 提交: {message}")
            return True
        print(f"⚠️ Git 提交失败: {result.stderr}")
        return False
    except Exception as e:
        print(f"❌ Git 提交异常: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Query-to-Wiki 归档器")
    parser.add_argument("--title", help="问答主题/标题")
    parser.add_argument("--definition", help="一句话定义")
    parser.add_argument("--content", default="", help="详细内容")
    parser.add_argument("--sources", help="来源文档，逗号分隔")
    parser.add_argument("--create-evergreen", metavar="TITLE", help="创建新 Evergreen")
    parser.add_argument("--no-commit", action="store_true", help="跳过 Git 提交")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault 目录")

    args = parser.parse_args()
    vault_dir = resolve_vault_dir(args.vault_dir)

    title = args.create_evergreen or args.title
    if not title:
        print("❌ 需要提供 --title 或 --create-evergreen")
        return 1

    definition = args.definition or "从问答提取的核心洞察（待精炼）"
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None

    evergreen_path, slug = create_evergreen(vault_dir, title, definition, args.content, sources)
    registry = sync_registry(vault_dir, slug, title, definition)
    atlas_path = refresh_atlas(vault_dir)

    details = [
        f"创建 Evergreen: {title} ({slug})",
        f"定义: {definition[:50]}...",
    ]
    if sources:
        details.append(f"来源: {', '.join(sources[:3])}")
    log_path = append_pipeline_log(vault_dir, "query-to-wiki", title, details)

    if not args.no_commit:
        files_to_commit = [
            evergreen_path,
            registry.registry_path,
            registry.alias_index_path,
            atlas_path,
            log_path,
        ]
        commit_msg = f"query-to-wiki: {title} (auto-generated from discussion)"
        auto_git_commit(vault_dir, files_to_commit, commit_msg)
    else:
        print("\n💡 手动提交命令:")
        print(f"  git -C {vault_dir} add {evergreen_path} {registry.registry_path} {registry.alias_index_path} {atlas_path} {log_path}")
        print(f"  git -C {vault_dir} commit -m 'query-to-wiki: {title}'")

    print("\n" + "=" * 60)
    print("✅ 问答归档完成")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
