#!/usr/bin/env python3
"""
Auto GitHub Processor - GitHub项目13节深度解读生成器

针对GitHub项目的特殊处理流程：
1. 自动获取README和stars数
2. 生成13节结构化深度解读
3. 包含ASCII架构图和能力表格
4. 自动归档到Tools/Topics/YYYY-MM/

Usage:
    python3 auto_github_processor.py --input github_urls.txt
    python3 auto_github_processor.py --single https://github.com/owner/repo
    python3 auto_github_processor.py --input urls.txt --dry-run

输出: 20-Areas/Tools/Topics/YYYY-MM/YYYY-MM-DD_owner_repo_深度解读.md
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
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

try:
    import litellm
except ImportError:
    print("Error: litellm is required. Install with: pip install litellm")
    sys.exit(1)


# ========== 配置 ==========
OUTPUT_DIR = VAULT_DIR / "20-Areas" / "Tools" / "Topics" / datetime.now().strftime("%Y-%m")
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"


@dataclass
class GitHubRepo:
    url: str
    owner: str
    repo: str
    date: str
    tags: list[str]
    description: str
    readme_content: str = ""
    stars: int = 0


class PipelineLogger:
    """统一日志记录"""

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
    """LiteLLM client (参考spec-orch实现)"""

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


class GitHubDepthProcessor:
    """GitHub项目13节深度解读生成器"""

    SYSTEM_PROMPT = """你是专业的技术文档分析师，负责为GitHub项目创建13节深度解读。

## 13节结构

1. **一句话概述** (One-sentence Summary)
   - 项目的核心定位和价值主张

2. **项目定位** (Project Positioning)
   - 在生态系统中的位置
   - 目标用户群体

3. **README中文简介** (README Summary)
   - 翻译并提炼README核心内容

4. **核心能力** (Core Capabilities)
   - 表格形式：能力/说明/证据来源/置信度(1-5)

5. **技术架构** (Technical Architecture)
   - ASCII图表展示架构层次
   - 模块之间的关系

6. **关键模块映射** (Module Mapping)
   - 主要组件及其职责

7. **运行与开发方式** (Usage & Development)
   - 快速开始指南
   - 开发环境搭建

8. **外部接口** (External Interfaces)
   - API设计概览
   - 集成点

9. **数据流/控制流** (Data & Control Flow)
   - 关键流程的ASCII图表

10. **技术栈判断** (Technology Stack)
    - 主要语言和框架
    - 依赖分析

11. **成熟度与风险** (Maturity & Risks)
    - 维护状态评估
    - 潜在风险点

12. **关联概念** (Related Concepts)
    - [[双括号链接]]到其他笔记
    - 相关技术栈

13. **页脚** (Footer)
    - 参考链接
    - 更新日期

## 输出格式要求

1. YAML frontmatter必须包含：
   - title: 项目名称
   - github: 完整URL
   - owner: 仓库所有者
   - repo: 仓库名
   - date: 处理日期
   - type: github-project
   - tags: [tool, ai, ...]
   - stars: star数量

2. 技术架构和数据流必须用ASCII图表

3. 核心能力表格必须有置信度列(1-5)

4. 不确定的信息标注"未知"或"未说明"

5. 无README时基于名称推断，但标注低置信度
"""

    def __init__(self, llm_client: LiteLLMClient):
        self.llm = llm_client

    def generate_depth_doc(self, repo: GitHubRepo) -> tuple[str, dict]:
        user_prompt = f"""为以下GitHub项目创建13节深度解读：

项目URL: {repo.url}
Owner: {repo.owner}
Repo: {repo.repo}
日期: {repo.date}
Tags: {', '.join(repo.tags)}
描述: {repo.description}
Stars: {repo.stars if repo.stars else '未知'}

README内容:
```
{repo.readme_content[:8000] if repo.readme_content else '未获取README'}
```

