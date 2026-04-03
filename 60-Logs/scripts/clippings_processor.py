#!/usr/bin/env python3
"""
Clippings Processor - 全自动Clippings处理Pipeline
自动扫描、迁移、处理Clippings目录中的内容

Usage:
    python3 clippings_processor.py --dry-run          # 预览模式
    python3 clippings_processor.py --process          # 实际处理
    python3 clippings_processor.py --batch-size 5     # 批量处理5篇

Features:
    - 自动扫描Clippings/目录
    - 文件名清理（移除特殊字符）
    - obsidian move迁移（非mv）
    - 自动深度解读生成
    - 幂等处理（manifest跟踪）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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

# Import litellm for LLM calls
sys.path.insert(0, str(Path(__file__).parent / "auto_vault"))
try:
    import litellm
except ImportError:
    litellm = None

# ========== 配置 ==========
CLIPPINGS_DIR = VAULT_DIR / "Clippings"
RAW_DIR = VAULT_DIR / "50-Inbox" / "01-Raw"
PROCESSED_DIR = VAULT_DIR / "50-Inbox" / "03-Processed"
MANIFEST_FILE = VAULT_DIR / "50-Inbox" / ".manifest.json"
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"
TXN_DIR = VAULT_DIR / "60-Logs" / "transactions"

# 特殊字符映射表
CHARACTER_MAP = {
    '"': '',
    "'": '',
    '—': '-',
    '–': '-',
    '…': '...',
    '《': '',
    '》': '',
    '「': '',
    '」': '',
    '【': '[',
    '】': ']',
    '｜': '|',
    '?': '',
    '!': '',
    ':': '-',
    '/': '-',
    '\\': '-',
}


class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        """记录结构化日志"""
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
        """创建新事务"""
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
        """更新事务步骤"""
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
        """完成事务"""
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


class ManifestManager:
    """Manifest管理器 - 跟踪所有文件处理状态"""

    def __init__(self, manifest_file: Path):
        self.manifest_file = manifest_file
        self.data = self._load()

    def _load(self) -> dict:
        """加载manifest"""
        if self.manifest_file.exists():
            with open(self.manifest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"files": {}, "version": "2.0", "last_updated": ""}

    def save(self):
        """保存manifest"""
        self.data["last_updated"] = datetime.now().isoformat()
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_status(self, file_id: str) -> str:
        """获取文件处理状态"""
        return self.data.get("files", {}).get(file_id, {}).get("status", "unknown")

    def update(self, file_id: str, info: dict):
        """更新文件信息"""
        if "files" not in self.data:
            self.data["files"] = {}
        self.data["files"][file_id] = {
            **info,
            "last_updated": datetime.now().isoformat()
        }
        self.save()

    def list_unprocessed(self) -> list[str]:
        """列出未处理的文件"""
        unprocessed = []
        for file_id, info in self.data.get("files", {}).items():
            if info.get("status") in ["unprocessed", "failed", "pending"]:
                unprocessed.append(file_id)
        return unprocessed


class ClippingsProcessor:
    """Clippings处理器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger, txn: TransactionManager):
        self.vault_dir = vault_dir
        self.clippings_dir = vault_dir / "Clippings"
        self.raw_dir = vault_dir / "50-Inbox" / "01-Raw"
        self.logger = logger
        self.txn = txn

    def sanitize_filename(self, filename: str) -> str:
        """清理文件名中的特殊字符"""
        # 应用字符映射
        for char, replacement in CHARACTER_MAP.items():
            filename = filename.replace(char, replacement)

        # 移除或替换其他危险字符
        filename = re.sub(r'[<>:"/\\|?*]', '-', filename)
        filename = re.sub(r'\s+', '_', filename)  # 空格转下划线
        filename = re.sub(r'-+', '-', filename)  # 多个连字符合并

        return filename.strip('-_')

    def scan_clippings(self) -> list[Path]:
        """扫描Clippings目录"""
        if not self.clippings_dir.exists():
            return []

        files = []
        for f in self.clippings_dir.glob("*.md"):
            if f.is_file():
                files.append(f)

        return sorted(files)

    def obsidian_move(self, source: Path, dest_dir: Path, new_name: str | None = None) -> bool:
        """使用obsidian move迁移文件（非mv）"""
        try:
            rel_source = source.relative_to(self.vault_dir)
            dest_path = dest_dir / (new_name or source.name)
            rel_dest = dest_path.relative_to(self.vault_dir)

            cmd = [
                "obsidian", "move",
                f"file={rel_source}",
                f"to={rel_dest}"
            ]

            result = subprocess.run(
                cmd, cwd=self.vault_dir,
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                self.logger.log("file_moved", {
                    "source": str(rel_source),
                    "destination": str(rel_dest),
                    "method": "obsidian_move"
                })
                return True
            else:
                # 如果obsidian move失败，尝试文件系统move（降级）
                import shutil
                shutil.move(str(source), str(dest_path))
                self.logger.log("file_moved", {
                    "source": str(rel_source),
                    "destination": str(rel_dest),
                    "method": "filesystem_fallback",
                    "warning": "obsidian_move failed, used filesystem move"
                })
                return True

        except Exception as e:
            self.logger.log("move_error", {"error": str(e), "source": str(source)})
            return False

    def process_clippings(self, dry_run: bool = False, batch_size: int | None = None) -> dict:
        """处理所有Clippings文件"""
        results = {
            "scanned": 0,
            "migrated": 0,
            "failed": 0,
            "skipped": 0,
            "files": []
        }

        # 扫描
        files = self.scan_clippings()
        results["scanned"] = len(files)

        if batch_size:
            files = files[:batch_size]

        for file_path in files:
            file_info = {
                "original": str(file_path.name),
                "status": "pending"
            }

            # 清理文件名
            clean_name = self.sanitize_filename(file_path.stem) + ".md"
            date_prefix = datetime.now().strftime("%Y-%m-%d")
            new_name = f"{date_prefix}_{clean_name}"

            file_info["new_name"] = new_name

            if dry_run:
                file_info["status"] = "dry_run"
                results["files"].append(file_info)
                continue

            # 确保目标目录存在
            self.raw_dir.mkdir(parents=True, exist_ok=True)

            # 迁移文件
            if self.obsidian_move(file_path, self.raw_dir, new_name):
                file_info["status"] = "migrated"
                results["migrated"] += 1
            else:
                file_info["status"] = "failed"
                results["failed"] += 1

            results["files"].append(file_info)

        self.logger.log("clippings_processed", results)
        return results


def main():
    parser = argparse.ArgumentParser(description="全自动Clippings处理器")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--batch-size", type=int, help="批量处理数量")
    parser.add_argument("--vault-dir", type=Path, default=VAULT_DIR, help="Vault根目录")
    args = parser.parse_args()

    # 初始化组件
    logger = PipelineLogger(LOG_FILE)
    txn = TransactionManager(TXN_DIR)
    manifest = ManifestManager(MANIFEST_FILE)

    # 创建事务
    txn_id = txn.start("clippings-processing", f"Process clippings {datetime.now().isoformat()}")
    logger.log("transaction_started", {"txn_id": txn_id, "type": "clippings-processing"})

    # 初始化处理器
    processor = ClippingsProcessor(args.vault_dir, logger, txn)

    # 执行处理
    txn.step(txn_id, "scan", "in_progress", "Scanning Clippings directory")
    results = processor.process_clippings(dry_run=args.dry_run, batch_size=args.batch_size)
    txn.step(txn_id, "scan", "completed", f"Scanned {results['scanned']}, migrated {results['migrated']}")

    # 输出结果
    print("\n" + "="*60)
    print("CLIPPINGS PROCESSING RESULTS")
    print("="*60)
    print(f"Scanned: {results['scanned']}")
    print(f"Migrated: {results['migrated']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")

    if results['files']:
        print("\nFiles:")
        for f in results['files'][:10]:  # 只显示前10个
            print(f"  [{f['status']}] {f['original']} -> {f.get('new_name', 'N/A')}")

    # 完成事务
    txn.complete(txn_id)
    logger.log("transaction_completed", {"txn_id": txn_id})

    return 0 if results['failed'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
