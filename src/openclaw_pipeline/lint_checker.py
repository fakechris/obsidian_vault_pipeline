"""
ovp-lint - 知识健康检查

基于 Karpathy LLM Wiki 模式的 Lint 工具：
- L1: 事务状态检查（未完成事务）
- L2: 孤儿页面检测（无入链的 Evergreen，未链接到 MOC）
- L2: 断裂链接检测
- L3: Ingestion 层检查（Clippings 未处理、Pinboard 待处理、重复文件、Manifest）
- L4: Areas 层完整性（未索引的深度解读）
- L4: Git 提交完整性
- L5: Archive 层检查

Usage:
    ovp-lint --check           # 检查并报告
    ovp-lint --fix             # 自动修复低风险问题
    ovp-lint --interactive     # 交互式修复
    ovp-lint --wigs            # 启用 WIGS 5层架构检查
"""

import os
import re
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict

try:
    from .identity import canonicalize_note_id
    from .runtime import resolve_vault_dir
except ImportError:
    from identity import canonicalize_note_id  # type: ignore
    from runtime import resolve_vault_dir  # type: ignore


@dataclass
class LintIssue:
    """Lint 问题"""
    layer: str          # L1-L5 (WIGS layer)
    level: str          # error, warning, info
    type: str           # orphan, missing-concept, stale, broken-link, etc.
    file: str
    message: str
    suggestion: str
    auto_fixable: bool = False


def issue_to_operation_proposal(issue: LintIssue) -> dict[str, object]:
    queue_name = "frontmatter" if "frontmatter" in issue.type else "review"
    return {
        "proposal_type": "lint_issue",
        "queue_name": queue_name,
        "review_required": True,
        "file": issue.file,
        "message": issue.message,
        "suggestion": issue.suggestion,
        "level": issue.level,
    }


