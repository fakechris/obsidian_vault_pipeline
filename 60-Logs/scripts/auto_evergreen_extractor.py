#!/usr/bin/env python3
"""
Auto Evergreen Extractor - 自动Evergreen笔记提取器
从深度解读中自动提取核心概念并创建原子笔记

Usage:
    python3 auto_evergreen_extractor.py --dir 20-Areas/AI-Research/Topics/2026-03/
    python3 auto_evergreen_extractor.py --file article.md
    python3 auto_evergreen_extractor.py --recent 7  # 最近7天的解读

Features:
    - 自动识别核心概念
    - 创建原子化Evergreen笔记
    - 自动双向链接
    - 幂等处理（跳过已存在）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 自动加载 .env 文件
VAULT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = VAULT_DIR / ".env"
if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_FILE, override=True)
    except ImportError:
        pass  # dotenv 未安装，跳过

sys.path.insert(0, str(Path(__file__).parent / "auto_vault"))
try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False

# ========== 配置 ==========
EVERGREEN_DIR = VAULT_DIR / "10-Knowledge" / "Evergreen"
ATLAS_DIR = VAULT_DIR / "10-Knowledge" / "Atlas"
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"


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
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class LiteLLMClient:
    """LiteLLM客户端"""

    def __init__(
        self,
        *,
        model: str = "MiniMax-M2.5",
        api_type: str = "anthropic",
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.3,
    ):
        self.api_type = api_type
        if "/" in model:
            self.model = model
        else:
            self.model = f"{api_type}/{model}"
        self._api_key = api_key or os.environ.get("AUTO_VAULT_API_KEY")
        self.api_base = api_base or os.environ.get("AUTO_VAULT_API_BASE")
        self.temperature = temperature

    def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str:
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

        response = litellm.completion(**kwargs)
        return response.choices[0].message.content or ""


class EvergreenExtractor:
    """Evergreen提取器"""

    SYSTEM_PROMPT = """你是知识提取专家。请从文章中提取3-5个核心概念，每个概念应适合创建为原子化的Evergreen笔记。

Evergreen笔记标准：
1. 原子性：一个概念一个笔记
2. 永久性：时间无关的知识
3. 可链接：能与其他概念连接
4. 命名：用陈述句命名（如"AI agents require persistent memory"）

输出格式（严格JSON数组）：
[
  {
    "concept_name": "Concept-Name-Kebab-Case",
    "title": "AI agents require persistent memory",
    "one_sentence_def": "一句话定义（中文，但保留技术术语英文）",
    "explanation": "详细解释（中文，技术术语不翻译）",
    "importance": "为什么重要",
    "related_concepts": ["Related-Concept-1", "Related-Concept-2"]
  }
]

要求：
- 每个概念必须是一个可独立理解的知识单元
- 技术术语保持英文（如MCP Protocol, function calling）
- 解释部分使用中文
- 最多5个概念，选择最有价值的
"""

    def __init__(self, llm_client: LiteLLMClient, logger: PipelineLogger):
        self.llm = llm_client
        self.logger = logger

    def extract_concepts(self, file_path: Path, content: str) -> list[dict]:
        """从内容中提取概念"""
        user_prompt = f"""请从以下深度解读中提取3-5个核心概念：

文件: {file_path}

内容（前6000字符）：
```
{content[:6000]}
```

请按JSON格式输出概念列表。"""

        result_text = self.llm.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=4000
        )

        # 尝试解析JSON
        try:
            json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
            if json_match:
                concepts = json.loads(json_match.group())
            else:
                concepts = []
        except json.JSONDecodeError:
            concepts = []

        return concepts

    def create_evergreen_note(self, concept: dict, source_file: Path) -> str:
        """创建Evergreen笔记内容"""
        concept_name = concept.get("concept_name", "Untitled")
        title = concept.get("title", concept_name.replace("-", " "))
        definition = concept.get("one_sentence_def", "")
        explanation = concept.get("explanation", "")
        importance = concept.get("importance", "")
        related = concept.get("related_concepts", [])

        # 构建相关链接
        related_links = "\n".join([f"- [[{c}]]" for c in related if c])

        note = f"""---
title: "{title}"
type: evergreen
date: {datetime.now().strftime('%Y-%m-%d')}
tags: [evergreen]
aliases: ["{concept_name}"]
---

# {title}

> **一句话定义**: {definition}

## 📝 详细解释

### 是什么？
{explanation}

### 为什么重要？
{importance}

## 🔗 关联概念
{related_links}

