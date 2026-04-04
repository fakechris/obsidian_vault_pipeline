"""
目录监控 - 检测新文件和变更
"""

import time
from pathlib import Path
from typing import Callable, List, Set, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent


class VaultEventHandler(FileSystemEventHandler):
    """文件系统事件处理器"""

    def __init__(self, callback: Callable[[str, str], None]):
        """
        Args:
            callback: 回调函数(source_type, file_path)
        """
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.md'):
            self.callback('inbox', event.src_path)

    def on_modified(self, event):
        # 可选：处理修改事件
        pass


class DirectoryWatcher:
    """
    目录监控器 - 使用 watchdog 实时监控

    支持两种模式：
    1. 实时监控（observer）- 长时间运行用
    2. 扫描模式（scan）- 启动时同步历史文件
    """

    def __init__(self, inbox_path: Path, callback: Callable[[str, str], None]):
        self.inbox_path = inbox_path
        self.callback = callback
        self.observer: Optional[Observer] = None

    def scan_existing(self) -> List[str]:
        """扫描现有 .md 文件（启动时使用）"""
        files = []
        if self.inbox_path.exists():
            for f in self.inbox_path.glob('*.md'):
                files.append(str(f))
        return files

    def start(self):
        """启动实时监控"""
        if not self.inbox_path.exists():
            self.inbox_path.mkdir(parents=True, exist_ok=True)

        handler = VaultEventHandler(self.callback)
        self.observer = Observer()
        self.observer.schedule(handler, str(self.inbox_path), recursive=False)
        self.observer.start()

    def stop(self):
        """停止监控"""
        if self.observer:
            self.observer.stop()
            self.observer.join()


class PollingWatcher:
    """
    轮询监控器 - 纯 Python 实现，无额外依赖

    适用于：
    - 不想安装 watchdog
    - 网络文件系统（NAS/Samba）
    """

    def __init__(self, inbox_path: Path, callback: Callable[[str, str], None]):
        self.inbox_path = inbox_path
        self.callback = callback
        self.known_files: Set[str] = set()
        self.running = False

    def scan(self) -> List[str]:
        """扫描并返回新文件"""
        new_files = []

        if not self.inbox_path.exists():
            return new_files

        current_files = set(str(f) for f in self.inbox_path.glob('*.md'))

        # 新增文件
        for f in current_files - self.known_files:
            new_files.append(f)
            self.callback('inbox', f)

        self.known_files = current_files
        return new_files

    def run(self, interval: float = 5.0):
        """持续轮询（阻塞）"""
        self.running = True

        # 首次扫描，记录现有文件但不触发回调
        if self.inbox_path.exists():
            self.known_files = set(str(f) for f in self.inbox_path.glob('*.md'))

        print(f"📁 开始监控: {self.inbox_path}")
        print(f"⏱️  检查间隔: {interval}s")
        print(f"📊 现有文件: {len(self.known_files)} 个")

        while self.running:
            new = self.scan()
            if new:
                print(f"📝 检测到 {len(new)} 个新文件")

            time.sleep(interval)

    def stop(self):
        """停止轮询"""
        self.running = False
