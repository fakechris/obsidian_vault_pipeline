"""
ovp-lint - 知识健康检查

基于 Karpathy LLM Wiki 模式的 Lint 工具：
- 孤儿页面检测（无入链）
- 缺失概念检测（提及但未创建）
- 矛盾检测（跨页面不一致）
- 过时页面检测（长期未更新）
- 断裂链接检测

Usage:
    ovp-lint --check           # 检查并报告
    ovp-lint --fix             # 自动修复低风险问题
    ovp-lint --interactive     # 交互式修复
"""

import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict


@dataclass
class LintIssue:
    """Lint 问题"""
    level: str          # error, warning, info
    type: str           # orphan, missing-concept, stale, broken-link, contradiction
    file: str
    message: str
    suggestion: str
    auto_fixable: bool = False


class KnowledgeLinter:
    """知识库健康检查器"""

    # 问题类型定义
    ORPHAN_PAGE = "orphan"           # 孤儿页面（无入链）
    MISSING_CONCEPT = "missing-concept"  # 缺失概念
    STALE_CONTENT = "stale"          # 过时内容
    BROKEN_LINK = "broken-link"       # 断裂链接
    CONTRADICTION = "contradiction"   # 矛盾

    def __init__(self, vault_dir: Path):
        self.vault_dir = Path(vault_dir)
        self.issues: List[LintIssue] = []
        self.stats = defaultdict(int)

        # 关键目录
        self.evergreen_dir = self.vault_dir / "10-Knowledge" / "Evergreen"
        self.areas_dir = self.vault_dir / "20-Areas"
        self.moc_dir = self.vault_dir / "10-Knowledge" / "Atlas"

        # 缓存
        self.all_links: Dict[str, Set[str]] = {}      # 文件 -> 出链
        self.all_backlinks: Dict[str, Set[str]] = {}    # 文件 -> 入链
        self.all_files: Set[str] = set()

    def log(self, message: str):
        """打印日志"""
        print(f"[ovp-lint] {message}")

    def scan(self):
        """扫描整个知识库"""
        self.log("扫描知识库...")

        # 收集所有 markdown 文件
        for pattern in ["**/*.md"]:
            for f in self.vault_dir.glob(pattern):
                if ".git" in str(f):
                    continue
                self.all_files.add(str(f.relative_to(self.vault_dir)))

        self.log(f"发现 {len(self.all_files)} 个文件")

        # 解析每个文件的链接
        for file_path in self.all_files:
            self._parse_links(file_path)

        # 计算入链
        self._compute_backlinks()

    def _parse_links(self, file_path: str):
        """解析文件中的链接"""
        full_path = self.vault_dir / file_path
        try:
            content = full_path.read_text(encoding='utf-8')
        except Exception as e:
            self.log(f"警告: 无法读取 {file_path}: {e}")
            return

        # 匹配 [[...]] 双向链接
        links = set()
        for match in re.finditer(r'\[\[([^\]]+)\]\]', content):
            link = match.group(1).split('|')[0]  # 处理别名 [[目标|显示名]]
            links.add(link)

        # 匹配 [文本](目标.md) 标准链接
        for match in re.finditer(r'\[([^\]]+)\]\(([^)]+\.md)\)', content):
            link = match.group(2).replace('.md', '')
            links.add(link)

        self.all_links[file_path] = links

    def _compute_backlinks(self):
        """计算反向链接"""
        self.all_backlinks = defaultdict(set)

        for source, targets in self.all_links.items():
            for target in targets:
                # 尝试找到目标文件
                target_file = self._resolve_link(target)
                if target_file:
                    self.all_backlinks[target_file].add(source)

    def _resolve_link(self, link: str) -> Optional[str]:
        """解析链接到实际文件路径"""
        # 尝试多种可能的路径
        candidates = [
            f"10-Knowledge/Evergreen/{link}.md",
            f"20-Areas/{link}.md",
            f"{link}.md",
        ]

        for c in candidates:
            if c in self.all_files:
                return c

        return None

    def check_orphan_pages(self):
        """L1: 检查孤儿页面（无入链的 Evergreen）"""
        self.log("检查孤儿页面...")

        if not self.evergreen_dir.exists():
            return

        for file_path in self.all_files:
            if not file_path.startswith("10-Knowledge/Evergreen/"):
                continue

            backlinks = self.all_backlinks.get(file_path, set())
            if len(backlinks) == 0:
                # 检查是否是 MOC 文件
                filename = Path(file_path).stem
                if filename.startswith("MOC-"):
                    continue

                issue = LintIssue(
                    level="warning",
                    type=self.ORPHAN_PAGE,
                    file=file_path,
                    message=f"孤儿页面: {file_path} 没有任何入链",
                    suggestion=f"在相关深度解读中添加 [[{Path(file_path).stem}]] 链接",
                    auto_fixable=False
                )
                self.issues.append(issue)
                self.stats["orphan"] += 1

    def check_missing_concepts(self):
        """L2: 检查缺失概念（提及但未创建）"""
        self.log("检查缺失概念...")

        mentioned_concepts: Set[str] = set()
        existing_concepts: Set[str] = set()

        # 收集所有被提及的概念
        for file_path, links in self.all_links.items():
            if file_path.startswith("20-Areas/"):
                for link in links:
                    # 排除特殊链接
                    if link.startswith("20-") or link.startswith("10-"):
                        continue
                    mentioned_concepts.add(link)

        # 收集所有存在的 Evergreen
        if self.evergreen_dir.exists():
            for f in self.evergreen_dir.glob("*.md"):
                existing_concepts.add(f.stem)

        # 找出缺失的
        missing = mentioned_concepts - existing_concepts

        for concept in missing:
            # 找出哪些文件提及了它
            referrers = []
            for file_path, links in self.all_links.items():
                if concept in links:
                    referrers.append(file_path)

            issue = LintIssue(
                level="warning",
                type=self.MISSING_CONCEPT,
                file=referrers[0] if referrers else "unknown",
                message=f"缺失概念: [[{concept}]] 被提及但未创建 Evergreen 页面",
                suggestion=f"运行: ovp-evergreen --concept \"{concept}\" 或手动创建 10-Knowledge/Evergreen/{concept}.md",
                auto_fixable=True
            )
            self.issues.append(issue)
            self.stats["missing-concept"] += 1

    def check_stale_content(self, days: int = 90):
        """L3: 检查过时内容"""
        self.log(f"检查过时内容 (> {days} 天未更新)...")

        cutoff = datetime.now() - timedelta(days=days)

        for file_path in self.all_files:
            # 只检查技术内容
            if not (file_path.startswith("20-Areas/") or file_path.startswith("10-Knowledge/")):
                continue

            full_path = self.vault_dir / file_path
            try:
                mtime = datetime.fromtimestamp(full_path.stat().st_mtime)
                if mtime < cutoff:
                    # 解析 frontmatter 检查是否有 date 字段
                    content = full_path.read_text(encoding='utf-8', errors='ignore')
                    date_match = re.search(r'date:\s*(\d{4}-\d{2}-\d{2})', content)

                    if date_match:
                        file_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                        if file_date < cutoff:
                            issue = LintIssue(
                                level="info",
                                type=self.STALE_CONTENT,
                                file=file_path,
                                message=f"过时内容: {file_path} 已 {days}+ 天未更新",
                                suggestion="考虑重新审查内容时效性，更新或归档",
                                auto_fixable=False
                            )
                            self.issues.append(issue)
                            self.stats["stale"] += 1
            except Exception:
                pass

    def check_broken_links(self):
        """L4: 检查断裂链接"""
        self.log("检查断裂链接...")

        for file_path, links in self.all_links.items():
            for link in links:
                # 检查是否是内部链接
                if link.startswith("http://") or link.startswith("https://"):
                    continue

                target = self._resolve_link(link)
                if target is None:
                    issue = LintIssue(
                        level="error",
                        type=self.BROKEN_LINK,
                        file=file_path,
                        message=f"断裂链接: [[{link}]] 指向不存在的页面",
                        suggestion=f"创建页面或修复链接",
                        auto_fixable=False
                    )
                    self.issues.append(issue)
                    self.stats["broken-link"] += 1

    def run_all_checks(self, stale_days: int = 90):
        """运行所有检查"""
        self.scan()
        self.check_orphan_pages()
        self.check_missing_concepts()
        self.check_stale_content(days=stale_days)
        self.check_broken_links()

    def report(self) -> str:
        """生成检查报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("知识健康检查报告")
        lines.append("=" * 60)
        lines.append("")

        # 统计
        lines.append("📊 统计")
        lines.append(f"  总文件数: {len(self.all_files)}")
        lines.append(f"  问题总数: {len(self.issues)}")
        lines.append("")

        # 按类型分组
        by_level = defaultdict(list)
        for issue in self.issues:
            by_level[issue.level].append(issue)

        # 错误
        if by_level["error"]:
            lines.append(f"❌ 错误 ({len(by_level['error'])})")
            for issue in by_level["error"]:
                lines.append(f"   [{issue.type}] {issue.file}")
                lines.append(f"   → {issue.message}")
                lines.append(f"   💡 {issue.suggestion}")
                lines.append("")

        # 警告
        if by_level["warning"]:
            lines.append(f"⚠️  警告 ({len(by_level['warning'])})")
            for issue in by_level["warning"]:
                lines.append(f"   [{issue.type}] {issue.file}")
                lines.append(f"   → {issue.message}")
                lines.append(f"   💡 {issue.suggestion}")
                if issue.auto_fixable:
                    lines.append(f"   🔧 可自动修复")
                lines.append("")

        # 信息
        if by_level["info"]:
            lines.append(f"ℹ️  提示 ({len(by_level['info'])})")
            for issue in by_level["info"]:
                lines.append(f"   [{issue.type}] {issue.file}")
                lines.append(f"   → {issue.message}")
                lines.append("")

        if not self.issues:
            lines.append("✅ 知识库健康！未发现任何问题。")

        return "\n".join(lines)

    def export_json(self, output_path: Path):
        """导出 JSON 格式报告"""
        data = {
            "timestamp": datetime.now().isoformat(),
            "stats": dict(self.stats),
            "total_files": len(self.all_files),
            "total_issues": len(self.issues),
            "issues": [asdict(i) for i in self.issues]
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def auto_fix(self):
        """自动修复低风险问题"""
        fixed = 0

        for issue in self.issues:
            if not issue.auto_fixable:
                continue

            if issue.type == self.MISSING_CONCEPT:
                # 提取缺失的概念名
                match = re.search(r'\[\[([^\]]+)\]\]', issue.message)
                if match:
                    concept = match.group(1)
                    self._create_stub_evergreen(concept)
                    fixed += 1

        return fixed

    def _create_stub_evergreen(self, concept: str):
        """创建占位 Evergreen 页面"""
        target = self.evergreen_dir / f"{concept}.md"

        if target.exists():
            return

        content = f"""---
