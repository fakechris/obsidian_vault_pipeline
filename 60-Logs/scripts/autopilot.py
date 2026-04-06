#!/usr/bin/env python3
"""
OpenClaw AutoPilot - 全自动知识管理守护进程

激进自动化模式：LLM全权维护，人类只监控

Usage:
    # 标准模式（带费用警告）
    ./60-Logs/scripts/autopilot.py --watch=inbox --parallel=1

    # 跳过警告（确认了解费用风险）
    ./60-Logs/scripts/autopilot.py --yes

    # 只监控不处理（dry-run）
    ./60-Logs/scripts/autopilot.py --dry-run

Features:
    - 监控目录自动检测新内容
    - SQLite队列持久化
    - 6维度质量评分
    - 失败自动重试
    - 自动Git提交

Cost Warning:
    每篇文章处理消耗 ~10K-20K tokens
    批量处理可能产生 $10-$100+ 费用
    建议使用包月计划或限制并行数
"""

import os
import sys
import signal
import argparse
import time
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# 将脚本目录加入路径
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from autopilot_queue import TaskQueue, Task


class QualityChecker:
    """启发式质量评分器（简化版，无需LLM API）"""

    def heuristic_score(self, file_path: Path) -> dict:
        """基于文件特征的启发式评分"""
        try:
            content = file_path.read_text(encoding='utf-8')
        except:
            return {"total": 0, "method": "error"}

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
            "method": "heuristic"
        }


class AutoPilotDaemon:
    """AutoPilot守护进程"""

    def __init__(
        self,
        vault_dir: Path,
        watch_sources: List[str],
        parallel: int = 1,
        interval: float = 5.0,
        quality_threshold: float = 3.0,
        auto_commit: bool = True,
        dry_run: bool = False
    ):
        self.vault_dir = Path(vault_dir)
        self.watch_sources = watch_sources
        self.parallel = parallel
        self.interval = interval
        self.quality_threshold = quality_threshold
        self.auto_commit = auto_commit
        self.dry_run = dry_run

        self.queue = TaskQueue(self.vault_dir / "60-Logs" / "autopilot.db")
        self.quality_checker = QualityChecker()

        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.print_lock = Lock()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """优雅关闭"""
        self.log("🛑 收到关闭信号，正在优雅退出...")
        self.running = False

    def log(self, message: str):
        """带时间戳日志"""
        with self.print_lock:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {message}")

    def on_new_file(self, source: str, file_path: str):
        """新文件回调"""
        task = Task(
            source=source,
            file_path=file_path,
            stage='ingestion',
            priority=1  # 高优先级
        )
        task_id = self.queue.add_task(task)
        self.log(f"📥 加入队列 [#{task_id}] {Path(file_path).name}")

    def process_task(self, task: Task) -> dict:
        """处理单个任务"""
        result = {
            'task_id': task.id,
            'file': task.file_path,
            'stages': [],
            'success': False,
            'quality': None
        }

        if self.dry_run:
            self.log(f"🔍 [DRY-RUN] 将处理: {task.file_path}")
            result['success'] = True
            return result

        try:
            # Stage 1: 运行Pipeline（使用现有unified_pipeline.py）
            self.log(f"📝 处理文章 [#{task.id}] {Path(task.file_path).name}")

            cmd = [
                sys.executable, str(SCRIPTS_DIR / "unified_pipeline.py"),
                "--from-step", "articles",
                "--batch-size", "1"
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self.vault_dir),
                timeout=600
            )

            if proc.returncode != 0:
                raise RuntimeError(f"Pipeline失败: {proc.stderr[:200]}")

            result['stages'].append('pipeline')

            # Stage 2: 质量检查
            # 找到生成的解读文件
            quality = self._check_quality(task)
            result['quality'] = quality

            if quality < self.quality_threshold:
                self.log(f"⚠️ 质量不足 ({quality:.1f}/{self.quality_threshold})，标记待审核")
                result['needs_review'] = True
            else:
                # Stage 3: 自动提交
                if self.auto_commit:
                    self._auto_commit(task, result)
                result['success'] = True
                self.processed_count += 1

        except Exception as e:
            result['error'] = str(e)
            self.log(f"❌ 处理失败 [#{task.id}] {e}")
            self.failed_count += 1

        return result

    def _check_quality(self, task: Task) -> float:
        """质量评分"""
        try:
            # 简化：检查原始文件质量
            # 实际应该检查生成的深度解读
            result = self.quality_checker.heuristic_score(Path(task.file_path))
            return result.get("total", 0)
        except:
            return 0.0

    def _auto_commit(self, task: Task, result: dict):
        """自动Git提交"""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                capture_output=True,
                cwd=str(self.vault_dir)
            )

            file_name = Path(task.file_path).name
            commit_msg = f"autopilot: {file_name}"

            r = subprocess.run(
                ["git", "commit", "-m", commit_msg],
                capture_output=True,
                cwd=str(self.vault_dir)
            )

            if r.returncode == 0:
                self.log(f"✅ 已提交: {commit_msg}")
            else:
                self.log(f"⚠️ 提交失败或无变更")

        except Exception as e:
            self.log(f"⚠️ 提交异常: {e}")

    def worker_loop(self):
        """工作线程"""
        while self.running:
            tasks = self.queue.get_pending(limit=1)

            if not tasks:
                time.sleep(1)
                continue

            task = tasks[0]

            if not self.queue.claim_task(task.id):
                continue

            result = self.process_task(task)

            if result.get('success'):
                self.queue.complete_task(task.id, result)
            else:
                self.queue.fail_task(
                    task.id,
                    result.get('error', 'Unknown'),
                    max_retries=3
                )

    def scan_existing(self) -> int:
        """扫描现有待处理文件"""
        count = 0
        inbox_path = self.vault_dir / "50-Inbox" / "01-Raw"

        if inbox_path.exists():
            for f in inbox_path.glob("*.md"):
                # 检查是否已在队列
                task = Task(source="inbox", file_path=str(f), priority=2)
                self.queue.add_task(task)
                count += 1

        return count

    def run(self):
        """主循环"""
        self.running = True

        self.log("🤖 OpenClaw AutoPilot 启动")
        self.log(f"📁 Vault: {self.vault_dir}")
        self.log(f"👀 监控: {', '.join(self.watch_sources)}")
        self.log(f"🔧 并发: {self.parallel}")
        self.log(f"🎯 质量阈值: {self.quality_threshold}")
        if self.dry_run:
            self.log("🔍 DRY-RUN模式: 只检测不处理")
        self.log("─" * 50)

        # 首次扫描
        existing = self.scan_existing()
        if existing:
            self.log(f"📚 发现 {existing} 个现有文件待处理")

        # 启动工作线程
        executor = ThreadPoolExecutor(max_workers=self.parallel)
        for _ in range(self.parallel):
            executor.submit(self.worker_loop)

        # 主循环（监控新文件）
        inbox_path = self.vault_dir / "50-Inbox" / "01-Raw"
        known_files = set()

        try:
            while self.running:
                if inbox_path.exists():
                    current_files = set(inbox_path.glob("*.md"))
                    new_files = current_files - known_files

                    for f in new_files:
                        self.on_new_file("inbox", str(f))

                    known_files = current_files

                time.sleep(self.interval)

        except KeyboardInterrupt:
            pass
        finally:
            self.log("🛑 正在关闭...")
            self.running = False
            executor.shutdown(wait=True)

            stats = self.queue.get_stats()
            self.log("─" * 50)
            self.log("📊 本次运行统计")
            self.log(f"   处理成功: {self.processed_count}")
            self.log(f"   处理失败: {self.failed_count}")
            self.log(f"   队列状态: {stats}")
            self.log("👋 AutoPilot 已停止")


