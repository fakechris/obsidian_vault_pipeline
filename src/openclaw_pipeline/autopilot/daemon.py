"""
AutoPilot 守护进程 - 前台运行，全自动处理

Usage:
    ovp-autopilot --watch=inbox --parallel=2 --interval=5
    ovp-autopilot --watch=inbox,pinboard --parallel=1

信号处理:
    Ctrl+C  graceful shutdown
"""

import os
import sys
import signal
import argparse
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from .queue import TaskQueue, Task
from ..runtime import resolve_vault_dir
from .watcher import MultiSourceWatcher


class LLMQualityChecker:
    """LLM 深度质量评分器"""

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("AUTO_VAULT_API_KEY")
        self.api_base = api_base or os.getenv("AUTO_VAULT_API_BASE")
        self.model = model or os.getenv("AUTO_VAULT_MODEL", "minimax/MiniMax-M2.5")

        # 导入 litellm
        try:
            import litellm
            self.litellm = litellm
            self.available = True
        except ImportError:
            self.available = False

    def score(self, content: str) -> dict:
        """
        LLM 深度评分：6 维度质量评估
        返回: {"total": 0-5, "dimensions": {...}, "feedback": str}
        """
        if not self.available:
            # Fallback: 启发式评分
            return self._heuristic_score(content)

        prompt = f"""你是一个专业的知识内容质量评估专家。请对以下深度解读内容进行 6 维度质量评分。

评分标准（每项 0-5 分）：
1. **定义清晰度**: 一句话定义是否准确、简洁、无歧义
2. **解释深度**: What/Why/How 是否完整，有无明显遗漏
3. **细节丰富度**: 技术细节是否具体、可验证、有信息量
4. **结构化程度**: 架构图/流程图是否清晰表达核心逻辑
5. **可执行性**: 行动建议是否具体可落地
6. **知识网络**: 双向链接是否合理、有助于知识导航

待评分内容：
```markdown
{content[:4000]}  # 限制长度避免 token 过多
```

请以 JSON 格式输出：
{{
    "dimensions": {{
        "definition": 0-5,
        "explanation": 0-5,
        "details": 0-5,
        "structure": 0-5,
        "actionable": 0-5,
        "linking": 0-5
    }},
    "total": 0-5,  // 平均分
    "feedback": "简要评价优缺点"
}}
"""

        try:
            response = self.litellm.completion(
                model=self.model,
                api_key=self.api_key,
                api_base=self.api_base,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )

            result_text = response.choices[0].message.content

            # 提取 JSON
            json_match = result_text.strip()
            if "```json" in json_match:
                json_match = json_match.split("```json")[1].split("```")[0]
            elif "```" in json_match:
                json_match = json_match.split("```")[1].split("```")[0]

            scores = json.loads(json_match.strip())

            return {
                "total": float(scores.get("total", 0)),
                "dimensions": scores.get("dimensions", {}),
                "feedback": scores.get("feedback", ""),
                "method": "llm"
            }

        except Exception as e:
            # LLM 评分失败，回退到启发式
            result = self._heuristic_score(content)
            result["error"] = str(e)
            return result

    def _heuristic_score(self, content: str) -> dict:
        """启发式评分（无需 LLM）"""
        checks = {
            "definition": '一句话定义' in content or '一句话定義' in content,
            "explanation": '详细解释' in content or '詳細解釋' in content,
            "details": '重要细节' in content or '重要細節' in content,
            "structure": ('架构图' in content or '流程图' in content or
                         '```' in content and ('┌' in content or '▶' in content)),
            "actionable": '行动建议' in content or '行動建議' in content,
            "linking": '[[' in content and ']]' in content
        }

        scores = {k: (5.0 if v else 1.0) for k, v in checks.items()}
        total = sum(scores.values()) / len(scores)

        return {
            "total": round(total, 1),
            "dimensions": scores,
            "feedback": "启发式评分（LLM 不可用）",
            "method": "heuristic"
        }


