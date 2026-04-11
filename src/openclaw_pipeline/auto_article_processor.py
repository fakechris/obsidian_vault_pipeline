#!/usr/bin/env python3
"""
Auto Article Processor - 全自动文章深度解读生成器
基于LLM API自动生成6维度深度解读

Usage:
    python3 auto_article_processor.py --input urls.txt
    python3 auto_article_processor.py --single https://example.com/article
    python3 auto_article_processor.py --process-inbox  # 处理50-Inbox/01-Raw/

Features:
    - WebFetch自动获取文章内容
    - 6维度深度解读生成
    - 自动分类（AI/工具/投资/编程）
    - 幂等处理（跳过已处理）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - optional dependency
    BeautifulSoup = None

try:
    from .runtime import VaultLayout, resolve_vault_dir
except ImportError:  # pragma: no cover - script mode fallback
    from runtime import VaultLayout, resolve_vault_dir

try:
    from .llm_defaults import DEFAULT_MINIMAX_MODEL, normalize_model_for_api_base
except ImportError:  # pragma: no cover - script mode fallback
    from llm_defaults import DEFAULT_MINIMAX_MODEL, normalize_model_for_api_base

try:
    from .markdown_generation import sanitize_generated_markdown
except ImportError:  # pragma: no cover - script mode fallback
    from markdown_generation import sanitize_generated_markdown

# 自动加载 .env 文件（尝试多个位置）
def _load_env_files():
    """加载 .env 文件，尝试多个位置"""
    env_paths = [
        Path.cwd() / ".env",  # 当前工作目录（优先）
        Path(__file__).parent.parent.parent / ".env",  # 脚本相对路径
    ]
    for env_path in env_paths:
        if env_path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_path, override=True)
                return env_path
            except ImportError:
                pass
    return None

_LOADED_ENV = _load_env_files()

# 确定 VAULT_DIR（优先使用当前工作目录）
VAULT_DIR = resolve_vault_dir()
DEFAULT_LAYOUT = VaultLayout.from_vault(VAULT_DIR)

# Import litellm
try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    print("Warning: litellm not available, LLM calls will fail")

# Import concept resolver
try:
    from .concept_registry import ConceptRegistry
    from .concept_resolver import (
        MentionExtractor,
        ConceptResolver,
        LinkRenderer,
        LinkResolutionSidecar,
    )
    RESOLVER_AVAILABLE = True
except ImportError:
    RESOLVER_AVAILABLE = False
    print("Warning: concept_resolver not available, link resolution disabled")

# ========== 配置 ==========
RAW_DIR = DEFAULT_LAYOUT.raw_dir
PROCESSED_DIR = DEFAULT_LAYOUT.processed_dir
MANIFEST_FILE = DEFAULT_LAYOUT.vault_dir / "50-Inbox" / ".manifest.json"
LOG_FILE = DEFAULT_LAYOUT.pipeline_log
TXN_DIR = DEFAULT_LAYOUT.transactions_dir
EVERGREEN_DIR = DEFAULT_LAYOUT.evergreen_dir
LINK_RESOLUTION_DIR = DEFAULT_LAYOUT.link_resolution_dir
RESOLVER_VERSION = "v2"


class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            **data
        }
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TransactionManager:
    """事务管理器"""

    def __init__(self, txn_dir: Path):
        self.txn_dir = txn_dir

    def start(self, workflow_type: str, description: str) -> str:
        txn_id = f"txn-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()[:8]}"
        txn_file = self.txn_dir / f"{txn_id}.json"

        txn_data = {
            "id": txn_id,
            "type": workflow_type,
            "description": description,
            "start_time": datetime.now().isoformat(),
            "status": "in_progress",
            "steps": {},
            "checkpoint": "initialized",
            "last_updated": datetime.now().isoformat()
        }

        txn_file.parent.mkdir(parents=True, exist_ok=True)
        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

        return txn_id

    def step(self, txn_id: str, step_name: str, status: str, output: str = ""):
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["steps"][step_name] = {
            "status": status,
            "output": output,
            "updated_at": datetime.now().isoformat()
        }
        txn_data["checkpoint"] = step_name
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

    def complete(self, txn_id: str):
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["status"] = "completed"
        txn_data["completed_at"] = datetime.now().isoformat()
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)


class LiteLLMClient:
    """LiteLLM客户端（参考spec-orch实现）"""

    VALID_API_TYPES = ("anthropic", "openai")

    # API密钥回退链
    _API_KEY_FALLBACKS = (
        "AUTO_VAULT_API_KEY",
        "SPEC_ORCH_LLM_API_KEY",
        "MINIMAX_API_KEY",
        "MINIMAX_CN_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    )

    _API_BASE_FALLBACKS = (
        "AUTO_VAULT_API_BASE",
        "SPEC_ORCH_LLM_API_BASE",
        "MINIMAX_ANTHROPIC_BASE_URL",
        "ANTHROPIC_BASE_URL",
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        api_type: str = "anthropic",
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.3,
    ):
        if api_type not in self.VALID_API_TYPES:
            raise ValueError(f"api_type must be one of {self.VALID_API_TYPES}")
        self.api_type = api_type
        self._api_key = api_key or self._resolve_api_key()
        self.api_base = api_base or self._resolve_api_base()
        self.model = normalize_model_for_api_base(
            model or os.environ.get("AUTO_VAULT_MODEL", DEFAULT_MINIMAX_MODEL),
            api_type=api_type,
            api_base=self.api_base,
            default_model=DEFAULT_MINIMAX_MODEL,
        )
        self.temperature = temperature
        self._total_calls = 0
        self._total_tokens = 0

        if not self._api_key:
            raise ValueError("API key required. Set AUTO_VAULT_API_KEY env var.")

    def _resolve_api_key(self) -> str | None:
        """从回退链解析API密钥"""
        for env_name in self._API_KEY_FALLBACKS:
            value = os.environ.get(env_name)
            if value and value not in ("", "test_key_for_testing_only"):
                return value
        return None

    def _resolve_api_base(self) -> str | None:
        """从回退链解析API Base"""
        for env_name in self._API_BASE_FALLBACKS:
            value = os.environ.get(env_name)
            if value:
                return value
        return None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8000,
    ) -> tuple[str, dict]:
        if not LITELLM_AVAILABLE:
            raise RuntimeError("litellm not available")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = litellm.completion(**kwargs)
                break
            except Exception as exc:  # pragma: no cover - exercised via tests
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        else:  # pragma: no cover - defensive fallback
            raise last_error or RuntimeError("litellm completion failed")
        self._total_calls += 1

        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", {})
        tokens = getattr(usage, "total_tokens", 0) or 0
        self._total_tokens += tokens

        metadata = {
            "model": self.model,
            "tokens": tokens,
            "finish_reason": response.choices[0].finish_reason,
        }
        return content, metadata

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_tokens(self) -> int:
        return self._total_tokens


class ArticleProcessor:
    """文章处理器 - 6维度深度解读生成"""

    SYSTEM_PROMPT = """你是专业的技术文章分析师，负责创建6维度深度解读。