请严格按照13节结构输出完整Markdown（从--- frontmatter开始）。"""

        return self.llm.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=8000,
        )


def fetch_github_readme(owner: str, repo: str) -> tuple[str, int]:
    """获取GitHub项目README和stars数"""
    import requests
    import json

    readme_content = ""
    stars = 0

    # 尝试获取README
    branches = ["main", "master", "develop"]
    for branch in branches:
        for filename in ["README.md", "readme.md"]:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    readme_content = resp.text
                    break
            except Exception:
                continue
        if readme_content:
            break

    # 获取stars数
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            stars = resp.json().get("stargazers_count", 0)
    except Exception:
        pass

    return readme_content, stars


def parse_github_url(url: str) -> tuple[str, str] | None:
    """解析GitHub URL提取owner和repo"""
    parsed = urlparse(url)
    if parsed.netloc not in ["github.com", "www.github.com"]:
        return None

    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        return None

    # 过滤非仓库路径
    skip_paths = {"blob", "tree", "issues", "pull", "wiki", "actions", "releases"}
    if any(p in parts for p in skip_paths):
        return None

    return parts[0], parts[1]


def process_single_repo(
    url: str,
    date: str,
    tags: list[str],
    description: str,
    llm_client: LiteLLMClient,
    output_dir: Path,
    dry_run: bool = False,
) -> dict:
    """处理单个GitHub项目"""
    result = {
        "url": url,
        "status": "pending",
        "output_file": None,
        "tokens_used": 0,
        "error": None,
    }

    # 解析URL
    parsed = parse_github_url(url)
    if not parsed:
        result["status"] = "error"
        result["error"] = "Invalid GitHub URL"
        return result

    owner, repo_name = parsed

    # 获取README
    print(f"  Fetching README for {owner}/{repo_name}...")
    readme_content, stars = fetch_github_readme(owner, repo_name)

    repo = GitHubRepo(
        url=url,
        owner=owner,
        repo=repo_name,
        date=date,
        tags=tags,
        description=description,
        readme_content=readme_content,
        stars=stars,
    )

    # 生成深度解读
    print(f"  Generating 13-section depth document...")
    processor = GitHubDepthProcessor(llm_client)

    try:
        content, metadata = processor.generate_depth_doc(repo)
        result["tokens_used"] = metadata.get("tokens", 0)

        if dry_run:
            result["status"] = "dry_run"
            result["output_file"] = "(dry run)"
            return result

        # 写入文件
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{date}_{owner}_{repo_name}_深度解读.md"
        output_path = output_dir / safe_name

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        result["status"] = "completed"
        result["output_file"] = str(output_path)
        print(f"  ✓ Created: {output_path}")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"  ✗ Error: {e}")

    return result


def main():
    parser = argparse.ArgumentParser(description="GitHub项目13节深度解读生成器")
    parser.add_argument("--input", "-i", help="输入文件（每行一个GitHub URL）")
    parser.add_argument("--single", "-s", help="单个GitHub URL")
    parser.add_argument("--output-dir", "-o", type=Path, default=OUTPUT_DIR,
                        help=f"输出目录（默认: {OUTPUT_DIR}）")
    parser.add_argument("--model", "-m", default="MiniMax-M2.5", help="LLM模型")
    parser.add_argument("--api-type", default="anthropic", choices=["anthropic", "openai"])
    parser.add_argument("--api-key", help="API Key（或设置AUTO_VAULT_API_KEY环境变量）")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="调用间隔（秒）")

    args = parser.parse_args()

    if not args.input and not args.single:
        parser.print_help()
        sys.exit(1)

    # 初始化LLM
    try:
        llm_client = LiteLLMClient(
            model=args.model,
            api_type=args.api_type,
            api_key=args.api_key,
            api_base=args.api_base,
        )
        print(f"✓ LLM Client: {llm_client.model}")
    except ValueError as e:
        print(f"✗ {e}")
        sys.exit(1)

    # 构建URL列表
    urls_to_process = []
    if args.single:
        urls_to_process.append({
            "url": args.single,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tags": [],
            "description": "",
        })
    elif args.input:
        with open(args.input) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls_to_process.append({
                        "url": line,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "tags": [],
                        "description": "",
                    })

    print(f"\nProcessing {len(urls_to_process)} GitHub repositories...")
    print(f"Output: {args.output_dir}")
    print("=" * 60)

    # 处理
    results = []
    for i, item in enumerate(urls_to_process, 1):
        print(f"\n[{i}/{len(urls_to_process)}] {item['url']}")
        result = process_single_repo(
            url=item["url"],
            date=item["date"],
            tags=item["tags"],
            description=item["description"],
            llm_client=llm_client,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )
        results.append(result)

        if i < len(urls_to_process):
            time.sleep(args.delay)

    # 汇总
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    successful = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] == "error")
    total_tokens = sum(r.get("tokens_used", 0) for r in results)

    print(f"Total: {len(results)} | Success: {successful} | Failed: {failed}")
    print(f"Total Tokens: {total_tokens:,}")
    print(f"Total Calls: {llm_client.total_calls}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import json
    sys.exit(main())
