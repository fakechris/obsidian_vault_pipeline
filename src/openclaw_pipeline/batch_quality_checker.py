#!/usr/bin/env python3
"""
Batch Quality Checker - 批量文章质量质检器
基于6维度标准自动评分

Usage:
    python3 batch_quality_checker.py --dir 20-Areas/AI-Research/Topics/2026-03/
    python3 batch_quality_checker.py --file article.md
    python3 batch_quality_checker.py --all

Features:
    - 6维度质量评分（1-5分）
    - 批量并行处理
    - 不合格文章标记
    - 生成质检报告
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
REPORT_DIR = VAULT_DIR / "60-Logs" / "quality-reports"
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"

# 6维度检查项
DIMENSIONS = [
    "一句话定义",
    "详细解释",
    "重要细节",
    "架构图/流程图",
    "行动建议",
    "关联知识"
]


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
        temperature: float = 0.1,  # 质检用低temperature
    ):
        self.api_type = api_type
        if "/" in model:
            self.model = model
        else:
            self.model = f"{api_type}/{model}"
        self._api_key = api_key or os.environ.get("AUTO_VAULT_API_KEY")
        self.api_base = api_base or os.environ.get("AUTO_VAULT_API_BASE")
        self.temperature = temperature

    def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
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


class QualityChecker:
    """质量检查器 - 基于LLM的6维度评估"""

    SYSTEM_PROMPT = """你是文章质量评估专家。请严格按以下6维度评估文章质量，每项1-5分。

评分标准（每项）：
- 5分：优秀，内容完整且有深度洞察
- 4分：良好，基本完整但某处稍弱
- 3分：及格，有主要结构但缺少重要内容
- 2分：不及格，严重缺失
- 1分：极差，几乎不可用

6个维度：
1. 一句话定义：是否有清晰的一句话概括核心概念
2. 详细解释：是否有what/why/how的完整分析
3. 重要细节：是否有至少3个关键技术点/数据/案例
4. 架构图/流程图：如有技术架构，是否有图表展示（无架构的给5分）
5. 行动建议：是否有至少2条可落地的具体建议
6. 关联知识：是否有[[...]]格式的相关文章链接

输出格式（严格JSON）：
{
  "scores": {
    "一句话定义": 5,
    "详细解释": 4,
    "重要细节": 3,
    "架构图/流程图": 5,
    "行动建议": 4,
    "关联知识": 5
  },
  "total_score": 26,
  "is_qualified": true,
  "issues": ["重要细节只有2个，不足3个"],
  "suggestions": "建议补充第3个重要细节"
}
"""

    def __init__(self, llm_client: LiteLLMClient, logger: PipelineLogger):
        self.llm = llm_client
        self.logger = logger

    def check_file(self, file_path: Path) -> dict[str, Any]:
        """检查单个文件"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 统计行数
            line_count = len(content.split("\n"))

            user_prompt = f"""请评估以下文章的质量（6维度，每项1-5分）：

文件路径: {file_path}
行数: {line_count}

内容（前5000字符）：
```
{content[:5000]}
```

请严格按JSON格式输出评估结果。"""

            result_text = self.llm.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=2000
            )

            # 尝试解析JSON
            try:
                # 提取JSON部分
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    result = self._fallback_parse(result_text)
            except json.JSONDecodeError:
                result = self._fallback_parse(result_text)

            result["file"] = str(file_path)
            result["line_count"] = line_count

            self.logger.log("quality_checked", {
                "file": str(file_path.name),
                "score": result.get("total_score", 0),
                "qualified": result.get("is_qualified", False)
            })

            return result

        except Exception as e:
            return {
                "file": str(file_path),
                "error": str(e),
                "total_score": 0,
                "is_qualified": False
            }

    def _fallback_parse(self, text: str) -> dict:
        """降级解析（当JSON解析失败时）"""
        # 尝试提取分数
        scores = {}
        for dim in DIMENSIONS:
            pattern = rf'{dim}[：:]\s*(\d)'
            match = re.search(pattern, text)
            if match:
                scores[dim] = int(match.group(1))
            else:
                scores[dim] = 0

        total = sum(scores.values())
        return {
            "scores": scores,
            "total_score": total,
            "is_qualified": total >= 18,  # 平均3分以上
            "issues": ["JSON解析失败，使用降级解析"],
            "raw_output": text[:500]
        }


