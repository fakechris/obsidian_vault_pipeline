#!/usr/bin/env python3
"""
Auto Paper Processor - 学术论文深层解读处理器

针对学术论文的特殊处理流程：
1. 支持arXiv URL自动获取PDF
2. 提取论文结构（摘要、方法、实验、结论）
3. 生成学术深度解读（侧重方法复现和核心贡献）
4. 自动处理引用和参考文献

Usage:
    python3 auto_paper_processor.py --arxiv https://arxiv.org/abs/2401.12345
    python3 auto_paper_processor.py --pdf paper.pdf --title "Paper Title"
    python3 auto_paper_processor.py --input papers.txt

输出: 20-Areas/AI-Research/Papers/YYYY-MM-DD_arxiv-id_深度解读.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
OUTPUT_DIR = VAULT_DIR / "20-Areas" / "AI-Research" / "Papers"
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"


@dataclass
class Paper:
    source: str  # arxiv URL, pdf path, or DOI
    title: str
    authors: list[str]
    date: str
    abstract: str = ""
    pdf_content: str = ""  # 提取的文本内容
    arxiv_id: str = ""


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
    """LiteLLM client"""

    VALID_API_TYPES = ("anthropic", "openai")

    def __init__(
        self,
        *,
        model: str = "MiniMax-M2.5",
        api_type: str = "anthropic",
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.2,  # 学术论文用更低temperature
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


class PaperProcessor:
    """学术论文深层解读处理器"""

    SYSTEM_PROMPT = """你是专业的学术论文分析师，负责创建学术论文的深层解读。

## 学术论文深度解读结构

1. **元信息** (Metadata)
   - 标题、作者、发表日期
   - 期刊/会议信息
   - arXiv ID 或 DOI

2. **一句话核心贡献** (Core Contribution)
   - 论文最核心的创新点
   - 解决了什么问题

3. **研究背景与动机** (Background & Motivation)
   - 领域现状
   - 现有方法的局限
   - 本文的切入点

4. **方法详解** (Methodology) ⭐重点
   - 核心方法框架（ASCII图表）
   - 关键技术细节
   - 与现有方法的对比
   - 方法选择的理由

5. **实验设计** (Experiments)
   - 数据集选择理由
   - 评估指标说明
   - 主要结果摘要

6. **核心洞察** (Key Insights)
   - 3-5个重要发现
   - 意外的实验结果
   - 消融实验结论

7. **方法复现指南** (Reproduction Guide) ⭐重点
   - 伪代码或关键算法步骤
   - 超参数设置
   - 潜在实现难点

8. **局限性与未来工作** (Limitations)
   - 作者自述的局限
   - 潜在改进方向

9. **关联研究** (Related Work)
   - [[相关工作1]] - 关系说明
   - [[相关工作2]] - 关系说明

10. **个人思考** (Personal Notes)
    - 应用场景设想
    - 待验证的假设

## 学术特殊要求

1. **技术术语保留英文**：
   - 正确：使用 "transformer" 架构
   - 错误：使用 "变换器" 架构

2. **公式和算法**：
   - 保留LaTeX格式
   - 关键公式必须保留

3. **置信度标注**：
   - 基于原文明确表述的内容标注高置信度(5/5)
   - 推断性内容标注低置信度(2-3/5)

4. **引用处理**：
   - 保留关键引用的作者和年份
   - 如 [Krizhevsky et al., 2012]
"""

    def __init__(self, llm_client: LiteLLMClient):
        self.llm = llm_client

    def generate_paper_analysis(self, paper: Paper) -> tuple[str, dict]:
        user_prompt = f"""请为以下学术论文创建深层解读：

**论文信息：**
- 来源：{paper.source}
- 标题：{paper.title}
- 作者：{', '.join(paper.authors) if paper.authors else '未知'}
- 日期：{paper.date}
- arXiv ID：{paper.arxiv_id if paper.arxiv_id else 'N/A'}

**摘要：**
```
{paper.abstract[:3000] if paper.abstract else '未获取'}
```

**PDF内容片段：**
```
{paper.pdf_content[:6000] if paper.pdf_content else '未提取'}
```