class KnowledgeLinter:
    """知识库健康检查器"""

    # 问题类型定义
    ORPHAN_PAGE = "orphan"           # 孤儿页面（无入链）
    MISSING_CONCEPT = "missing-concept"  # 缺失概念
    STALE_CONTENT = "stale"          # 过时内容
    BROKEN_LINK = "broken-link"       # 断裂链接
    CONTRADICTION = "contradiction"   # 矛盾
    INCOMPLETE_TXN = "incomplete-txn"  # 未完成事务
    UNLINKED_EVERGREEN = "unlinked-evergreen"  # 未链接到MOC的Evergreen
    CLIPPINGS_UNPROCESSED = "clippings-unprocessed"  # Clippings未迁移
    PINBOARD_PENDING = "pinboard-pending"  # Pinboard待处理
    DUPLICATE_FILES = "duplicate"  # 重复文件
    UNINDEXED_DEEP = "unindexed-deep"  # 未索引的深度解读
    GIT_UNCOMMITTED = "git-uncommitted"  # Git未提交
    ARCHIVE_OLD = "archive-old"  # 需归档的旧文件

    def __init__(self, vault_dir: Path, wigs_mode: bool = True):
        self.vault_dir = Path(vault_dir)
        self.wigs_mode = wigs_mode
        self.issues: List[LintIssue] = []
        self.stats = defaultdict(int)

        # 关键目录
        self.evergreen_dir = self.vault_dir / "10-Knowledge" / "Evergreen"
        self.areas_dir = self.vault_dir / "20-Areas"
        self.moc_dir = self.vault_dir / "10-Knowledge" / "Atlas"
        self.transactions_dir = self.vault_dir / "60-Logs" / "transactions"
        self.clippings_dir = self.vault_dir / "Clippings"
        self.inbox_dir = self.vault_dir / "50-Inbox"
        self.raw_dir = self.inbox_dir / "01-Raw"
        self.archive_dir = self.vault_dir / "70-Archive"
        self.manifest_file = self.inbox_dir / ".manifest.json"

        # 缓存
        self.all_links: Dict[str, Set[str]] = {}      # 文件 -> 出链
        self.all_backlinks: Dict[str, Set[str]] = {}    # 文件 -> 入链
        self.all_files: Set[str] = set()
        self.moc_links: Set[str] = set()  # 所有MOC中的链接
        self.file_identities: Dict[str, Set[str]] = {}
        self.identity_to_file: Dict[str, str] = {}

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

        self._index_file_identities(file_path, content)

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

    def _index_file_identities(self, file_path: str, content: str) -> None:
        """Index stable identities for a note so lint resolution uses note_id, aliases and titles."""
        identities = {file_path, str(Path(file_path).with_suffix("")), Path(file_path).stem}
        metadata: dict = {}

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml

                    metadata = yaml.safe_load(parts[1]) or {}
                except Exception:
                    metadata = {}

        note_id = metadata.get("note_id")
        title = metadata.get("title")
        aliases = metadata.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]

        for value in [note_id, title, *aliases]:
            if not value:
                continue
            text = str(value).strip()
            identities.add(text)
            normalized = canonicalize_note_id(text)
            if normalized:
                identities.add(normalized)

        self.file_identities[file_path] = identities
        for identity in identities:
            self.identity_to_file.setdefault(identity, file_path)

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
        # 清理链接（移除路径分隔符，只保留文件名部分用于匹配）
        link_clean = link.strip()

        # 处理相对路径 like ../../../10-Knowledge/Evergreen/Claude
        if '/' in link_clean:
            # 提取最后一部分作为文件名
            link_clean = link_clean.split('/')[-1]

        # 移除 .md 扩展名（如果有）
        if link_clean.endswith('.md'):
            link_clean = link_clean[:-3]

        direct_candidates = {
            link.strip(),
            link_clean,
            canonicalize_note_id(link_clean),
            str(Path(link_clean).with_suffix("")),
        }
        for candidate in direct_candidates:
            if candidate and candidate in self.identity_to_file:
                return self.identity_to_file[candidate]

        # 精确匹配: 文件名完全一致
        for f in self.all_files:
            # 取文件名（不含路径和扩展名）
            f_stem = Path(f).stem
            if f_stem == link_clean or f == link_clean:
                return f

        # 前缀匹配: link 是文件名开头 (处理日期前缀的文件)
        # 例如 link="2026-01-30_Polymarket" 匹配 "2026-01-30_Polymarket_深度解读"
        for f in self.all_files:
            f_stem = Path(f).stem
            if f_stem.startswith(link_clean) and len(link_clean) > 10:
                return f

        # 模糊匹配: link 是文件名的子串
        for f in self.all_files:
            f_stem = Path(f).stem
            if link_clean in f_stem and len(link_clean) > 5:
                return f

        return None

    # =============================================================================
    # WIGS Layer 1: Transaction State
    # =============================================================================

    def check_incomplete_transactions(self):
        """L1: 检查未完成事务"""
        self.log("检查 L1: 未完成事务...")

        if not self.transactions_dir.exists():
            self.issues.append(LintIssue(
                layer="L1",
                level="warning",
                type=self.INCOMPLETE_TXN,
                file=str(self.transactions_dir),
                message="事务目录不存在",
                suggestion="这是正常的如果还没有运行过事务",
                auto_fixable=False
            ))
            return

        for txn_file in sorted(self.transactions_dir.glob("*.json")):
            if txn_file.stem == "archive":
                continue
            try:
                txn_data = json.loads(txn_file.read_text())
                if txn_data.get("status") == "in_progress":
                    self.issues.append(LintIssue(
                        layer="L1",
                        level="error",
                        type=self.INCOMPLETE_TXN,
                        file=str(txn_file.relative_to(self.vault_dir)),
                        message=f"未完成事务: {txn_data.get('id')} | {txn_data.get('type')} | {txn_data.get('description')}",
                        suggestion=f"运行: ovp-repair --fix-transactions 或手动完成事务 {txn_data.get('id')}",
                        auto_fixable=False
                    ))
                    self.stats["incomplete_txn"] += 1
            except (json.JSONDecodeError, KeyError):
                continue

    # =============================================================================
    # WIGS Layer 2: Knowledge Graph
    # =============================================================================

    def _collect_moc_links(self):
        """收集所有 MOC 文件中的链接"""
        if self.moc_links:
            return  # Already collected

        for moc_pattern in ["**/MOC.md", "**/*MOC*.md"]:
            for moc_file in self.vault_dir.glob(moc_pattern):
                if ".git" in str(moc_file):
                    continue
                try:
                    content = moc_file.read_text(encoding='utf-8', errors='ignore')
                    # 匹配各种 wiki-link 格式
                    for match in re.finditer(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]', content):
                        self.moc_links.add(match.group(1).strip())
                except Exception:
                    continue

    def check_unlinked_evergreen(self):
        """L2: 检查未链接到 MOC 的 Evergreen"""
        self.log("检查 L2: 未链接到 MOC 的 Evergreen...")

        if not self.evergreen_dir.exists():
            return

        self._collect_moc_links()

        for evergreen_file in self.evergreen_dir.glob("*.md"):
            if evergreen_file.stem.startswith("_"):
                continue  # Skip templates

            rel_path = str(evergreen_file.relative_to(self.vault_dir))
            moc_targets = {self._resolve_link(link) for link in self.moc_links}
            if rel_path not in moc_targets:
                self.issues.append(LintIssue(
                    layer="L2",
                    level="warning",
                    type=self.UNLINKED_EVERGREEN,
                    file=str(evergreen_file.relative_to(self.vault_dir)),
                    message=f"Evergreen 未链接到任何 MOC: {evergreen_file.stem}",
                    suggestion=f"在相关 MOC 中添加 [[{evergreen_file.stem}]] 链接",
                    auto_fixable=False
                ))
                self.stats["unlinked_evergreen"] += 1

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
                    layer="L2",
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

        # 收集所有被提及的概念
        for file_path, links in self.all_links.items():
            if file_path.startswith("20-Areas/"):
                for link in links:
                    # 排除特殊链接
                    if link.startswith("20-") or link.startswith("10-"):
                        continue
                    mentioned_concepts.add(link)

        # 找出缺失的
        missing = {concept for concept in mentioned_concepts if self._resolve_link(concept) is None}

        for concept in missing:
            # 找出哪些文件提及了它
            referrers = []
            for file_path, links in self.all_links.items():
                if concept in links:
                    referrers.append(file_path)

            issue = LintIssue(
                layer="L2",
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
                                layer="L2",
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
                        layer="L2",
                        level="error",
                        type=self.BROKEN_LINK,
                        file=file_path,
                        message=f"断裂链接: [[{link}]] 指向不存在的页面",
                        suggestion=f"创建页面或修复链接",
                        auto_fixable=False
                    )
                    self.issues.append(issue)
                    self.stats["broken-link"] += 1

    # =============================================================================
    # WIGS Layer 3: Ingestion Pipeline
    # =============================================================================

    def check_ingestion_layer(self):
        """L3: 检查 Ingestion 层一致性"""
        self.log("检查 L3: Ingestion Pipeline...")

        # 3.1: Clippings 未迁移
        if self.clippings_dir.exists():
            clippings_md = list(self.clippings_dir.glob("*.md"))
            if clippings_md:
                for f in clippings_md[:5]:  # Show first 5
                    self.issues.append(LintIssue(
                        layer="L3",
                        level="error",
                        type=self.CLIPPINGS_UNPROCESSED,
                        file=str(f.relative_to(self.vault_dir)),
                        message=f"Clippings 有未迁移文件: {f.name}",
                        suggestion="运行: python3 clippings-processor.py 或手动迁移到 50-Inbox/01-Raw/",
                        auto_fixable=False
                    ))
                if len(clippings_md) > 5:
                    self.stats["clippings_unprocessed"] = len(clippings_md)
                else:
                    self.stats["clippings_unprocessed"] = len(clippings_md)

        # 3.2: 重复文件检查（带日期前缀和不带）
        if self.raw_dir.exists():
            for file_path in self.raw_dir.glob("*.md"):
                if file_path.stem.startswith("20") or "_深度解读" in file_path.name:
                    continue  # Skip dated files
                # Check if a dated version exists
                # Pattern: YYYY-MM-DD_filename.md
                date_pattern = f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_{file_path.name}"
                dated_versions = list(self.raw_dir.glob(date_pattern))
                if dated_versions:
                    self.issues.append(LintIssue(
                        layer="L3",
                        level="warning",
                        type=self.DUPLICATE_FILES,
                        file=str(file_path.relative_to(self.vault_dir)),
                        message=f"发现重复文件（带/不带日期前缀）: {file_path.name}",
                        suggestion="确认后删除旧版本",
                        auto_fixable=False
                    ))
                    self.stats["duplicate_files"] += 1

        # 3.3: Manifest 检查
        if not self.manifest_file.exists():
            self.issues.append(LintIssue(
                layer="L3",
                level="error",
                type="missing-manifest",
                file=str(self.manifest_file.relative_to(self.vault_dir)),
                message="Manifest 文件不存在",
                suggestion="这是正常的如果还没有运行过处理流程",
                auto_fixable=False
            ))

    # =============================================================================
    # WIGS Layer 4: Areas/Projects Layer
    # =============================================================================

    def check_unindexed_deep_interpretations(self):
        """L4: 检查 Areas 层未索引的深度解读"""
        self.log("检查 L4: Areas 层未索引的深度解读...")

        areas = ["AI-Research", "Tools", "Investing", "Programming"]
        total_unindexed = 0

        for area in areas:
            topics_dir = self.vault_dir / "20-Areas" / area / "Topics"
            if not topics_dir.exists():
                continue

            # Find all MOC files in this area
            moc_files = list(topics_dir.glob("*MOC*.md")) + list(topics_dir.glob("MOC.md"))
            moc_content = ""
            for moc in moc_files:
                try:
                    moc_content += moc.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    continue

            # Check each deep interpretation file
            for di_file in topics_dir.glob("*_深度解读.md"):
                stem = di_file.stem
                if stem not in moc_content:
                    self.issues.append(LintIssue(
                        layer="L4",
                        level="warning",
                        type=self.UNINDEXED_DEEP,
                        file=str(di_file.relative_to(self.vault_dir)),
                        message=f"深度解读未在 {area} MOC 中索引: {stem}",
                        suggestion=f"在 MOC 中添加 [[{stem}]] 链接",
                        auto_fixable=False
                    ))
                    total_unindexed += 1

        self.stats["unindexed_deep"] = total_unindexed

    def check_git_integrity(self):
        """L4: 检查 Git 提交完整性"""
        self.log("检查 L4: Git 提交完整性...")

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.vault_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.stdout.strip():
                # Has uncommitted changes
                for line in result.stdout.strip().split("\n")[:5]:
                    self.issues.append(LintIssue(
                        layer="L4",
                        level="warning",
                        type=self.GIT_UNCOMMITTED,
                        file=line,
                        message="存在未提交的修改",
                        suggestion="运行: git add . && git commit -m '描述'",
                        auto_fixable=False
                    ))
                self.stats["git_uncommitted"] = len(result.stdout.strip().split("\n"))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # Not a git repo or git not available

    # =============================================================================
    # WIGS Layer 5: Archive Layer
    # =============================================================================

    def check_archive_layer(self):
        """L5: 检查归档层"""
        self.log("检查 L5: Archive 层...")

        if not self.archive_dir.exists():
            self.issues.append(LintIssue(
                layer="L5",
                level="info",
                type="no-archive",
                file=str(self.archive_dir.relative_to(self.vault_dir)),
                message="归档目录不存在",
                suggestion="这是正常的",
                auto_fixable=False
            ))
            return

        archive_count = len(list(self.archive_dir.rglob("*.*")))
        self.log(f"Archive 包含 {archive_count} 个文件")

        # Check for files older than 1 year
        cutoff = datetime.now() - timedelta(days=365)
        old_files = []
        for f in self.archive_dir.rglob("*"):
            if f.is_file():
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        old_files.append(f)
                except Exception:
                    continue

        if old_files:
            self.issues.append(LintIssue(
                layer="L5",
                level="info",
                type=self.ARCHIVE_OLD,
                file=str(self.archive_dir.relative_to(self.vault_dir)),
                message=f"Archive 中有 {len(old_files)} 个文件超过1年未访问",
                suggestion="考虑迁移到冷存储",
                auto_fixable=False
            ))
            self.stats["archive_old"] = len(old_files)

    def run_all_checks(self, stale_days: int = 90):
        """运行所有检查"""
        self.scan()

        if self.wigs_mode:
            # WIGS 5层架构检查
            self.check_incomplete_transactions()
            self.check_unlinked_evergreen()
            self.check_ingestion_layer()
            self.check_unindexed_deep_interpretations()
            self.check_git_integrity()
            self.check_archive_layer()

        self.check_orphan_pages()
        self.check_missing_concepts()
        self.check_stale_content(days=stale_days)
        self.check_broken_links()

    def report(self) -> str:
        """生成检查报告"""
        lines = []
        lines.append("=" * 60)
        if self.wigs_mode:
            lines.append("WIGS 知识健康检查报告 (5层架构)")
        else:
            lines.append("知识健康检查报告")
        lines.append("=" * 60)
        lines.append("")

        # 统计
        lines.append("📊 统计")
        lines.append(f"  总文件数: {len(self.all_files)}")
        lines.append(f"  问题总数: {len(self.issues)}")
        if self.wigs_mode:
            lines.append("  模式: WIGS 5层架构检查")
        lines.append("")

        # 按层级分组
        if self.wigs_mode:
            by_layer = defaultdict(list)
            for issue in self.issues:
                by_layer[issue.layer].append(issue)

            for layer in sorted(by_layer.keys()):
                layer_issues = by_layer[layer]
                layer_errors = [i for i in layer_issues if i.level == "error"]
                layer_warnings = [i for i in layer_issues if i.level == "warning"]

                if layer_errors or layer_warnings:
                    lines.append(f"{layer} 层:")
                    if layer_errors:
                        lines.append(f"  ❌ 错误 ({len(layer_errors)})")
                        for issue in layer_errors[:10]:
                            lines.append(f"     [{issue.type}] {issue.file}")
                            lines.append(f"     → {issue.message}")
                    if layer_warnings:
                        lines.append(f"  ⚠️  警告 ({len(layer_warnings)})")
                        for issue in layer_warnings[:10]:
                            lines.append(f"     [{issue.type}] {issue.file}")
                            lines.append(f"     → {issue.message}")
                    lines.append("")

        # 按类型分组（Legacy 模式）
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
        description="ovp-lint: 知识健康检查 (Karpathy LLM Wiki Pattern + WIGS)"
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
    parser.add_argument(
        "--wigs",
        action="store_true",
        default=True,
        help="启用 WIGS 5层架构检查 (默认开启)"
    )
    parser.add_argument(
        "--no-wigs",
        action="store_true",
        help="禁用 WIGS 5层架构检查"
    )

    args = parser.parse_args()

    vault_dir = resolve_vault_dir(args.vault_dir)

    # 检查是否是 vault 根目录
    if not (vault_dir / "10-Knowledge").exists() and not (vault_dir / "50-Inbox").exists():
        print(f"❌ 错误: {vault_dir} 看起来不是 Vault 根目录")
        print("提示: 请在 Vault 目录下运行，或使用 --vault-dir 指定")
        return 1

    wigs_mode = not args.no_wigs
    linter = KnowledgeLinter(vault_dir, wigs_mode=wigs_mode)
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