class AutoPilotDaemon:
    """
    全自动知识管理守护进程

    工作流程:
    1. 监控目录/来源，检测新内容
    2. 创建任务，加入 SQLite 队列
    3. 工作线程从队列取任务执行
    4. 质量检查，失败自动重试
    5. 完成自动提交 Git
    """

    def __init__(
        self,
        vault_dir: Path,
        watch_sources: List[str],
        parallel: int = 1,
        interval: float = 5.0,
        quality_threshold: float = 3.0,
        auto_commit: bool = True
    ):
        self.vault_dir = Path(vault_dir)
        self.watch_sources = watch_sources
        self.parallel = parallel
        self.interval = interval
        self.quality_threshold = quality_threshold
        self.auto_commit = auto_commit

        # 组件初始化
        self.queue = TaskQueue(self.vault_dir / "60-Logs" / "autopilot.db")
        self.watcher: Optional[PollingWatcher] = None
        self.executor: Optional[ThreadPoolExecutor] = None

        # 质量检查器
        self.quality_checker = LLMQualityChecker()

        # 状态
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.print_lock = Lock()

        # 信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """优雅关闭"""
        self.log("🛑 收到关闭信号，正在优雅退出...")
        self.running = False

    def log(self, message: str):
        """带时间戳的日志"""
        with self.print_lock:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {message}")

    def on_new_file(self, source: str, file_path: str):
        """新文件回调"""
        # 根据来源设置处理阶段
        if source == "pinboard":
            stage = "github"
        elif source == "clippings":
            stage = "ingestion"  # clippings 迁移后作为 raw 处理
        else:
            stage = "ingestion"

        task = Task(
            source=source,
            file_path=file_path,
            stage=stage,
            priority=1  # 高优先级
        )
        task_id = self.queue.add_task(task)
        self.log(f"📥 加入队列 [#{task_id}] {source}:{Path(file_path).name}")

    def process_task(self, task: Task) -> dict:
        """
        处理单个任务，返回结果

        执行流水线:
        1. 根据source类型和url_type选择处理器:
           - pinboard/github: ovp-github
           - pinboard/paper: ovp-paper
           - pinboard/article: ovp-article
           - pinboard/website: ovp-article
           - pinboard/social: 跳过
           - raw/clippings: ovp-article
        2. 质量评分
        3. 质量达标 → ovp-evergreen (L2→L3)
        4. 质量达标 → ovp-moc --scan
        5. 自动 git commit
        """
        result = {
            'task_id': task.id,
            'file': task.file_path,
            'source': task.source,
            'stages': [],
            'success': False,
            'quality': None
        }

        try:
            # Stage 1: 根据source和url_type选择处理器
            self.log(f"📝 处理 [{task.source}] [#{task.id}] {Path(task.file_path).name}")

            if task.source == "pinboard":
                # 读取 pinboard 文件的 frontmatter 获取 url_type
                url_type = self._get_pinboard_url_type(task.file_path)

                if url_type == "github":
                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.auto_github_processor",
                        "--process-single", task.file_path,
                        "--vault-dir", str(self.vault_dir),
                    ]
                elif url_type == "paper":
                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.auto_paper_processor",
                        "--process-single", task.file_path,
                        "--vault-dir", str(self.vault_dir),
                    ]
                elif url_type in ("article", "website"):
                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.auto_article_processor",
                        "--process-single", task.file_path,
                        "--vault-dir", str(self.vault_dir),
                    ]
                else:
                    self.log(f"⏭️ 跳过 social 类型: {task.file_path}")
                    result['stages'].append('skipped')
                    return result
            else:
                # Raw/Clippings 使用 ovp-article
                cmd = [
                    sys.executable, "-m", "openclaw_pipeline.auto_article_processor",
                    "--process-single", task.file_path,
                    "--vault-dir", str(self.vault_dir),
                ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self.vault_dir),
                timeout=300  # 5分钟超时
            )

            if proc.returncode != 0:
                raise RuntimeError(f"处理失败: {proc.stderr[:200]}")

            result['stages'].append('interpretation')

            # Stage 2: 质量评分
            quality, dimensions = self._check_quality(task)
            result['quality'] = quality
            result['quality_dimensions'] = dimensions

            if quality < self.quality_threshold:
                self.log(f"⚠️ 质量不足 ({quality:.1f}/{self.quality_threshold})，尝试重试")
                # 自动重试逻辑：换更强模型或调整参数
                quality = self._retry_with_fallback(task)
                result['quality'] = quality

            if quality >= self.quality_threshold:
                # Stage 3: 提取 Evergreen
                self._run_evergreen_extraction()
                result['stages'].append('evergreen')

                # Stage 4: 更新 MOC
                self._run_moc_update()
                result['stages'].append('moc')

                # Stage 5: 自动提交
                if self.auto_commit:
                    self._auto_commit(task, result)

                result['success'] = True
                self.processed_count += 1
            else:
                raise RuntimeError(f"质量不达标: {quality:.1f} < {self.quality_threshold}")

        except Exception as e:
            result['error'] = str(e)
            self.log(f"❌ 处理失败 [#{task.id}] {e}")
            self.failed_count += 1

        return result

    def _get_pinboard_url_type(self, file_path: str) -> str:
        """从 pinboard 文件的 frontmatter 读取 url_type"""
        try:
            import re
            content = Path(file_path).read_text(encoding="utf-8")
            # frontmatter 中 type: pinboard-{url_type}
            match = re.search(r'^type:\s*pinboard-(\w+)', content, re.MULTILINE)
            if match:
                return match.group(1)
        except Exception:
            pass
        return "unknown"

    def _check_quality(self, task: Task) -> tuple[float, dict]:
        """
        LLM 深度评分
        读取生成的深度解读文件，使用 LLM 进行 6 维度质量评估
        返回: (总分, 详细评分维度)
        """
        try:
            # 找到生成的解读文件
            file_name = Path(task.file_path).stem
            areas_dir = self.vault_dir / "20-Areas"

            # 搜索生成的文件
            for area_dir in areas_dir.iterdir():
                if area_dir.is_dir():
                    for month_dir in area_dir.glob("Topics/202*"):
                        target = month_dir / f"{file_name}_深度解读.md"
                        if target.exists():
                            content = target.read_text(encoding='utf-8')

                            # 使用 LLM 进行深度评分
                            if self.quality_checker.available:
                                self.log(f"🔍 LLM 质量评分 [#{task.id}] {target.name}")
                                result = self.quality_checker.score(content)
                                total = result.get("total", 0)
                                dimensions = result.get("dimensions", {})
                                method = result.get("method", "unknown")

                                if method == "heuristic":
                                    self.log(f"⚠️ 使用启发式评分 (LLM 不可用): {total:.1f}")
                                else:
                                    self.log(f"✅ LLM 评分完成: {total:.1f}/5")

                                return total, dimensions
                            else:
                                # LLM 不可用，回退到启发式评分
                                self.log(f"⚠️ LLM 不可用，使用启发式评分 [#{task.id}]")
                                result = self.quality_checker._heuristic_score(content)
                                return result.get("total", 0), result.get("dimensions", {})

            return 0.0, {}  # 未找到生成文件

        except Exception as e:
            self.log(f"⚠️ 质量检查异常: {e}")
            return 0.0, {}

    def _retry_with_fallback(self, task: Task) -> float:
        """使用备用策略重试（简化实现）"""
        time.sleep(2)
        # 重新检查质量（可能文件已更新）
        quality, dimensions = self._check_quality(task)
        # 更新任务的维度信息以便调试
        return quality

    def _run_evergreen_extraction(self):
        """运行 Evergreen 提取"""
        cmd = [
            sys.executable, "-m", "openclaw_pipeline.auto_evergreen_extractor",
            "--recent", "1",
            "--vault-dir", str(self.vault_dir),
        ]
        subprocess.run(cmd, capture_output=True, cwd=str(self.vault_dir))

    def _run_moc_update(self):
        """运行 MOC 更新"""
        cmd = [
            sys.executable, "-m", "openclaw_pipeline.auto_moc_updater",
            "--scan",
            "--vault-dir", str(self.vault_dir),
        ]
        subprocess.run(cmd, capture_output=True, cwd=str(self.vault_dir))

    def _auto_commit(self, task: Task, result: dict):
        """自动 Git 提交"""
        try:
            # 添加所有变更
            subprocess.run(
                ["git", "add", "-A"],
                capture_output=True,
                cwd=str(self.vault_dir)
            )

            # 提交
            file_name = Path(task.file_path).name
            commit_msg = f"auto: {file_name} (q:{result.get('quality', 0):.1f})"

            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                capture_output=True,
                cwd=str(self.vault_dir)
            )

            self.log(f"✅ 已提交: {commit_msg}")

        except Exception as e:
            self.log(f"⚠️ 提交失败: {e}")

    def worker_loop(self):
        """工作线程主循环"""
        while self.running:
            # 获取待处理任务
            tasks = self.queue.get_pending(limit=1)

            if not tasks:
                time.sleep(1)
                continue

            task = tasks[0]

            # 认领任务
            if not self.queue.claim_task(task.id):
                continue  # 被其他线程认领

            # 处理
            result = self.process_task(task)

            # 更新状态
            if result['success']:
                self.queue.complete_task(task.id, result)
            else:
                self.queue.fail_task(
                    task.id,
                    result.get('error', 'Unknown error'),
                    max_retries=3
                )

    def _run_pinboard(self):
        """运行 Pinboard 抓取"""
        self.log("📡 抓取 Pinboard 书签...")
        cmd = [
            sys.executable, "-m", "openclaw_pipeline.unified_pipeline_enhanced",
            "--step", "pinboard"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.vault_dir))
        if result.returncode == 0:
            self.log("✅ Pinboard 抓取完成")
        else:
            self.log(f"⚠️ Pinboard 抓取失败: {result.stderr[:200]}")

    def _run_clippings(self):
        """运行 Clippings 处理"""
        self.log("📚 处理 Clippings...")
        cmd = [
            sys.executable, "-m", "openclaw_pipeline.unified_pipeline_enhanced",
            "--step", "clippings"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.vault_dir))
        if result.returncode == 0:
            self.log("✅ Clippings 处理完成")
        else:
            self.log(f"⚠️ Clippings 处理失败: {result.stderr[:200]}")

    def run(self):
        """主循环 - 前台运行"""
        self.running = True

        # 启动信息
        self.log("🤖 AutoPilot 启动")
        self.log(f"📁 Vault: {self.vault_dir}")
        self.log(f"👀 监控: {', '.join(self.watch_sources)}")
        self.log(f"🔧 并发: {self.parallel}")
        self.log(f"🎯 质量阈值: {self.quality_threshold}")
        self.log("─" * 50)

        # 🔑 第一入口：运行 pinboard 和 clippings 抓取新内容
        if "pinboard" in self.watch_sources:
            self._run_pinboard()
        if "clippings" in self.watch_sources:
            self._run_clippings()

        # 设置多来源监控
        source_map = {}
        if "inbox" in self.watch_sources:
            source_map["inbox"] = self.vault_dir / "50-Inbox" / "01-Raw"
        if "pinboard" in self.watch_sources:
            source_map["pinboard"] = self.vault_dir / "50-Inbox" / "02-Pinboard"
        if "clippings" in self.watch_sources:
            source_map["clippings"] = self.vault_dir / "Clippings"

        self.watcher = MultiSourceWatcher(source_map, self.on_new_file)

        # 首次扫描现有文件
        existing = self.watcher.scan()
        if existing:
            self.log(f"📚 发现 {len(existing)} 个现有文件待处理")

        # 启动工作线程池
        self.executor = ThreadPoolExecutor(max_workers=self.parallel)
        for _ in range(self.parallel):
            self.executor.submit(self.worker_loop)

        # 监控循环（主线程）
        try:
            self.watcher.run(interval=self.interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        """优雅关闭"""
        self.log("🛑 正在关闭...")
        self.running = False

        if self.executor:
            self.executor.shutdown(wait=True)

        if self.watcher:
            self.watcher.stop()

        # 统计
        stats = self.queue.get_stats()
        self.log("─" * 50)
        self.log("📊 本次运行统计")
        self.log(f"   处理成功: {self.processed_count}")
        self.log(f"   处理失败: {self.failed_count}")
        self.log(f"   队列状态: {stats}")
        self.log("👋 AutoPilot 已停止")


def print_cost_warning():
    """打印Token消耗风险提示 - 中英文双语"""
    warning = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                           ⚠️  COST WARNING / 费用警告 ⚠️                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  EN: AutoPilot mode may consume SIGNIFICANT TOKENS (potentially $10-$100+)   ║
║      Each processed article requires 3-4 LLM calls:                            ║
║      - Article interpretation (L1→L2): ~4K-8K tokens                          ║
║      - Quality scoring (6-dimension LLM evaluation): ~2K-4K tokens           ║
║      - Evergreen extraction (L2→L3): ~2K-4K tokens                           ║
║      - MOC indexing: ~1K-2K tokens                                             ║
║                                                                               ║
║  CN: AutoPilot 模式可能消耗大量 Token（可能 $10-$100+）                        ║
║      每篇文章处理需要 3-4 次 LLM 调用：                                        ║
║      - 深度解读生成 (L1→L2): ~4K-8K tokens                                     ║
║      - 质量评分 (6维度LLM评估): ~2K-4K tokens                                  ║
║      - Evergreen提取 (L2→L3): ~2K-4K tokens                                    ║
║      - MOC索引更新: ~1K-2K tokens                                              ║
║                                                                               ║
║  💡 RECOMMENDATION / 建议:                                                     ║
║     • Use monthly "Coding Plan" / 使用包月 "Coding Plan"                        ║
║     • Monitor costs with --parallel=1 initially / 初始建议 --parallel=1        ║
║     • Test with small batches first / 先小批量测试                              ║
║                                                                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    print(warning)


def confirm_continue() -> bool:
    """询问用户是否确认继续"""
    print("Do you want to continue? / 是否继续? (yes/no): ", end="")
    try:
        response = input().strip().lower()
        return response in ('yes', 'y', '是', '继续', 'continue', 'c')
    except (EOFError, KeyboardInterrupt):
        print("\nAborted. / 已取消。")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="AutoPilot - 全自动知识管理守护进程"
    )
    parser.add_argument(
        "--watch",
        default="inbox",
        help="监控来源，逗号分隔 (inbox,pinboard)"
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="并发处理数 (默认: 1)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="检查间隔秒数 (默认: 5)"
    )
    parser.add_argument(
        "--quality",
        type=float,
        default=3.0,
        help="质量阈值 0-5 (默认: 3.0)"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault 目录 (默认: 当前目录)"
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="禁用自动 Git 提交"
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip cost warning confirmation / 跳过费用警告确认"
    )

    args = parser.parse_args()

    # 打印风险提示
    if not args.yes:
        print_cost_warning()
        if not confirm_continue():
            sys.exit(0)
        print("\n" + "─" * 50)

    vault_dir = resolve_vault_dir(args.vault_dir)
    watch_sources = [s.strip() for s in args.watch.split(",")]

    daemon = AutoPilotDaemon(
        vault_dir=vault_dir,
        watch_sources=watch_sources,
        parallel=args.parallel,
        interval=args.interval,
        quality_threshold=args.quality,
        auto_commit=not args.no_commit
    )

    daemon.run()


if __name__ == "__main__":
    main()