## 关联知识处理（关键规则）

处理 [[概念链接]] 时必须遵循以下优先级：

**第1优先级 - 链接已存在的 Evergreen：**
- 优先搜索并链接已存在的 Evergreen 概念
- 例如：已有 `DCF-Valuation.md`，则使用 `[[DCF-Valuation]]`

**第2优先级 - 合并到相似概念：**
- 如果新概念与现有概念语义相近，合并链接到现有概念
- 例如：没有 `折现率.md` 但有 `WACC.md`，可考虑合并或明确区分

**关于未知概念：**
- 如果概念不存在，不要创建 Evergreen 文件
- 直接使用 [[概念名]] 格式即可，resolver 会自动处理
- resolver 会决定：链接到现有概念、创建候选概念、或移除链接

## 输出格式要求：
1. YAML frontmatter必须包含：title, source, author, date, type, tags, status
2. 6个标准维度：
   - 一句话定义：核心概念的精准概括
   - 详细解释：what/why/how的完整分析
   - 重要细节：至少3个关键技术点/数据/案例
   - 架构图/流程图：如有技术架构，用ASCII图表展示
   - 行动建议：至少2条可落地的具体建议
   - 关联知识：使用 [[概念名]] 格式链接到已存在的 Evergreen

质量规则：
- 不确定的信息标注"原文未说明"，禁止编造
- 技术术语保持英文（如MCP Protocol, function calling）
- 使用中文撰写，但保留技术术语原文
- 关联知识中的概念名用 kebab-case（如 DCF-Valuation）
"""

    def __init__(self, llm_client: LiteLLMClient, logger: PipelineLogger):
        self.llm = llm_client
        self.logger = logger

    def classify_article(self, title: str, content: str) -> str:
        """自动分类文章领域"""
        title_lower = title.lower()
        content_lower = content[:2000].lower()  # 只检查前2000字符

        ai_keywords = ['ai', 'agent', 'llm', 'claude', 'gpt', 'model', 'machine learning',
                       '人工智能', '智能体', '大模型', '深度学习']
        tools_keywords = ['tool', 'cli', 'terminal', 'editor', 'vscode', 'plugin',
                          '工具', '编辑器', '终端', '插件']
        investing_keywords = ['invest', 'stock', 'crypto', 'trading', 'market', 'finance',
                              '投资', '股票', '交易', '市场', '金融']
        programming_keywords = ['code', 'programming', 'rust', 'python', 'javascript', 'api',
                                '编程', '代码', '开发', '架构']

        ai_score = sum(1 for k in ai_keywords if k in title_lower or k in content_lower)
        tools_score = sum(1 for k in tools_keywords if k in title_lower or k in content_lower)
        investing_score = sum(1 for k in investing_keywords if k in title_lower or k in content_lower)
        programming_score = sum(1 for k in programming_keywords if k in title_lower or k in content_lower)

        scores = {
            "ai": ai_score,
            "tools": tools_score,
            "investing": investing_score,
            "programming": programming_score
        }

        return max(scores, key=scores.get)

    def generate_interpretation(self, title: str, author: str, source: str,
                               content: str, date: str) -> tuple[str, dict]:
        """生成6维度深度解读"""
        classification = self.classify_article(title, content)

        user_prompt = f"""为以下文章创建6维度深度解读：