请严格按照学术论文深度解读结构输出完整Markdown（从--- frontmatter开始）。
重点关注：
1. 方法详解（带ASCII图表）
2. 方法复现指南
3. 核心洞察
4. 关联研究的[[双括号链接]]"""

        return self.llm.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=8000,
        )


def extract_arxiv_id(url: str) -> str | None:
    """从arXiv URL提取ID"""
    patterns = [
        r'arxiv\.org/abs/(\d+\.\d+)',
        r'arxiv\.org/pdf/(\d+\.\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def fetch_arxiv_info(arxiv_id: str) -> dict:
    """获取arXiv论文信息"""
    import requests

    try:
        # arXiv API
        url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
        resp = requests.get(url, timeout=15)

        if resp.status_code == 200:
            # 简单解析XML
            content = resp.text

            # 提取标题
            title_match = re.search(r'<title>([^<]+)</title>', content)
            title = title_match.group(1) if title_match else "Unknown"

            # 提取摘要
            abstract_match = re.search(r'<summary>([^<]+)</summary>', content, re.DOTALL)
            abstract = abstract_match.group(1).strip() if abstract_match else ""

            # 提取作者
            authors = re.findall(r'<name>([^<]+)</name>', content)

            return {
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "success": True,
            }
    except Exception as e:
        print(f"Error fetching arXiv info: {e}")

    return {"success": False}


def extract_pdf_text(pdf_path: str, max_chars: int = 10000) -> str:
    """提取PDF文本（需要pdftotext或PyPDF2）"""
    try:
        # 尝试使用pdftotext（更精确）
        result = subprocess.run(
            ["pdftotext", "-l", "10", pdf_path, "-"],  # 只取前10页
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout[:max_chars]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"pdftotext error: {e}")

    # 降级：尝试PyPDF2
    try:
        import PyPDF2
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for i, page in enumerate(reader.pages):
                if i >= 10:  # 只取前10页
                    break
                text += page.extract_text() + "\n"
            return text[:max_chars]
    except ImportError:
        print("Warning: PyPDF2 not installed. Install with: pip install PyPDF2")
    except Exception as e:
        print(f"PyPDF2 error: {e}")

    return ""


def process_single_paper(
    source: str,
    title: str | None,
    authors: list[str],
    date: str,
    llm_client: LiteLLMClient,
    output_dir: Path,
    dry_run: bool = False,
) -> dict:
    """处理单个论文"""
    result = {
        "source": source,
        "status": "pending",
        "output_file": None,
        "tokens_used": 0,
        "error": None,
    }

    arxiv_id = ""
    abstract = ""
    pdf_content = ""

    # 判断来源类型并获取内容
    if "arxiv.org" in source:
        arxiv_id = extract_arxiv_id(source) or ""
        if arxiv_id:
            print(f"  Fetching arXiv:{arxiv_id} info...")
            info = fetch_arxiv_info(arxiv_id)
            if info.get("success"):
                if not title:
                    title = info.get("title", "Unknown Paper")
                abstract = info.get("abstract", "")
                if not authors:
                    authors = info.get("authors", [])
                print(f"  ✓ Got arXiv info: {title[:60]}...")
    elif source.endswith(".pdf"):
        print(f"  Extracting PDF: {source}...")
        pdf_content = extract_pdf_text(source)
        if not title:
            title = Path(source).stem
        print(f"  ✓ Extracted {len(pdf_content)} chars")

    if not title:
        title = "Unknown Paper"

    paper = Paper(
        source=source,
        title=title,
        authors=authors,
        date=date,
        abstract=abstract,
        pdf_content=pdf_content,
        arxiv_id=arxiv_id,
    )

    # 生成深度解读
    print(f"  Generating paper analysis...")
    processor = PaperProcessor(llm_client)

    try:
        content, metadata = processor.generate_paper_analysis(paper)
        result["tokens_used"] = metadata.get("tokens", 0)

        if dry_run:
            result["status"] = "dry_run"
            result["output_file"] = "(dry run)"
            return result

        # 写入文件
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r'[^\w\s-]', '', title)[:50].strip()
        safe_id = arxiv_id or datetime.now().strftime("%Y%m%d")
        output_name = f"{date}_{safe_id}_{safe_title}_深度解读.md"
        output_path = output_dir / output_name

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
    parser = argparse.ArgumentParser(description="学术论文深层解读处理器")
    parser.add_argument("--arxiv", help="arXiv URL")
    parser.add_argument("--pdf", help="本地PDF路径")
    parser.add_argument("--input", "-i", help="输入文件（每行一个URL或PDF路径）")
    parser.add_argument("--title", help="论文标题（PDF处理时需要）")
    parser.add_argument("--authors", help="作者（逗号分隔）")
    parser.add_argument("--output-dir", "-o", type=Path, default=OUTPUT_DIR,
                        help=f"输出目录（默认: {OUTPUT_DIR}）")
    parser.add_argument("--model", "-m", default="MiniMax-M2.5", help="LLM模型")
    parser.add_argument("--api-type", default="anthropic", choices=["anthropic", "openai"])
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--delay", "-d", type=float, default=2.0, help="调用间隔（秒）")

    args = parser.parse_args()

    if not any([args.arxiv, args.pdf, args.input]):
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

    # 构建论文列表
    papers_to_process = []

    if args.arxiv:
        papers_to_process.append({
            "source": args.arxiv,
            "title": args.title,
            "authors": args.authors.split(",") if args.authors else [],
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
    elif args.pdf:
        papers_to_process.append({
            "source": args.pdf,
            "title": args.title or Path(args.pdf).stem,
            "authors": args.authors.split(",") if args.authors else [],
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
    elif args.input:
        with open(args.input) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    papers_to_process.append({
                        "source": line,
                        "title": None,  # 需要自动获取
                        "authors": [],
                        "date": datetime.now().strftime("%Y-%m-%d"),
                    })

    print(f"\nProcessing {len(papers_to_process)} papers...")
    print(f"Output: {args.output_dir}")
    print("=" * 60)

    # 处理
    results = []
    for i, item in enumerate(papers_to_process, 1):
        print(f"\n[{i}/{len(papers_to_process)}] {item['source'][:60]}...")
        result = process_single_paper(
            source=item["source"],
            title=item["title"],
            authors=item["authors"],
            date=item["date"],
            llm_client=llm_client,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )
        results.append(result)

        if i < len(papers_to_process):
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
    print(f"Estimated Cost: ¥{total_tokens * 0.00001:.2f} (MiniMax)")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
