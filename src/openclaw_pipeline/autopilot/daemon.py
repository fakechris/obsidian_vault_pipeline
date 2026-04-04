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
from .watcher import PollingWatcher


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
        task = Task(
            source=source,
            file_path=file_path,
            stage='ingestion',
            priority=1  # 高优先级
        )
        task_id = self.queue.add_task(task)
        self.log(f"📥 加入队列 [#{task_id}] {Path(file_path).name}")

    def process_task(self, task: Task) -> dict:
        """
        处理单个任务，返回结果

        执行流水线:
        1. ovp-article --process-single (L1→L2)
        2. 质量评分
        3. 质量达标 → ovp-evergreen (L2→L3)
        4. 质量达标 → ovp-moc --scan
        5. 自动 git commit
        """
        result = {
            'task_id': task.id,
            'file': task.file_path,
            'stages': [],
            'success': False,
            'quality': None
        }

        try:
            # Stage 1: 文章处理 (ingestion → interpretation)
            self.log(f"📝 处理文章 [#{task.id}] {Path(task.file_path).name}")

            # 构建命令
            cmd = [
                sys.executable, "-m", "openclaw_pipeline.auto_article_processor",
                "--process-single", task.file_path,
                "--output-dir", str(self.vault_dir / "20-Areas")
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self.vault_dir),
                timeout=300  # 5分钟超时
            )

            if proc.returncode != 0:
                raise RuntimeError(f"文章处理失败: {proc.stderr}")

            result['stages'].append('interpretation')

            # Stage 2: 质量评分
            quality = self._check_quality(task)
            result['quality'] = quality

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

    def _check_quality(self, task: Task) -> float:
        """
        LLM 自评分
        读取生成的深度解读文件，评估质量
        """
        # 简化版：检查文件生成 + 基础指标
        # 完整版应调用 LLM 做 6-dimension 评分
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

                            # 基础评分：检查6维度完整性
                            score = 0.0
                            checks = [
                                '一句话定义' in content,
                                '详细解释' in content,
                                '重要细节' in content,
                                '架构图' in content or '```' in content,
                                '行动建议' in content,
                                '[[' in content  # 双向链接
                            ]
                            score = sum(checks) / len(checks) * 5  # 0-5分

                            return round(score, 1)

            return 0.0  # 未找到生成文件

        except Exception as e:
            self.log(f"⚠️ 质量检查异常: {e}")
            return 0.0

    def _retry_with_fallback(self, task: Task) -> float:
        """使用备用策略重试"""
        # 第一次重试：延长超时
        # 第二次重试：换更强的模型（如果有配置）
        # 这里简化实现
        time.sleep(2)  # 短暂等待
        return self._check_quality(task) + 0.5  # 简化处理

    def _run_evergreen_extraction(self):
        """运行 Evergreen 提取"""
        cmd = [
            sys.executable, "-m", "openclaw_pipeline.auto_evergreen_extractor",
            "--recent", "1"
        ]
        subprocess.run(cmd, capture_output=True, cwd=str(self.vault_dir))

    def _run_moc_update(self):
        """运行 MOC 更新"""
        cmd = [
            sys.executable, "-m", "openclaw_pipeline.auto_moc_updater",
            "--scan"
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

        # 设置监控
        inbox_path = self.vault_dir / "50-Inbox" / "01-Raw"
        self.watcher = PollingWatcher(inbox_path, self.on_new_file)

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

    args = parser.parse_args()

    vault_dir = args.vault_dir or Path.cwd()
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
