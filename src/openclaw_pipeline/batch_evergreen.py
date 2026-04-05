"""
ovp-batch-evergreen - 批量创建常青笔记

从深度解读中提取核心概念，批量创建 Evergreen 笔记

Usage:
    ovp-batch-evergreen --candidates ./evergreen-candidates.json --limit 20
    ovp-batch-evergreen --dry-run --limit 10

Prerequisites:
    需要先用 extract-evergreen-candidates.py 生成候选清单:
        python3 extract-evergreen-candidates.py > evergreen-candidates.json
"""

import json
import re
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


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


def load_candidates(candidates_path: Path) -> dict:
    """加载候选清单"""
    with open(candidates_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_definitions(concept: str, files_data: list) -> list:
    """从所有文件提取特定概念的定义"""
    definitions = []
    concept_pattern = re.compile(re.escape(concept), re.IGNORECASE)

    for file_info in files_data:
        filepath = file_info.get('path')
        if not filepath:
            continue

        try:
            content = Path(filepath).read_text(encoding='utf-8')

            if not concept_pattern.search(content):
                continue

            # 提取一句话定义
            one_sentence = None
            match = re.search(
                r'[>#]\s*\*\*一句话定义\*\*[:：]\s*(.+?)(?:\n|\r|$)',
                content,
                re.DOTALL
            )
            if match:
                one_sentence = match.group(1).strip()[:250]

            definitions.append({
                'source': file_info.get('file', 'unknown'),
                'one_sentence': one_sentence,
                'related': content[:500]
            })

        except Exception:
            continue

    return definitions


def create_evergreen_note(concept: str, filename: str, definitions: list, existing: set) -> Optional[str]:
    """创建常青笔记内容"""
    if filename in existing:
        return None

    # 合并定义
    best_definition = None
    for d in definitions:
        if d.get('one_sentence') and len(d['one_sentence']) > 20:
            best_definition = d['one_sentence']
            break

    if not best_definition:
        best_definition = f"{concept} is a core concept in AI/Agent systems."

    # 生成 aliases
    aliases = [concept]
    if '-' in concept:
        aliases.append(concept.replace('-', ' '))
    if ' ' in concept:
        aliases.append(concept.replace(' ', '-'))

    aliases_str = ', '.join([f'"{a}"' for a in aliases[:3]])

    # 清理 concept 用于 tag
    tag_name = concept.lower().replace(' ', '-').replace('-', '')[:20]

    content = f"""---
title: "{concept}"
type: evergreen
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [evergreen, {tag_name}]
aliases: [{aliases_str}]
---

# {concept}

> **一句话定义**: {best_definition}

---

## 核心思想

{concept} 是AI系统架构中的关键组件/方法论。

## 关键洞察

- 洞察1：来自 {len(definitions)} 篇深度解读的综合
- 洞察2：在多个Agent/Harness场景中重复出现
- 洞察3：与其他核心概念紧密关联

## 来源引用

"""

    for d in definitions[:5]:
        source_name = d.get('source', 'unknown').replace('_深度解读.md', '')
        content += f"- [[{source_name}]]\n"

    content += f"""
---

*自动生成于 {datetime.now().strftime("%Y-%m-%d")}，从 {len(definitions)} 篇深度解读中提取*
"""
    return content


class BatchEvergreenCreator:
    """批量 Evergreen 创建器"""

    def __init__(self, vault_dir: Path, dry_run: bool = False):
        self.vault_dir = vault_dir
        self.dry_run = dry_run
        self.evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"
        self.created_count = 0
        self.skipped_count = 0

    def log(self, level: str, message: str):
        symbols = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅", "dry": "🔍"}
        print(f"{symbols.get(level, '•')} {message}")

    def get_existing_evergreens(self) -> set:
        """获取已存在的 Evergreen"""
        if not self.evergreen_dir.exists():
            return set()
        return {f.name for f in self.evergreen_dir.glob("*.md")}

    def run(self, candidates_path: Path, limit: int = 20):
        """运行批量创建"""
        print("=" * 60)
        print("批量创建常青笔记 (Evergreen Notes)")
        print("=" * 60)
        print(f"Vault: {self.vault_dir}")
        print(f"候选清单: {candidates_path}")
        print(f"限制: {limit} 个")
        print("")

        # 加载数据
        try:
            data = load_candidates(candidates_path)
        except FileNotFoundError:
            self.log("error", f"文件不存在: {candidates_path}")
            return
        except json.JSONDecodeError as e:
            self.log("error", f"JSON 解析失败: {e}")
            return

        candidates = data.get('candidates', [])
        files_data = data.get('files', [])

        self.log("info", f"加载了 {len(candidates)} 个候选概念")
        print("")

        # 去重映射
        concept_mapping = {
            'Claude Code': 'Claude-Code',
            'claude-code': 'Claude-Code',
            'agent-harness': 'Agent-Harness',
            'Evergreen/Agent-Harness': 'Agent-Harness',
            'Context Engineering': 'Context-Engineering',
            'context-engineering': 'Context-Engineering',
            'mcp': 'MCP-Protocol',
            'MCP': 'MCP-Protocol',
        }

        existing = self.get_existing_evergreens()

        for i, candidate in enumerate(candidates[:limit]):
            concept = candidate.get('concept', '')
            filename = candidate.get('suggested_filename', f'{concept}.md')

            # 应用去重映射
            if concept in concept_mapping:
                filename = f"{concept_mapping[concept]}.md"

            # 清理文件名
            filename = filename.replace('(', '').replace(')', '').replace('"', '')

            if filename in existing:
                self.log("warn", f"{i+1}. {filename} 已存在，跳过")
                self.skipped_count += 1
                continue

            # 提取定义
            definitions = extract_definitions(concept, files_data)

            if len(definitions) < 2:
                self.log("warn", f"{i+1}. {concept}: 来源不足 ({len(definitions)})，跳过")
                self.skipped_count += 1
                continue

            # 创建内容
            content = create_evergreen_note(concept, filename, definitions, existing)

            if not content:
                self.skipped_count += 1
                continue

            if self.dry_run:
                self.log("dry", f"{i+1}. 创建: {filename} (来自 {len(definitions)} 个来源)")
            else:
                filepath = self.evergreen_dir / filename
                filepath.write_text(content, encoding='utf-8')
                self.log("success", f"{i+1}. 创建: {filename} (来自 {len(definitions)} 个来源)")
                existing.add(filename)

            self.created_count += 1

        print("")
        print("=" * 60)
        print(f"完成: 创建 {self.created_count}, 跳过 {self.skipped_count}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="批量创建常青笔记")
    parser.add_argument("--candidates", type=Path, required=True, help="候选清单 JSON 文件")
    parser.add_argument("--limit", type=int, default=20, help="处理限制")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault 目录")

    args = parser.parse_args()

    vault_dir = args.vault_dir or get_vault_dir()
    creator = BatchEvergreenCreator(vault_dir, dry_run=args.dry_run)
    creator.run(args.candidates, limit=args.limit)


if __name__ == "__main__":
    main()
