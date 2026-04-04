"""
ovp-query - 查询知识库并归档回写

基于 Karpathy LLM Wiki 模式：Query → Output → 回写 wiki → 下次 Query 可用
形成知识复利闭环。

Usage:
    ovp-query "对比 AI Agent 和 RAG 的架构差异"
    ovp-query "什么是注意力机制" --save-to "20-Areas/Queries/"
    ovp-query "2025年AI趋势分析" --output-format slides  # 生成 Marp 幻灯片
"""

import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Set, Optional
from dataclasses import dataclass

try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False


@dataclass
class SearchResult:
    """搜索结果"""
    file: str
    title: str
    relevance: float
    excerpt: str


class VaultQuerier:
    """知识库查询器"""

    def __init__(self, vault_dir: Path):
        self.vault_dir = Path(vault_dir)
        self.api_key = os.getenv("AUTO_VAULT_API_KEY")
        self.api_base = os.getenv("AUTO_VAULT_API_BASE")
        # 支持多种模型格式：minimax/xxx 或 openai/xxx
        raw_model = os.getenv("AUTO_VAULT_MODEL", "minimax/MiniMax-M2.5")
        # 如果模型名没有 / 前缀，添加 openai/
        if "/" not in raw_model:
            self.model = f"openai/{raw_model}"
        else:
            self.model = raw_model

        # 关键目录
        self.evergreen_dir = self.vault_dir / "10-Knowledge" / "Evergreen"
        self.areas_dir = self.vault_dir / "20-Areas"
        self.moc_dir = self.vault_dir / "10-Knowledge" / "Atlas"

        # 索引缓存
        self.all_pages: Dict[str, dict] = {}

    def log(self, message: str):
        """打印日志"""
        print(f"[ovp-query] {message}")

    def build_index(self) -> Dict[str, dict]:
        """构建知识库索引"""
        self.log("构建知识库索引...")

        # 扫描所有 markdown 文件
        for pattern in ["10-Knowledge/**/*.md", "20-Areas/**/*.md"]:
            for f in self.vault_dir.glob(pattern):
                if ".git" in str(f):
                    continue

                rel_path = str(f.relative_to(self.vault_dir))

                try:
                    content = f.read_text(encoding='utf-8')

                    # 解析 frontmatter
                    frontmatter = self._parse_frontmatter(content)
                    title = frontmatter.get('title', f.stem)

                    # 提取摘要（前 500 字符）
                    excerpt = self._extract_excerpt(content)

                    self.all_pages[rel_path] = {
                        'path': rel_path,
                        'title': title,
                        'type': frontmatter.get('type', 'unknown'),
                        'tags': frontmatter.get('tags', []),
                        'excerpt': excerpt,
                        'content': content[:2000]  # 保留前 2000 字符用于搜索
                    }
                except Exception as e:
                    self.log(f"警告: 无法索引 {rel_path}: {e}")

        self.log(f"索引完成: {len(self.all_pages)} 个页面")
        return self.all_pages

    def _parse_frontmatter(self, content: str) -> dict:
        """解析 YAML frontmatter"""
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    return yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
        return {}

    def _extract_excerpt(self, content: str, max_len: int = 200) -> str:
        """提取内容摘要"""
        # 移除 frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2]

        # 移除 markdown 标记
        text = re.sub(r'[#\*\[\]\(\)\|`]', '', content)
        text = re.sub(r'\s+', ' ', text).strip()

        return text[:max_len] + "..." if len(text) > max_len else text

    def search(self, query: str, top_k: int = 10) -> List[SearchResult]:
        """
        搜索知识库
        优先使用 qmd（如果安装并配置），否则使用内置 BM25 近似搜索
        """
        # 尝试使用 qmd（如果可用）
        qmd_results = self._search_with_qmd(query, top_k)
        if qmd_results:
            return qmd_results

        # 回退到内置搜索
        return self._search_builtin(query, top_k)

    def _search_with_qmd(self, query: str, top_k: int) -> Optional[List[SearchResult]]:
        """尝试使用 qmd 搜索引擎"""
        try:
            # 检查 qmd 是否可用
            import subprocess
            result = subprocess.run(
                ["qmd", "search", query, "--limit", str(top_k)],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return None

            # 解析 qmd 输出
            results = []
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    parts = line.split('|')
                    if len(parts) >= 3:
                        file_path = parts[0].strip()
                        score = float(parts[1].strip())
                        title = parts[2].strip()

                        # 获取摘录
                        page = self.all_pages.get(file_path, {})
                        excerpt = page.get('excerpt', '')

                        results.append(SearchResult(
                            file=file_path,
                            title=title,
                            relevance=score,
                            excerpt=excerpt
                        ))

            if results:
                self.log(f"使用 qmd 搜索引擎，找到 {len(results)} 个结果")
                return results

        except (subprocess.SubprocessError, FileNotFoundError, ValueError):
            pass

        return None

    def _search_builtin(self, query: str, top_k: int = 10) -> List[SearchResult]:
        """
        内置 BM25 近似搜索（qmd 不可用时回退）
        """
        self.log("使用内置搜索引擎 (qmd 未配置或不可用)")

        query_terms = set(query.lower().split())
        scores = []

        for path, page in self.all_pages.items():
            score = 0.0

            # 标题匹配（高权重）
            title_lower = page['title'].lower()
            for term in query_terms:
                if term in title_lower:
                    score += 10.0

            # 内容匹配
            content_lower = page['content'].lower()
            for term in query_terms:
                count = content_lower.count(term)
                score += count * 1.0

            # 标签匹配
            for tag in page.get('tags', []):
                for term in query_terms:
                    if term in tag.lower():
                        score += 5.0

            if score > 0:
                scores.append((path, score, page))

        # 排序取 top_k
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for path, score, page in scores[:top_k]:
            results.append(SearchResult(
                file=path,
                title=page['title'],
                relevance=score,
                excerpt=page['excerpt']
            ))

        return results

    def query(self, question: str, search_results: List[SearchResult]) -> dict:
        """
        使用 LLM 回答问题
        返回包含 answer、sources、related_concepts 的字典
        """
        if not LITELLM_AVAILABLE:
            return {
                'answer': "错误: litellm 未安装，无法使用 LLM 查询",
                'sources': [],
                'related_concepts': []
            }

        # 构建上下文
        context_parts = []
        for r in search_results[:5]:  # 取前 5 个结果
            page = self.all_pages.get(r.file, {})
            context_parts.append(f"""
来源: {r.title} ({r.file})
摘要: {r.excerpt}
""")

        context = "\n---\n".join(context_parts)

        # 构建 prompt
        prompt = f"""你是一个专业的知识库助手。基于以下知识库内容回答问题。

知识库内容:
{context}

用户问题: {question}

请用中文回答，并遵循以下格式:

1. 首先给出**一句话总结**（核心结论）
2. 然后给出**详细回答**，包含:
   - 关键概念解释
   - 对比分析（如果是比较类问题）
   - 相关技术细节
3. 列出**参考来源**（使用 [[文件名]] 格式）
4. 列出**相关概念**（可以链接到 Evergreen 的概念）

回答:"""

        try:
            response = litellm.completion(
                model=self.model,
                api_key=self.api_key,
                api_base=self.api_base,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000
            )

            answer = response.choices[0].message.content

            # 提取相关信息
            sources = [r.file for r in search_results[:5]]
            related_concepts = self._extract_concepts(answer)

            return {
                'answer': answer,
                'sources': sources,
                'related_concepts': related_concepts
            }

        except Exception as e:
            return {
                'answer': f"查询失败: {e}",
                'sources': [],
                'related_concepts': []
            }

    def _extract_concepts(self, text: str) -> List[str]:
        """从回答中提取概念"""
        # 匹配 [[...]] 双向链接
        concepts = set()
        for match in re.finditer(r'\[\[([^\]]+)\]\]', text):
            concepts.add(match.group(1).split('|')[0])
        return list(concepts)

    def save_to_wiki(
        self,
        question: str,
        result: dict,
        output_dir: Path,
        output_format: str = "markdown"
    ) -> Path:
        """
        将查询结果保存到 wiki
        形成知识复利闭环
        """
        # 生成文件名
        safe_name = re.sub(r'[^\w\s-]', '', question)[:50]
        safe_name = re.sub(r'\s+', '_', safe_name)

        timestamp = datetime.now().strftime('%Y-%m-%d')
        file_name = f"{safe_name}.md"

        # 确保目录存在
        target_dir = output_dir / datetime.now().strftime('%Y-%m')
        target_dir.mkdir(parents=True, exist_ok=True)

        target_file = target_dir / file_name

        # 生成内容
        if output_format == "slides":
            content = self._generate_marp_slides(question, result)
        else:
            content = self._generate_markdown(question, result)

        # 写入文件
        target_file.write_text(content, encoding='utf-8')
        self.log(f"已保存到: {target_file}")

        # 更新 MOC-Queries
        self._update_moc_queries(question, target_file, result)

        return target_file

    def _generate_markdown(self, question: str, result: dict) -> str:
        """生成 Markdown 格式输出"""
        timestamp = datetime.now().strftime('%Y-%m-%d')
        sources_links = "\n".join([f"- [[{s.replace('.md', '')}]]" for s in result['sources']])
        concepts_links = "\n".join([f"- [[{c}]]" for c in result['related_concepts'][:10]])

        return f"""---
title: "{question}"
date: {timestamp}
type: query
query: "{question}"
sources: {json.dumps(result['sources'], ensure_ascii=False)}
---

# {question}

> **一句话总结**: {self._extract_summary(result['answer'])}

## 详细回答

{result['answer']}

## 🔗 参考来源

{sources_links if sources_links else "- (自动生成，请参考原始知识库)"}

## 🌳 相关概念

{concepts_links if concepts_links else "- (未提取到相关概念)"}

---

*此页面由 ovp-query 自动生成于 {timestamp}*
*遵循知识复利原则：查询 → 回答 → 归档 → 下次可复用*
"""

    def _extract_summary(self, answer: str) -> str:
        """从回答中提取一句话总结"""
        lines = answer.split('\n')
        for line in lines[:5]:
            if '总结' in line or '结论' in line:
                # 清理标记
                line = re.sub(r'\*\*?|\[|\]', '', line)
                return line.strip()[:100]
        return "(详见正文)"

    def _generate_marp_slides(self, question: str, result: dict) -> str:
        """生成 Marp 幻灯片格式"""
        timestamp = datetime.now().strftime('%Y-%m-%d')

        # 简化回答到幻灯片
        slides_content = self._answer_to_slides(result['answer'])

        return f"""---
marp: true
theme: default
title: {question}
---

# {question}

生成时间: {timestamp}

---

{slides_content}

---

# 参考来源

{chr(10).join([f"- {s}" for s in result['sources']])}

---

*Generated by ovp-query*
"""

    def _answer_to_slides(self, answer: str) -> str:
        """将回答转换为幻灯片格式"""
        # 简单分割成多个 slide
        paragraphs = [p.strip() for p in answer.split('\n\n') if p.strip()]
        slides = []

        for i, para in enumerate(paragraphs[:10]):  # 最多 10 页
            if len(para) > 100:
                slides.append(f"## 要点 {i+1}\n\n{para[:300]}...")
            else:
                slides.append(f"## {para}")

        return "\n\n---\n\n".join(slides)

    def _update_moc_queries(self, question: str, target_file: Path, result: dict):
        """更新 MOC-Queries.md"""
        moc_file = self.moc_dir / "MOC-Queries.md"

        timestamp = datetime.now().strftime('%Y-%m-%d')
        # 确保 target_file 是绝对路径或相对于 vault_dir
        target_file = Path(target_file)
        if not target_file.is_absolute():
            target_file = self.vault_dir / target_file
        rel_path = str(target_file.relative_to(self.vault_dir))

        entry = f"- [[{rel_path.replace('.md', '')}|{question}]] - {timestamp}\n"

        if moc_file.exists():
            content = moc_file.read_text(encoding='utf-8')
        else:
            content = """---
title: "MOC - 查询归档"
date: 2026-04-03
type: moc
---

# MOC - 查询归档

所有通过 ovp-query 生成的查询结果。

---

## 查询历史

"""

        # 添加到列表
        content = content + entry

        moc_file.parent.mkdir(parents=True, exist_ok=True)
        moc_file.write_text(content, encoding='utf-8')
        self.log(f"已更新 MOC-Queries.md")


def main():
    parser = argparse.ArgumentParser(
        description="ovp-query: 查询知识库并归档回写 (Karpathy LLM Wiki Pattern)"
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="查询问题（例如：对比 AI Agent 和 RAG）"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault 目录 (默认: 当前目录)"
    )
    parser.add_argument(
        "--save-to",
        type=Path,
        default=None,
        help="保存到指定目录 (默认: 20-Areas/Queries/)"
    )
    parser.add_argument(
        "--output-format",
        choices=["markdown", "slides"],
        default="markdown",
        help="输出格式: markdown 或 marp slides (默认: markdown)"
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="搜索返回的相关页面数量 (默认: 10)"
    )

    args = parser.parse_args()

    vault_dir = args.vault_dir or Path.cwd()

    # 检查是否是 vault 根目录
    if not (vault_dir / "10-Knowledge").exists() and not (vault_dir / "50-Inbox").exists():
        print(f"❌ 错误: {vault_dir} 看起来不是 Vault 根目录")
        return 1

    # 如果没有提供问题，提示输入
    question = args.question
    if not question:
        question = input("请输入查询问题: ").strip()

    if not question:
        print("❌ 错误: 需要提供查询问题")
        return 1

    querier = VaultQuerier(vault_dir)

    # 构建索引
    querier.build_index()

    # 搜索
    querier.log(f"搜索: {question}")
    results = querier.search(question, top_k=args.top_k)

    if not results:
        print("❌ 未找到相关内容")
        return 1

    print(f"\n🔍 找到 {len(results)} 个相关页面:")
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. {r.title} ({r.file})")
        print(f"     {r.excerpt[:80]}...")

    # 查询
    querier.log("使用 LLM 生成回答...")
    answer = querier.query(question, results)

    print(f"\n💡 回答:\n")
    print(answer['answer'])

    # 保存到 wiki
    save_dir = args.save_to or (vault_dir / "20-Areas" / "Queries")

    querier.log("归档到 wiki...")
    saved_file = querier.save_to_wiki(
        question,
        answer,
        save_dir,
        output_format=args.output_format
    )

    print(f"\n✅ 查询完成！")
    print(f"   结果已保存: {saved_file}")
    print(f"   形成知识复利: 下次查询可使用此结果作为输入")

    return 0


if __name__ == "__main__":
    exit(main())