## 📚 来源与扩展阅读
- [[{source_file.stem}]]
"""

        return note


class AutoEvergreenExtractor:
    """自动Evergreen提取器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger):
        self.vault_dir = vault_dir
        self.evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"
        self.logger = logger
        self.extractor = None

    def init_llm(self, api_key: str | None = None, api_base: str | None = None):
        """初始化LLM"""
        llm_client = LiteLLMClient(
            api_key=api_key,
            api_base=api_base,
            model="MiniMax-M2.5",
            api_type="anthropic"
        )
        self.extractor = EvergreenExtractor(llm_client, self.logger)

    def evergreen_exists(self, concept_name: str) -> bool:
        """检查Evergreen笔记是否已存在"""
        possible_paths = [
            self.evergreen_dir / f"{concept_name}.md",
            self.evergreen_dir / f"{concept_name.replace('-', '_')}.md",
        ]
        return any(p.exists() for p in possible_paths)

    def process_file(self, file_path: Path, dry_run: bool = False) -> dict:
        """处理单个文件"""
        result = {
            "file": str(file_path),
            "concepts_extracted": 0,
            "concepts_created": 0,
            "concepts_skipped": 0,
            "concepts": []
        }

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 提取概念
            concepts = self.extractor.extract_concepts(file_path, content)
            result["concepts_extracted"] = len(concepts)

            for concept in concepts:
                concept_name = concept.get("concept_name")
                if not concept_name:
                    continue

                concept_info = {
                    "name": concept_name,
                    "status": "pending"
                }

                # 检查是否已存在
                if self.evergreen_exists(concept_name):
                    concept_info["status"] = "exists"
                    result["concepts_skipped"] += 1
                    result["concepts"].append(concept_info)
                    continue

                if dry_run:
                    concept_info["status"] = "dry_run"
                    result["concepts_created"] += 1
                    result["concepts"].append(concept_info)
                    continue

                # 创建Evergreen笔记
                note_content = self.extractor.create_evergreen_note(concept, file_path)
                output_path = self.evergreen_dir / f"{concept_name}.md"

                self.evergreen_dir.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(note_content)

                concept_info["status"] = "created"
                concept_info["path"] = str(output_path)
                result["concepts_created"] += 1
                result["concepts"].append(concept_info)

                self.logger.log("evergreen_created", {
                    "concept": concept_name,
                    "source": str(file_path.name),
                    "path": str(output_path)
                })

        except Exception as e:
            result["error"] = str(e)
            self.logger.log("evergreen_error", {"file": str(file_path), "error": str(e)})

        return result

    def process_directory(self, directory: Path, dry_run: bool = False) -> list[dict]:
        """处理整个目录"""
        if not directory.exists():
            return []

        # 只处理深度解读文件
        files = list(directory.glob("*_深度解读.md"))

        results = []
        for file_path in files:
            print(f"  Processing: {file_path.name}")
            result = self.process_file(file_path, dry_run)
            results.append(result)
            print(f"    Extracted: {result['concepts_extracted']}, "
                  f"Created: {result['concepts_created']}, "
                  f"Skipped: {result['concepts_skipped']}")

        return results


def main():
    parser = argparse.ArgumentParser(description="自动Evergreen笔记提取器")
    parser.add_argument("--dir", type=Path, help="处理目录")
    parser.add_argument("--file", type=Path, help="处理单个文件")
    parser.add_argument("--recent", type=int, help="处理最近N天的深度解读")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--vault-dir", type=Path, default=VAULT_DIR, help="Vault根目录")
    args = parser.parse_args()

    # 初始化
    logger = PipelineLogger(LOG_FILE)
    extractor = AutoEvergreenExtractor(args.vault_dir, logger)

    try:
        extractor.init_llm(api_key=args.api_key, api_base=args.api_base)
        print(f"✓ LLM Client initialized")
    except Exception as e:
        print(f"✗ {e}")
        sys.exit(1)

    # 执行处理
    if args.dir:
        print(f"\nProcessing directory: {args.dir}")
        results = extractor.process_directory(args.dir, dry_run=args.dry_run)
    elif args.file:
        print(f"\nProcessing file: {args.file}")
        results = [extractor.process_file(args.file, dry_run=args.dry_run)]
    elif args.recent:
        # 处理最近N天的所有Areas
        areas = ["AI-Research", "Tools", "Investing", "Programming"]
        all_results = []
        for area in areas:
            # 查找最近N天的目录
            for days_ago in range(args.recent):
                date_dir = args.vault_dir / "20-Areas" / area / "Topics" / (
                    datetime.now() - __import__('datetime').timedelta(days=days_ago)
                ).strftime("%Y-%m")
                if date_dir.exists():
                    print(f"\nProcessing {area} - {date_dir.name}...")
                    results = extractor.process_directory(date_dir, dry_run=args.dry_run)
                    all_results.extend(results)
        results = all_results
    else:
        parser.print_help()
        sys.exit(1)

    # 汇总
    total_extracted = sum(r.get("concepts_extracted", 0) for r in results)
    total_created = sum(r.get("concepts_created", 0) for r in results)
    total_skipped = sum(r.get("concepts_skipped", 0) for r in results)

    print(f"\n{'='*60}")
    print(f"EVERGREEN EXTRACTION COMPLETE")
    print(f"{'='*60}")
    print(f"Files processed: {len(results)}")
    print(f"Concepts extracted: {total_extracted}")
    print(f"Concepts created: {total_created}")
    print(f"Concepts skipped (exists): {total_skipped}")

    logger.log("evergreen_extraction_complete", {
        "files": len(results),
        "extracted": total_extracted,
        "created": total_created,
        "skipped": total_skipped
    })

    return 0


if __name__ == "__main__":
    sys.exit(main())
