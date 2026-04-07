"""
Link Parser - 解析markdown中的wikilink和其他链接关系
"""

import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class Link:
    """链接结构"""
    source: str  # 源文件note_id
    target: str  # 目标文件note_id
    anchor: str = ""  # 显示文本 [[target|display text]]
    link_type: str = "wikilink"  # wikilink, reference, derived_from
    line_number: int = 0


class LinkParser:
    """链接解析器"""

    # Wikilink正则: [[target]] 或 [[target|display]]
    WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')

    # Markdown链接: [text](url)
    MDLINK_PATTERN = re.compile(r'\[([^\]]+)\]\(([^\)]+)\)')

    # 引用/参考标记: ^ref-xxx 或 ^cite-xxx
    REF_PATTERN = re.compile(r'\^([a-z]+)-([a-z0-9]+)')

    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self._note_id_cache: dict[str, str] = {}  # filename -> note_id

    def parse_file(self, file_path: Path) -> list[Link]:
        """解析单个文件中的所有链接"""
        content = file_path.read_text(encoding="utf-8")
        note_id = self._get_note_id(file_path)

        links = []
        lines = content.split('\n')

        for line_num, line in enumerate(lines, 1):
            # 解析wikilinks
            wikilinks = self._parse_wikilinks(line, note_id, line_num)
            links.extend(wikilinks)

            # 解析引用标记
            refs = self._parse_refs(line, note_id, line_num)
            links.extend(refs)

        return links

    def _parse_wikilinks(self, line: str, source_id: str, line_num: int) -> list[Link]:
        """解析wikilink"""
        links = []
        for match in self.WIKILINK_PATTERN.finditer(line):
            target_raw = match.group(1).strip()
            anchor = match.group(2).strip() if match.group(2) else target_raw

            # 转换为note_id (简化处理，使用slug)
            target_id = self._slug_to_note_id(target_raw)

            link_type = self._infer_link_type(target_raw)

            links.append(Link(
                source=source_id,
                target=target_id,
                anchor=anchor,
                link_type=link_type,
                line_number=line_num
            ))

        return links

    def _parse_refs(self, line: str, source_id: str, line_num: int) -> list[Link]:
        """解析引用标记"""
        links = []
        for match in self.REF_PATTERN.finditer(line):
            ref_type = match.group(1)  # ref, cite
            ref_id = match.group(2)

            link_type = "reference" if ref_type == "cite" else "reference"

            links.append(Link(
                source=source_id,
                target=ref_id,
                anchor="",
                link_type=link_type,
                line_number=line_num
            ))

        return links

    def _slug_to_note_id(self, slug: str) -> str:
        """将 wikilink surface 规范化为 note_id（与 Registry slug 兼容）。

        处理:
        - 去除 heading (#) 和 query (?) 后缀
        - 空格/下划线转连字符
        - 移除非法字符
        - 小写化
        """
        # 去除 heading 和 query
        slug = re.sub(r'[#?].*$', '', slug)
        slug = slug.strip()
        # 空格/下划线转连字符
        slug = re.sub(r'[\s_]+', '-', slug)
        # 移除非法字符（保留连字符）
        slug = re.sub(r'[^\w\-]', '', slug)
        # 合并连续连字符
        slug = re.sub(r'-+', '-', slug)
        # 小写化
        return slug.lower()

    def _infer_link_type(self, target: str) -> str:
        """推断链接类型"""
        target_lower = target.lower()

        if 'derived_from' in target_lower or target.startswith('^'):
            return "derived_from"
        elif 'reference' in target_lower or target.startswith('^ref'):
            return "reference"
        else:
            return "wikilink"

    def _get_note_id(self, file_path: Path) -> str:
        """获取文件的note_id"""
        # 简单实现：使用文件名作为note_id
        # 完整实现需要读取frontmatter
        return file_path.stem

    def parse_directory(self, directory: Path, recursive: bool = True) -> list[Link]:
        """解析目录下所有文件的链接"""
        links = []
        pattern = "**/*.md" if recursive else "*.md"

        for md_file in directory.glob(pattern):
            if any(part.startswith('.') for part in md_file.parts):
                continue
            try:
                file_links = self.parse_file(md_file)
                links.extend(file_links)
            except Exception as e:
                print(f"⚠️ 解析链接失败 {md_file}: {e}")

        return links