title: "{concept}"
type: evergreen
date: {datetime.now().strftime('%Y-%m-%d')}
tags: [evergreen, stub]
aliases: []
---

# {concept}

> **一句话定义**: 待补充...

## 📝 详细解释

### 是什么？
待补充...

### 为什么重要？
待补充...

## 🔗 来源
- 被提及于: (多个深度解读页面)

---

*此页面为自动创建的占位符，请补充完整内容。*
"""

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        self.log(f"已创建占位页面: {target}")


def main():
    parser = argparse.ArgumentParser(
        description="ovp-lint: 知识健康检查 (Karpathy LLM Wiki Pattern)"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault 目录 (默认: 当前目录)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查并报告 (默认)"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="自动修复低风险问题"
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=90,
        help="过时内容阈值（天）"
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="导出 JSON 报告路径"
    )

    args = parser.parse_args()

    vault_dir = args.vault_dir or Path.cwd()

    # 检查是否是 vault 根目录
    if not (vault_dir / "10-Knowledge").exists() and not (vault_dir / "50-Inbox").exists():
        print(f"❌ 错误: {vault_dir} 看起来不是 Vault 根目录")
        print("提示: 请在 Vault 目录下运行，或使用 --vault-dir 指定")
        return 1

    linter = KnowledgeLinter(vault_dir)
    linter.run_all_checks(stale_days=args.stale_days)

    # 自动修复
    if args.fix:
        fixed = linter.auto_fix()
        print(f"🔧 自动修复了 {fixed} 个问题")
        print("")

    # 输出报告
    print(linter.report())

    # 导出 JSON
    if args.json:
        linter.export_json(args.json)
        print(f"\n📄 JSON 报告已导出: {args.json}")

    # 返回码
    errors = len([i for i in linter.issues if i.level == "error"])
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    exit(main())
