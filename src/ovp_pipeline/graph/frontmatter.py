"""
Frontmatter Parser - 解析和标准化笔记frontmatter

根据 frontmatter.schema.json 生成 note_id 和标准化元数据
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

from ..identity import canonicalize_note_id
from ..runtime import iter_markdown_files


class NoteType(Enum):
    RAW = "raw"
    DEEP_DIVE = "deep_dive"
    EVERGREEN = "evergreen"
    MOC = "moc"
    DAILY_VIEW = "daily_view"


class NoteStatus(Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    STABLE = "stable"
    ARCHIVED = "archived"


@dataclass
class NoteMetadata:
    """笔记元数据结构 (对应 frontmatter.schema.json)"""
    note_id: str = ""
    title: str = ""
    note_type: str = ""
    created_at: str = ""
    updated_at: str = ""
    day_id: str = ""

    entity_type: str = ""

    # 可选字段
    schema_version: str = "1.0.0"
    vault_id: str = ""
    status: str = "draft"
    timezone: str = "Asia/Tokyo"
    aliases: list = field(default_factory=list)
    tags: list = field(default_factory=list)

    # 来源信息
    source_url: str = ""
    source_domain: str = ""
    source_title: str = ""
    source_authors: list = field(default_factory=list)
    source_published_at: str = ""
    source_language: str = ""
    source_type: str = ""  # article, paper, repo, tweet, video, podcast, doc, manual, other
    source_fingerprint: str = ""

    # 摄取信息
    ingest_id: str = ""
    ingested_at: str = ""
    ingest_method: str = ""  # pinboard, clipper, manual, rss, import, api, other

    # 质量指标
    quality_score: float = 0.0
    confidence_score: float = 0.0
    importance_score: float = 0.0
    freshness_score: float = 0.0

    # 关联信息
    derived_from: list = field(default_factory=list)
    references: list = field(default_factory=list)
    moc_parents: list = field(default_factory=list)
    topic_clusters: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    keywords: list = field(default_factory=list)

    # 图谱提示
    graph_hints: dict = field(default_factory=dict)

    # 路径信息
    path: str = ""

    def to_dict(self) -> dict:
        """转换为字典，用于序列化"""
        d = asdict(self)
        return d

    @classmethod
    def from_file(cls, file_path: Path) -> "NoteMetadata":
        """从文件读取并解析frontmatter"""
        content = file_path.read_text(encoding="utf-8")
        return cls.from_markdown(content, path=str(file_path))

    @classmethod
    def from_markdown(cls, markdown: str, path: str = "") -> "NoteMetadata":
        """从markdown内容解析frontmatter"""
        meta = cls()

        # Tolerate frontmatter wrapped in a ```yaml code fence (~387 such files
        # in production vaults). LinkParser._get_note_id mirrors this so both
        # parsers agree on the source slug — divergence here causes
        # pages_index.slug ≠ page_links.source_slug and outbound-link queries
        # silently return zero.
        text_for_fm = re.sub(r"^```yaml\s*\n", "", markdown, count=1)
        fm_match = re.match(r'^---\n(.*?)\n---', text_for_fm, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            meta._parse_fm_text(fm_text)

        # 设置路径
        if path:
            meta.path = path
            # 从路径推断note_id
            if not meta.note_id:
                meta.note_id = cls._generate_note_id(path)

        # 推断 note_type
        if not meta.note_type:
            meta.note_type = cls._infer_note_type(path, markdown)

        # 推断 day_id
        if not meta.day_id:
            meta.day_id = cls._infer_day_id(path, meta)

        # 推断 title (如果frontmatter没有)
        if not meta.title:
            meta.title = cls._infer_title(path, markdown)

        # 设置时间戳
        if not meta.updated_at:
            meta.updated_at = datetime.now().isoformat()
        if not meta.created_at:
            meta.created_at = meta.updated_at

        return meta

    def _parse_fm_text(self, fm_text: str):
        """解析 frontmatter 文本.

        Uses ``yaml.safe_load`` (instead of the legacy line-by-line
        parser) so that multi-line YAML lists like::

            tags:
              - AI-Agents
              - LLM-Infrastructure

        are recognised as lists.  The previous ad-hoc parser dropped
        every multi-line list and silently produced ``tags=[]``.  This
        was the symptom seen in May 2026's KG audit on the live vault.
        """
        try:
            import yaml
            data = yaml.safe_load(fm_text) or {}
        except Exception:
            # Malformed YAML: fall through with no fields set, just like
            # the old parser would on a syntax error.
            data = {}
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if value is None or value == "":
                continue
            self._set_field(str(key), value)

    # Aliases: markdown frontmatter often uses a slightly different field
    # name than NoteMetadata's canonical attribute.  Keep mappings here
    # so generators can stay close to natural prose.
    _FIELD_ALIASES = {
        "source": "source_url",
        "author": "source_authors",   # scalar string → wrapped to list
        "authors": "source_authors",
    }

    _LIST_FIELDS = frozenset({
        "aliases", "tags", "source_authors", "derived_from",
        "references", "moc_parents", "topic_clusters", "entities", "keywords",
    })

    @staticmethod
    def _coerce_list(value) -> list:
        """Best-effort list coercion for fields declared list-typed."""
        if isinstance(value, list):
            return [str(v).strip() for v in value if v not in (None, "")]
        if isinstance(value, str):
            v = value.strip()
            if not v:
                return []
            # Inline ``[a, b, c]`` form (legacy line-based generators).
            if v.startswith("[") and v.endswith("]"):
                items = v[1:-1].split(",")
                return [i.strip().strip('"').strip("'") for i in items if i.strip()]
            return [v]
        # numbers etc. — coerce to single-item string list
        return [str(value)]

    def _set_field(self, key: str, value):
        """根据 key 设置字段.

        ``value`` can be any YAML-loaded type (str, int, list, dict, bool).
        List-typed fields are coerced via ``_coerce_list``; scalar fields
        are stringified.  Unknown keys are silently ignored (we don't
        want to crash on generator-emitted custom fields).
        """
        # Resolve aliases (e.g. ``source`` → ``source_url``).
        canonical = self._FIELD_ALIASES.get(key, key)

        if canonical in self._LIST_FIELDS:
            setattr(self, canonical, self._coerce_list(value))
            return

        # Scalar fields — coerce non-string values to string.
        sv = str(value).strip() if not isinstance(value, str) else value.strip()
        if canonical == 'title':
            self.title = sv
        elif canonical == 'note_id':
            self.note_id = canonicalize_note_id(sv)
        elif canonical == 'type':
            self.note_type = sv
        elif canonical == 'note_type':
            self.note_type = sv
        elif canonical == 'status':
            self.status = sv
        elif canonical == 'source_url':
            self.source_url = sv
        elif canonical == 'entity_type':
            self.entity_type = sv
        elif canonical == 'date':
            # 兼容旧格式
            self.day_id = sv

    @staticmethod
    def _generate_note_id(path: str) -> str:
        """从文件路径生成 note_id（与 Registry slug 兼容）。

        注意：废弃 hash 后缀，改为直接返回规范化 slug。
        这确保 Graph 模块的 note_id 与 Registry slug 完全一致。
        """
        return canonicalize_note_id(Path(path).stem)[:50]

    @staticmethod
    def _infer_note_type(path: str, markdown: str) -> str:
        """从路径和内容推断note_type"""
        path_lower = path.lower()

        if '/01-raw/' in path_lower:
            return NoteType.RAW.value
        elif '/evergreen/' in path_lower:
            return NoteType.EVERGREEN.value
        elif '/moc/' in path_lower or '/atlas/' in path_lower:
            return NoteType.MOC.value
        elif '/daily/' in path_lower or '/views/' in path_lower:
            return NoteType.DAILY_VIEW.value
        elif '/topics/' in path_lower or '/deep-dive' in path_lower:
            return NoteType.DEEP_DIVE.value
        else:
            # 默认尝试从内容推断
            if '深度解读' in markdown or '一句话定义' in markdown:
                return NoteType.DEEP_DIVE.value
            elif '## 关联概念' in markdown or '## 相关' in markdown:
                return NoteType.EVERGREEN.value

        return NoteType.RAW.value

    @staticmethod
    def _infer_day_id(path: str, meta) -> str:
        """从路径或时间戳推断day_id"""
        # 尝试从文件名提取日期 YYYY-MM-DD
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', path)
        if date_match:
            return date_match.group(1)

        # 尝试从frontmatter的date字段
        if hasattr(meta, 'day_id') and meta.day_id:
            return meta.day_id

        # 默认今天
        return datetime.now().strftime('%Y-%m-%d')

    @staticmethod
    def _infer_title(path: str, markdown: str) -> str:
        """从文件名或内容推断title"""
        # 从文件名推断
        if path:
            # MOC.md -> "MOC"
            # 20-Areas/Tools/MOC.md -> "Tools MOC"
            stem = Path(path).stem
            # 清理特殊字符
            title = re.sub(r'[_-]', ' ', stem)
            # 如果是MOC文件
            if title.lower() == 'moc':
                # 从路径获取_area名
                parts = Path(path).parts
                if len(parts) > 1:
                    area = parts[-2] if parts[-1].lower() == 'moc.md' else parts[-1]
                    area = re.sub(r'[_-]', ' ', area)
                    title = f"{area} MOC"
            return title

        # 从markdown内容推断 (第一个#标题)
        h1_match = re.search(r'^#\s+(.+)$', markdown, re.MULTILINE)
        if h1_match:
            return h1_match.group(1).strip()

        return "Untitled"


class FrontmatterParser:
    """Frontmatter解析器"""

    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir

    def parse_file(self, file_path: Path) -> NoteMetadata:
        """解析单个文件"""
        return NoteMetadata.from_file(file_path)

    def parse_directory(self, directory: Path, recursive: bool = True) -> list[NoteMetadata]:
        """解析目录下的所有markdown文件"""
        results = []
        for md_file in iter_markdown_files(directory, recursive=recursive):
            try:
                meta = self.parse_file(md_file)
                results.append(meta)
            except Exception as e:
                print(f"⚠️ 解析失败 {md_file}: {e}")

        return results

    def upgrade_frontmatter(self, file_path: Path) -> bool:
        """升级现有文件的frontmatter，添加缺失字段"""
        content = file_path.read_text(encoding="utf-8")
        meta = NoteMetadata.from_markdown(content, path=str(file_path))

        # 如果文件没有note_id，添加
        if not self._has_note_id(content):
            new_content = self._inject_note_id(content, meta.note_id)
            file_path.write_text(new_content, encoding="utf-8")
            return True

        return False

    def _has_note_id(self, content: str) -> bool:
        """检查content是否有note_id"""
        return 'note_id:' in content

    def _inject_note_id(self, content: str, note_id: str) -> str:
        """在frontmatter中注入note_id"""
        if '---' not in content:
            # 文件没有frontmatter，创建新的
            new_frontmatter = f"""---
schema_version: "1.0.0"
note_id: {note_id}
---

{content}"""
            return new_frontmatter

        # 找到第一个---后的位置插入
        lines = content.split('\n')
        new_lines = []
        inserted = False

        for i, line in enumerate(lines):
            new_lines.append(line)
            if not inserted and line.strip() == '---':
                # 在第一个---后的下一行插入schema_version和note_id
                new_lines.append(f"schema_version: \"1.0.0\"")
                new_lines.append(f"note_id: {note_id}")
                inserted = True

        return '\n'.join(new_lines)