标题: {title}
作者: {author}
来源: {source}
日期: {date}
分类: {classification}

原文内容（前8000字符）：
```
{content[:8000]}
```

输出完整Markdown（从--- frontmatter开始），包含：
1. YAML frontmatter
2. 一句话定义
3. 详细解释（what/why/how）
4. 重要细节（至少3个）
5. 架构图/流程图（如有）
6. 行动建议（至少2条）
7. 关联知识：
   - 使用 [[概念名]] 格式链接到已存在的 Evergreen 概念
   - resolver 会自动处理未知概念
   - 概念名用 kebab-case（如 DCF-Valuation）"""

        content_result, metadata = self.llm.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=8000,
        )

        content_result = sanitize_generated_markdown(content_result)

        return content_result, metadata, classification

    def create_embedded_evergreens(self, content: str, output_dir: Path) -> list[str]:
        """
        从 ```evergreen 块提取并创建 Evergreen 文件

        格式：
        ```evergreen
        name: concept-name
        title: 显示标题
        definition: 一句话定义
        ```
        """
        created = []
        evergreen_blocks = re.findall(
            r'```evergreen\s*\n(.*?)\n```',
            content,
            re.DOTALL
        )

        for block in evergreen_blocks:
            try:
                lines = block.strip().split('\n')
                data = {}
                for line in lines:
                    if ':' in line:
                        key, val = line.split(':', 1)
                        data[key.strip()] = val.strip()

                name = data.get('name', '')
                title = data.get('title', data.get('name', ''))
                definition = data.get('definition', '')

                if not name or not definition:
                    continue

                # Kebab-case 确保
                name = name.strip().replace(' ', '-')

                # 检查是否已存在
                evergreen_path = DEFAULT_LAYOUT.evergreen_dir / f"{name}.md"
                if evergreen_path.exists():
                    continue

                # 创建 Evergreen
                frontmatter = f'''---
title: "{title}"
type: evergreen
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [evergreen, auto-created]
aliases: []
---

# {title}

> **一句话定义**: {definition}

---

*自动创建于 {datetime.now().strftime("%Y-%m-%d")}*
'''
                evergreen_path.parent.mkdir(parents=True, exist_ok=True)
                evergreen_path.write_text(frontmatter, encoding='utf-8')
                created.append(name)

                # 移除 ```evergreen 块从内容中（可选，保留给后续处理）
                content = re.sub(
                    rf'```evergreen\s*\n{re.escape(block)}\n```',
                    f'[[{name}]]',
                    content,
                    flags=re.DOTALL
                )

            except Exception as e:
                print(f"Warning: failed to create evergreen from block: {e}")
                continue

        return created


