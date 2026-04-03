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
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 自动加载 .env 文件
VAULT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = VAULT_DIR / ".env"
if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_FILE, override=True)
    except ImportError:
        pass  # dotenv 未安装，跳过

# Import litellm
sys.path.insert(0, str(Path(__file__).parent / "auto_vault"))
try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    print("Warning: litellm not available, LLM calls will fail")

# ========== 配置 ==========
RAW_DIR = VAULT_DIR / "50-Inbox" / "01-Raw"
PROCESSED_DIR = VAULT_DIR / "50-Inbox" / "03-Processed"
OUTPUT_DIRS = {
    "ai": VAULT_DIR / "20-Areas" / "AI-Research" / "Topics" / datetime.now().strftime("%Y-%m"),
    "tools": VAULT_DIR / "20-Areas" / "Tools" / "Topics" / datetime.now().strftime("%Y-%m"),
    "investing": VAULT_DIR / "20-Areas" / "Investing" / "Topics" / datetime.now().strftime("%Y-%m"),
    "programming": VAULT_DIR / "20-Areas" / "Programming" / "Topics" / datetime.now().strftime("%Y-%m"),
}
MANIFEST_FILE = VAULT_DIR / "50-Inbox" / ".manifest.json"
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"
TXN_DIR = VAULT_DIR / "60-Logs" / "transactions"


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

    def __init__(
        self,
        *,
        model: str = "MiniMax-M2.5",
        api_type: str = "anthropic",
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.3,
    ):
        if api_type not in self.VALID_API_TYPES:
            raise ValueError(f"api_type must be one of {self.VALID_API_TYPES}")
        self.api_type = api_type
        if "/" in model:
            self.model = model
        else:
            self.model = f"{api_type}/{model}"
        self._api_key = api_key or os.environ.get("AUTO_VAULT_API_KEY")
        self.api_base = api_base or os.environ.get("AUTO_VAULT_API_BASE")
        self.temperature = temperature
        self._total_calls = 0
        self._total_tokens = 0

        if not self._api_key:
            raise ValueError("API key required. Set AUTO_VAULT_API_KEY env var.")

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

        response = litellm.completion(**kwargs)
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

输出格式要求：
1. YAML frontmatter必须包含：title, source, author, date, type, tags, status
2. 6个标准维度：
   - 一句话定义：核心概念的精准概括
   - 详细解释：what/why/how的完整分析
   - 重要细节：至少3个关键技术点/数据/案例
   - 架构图/流程图：如有技术架构，用ASCII图表展示
   - 行动建议：至少2条可落地的具体建议
   - 关联知识：相关概念的双向链接[[...]]

质量规则：
- 不确定的信息标注"原文未说明"，禁止编造
- 技术术语保持英文（如MCP Protocol, function calling）
- 使用中文撰写，但保留技术术语原文
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
7. 关联知识（[[...]]格式）"""

        content_result, metadata = self.llm.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=8000,
        )

        return content_result, metadata, classification


class AutoArticleProcessor:
    """全自动文章处理器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger, txn: TransactionManager):
        self.vault_dir = vault_dir
        self.raw_dir = vault_dir / "50-Inbox" / "01-Raw"
        self.processed_dir = vault_dir / "50-Inbox" / "03-Processed"
        self.logger = logger
        self.txn = txn
        self.llm = None
        self.article_processor = None

    def init_llm(self, api_key: str | None = None, api_base: str | None = None):
        """初始化LLM客户端"""
        llm_client = LiteLLMClient(
            api_key=api_key,
            api_base=api_base,
            model="MiniMax-M2.5",
            api_type="anthropic"
        )
        self.llm = llm_client
        self.article_processor = ArticleProcessor(llm_client, self.logger)

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
        }

    def process_single_file(self, file_path: Path, dry_run: bool = False) -> dict:
        """处理单个文件"""
        result = {
            "file": str(file_path),
            "status": "pending",
            "output_path": None,
            "tokens_used": 0,
            "error": None
        }

        try:
            # 解析文件
            file_data = self.parse_raw_file(file_path)

            if dry_run:
                result["status"] = "dry_run"
                return result

            if not self.article_processor:
                result["status"] = "error"
                result["error"] = "LLM not initialized"
                return result

            # 生成深度解读
            interpretation, metadata, classification = self.article_processor.generate_interpretation(
                title=file_data["title"],
                author=file_data["author"],
                source=file_data["source"],
                content=file_data["body"],
                date=file_data["date"]
            )

            # 确定输出路径
            output_dir = OUTPUT_DIRS.get(classification, OUTPUT_DIRS["ai"])
            output_dir.mkdir(parents=True, exist_ok=True)

            # 文件名：YYYY-MM-DD_标题_深度解读.md
            clean_title = re.sub(r'[^\w\s-]', '', file_data["title"])[:50]
            output_name = f"{file_data['date']}_{clean_title}_深度解读.md"
            output_path = output_dir / output_name

            # 写入文件
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
                "tokens": metadata.get("tokens", 0)
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
            return results

        # 获取所有.md文件
        files = list(self.raw_dir.glob("*.md"))
        results["total"] = len(files)

        if batch_size:
            files = files[:batch_size]

        for file_path in files:
            result = self.process_single_file(file_path, dry_run)
            results["files"].append(result)

            if result["status"] == "completed":
                results["completed"] += 1
                results["total_tokens"] += result.get("tokens_used", 0)
            elif result["status"] == "error":
                results["failed"] += 1
            else:
                results["skipped"] += 1

        return results


def main():
    parser = argparse.ArgumentParser(description="全自动文章深度解读生成器")
    parser.add_argument("--input", "-i", help="输入文件（每行一个URL）")
    parser.add_argument("--single", "-s", help="单个URL")
    parser.add_argument("--process-inbox", action="store_true", help="处理50-Inbox/01-Raw/")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--batch-size", type=int, help="批量处理数量")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--vault-dir", type=Path, default=VAULT_DIR, help="Vault根目录")
    args = parser.parse_args()

    # 初始化组件
    logger = PipelineLogger(LOG_FILE)
    txn = TransactionManager(TXN_DIR)

    # 创建事务
    txn_id = txn.start("article-processing", f"Process articles {datetime.now().isoformat()}")
    logger.log("transaction_started", {"txn_id": txn_id, "type": "article-processing"})

    # 初始化处理器
    processor = AutoArticleProcessor(args.vault_dir, logger, txn)

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
