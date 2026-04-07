"""
目录监控 - 检测新文件和变更

支持多来源监控:
- 01-Raw: 原始文档 (source=inbox)
- 02-Pinboard: Pinboard书签导出 (source=pinboard)
- Clippings: Kindle电子书 clippings (source=clippings)
"""

import time
from pathlib import Path
from typing import Callable, List, Set, Optional, Dict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent


class VaultEventHandler(FileSystemEventHandler):
    """文件系统事件处理器"""

    def __init__(self, source_map: Dict[str, Path], callback: Callable[[str, str], None]):
        """
        Args:
            source_map: source_name -> directory path 映射
            callback: 回调函数(source_type, file_path)
        """
        self.source_map = source_map
        self.callback = callback

    def _get_source(self, file_path: str) -> Optional[str]:
        """根据文件路径判断来源"""
        for source, dir_path in self.source_map.items():
            if file_path.startswith(str(dir_path)):
                return source
        return None

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.md'):
            source = self._get_source(event.src_path)
            if source:
                self.callback(source, event.src_path)

    def on_modified(self, event):
        pass


class MultiSourceWatcher:
    """
    多来源监控器 - 同时监控多个目录

    支持来源:
    - inbox: 50-Inbox/01-Raw/
    - pinboard: 50-Inbox/02-Pinboard/
    - clippings: Clippings/
    """

    def __init__(self, source_map: Dict[str, Path], callback: Callable[[str, str], None]):
        """
        Args:
            source_map: source_name -> directory path 映射
            callback: 回调函数(source_type, file_path)
        """
        self.source_map = source_map
        self.callback = callback
        self.known_files: Set[str] = set()
        self.running = False
        self.observer: Optional[Observer] = None

    def scan(self) -> List[str]:
        """扫描所有来源目录，返回新文件"""
        new_files = []

        for source, dir_path in self.source_map.items():
            if not dir_path.exists():
                continue

            current_files = set(str(f) for f in dir_path.glob('*.md'))

            # 新增文件
            for f in current_files - self.known_files:
                new_files.append(f)
                self.callback(source, f)

            # 更新已知文件集合
            self.known_files |= current_files

        return new_files

    def run(self, interval: float = 5.0):
        """持续轮询（阻塞）"""
        self.running = True

        # 首次扫描，记录现有文件但不触发回调
        for source, dir_path in self.source_map.items():
            if dir_path.exists():
                self.known_files |= set(str(f) for f in dir_path.glob('*.md'))

        # 打印监控信息
        for source, dir_path in self.source_map.items():
            count = len([f for f in dir_path.glob('*.md')]) if dir_path.exists() else 0
            print(f"📁 监控 [{source}]: {dir_path} ({count} 个文件)")

        print(f"⏱️ 检查间隔: {interval}s")
        print(f"📊 现有文件: {len(self.known_files)} 个")
        print("-" * 50)

        while self.running:
            new = self.scan()
            if new:
                print(f"📝 检测到 {len(new)} 个新文件")

            time.sleep(interval)

    def stop(self):
        """停止轮询"""
        self.running = False

    def start_realtime(self):
        """启动实时监控（使用 watchdog）"""
        if self.observer:
            return  # 已启动

        handler = VaultEventHandler(self.source_map, self.callback)
        self.observer = Observer()

        for source, dir_path in self.source_map.items():
            if dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)
                self.observer.schedule(handler, str(dir_path), recursive=False)

        self.observer.start()
        self.running = True

    def stop_realtime(self):
        """停止实时监控"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None


# 保留向后兼容的 PollingWatcher
class PollingWatcher(MultiSourceWatcher):
    """轮询监控器 - 纯 Python 实现，无额外依赖"""

    def __init__(self, inbox_path: Path, callback: Callable[[str, str], None]):
        # 兼容单目录模式
        super().__init__({"inbox": inbox_path}, callback)


class DirectoryWatcher(MultiSourceWatcher):
    """实时监控器 - 使用 watchdog"""

    def __init__(self, inbox_path: Path, callback: Callable[[str, str], None]):
        super().__init__({"inbox": inbox_path}, callback)

    def scan_existing(self) -> List[str]:
        """扫描现有 .md 文件（启动时使用）"""
        files = []
        for source, dir_path in self.source_map.items():
            if dir_path.exists():
                for f in dir_path.glob('*.md'):
                    files.append(str(f))
        return files

    def start(self):
        """启动实时监控"""
        for source, dir_path in self.source_map.items():
            if not dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)

        self.start_realtime()

    def stop(self):
        """停止监控"""
        self.stop_realtime()