class AutoArticleProcessor:
    """全自动文章处理器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger, txn: TransactionManager):
        self.layout = VaultLayout.from_vault(vault_dir)
        self.vault_dir = self.layout.vault_dir
        self.raw_dir = self.layout.raw_dir
        self.processing_dir = self.layout.processing_dir
        self.processed_dir = self.layout.processed_dir
        self.logger = logger
        self.txn = txn
        self.llm = None
        self.article_processor = None

    def _extract_source_date(self, file_path: Path) -> datetime:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})_", file_path.name)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        return datetime.now()

    def _move_source_file(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        return source.rename(destination)

    def _stage_source_for_processing(self, file_path: Path) -> Path:
        self.processing_dir.mkdir(parents=True, exist_ok=True)
        if file_path.parent == self.processing_dir:
            return file_path
        staged_path = self.processing_dir / file_path.name
        staged = self._move_source_file(file_path, staged_path)
        self.logger.log("source_staged_for_processing", {
            "source": str(file_path),
            "staged": str(staged),
        })
        return staged

    def _restore_source_to_raw(self, file_path: Path) -> Path:
        restored = self._move_source_file(file_path, self.raw_dir / file_path.name)
        self.logger.log("source_restored_to_raw", {
            "source": str(file_path),
            "restored": str(restored),
        })
        return restored

    def _archive_source_to_processed(self, file_path: Path) -> Path:
        destination = self.layout.processed_month_dir(self._extract_source_date(file_path)) / file_path.name
        archived = self._move_source_file(file_path, destination)
        self.logger.log("source_archived_to_processed", {
            "source": str(file_path),
            "archived": str(archived),
        })
        return archived

    @staticmethod
    def _clean_body_text(body: str, title: str = "") -> str:
        lines: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line in {"## Notes", "## Tags", "Notes", "Tags"}:
                continue
            if line.startswith("#") and not line.startswith("## "):
                continue
            if title and line == title.strip():
                continue
            if re.fullmatch(r"#\S+", line):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _has_substantive_content(text: str) -> bool:
        plain = re.sub(r"\s+", " ", text).strip()
        if len(plain) >= 600:
            return True
        words = re.findall(r"[A-Za-z0-9_]+", plain)
        han = re.findall(r"[\u4e00-\u9fff]", plain)
        return len(words) >= 120 or len(han) >= 180

    @staticmethod
    def _looks_like_paper_source(source: str, tags: Any, title: str = "") -> bool:
        source_lower = (source or "").lower().strip()
        title = (title or "").strip()
        if not source_lower and not tags:
            return False
        tag_text = " ".join(tags) if isinstance(tags, list) else str(tags or "")
        tag_text = tag_text.lower()
        return (
            "arxiv.org" in source_lower
            or source_lower.endswith(".pdf")
            or "paper" in tag_text
            or bool(re.match(r"^\[\d{4}\.\d+\]", title))
        )

    def _extract_html_text(self, html: str) -> tuple[str, list[str]]:
        if not html:
            return "", []

        if BeautifulSoup is None:
            text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
            text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
            return text, hrefs

        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        text = soup.get_text("\n", strip=True)
        hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
        return text, hrefs

    def _fetch_url_text(self, url: str) -> tuple[str, str | None]:
        if requests is None:
            return "", None

        headers = {"User-Agent": "openclaw-pipeline/1.0"}
        try:
            response = requests.get(url, timeout=15, headers=headers, allow_redirects=True)
        except requests.RequestException:
            return "", None

        if response.status_code != 200:
            return "", None

        content_type = response.headers.get("Content-Type", "").lower()
        if "pdf" in content_type:
            return "", None
        if "html" not in content_type and "text" not in content_type and "markdown" not in content_type:
            return "", None

        text, _ = self._extract_html_text(response.text)
        return text, response.text

    def _fetch_docs_fallback_text(self, source_url: str, homepage_html: str | None) -> tuple[str, str | None]:
        if requests is None or not homepage_html:
            return "", None

        _, hrefs = self._extract_html_text(homepage_html)
        candidates: list[tuple[int, str]] = []
        for href in hrefs:
            if not href:
                continue
            absolute = urljoin(source_url, href)
            label = f"{href} {absolute}".lower()
            score = 0
            if "docs" in label or "documentation" in label:
                score += 3
            if "readme" in label:
                score += 3
            if "guide" in label or "getting-started" in label or "start" in label:
                score += 2
            if score:
                candidates.append((score, absolute))

        tried: set[str] = set()
        for _, candidate in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]:
            if candidate in tried:
                continue
            tried.add(candidate)
            text, _ = self._fetch_url_text(candidate)
            if self._has_substantive_content(text):
                return text, candidate
        return "", None

    def _prepare_interpretation_source(self, file_data: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        title = str(file_data.get("title") or "").strip()
        source = str(file_data.get("source") or "").strip()
        tags = file_data.get("tags") or []
        body = self._clean_body_text(str(file_data.get("body") or ""), title=title)

        if self._looks_like_paper_source(source, tags, title):
            return None, {"reason": "paper_source_requires_paper_processor"}

        if self._has_substantive_content(body):
            return body, {"origin": "body"}

        if not source:
            return None, {"reason": "insufficient_source_material"}

        fetched_text, homepage_html = self._fetch_url_text(source)
        if self._has_substantive_content(fetched_text):
            return fetched_text, {"origin": "source_url", "resolved_url": source}

        docs_text, docs_url = self._fetch_docs_fallback_text(source, homepage_html)
        if self._has_substantive_content(docs_text):
            return docs_text, {"origin": "docs_fallback", "resolved_url": docs_url}

        return None, {"reason": "insufficient_source_material"}

    def init_llm(self, api_key: str | None = None, api_base: str | None = None):
        """初始化LLM客户端"""
        llm_client = LiteLLMClient(
            api_key=api_key,
            api_base=api_base,
            model=os.environ.get("AUTO_VAULT_MODEL", DEFAULT_MINIMAX_MODEL),
            api_type="anthropic"
        )
        self.llm = llm_client
        self.article_processor = ArticleProcessor(llm_client, self.logger)

    # ========== Link Resolution Methods ==========

    def _resolve_article_links(self, content: str, article_stem: str,
                                area: str, txn_id: str) -> tuple[str, list, LinkResolutionSidecar]:
        """
        Resolve wikilinks in article content using the concept registry.

        Returns: (resolved_content, decisions, sidecar)
        """
        if not RESOLVER_AVAILABLE:
            return content, [], None

        try:
            registry = ConceptRegistry(self.vault_dir).load()
        except Exception as e:
            print(f"Warning: could not load registry: {e}")
            return content, [], None

        # Extract mentions
        extractor = MentionExtractor()
        mentions = extractor.extract_all(content, area, self.llm)

        if not mentions:
            return content, [], None

        # Resolve mentions
        resolver = ConceptResolver(registry, self.llm)
        decisions = resolver.resolve_mentions(mentions, area)

        # Render deterministic wikilinks
        renderer = LinkRenderer(registry)
        resolved_content = renderer.render_all(content, decisions)

        # Build sidecar
        sidecar = LinkResolutionSidecar(
            article=article_stem,
            resolver_version=RESOLVER_VERSION,
            area=area,
            decisions=decisions,
        )

        return resolved_content, decisions, sidecar

    def _write_resolution_sidecar(self, article_path: Path, sidecar: LinkResolutionSidecar) -> None:
        """Write resolution sidecar file."""
        if sidecar is None:
            return
        stem = article_path.stem
        sidecar_path = self.layout.link_resolution_dir / f"{stem}.json"
        sidecar.write(sidecar_path)

    def _upsert_candidates(self, decisions: list, registry: Any) -> list[str]:
        """
        Upsert candidate decisions to registry.

        Returns: list of candidate slugs created/updated.
        """
        if not decisions:
            return []

        upserted = []
        for d in decisions:
            if d.action == "create_candidate" and d.proposed_slug:
                try:
                    registry.upsert_candidate(
                        slug=d.proposed_slug,
                        title=d.title or d.surface,
                        definition=d.definition or "",
                        area="general",  # Will be updated on promote
                        aliases=[d.surface] if d.surface != d.proposed_slug else [],
                    )
                    upserted.append(d.proposed_slug)
                except ValueError:
                    # Candidate already exists with different status
                    pass
                except Exception as e:
                    print(f"Warning: could not upsert candidate '{d.proposed_slug}': {e}")
        return upserted

    def _augment_frontmatter(self, content: str, decisions: list, area: str,
                              txn_id: str) -> str:
        """
        Augment article frontmatter with link resolution metadata.

        Adds: area, canonical_concepts, concept_candidates,
              link_resolution_status, link_resolution_version, pipeline_run_id
        """
        if not content.startswith("---"):
            return content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return content

        fm_text = parts[1].strip()
        body = parts[2]

        # Build new frontmatter fields
        canonical = sorted({d.slug for d in decisions if d.action == "link_existing" and d.slug})
        candidates = sorted({d.proposed_slug for d in decisions
                            if d.action == "create_candidate" and d.proposed_slug})

        # Parse existing frontmatter
        fm_lines = fm_text.split("\n")
        fm_dict: dict[str, str] = {}
        for line in fm_lines:
            if ":" in line:
                key, val = line.split(":", 1)
                fm_dict[key.strip()] = val.strip().strip('"').strip("'")

        # Update frontmatter
        fm_dict["area"] = area
        fm_dict["canonical_concepts"] = "[" + ", ".join(canonical) + "]"
        fm_dict["concept_candidates"] = "[" + ", ".join(candidates) + "]"
        fm_dict["link_resolution_status"] = "resolved"
        fm_dict["link_resolution_version"] = RESOLVER_VERSION
        fm_dict["pipeline_run_id"] = txn_id

        # Reconstruct frontmatter
        new_fm_lines = []
        for key, val in fm_dict.items():
            if isinstance(val, str):
                new_fm_lines.append(f'{key}: {val}')
            else:
                new_fm_lines.append(f'{key}: {val}')

        new_fm_text = "\n".join(new_fm_lines)
        return f"---\n{new_fm_text}\n---\n{body}"

    def parse_raw_file(self, file_path: Path) -> dict[str, Any]:
        """解析Raw文件，提取元数据和内容"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 解析frontmatter
        frontmatter = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm_text = parts[1].strip()
                body = parts[2].strip()

                # 简单解析YAML
                for line in fm_text.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        frontmatter[key.strip()] = value.strip().strip('"').strip("'")

        return {
            "frontmatter": frontmatter,
            "body": body,
            "title": frontmatter.get("title", file_path.stem),
            "author": frontmatter.get("author", "unknown"),
            "source": frontmatter.get("source", ""),
            "date": frontmatter.get("date", datetime.now().strftime("%Y-%m-%d")),
            "tags": frontmatter.get("tags", ""),
        }

    def process_single_file(self, file_path: Path, dry_run: bool = False) -> dict:
        """处理单个文件"""
        result = {
            "file": str(file_path),
            "status": "pending",
            "output_path": None,
            "tokens_used": 0,
            "images_downloaded": 0,
            "error": None
        }

        try:
            # Step 1: 下载图片（如果存在远程图片）
            from .image_downloader import ImageDownloader
            image_downloader = ImageDownloader(self.vault_dir)
            try:
                downloaded_images = image_downloader.process_file(file_path, backup=True)
                result["images_downloaded"] = len(downloaded_images)
                if downloaded_images:
                    self.logger.log("images_downloaded", {
                        "file": str(file_path.name),
                        "count": len(downloaded_images),
                        "images": downloaded_images
                    })
            except Exception as img_err:
                self.logger.log("image_download_error", {"file": str(file_path), "error": str(img_err)})
                # 图片下载失败不阻止主流程

            # Step 2: 解析文件
            file_data = self.parse_raw_file(file_path)

            if dry_run:
                result["status"] = "dry_run"
                return result

            if not self.article_processor:
                result["status"] = "error"
                result["error"] = "LLM not initialized"
                return result

            source_material, source_meta = self._prepare_interpretation_source(file_data)
            if not source_material:
                result["status"] = "skipped"
                result["error"] = str(source_meta.get("reason", "insufficient_source_material"))
                self.logger.log(
                    "article_abstained",
                    {
                        "file": str(file_path.name),
                        "reason": result["error"],
                        "source": file_data["source"],
                    },
                )
                return result

            # 生成深度解读
            interpretation, metadata, classification = self.article_processor.generate_interpretation(
                title=file_data["title"],
                author=file_data["author"],
                source=file_data["source"],
                content=source_material,
                date=file_data["date"]
            )

            # Step 3: Link Resolution (replaces create_embedded_evergreens)
            # Get txn_id for pipeline_run_id
            txn_id = self.logger.session_id
            article_stem = file_path.stem

            interpretation, decisions, sidecar = self._resolve_article_links(
                interpretation, article_stem, classification, txn_id
            )

            # Augment frontmatter with resolution metadata
            interpretation = self._augment_frontmatter(
                interpretation, decisions, classification, txn_id
            )

            # 确定输出路径
            output_dir = self.layout.classification_output_dir(classification)
            output_dir.mkdir(parents=True, exist_ok=True)

            # 文件名：YYYY-MM-DD_标题_深度解读.md
            clean_title = re.sub(r'[^\w\s-]', '', file_data["title"])[:50]
            output_name = f"{file_data['date']}_{clean_title}_深度解读.md"
            output_path = output_dir / output_name

            # Write sidecar
            self._write_resolution_sidecar(output_path, sidecar)

            # Upsert candidates to registry
            if decisions and RESOLVER_AVAILABLE:
                try:
                    registry = ConceptRegistry(self.vault_dir).load()
                    candidates = self._upsert_candidates(decisions, registry)
                    if candidates:
                        registry.save()
                        self.logger.log("candidates_upserted", {
                            "file": str(file_path.name),
                            "candidates": candidates
                        })
                except Exception as e:
                    self.logger.log("candidate_upsert_error", {
                        "file": str(file_path.name),
                        "error": str(e)
                    })

            # Write file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(interpretation)

            result["status"] = "completed"
            result["output_path"] = str(output_path)
            result["tokens_used"] = metadata.get("tokens", 0)
            result["classification"] = classification

            self.logger.log("article_processed", {
                "file": str(file_path.name),
                "output": str(output_path),
                "classification": classification,
                "tokens": metadata.get("tokens", 0),
                "source_material_origin": source_meta.get("origin", "body"),
            })

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            self.logger.log("article_error", {"file": str(file_path), "error": str(e)})

        return result

    def process_inbox(self, dry_run: bool = False, batch_size: int | None = None) -> dict:
        """处理整个inbox"""
        results = {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "total_tokens": 0,
            "files": []
        }

        if not self.raw_dir.exists():
            self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.processing_dir.mkdir(parents=True, exist_ok=True)

        processing_files = sorted(self.processing_dir.glob("*.md"))
        raw_files = sorted(self.raw_dir.glob("*.md"))
        files = processing_files + raw_files
        results["total"] = len(files)

        if batch_size:
            files = files[:batch_size]

        for file_path in files:
            working_path = file_path
            if not dry_run and file_path.parent != self.processing_dir:
                working_path = self._stage_source_for_processing(file_path)

            result = self.process_single_file(working_path, dry_run)
            results["files"].append(result)

            if result["status"] == "completed":
                results["completed"] += 1
                results["total_tokens"] += result.get("tokens_used", 0)
                if not dry_run:
                    self._archive_source_to_processed(working_path)
            elif result["status"] == "error":
                results["failed"] += 1
                if not dry_run and working_path.exists():
                    self._restore_source_to_raw(working_path)
            else:
                results["skipped"] += 1
                if not dry_run and working_path.exists():
                    self._restore_source_to_raw(working_path)

        return results