def print_cost_warning():
    """费用警告"""
    warning = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                           ⚠️  COST WARNING / 费用警告 ⚠️                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  EN: AutoPilot may consume SIGNIFICANT TOKENS (potentially $10-$100+)       ║
║      Each article requires LLM calls for interpretation, quality check, etc.  ║
║                                                                               ║
║  CN: AutoPilot 可能消耗大量 Token（可能 $10-$100+）                            ║
║      每篇文章需要多次LLM调用：深度解读、质量评分、Evergreen提取等              ║
║                                                                               ║
║  💡 RECOMMENDATION / 建议:                                                     ║
║     • Use monthly Coding Plan / 使用包月计划                                  ║
║     • Monitor with --parallel=1 / 初始建议并行=1                               ║
║     • Use --dry-run first / 先用dry-run预览                                   ║
║                                                                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    print(warning)


def confirm() -> bool:
    """确认继续"""
    try:
        r = input("Continue? / 是否继续? (yes/no): ").strip().lower()
        return r in ('yes', 'y', '是')
    except:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw AutoPilot - 全自动知识管理"
    )
    parser.add_argument("--watch", default="inbox", help="监控来源")
    parser.add_argument("--parallel", type=int, default=1, help="并发数")
    parser.add_argument("--interval", type=float, default=5.0, help="检查间隔")
    parser.add_argument("--quality", type=float, default=3.0, help="质量阈值")
    parser.add_argument("--no-commit", action="store_true", help="禁用自动提交")
    parser.add_argument("--dry-run", action="store_true", help="只检测不处理")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过警告")

    args = parser.parse_args()

    if not args.yes and not args.dry_run:
        print_cost_warning()
        if not confirm():
            sys.exit(0)
        print("\n" + "─" * 50)

    # 检测vault目录
    try:
        git_root = subprocess.check_output(
            ["git", "rev-parse", "show-toplevel"],
            text=True
        ).strip()
        vault_dir = Path(git_root)
    except:
        vault_dir = Path.cwd()

    daemon = AutoPilotDaemon(
        vault_dir=vault_dir,
        watch_sources=[s.strip() for s in args.watch.split(",")],
        parallel=args.parallel,
        interval=args.interval,
        quality_threshold=args.quality,
        auto_commit=not args.no_commit,
        dry_run=args.dry_run
    )

    daemon.run()


if __name__ == "__main__":
    main()