class BatchQualityChecker:
    """批量质量检查器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger):
        self.vault_dir = vault_dir
        self.logger = logger
        self.checker = None

    def init_llm(self, api_key: str | None = None, api_base: str | None = None):
        """初始化LLM"""
        llm_client = LiteLLMClient(
            api_key=api_key,
            api_base=api_base,
            model="MiniMax-M2.5",
            api_type="anthropic",
            temperature=0.1
        )
        self.checker = QualityChecker(llm_client, self.logger)

    def check_directory(self, directory: Path, max_workers: int = 3) -> list[dict]:
        """批量检查目录"""
        if not directory.exists():
            return []

        files = list(directory.glob("*.md"))

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(self.checker.check_file, f): f
                for f in files
            }

            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    print(f"  ✓ Checked: {file_path.name} - Score: {result.get('total_score', 0)}/30")
                except Exception as e:
                    results.append({
                        "file": str(file_path),
                        "error": str(e),
                        "total_score": 0
                    })
                    print(f"  ✗ Error: {file_path.name} - {e}")

        return sorted(results, key=lambda x: x.get("total_score", 0), reverse=True)

    def generate_report(self, results: list[dict]) -> str:
        """生成质检报告"""
        lines = []
        lines.append("# 批量质量质检报告")
        lines.append(f"\n生成时间: {datetime.now().isoformat()}")
        lines.append(f"检查文件数: {len(results)}")

        # 统计
        qualified = sum(1 for r in results if r.get("is_qualified", False))
        failed = len(results) - qualified
        avg_score = sum(r.get("total_score", 0) for r in results) / len(results) if results else 0

        lines.append(f"\n## 统计摘要")
        lines.append(f"\n| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 合格 | {qualified} |")
        lines.append(f"| 不合格 | {failed} |")
        lines.append(f"| 平均分 | {avg_score:.1f}/30 |")

        # 详细表格
        lines.append(f"\n## 详细结果\n")
        lines.append("| 文件名 | 行数 | 总分 | 状态 | 主要问题 |")
        lines.append("|--------|------|------|------|----------|")

        for r in results:
            file_name = Path(r["file"]).name
            line_count = r.get("line_count", 0)
            score = r.get("total_score", 0)
            status = "✅ 合格" if r.get("is_qualified", False) else "❌ 不合格"
            issues = "; ".join(r.get("issues", [])[:2])  # 只显示前2个问题
            lines.append(f"| {file_name} | {line_count} | {score}/30 | {status} | {issues} |")

        # 不合格详情
        unqualified = [r for r in results if not r.get("is_qualified", False)]
        if unqualified:
            lines.append(f"\n## 不合格文件详情\n")
            for r in unqualified:
                lines.append(f"\n### {Path(r['file']).name}")
                lines.append(f"- **总分**: {r.get('total_score', 0)}/30")
                lines.append(f"- **问题**: {', '.join(r.get('issues', []))}")
                lines.append(f"- **建议**: {r.get('suggestions', '无')}")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="批量文章质量质检器")
    parser.add_argument("--dir", type=Path, help="检查目录")
    parser.add_argument("--file", type=Path, help="检查单个文件")
    parser.add_argument("--all", action="store_true", help="检查所有Areas")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--max-workers", type=int, default=3, help="并行 workers")
    parser.add_argument("--vault-dir", type=Path, default=VAULT_DIR, help="Vault根目录")
    parser.add_argument("--dry-run", action="store_true", help="预览模式（只显示要检查的文件）")
    args = parser.parse_args()

    # 初始化
    logger = PipelineLogger(LOG_FILE)
    checker = BatchQualityChecker(args.vault_dir, logger)

    # 处理 --dry-run 模式：只列出要检查的文件
    if args.dry_run:
        print("\n" + "="*60)
        print("QUALITY CHECK (DRY RUN)")
        print("="*60)
        print("预览模式：只列出要检查的文件，不执行实际检查\n")

        files_to_check = []
        if args.dir and args.dir.exists():
            files_to_check = list(args.dir.glob("*.md"))
            print(f"📁 目录: {args.dir}")
        elif args.file and args.file.exists():
            files_to_check = [args.file]
            print(f"📄 文件: {args.file}")
        elif args.all:
            areas = ["AI-Research", "Tools", "Investing", "Programming"]
            for area in areas:
                area_dir = args.vault_dir / "20-Areas" / area / "Topics" / datetime.now().strftime("%Y-%m")
                if area_dir.exists():
                    area_files = list(area_dir.glob("*.md"))
                    files_to_check.extend(area_files)
                    if area_files:
                        print(f"📁 {area}: {len(area_files)} files")

        print(f"\n共计: {len(files_to_check)} 个文件待检查")
        print("="*60)
        return 0

    try:
        checker.init_llm(api_key=args.api_key, api_base=args.api_base)
        print(f"✓ LLM Client initialized")
    except Exception as e:
        print(f"✗ {e}")
        sys.exit(1)

    # 执行检查
    if args.dir:
        print(f"\nChecking directory: {args.dir}")
        results = checker.check_directory(args.dir, max_workers=args.max_workers)
    elif args.file:
        print(f"\nChecking file: {args.file}")
        results = [checker.checker.check_file(args.file)]
    elif args.all:
        # 检查所有Areas
        areas = ["AI-Research", "Tools", "Investing", "Programming"]
        all_results = []
        for area in areas:
            area_dir = args.vault_dir / "20-Areas" / area / "Topics" / datetime.now().strftime("%Y-%m")
            if area_dir.exists():
                print(f"\nChecking {area}...")
                results = checker.check_directory(area_dir, max_workers=args.max_workers)
                all_results.extend(results)
        results = all_results
    else:
        parser.print_help()
        sys.exit(1)

    # 生成报告
    report = checker.generate_report(results)

    # 保存报告
    report_dir = args.vault_dir / "60-Logs" / "quality-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"quality-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n{'='*60}")
    print(f"QUALITY CHECK COMPLETE")
    print(f"{'='*60}")
    print(f"Checked: {len(results)} files")
    print(f"Qualified: {sum(1 for r in results if r.get('is_qualified', False))}")
    print(f"Failed: {sum(1 for r in results if not r.get('is_qualified', False))}")
    print(f"Report saved: {report_file}")

    logger.log("quality_check_complete", {
        "files_checked": len(results),
        "qualified": sum(1 for r in results if r.get("is_qualified", False)),
        "report_file": str(report_file)
    })

    return 0


if __name__ == "__main__":
    sys.exit(main())