def main():
    parser = argparse.ArgumentParser(description="全自动文章深度解读生成器")
    parser.add_argument("--input", "-i", help="输入文件（每行一个URL）")
    parser.add_argument("--single", "-s", help="单个URL")
    parser.add_argument("--process-inbox", action="store_true", help="处理50-Inbox/01-Raw/")
    parser.add_argument("--process-single", type=Path, help="处理单个本地文件")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--batch-size", type=int, help="批量处理数量")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault根目录")
    parser.add_argument("--output-dir", type=Path, default=None, help="兼容旧入口，当前忽略")
    args = parser.parse_args()

    layout = VaultLayout.from_vault(args.vault_dir or VAULT_DIR)

    # 初始化组件
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)

    # 创建事务
    txn_id = txn.start("article-processing", f"Process articles {datetime.now().isoformat()}")
    logger.log("transaction_started", {"txn_id": txn_id, "type": "article-processing"})

    # 初始化处理器
    processor = AutoArticleProcessor(layout.vault_dir, logger, txn)

    try:
        processor.init_llm(api_key=args.api_key, api_base=args.api_base)
        print(f"✓ LLM Client: {processor.llm.model}")
    except Exception as e:
        print(f"✗ {e}")
        sys.exit(1)

    # 执行处理
    txn.step(txn_id, "process", "in_progress", "Processing articles")

    if args.process_inbox:
        results = processor.process_inbox(dry_run=args.dry_run, batch_size=args.batch_size)
    elif args.process_single:
        result = processor.process_single_file(args.process_single, dry_run=args.dry_run)
        results = {
            "total": 1,
            "completed": 1 if result["status"] == "completed" else 0,
            "failed": 1 if result["status"] == "error" else 0,
            "skipped": 1 if result["status"] == "skipped" else 0,
            "total_tokens": result.get("tokens_used", 0)
        }
    elif args.single:
        print("Single URL processing not yet implemented (requires WebFetch)")
        results = {"total": 0, "completed": 0, "failed": 0}
    elif args.input:
        print("Batch URL processing not yet implemented")
        results = {"total": 0, "completed": 0, "failed": 0}
    else:
        parser.print_help()
        sys.exit(1)

    txn.step(txn_id, "process", "completed", f"Completed {results['completed']}/{results['total']}")

    # 输出结果
    print("\n" + "="*60)
    print("ARTICLE PROCESSING RESULTS")
    print("="*60)
    print(f"Total: {results['total']}")
    print(f"Completed: {results['completed']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Total Tokens: {results['total_tokens']}")

    # 完成事务
    txn.complete(txn_id)
    logger.log("transaction_completed", {"txn_id": txn_id, "results": results})

    return 0 if results['failed'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
