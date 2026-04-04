"""
SQLite 任务队列 - 持久化待处理任务
"""

import sqlite3
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List


@dataclass
class Task:
    """单个处理任务"""
    id: Optional[int] = None
    source: str = ""  # inbox / pinboard / api
    file_path: Optional[str] = None
    url: Optional[str] = None
    status: str = "pending"  # pending / processing / completed / failed
    stage: str = "ingestion"  # ingestion / interpretation / evergreen / moc
    priority: int = 2  # 1=high, 2=normal, 3=low
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[str] = None  # JSON string
    error: Optional[str] = None
    retry_count: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class TaskQueue:
    """
    基于 SQLite 的持久化任务队列

    表结构:
    - tasks: 主任务表
    - processing_lock: 分布式锁（支持多进程）
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.cwd() / "60-Logs" / "autopilot.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    file_path TEXT,
                    url TEXT,
                    status TEXT DEFAULT 'pending',
                    stage TEXT DEFAULT 'ingestion',
                    priority INTEGER DEFAULT 2,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result TEXT,
                    error TEXT,
                    retry_count INTEGER DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_priority ON tasks(priority, created_at)
            """)

            conn.commit()

    def add_task(self, task: Task) -> int:
        """添加任务到队列，返回任务ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO tasks (source, file_path, url, status, stage,
                                 priority, created_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.source, task.file_path, task.url, task.status,
                task.stage, task.priority, task.created_at, task.retry_count
            ))
            conn.commit()
            return cursor.lastrowid

    def get_pending(self, limit: int = 10) -> List[Task]:
        """获取待处理任务（按优先级排序）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM tasks
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
            """, (limit,)).fetchall()

            return [self._row_to_task(row) for row in rows]

    def claim_task(self, task_id: int) -> bool:
        """认领任务（原子操作）"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE tasks
                SET status = 'processing', started_at = ?
                WHERE id = ? AND status = 'pending'
            """, (now, task_id))
            conn.commit()
            return cursor.rowcount > 0

    def complete_task(self, task_id: int, result: Optional[dict] = None):
        """标记任务完成"""
        now = datetime.now().isoformat()
        result_json = json.dumps(result, ensure_ascii=False) if result else None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE tasks
                SET status = 'completed', completed_at = ?, result = ?
                WHERE id = ?
            """, (now, result_json, task_id))
            conn.commit()

    def fail_task(self, task_id: int, error: str, max_retries: int = 3):
        """标记任务失败，可自动重试"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT retry_count FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()

            retry_count = row[0] + 1 if row else 1

            if retry_count < max_retries:
                # 重试：回到 pending 状态
                conn.execute("""
                    UPDATE tasks
                    SET status = 'pending', retry_count = ?, error = ?
                    WHERE id = ?
                """, (retry_count, error, task_id))
            else:
                # 超过重试次数，标记为失败
                now = datetime.now().isoformat()
                conn.execute("""
                    UPDATE tasks
                    SET status = 'failed', completed_at = ?, error = ?, retry_count = ?
                    WHERE id = ?
                """, (now, error, retry_count, task_id))

            conn.commit()

    def get_stats(self) -> dict:
        """获取队列统计"""
        with sqlite3.connect(self.db_path) as conn:
            counts = conn.execute("""
                SELECT status, COUNT(*) FROM tasks
                WHERE created_at > date('now', '-7 days')
                GROUP BY status
            """).fetchall()

            return {
                row[0]: row[1] for row in counts
            }

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """数据库行转 Task 对象"""
        return Task(
            id=row['id'],
            source=row['source'],
            file_path=row['file_path'],
            url=row['url'],
            status=row['status'],
            stage=row['stage'],
            priority=row['priority'],
            created_at=row['created_at'],
            started_at=row['started_at'],
            completed_at=row['completed_at'],
            result=row['result'],
            error=row['error'],
            retry_count=row['retry_count']
        )
